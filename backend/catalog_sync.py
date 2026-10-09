"""
catalog_sync.py — keeps `produtos_vector` in step with `produtos`.

The PoV stores the catalog twice on purpose: `produtos` (lexical index) and
`produtos_vector` (autoEmbed vector index + a second lexical index, because
native $rankFusion/$scoreFusion need both sub-pipelines on the same
collection). The seed copies a `$sample` subset; after that, every write to
`produtos` is mirrored here by a change stream on the same cluster:

  insert / replace / update  -> upsert the full document into produtos_vector
                                (autoEmbed then embeds `descricao` inside Atlas)
  delete                     -> delete the mirrored copy

No queue, no ETL, no embedding job: one change stream in the same database.
The resume token is persisted in `catalog_sync_state`, so a restart continues
where it stopped (inside the oplog window) and replays are idempotent.

`scripts/reset_demo.py --rebuild-catalog` pauses the mirror while it
regenerates the catalog (the seed decides the sample) and records a cluster
time; events at or before it are skipped, so a lagging worker never copies the
seed. Default on; `CATALOG_SYNC=0` only exists to revert.

Pure helpers (`plan_event`, `should_skip`) carry the logic and are unit-tested
offline; `CatalogMirror.run_forever()` is the thin I/O loop.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import timezone

from pymongo.errors import OperationFailure, PyMongoError

logger = logging.getLogger("searchxvector.catalog_sync")

SOURCE = "produtos"
TARGET = "produtos_vector"
STATE_COLLECTION = "catalog_sync_state"
STATE_ID = f"{SOURCE}->{TARGET}"
WATCHED_OPS = ("insert", "replace", "update", "delete")
STATE_REFRESH_S = 0.5
TOKEN_SAVE_EVERY_S = 1.0


def enabled() -> bool:
    return os.getenv("CATALOG_SYNC", "1").strip().lower() not in ("0", "false", "no", "off")


def plan_event(event: dict) -> tuple[str, dict | None, dict | None]:
    """Change event -> (action, filter, document). Pure.

    action is "upsert", "delete" or "skip". The mirror is keyed by
    `produto_id` (the seed copy gets fresh _ids); a mirrored insert keeps the
    source `_id` so a later delete without pre-image still finds it.
    """
    op = event.get("operationType")
    key = (event.get("documentKey") or {}).get("_id")
    if op in ("insert", "replace", "update"):
        doc = event.get("fullDocument")
        if not doc:  # updated then deleted before the lookup: the delete event follows
            return "skip", None, None
        return "upsert", {"produto_id": doc.get("produto_id")} if doc.get("produto_id") else {"_id": key}, doc
    if op == "delete":
        before = event.get("fullDocumentBeforeChange") or {}
        clauses = [{"_id": key}]
        if before.get("produto_id"):
            clauses.append({"produto_id": before["produto_id"]})
        return "delete", {"$or": clauses} if len(clauses) > 1 else clauses[0], None
    return "skip", None, None


def should_skip(event: dict, state: dict | None) -> bool:
    """Paused by reset_demo, or older than the cluster time it recorded."""
    state = state or {}
    if state.get("paused"):
        return True
    skip_before = state.get("skip_before")
    cluster_time = event.get("clusterTime")
    return bool(skip_before and cluster_time and cluster_time <= skip_before)


def apply_event(target, action: str, flt: dict | None, doc: dict | None) -> str:
    if action == "upsert":
        body = {k: v for k, v in doc.items() if k != "_id"}
        if target.replace_one(flt, body).matched_count:
            return "updated"
        target.replace_one({"_id": doc["_id"]}, doc, upsert=True)
        return "inserted"
    if action == "delete":
        return f"deleted:{target.delete_many(flt).deleted_count}"
    return "skipped"


class CatalogMirror:
    def __init__(self, db):
        self.db = db
        self.source = db[SOURCE]
        self.target = db[TARGET]
        self.state_coll = db[STATE_COLLECTION]
        self._stop = threading.Event()
        self._state: dict = {}
        self._state_at = 0.0
        self.stats = {"applied": 0, "skipped": 0, "errors": 0, "last_event_lag_ms": None,
                      "running": False, "last_error": None}

    # ── state ────────────────────────────────────────────────────────────
    def _refresh_state(self, force: bool = False) -> dict:
        now = time.monotonic()
        if force or now - self._state_at > STATE_REFRESH_S:
            self._state = self.state_coll.find_one({"_id": STATE_ID}) or {}
            self._state_at = now
        return self._state

    def _save_token(self, token) -> None:
        self.state_coll.update_one({"_id": STATE_ID}, {"$set": {"resume_token": token, "updated_at": time.time()}},
                                   upsert=True)

    def prepare(self) -> None:
        """Pre-images let a delete find the legacy seed copy (fresh _id). Best effort."""
        try:
            self.db.command("collMod", SOURCE, changeStreamPreAndPostImages={"enabled": True})
        except OperationFailure as exc:
            logger.info("catalog_sync: pre-images not enabled (%s); deletes match by _id", exc.code)
        try:
            self.target.create_index("produto_id")
        except PyMongoError as exc:
            logger.info("catalog_sync: produto_id index not created (%s)", type(exc).__name__)

    # ── loop ─────────────────────────────────────────────────────────────
    def run_once(self) -> None:
        state = self._refresh_state(force=True)
        token = state.get("resume_token")
        kwargs = {"full_document": "updateLookup", "full_document_before_change": "whenAvailable",
                  "max_await_time_ms": 1000}
        if token:
            kwargs["resume_after"] = token
        pipeline = [{"$match": {"operationType": {"$in": list(WATCHED_OPS)}}}]
        last_saved = time.monotonic()
        pending = None
        with self.source.watch(pipeline, **kwargs) as stream:
            self.stats["running"] = True
            while not self._stop.is_set() and stream.alive:
                event = stream.try_next()
                if event is None:
                    if pending is not None:
                        self._save_token(pending)
                        pending, last_saved = None, time.monotonic()
                    continue
                if should_skip(event, self._refresh_state()):
                    self.stats["skipped"] += 1
                else:
                    action, flt, doc = plan_event(event)
                    result = apply_event(self.target, action, flt, doc)
                    self.stats["applied"] += action != "skip"
                    if event.get("wallTime"):
                        wt = event["wallTime"]  # naive UTC from the driver
                        wall = (wt if wt.tzinfo else wt.replace(tzinfo=timezone.utc)).timestamp()
                        self.stats["last_event_lag_ms"] = round((time.time() - wall) * 1000)
                    logger.debug("catalog_sync %s %s", event.get("operationType"), result)
                pending = stream.resume_token
                if time.monotonic() - last_saved > TOKEN_SAVE_EVERY_S:
                    self._save_token(pending)
                    pending, last_saved = None, time.monotonic()
            if pending is not None:
                self._save_token(pending)
            invalidated = not stream.alive and not self._stop.is_set()
        if invalidated:
            # Source dropped/renamed (catalog rebuild): its token cannot be
            # resumed; start again from "now" on the new collection.
            logger.info("catalog_sync: stream invalidated; restarting from now")
            self.state_coll.update_one({"_id": STATE_ID}, {"$unset": {"resume_token": ""}})

    def run_forever(self) -> None:
        backoff = 1.0
        prepared = False
        while not self._stop.is_set():
            try:
                if not prepared:
                    self.prepare()
                    prepared = True
                self.run_once()
                backoff = 1.0
            except OperationFailure as exc:
                # Resume token outside the oplog window or stream invalidated
                # (collection dropped by a rebuild): restart from "now".
                self.stats.update(errors=self.stats["errors"] + 1, last_error=f"OperationFailure:{exc.code}")
                logger.warning("catalog_sync: stream failed (%s); restarting from now", exc.code)
                try:
                    self.state_coll.update_one({"_id": STATE_ID}, {"$unset": {"resume_token": ""}})
                except PyMongoError:
                    pass
            except PyMongoError as exc:
                self.stats.update(errors=self.stats["errors"] + 1, last_error=type(exc).__name__)
                logger.warning("catalog_sync: %s; retrying in %.0fs", type(exc).__name__, backoff)
            finally:
                self.stats["running"] = False
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, 30.0)

    def stop(self) -> None:
        self._stop.set()


_mirror: CatalogMirror | None = None
_thread: threading.Thread | None = None


def start(db) -> CatalogMirror | None:
    global _mirror, _thread
    if not enabled():
        logger.info("catalog_sync disabled (CATALOG_SYNC=0)")
        return None
    if _thread and _thread.is_alive():
        return _mirror
    _mirror = CatalogMirror(db)
    _thread = threading.Thread(target=_mirror.run_forever, name="catalog-sync", daemon=True)
    _thread.start()
    return _mirror


def stop() -> None:
    if _mirror:
        _mirror.stop()


def status() -> dict:
    if not enabled():
        return {"enabled": False}
    if not _mirror:
        return {"enabled": True, "running": False}
    return {"enabled": True, "source": SOURCE, "target": TARGET, **_mirror.stats}


# ── reset_demo hooks ─────────────────────────────────────────────────────────
def pause(db) -> None:
    db[STATE_COLLECTION].update_one({"_id": STATE_ID}, {"$set": {"paused": True}, "$unset": {"resume_token": ""}},
                                    upsert=True)


def resume_from_now(db) -> None:
    """Unpause and skip every event up to this cluster time (the seed's writes)."""
    client = db.client
    with client.start_session() as s:
        db.command("ping", session=s)
        now = s.operation_time
        db[STATE_COLLECTION].update_one({"_id": STATE_ID},
                                        {"$set": {"paused": False, "skip_before": now},
                                         "$unset": {"resume_token": ""}}, upsert=True, session=s)
