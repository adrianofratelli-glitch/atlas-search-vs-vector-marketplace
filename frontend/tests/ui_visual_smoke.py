"""Responsive and live-flow smoke test for the Search × Vector stage UI."""

import os
from pathlib import Path

from playwright.sync_api import sync_playwright


BASE_URL = "http://127.0.0.1:5273"
VIEWPORTS = ((1440, 1000), (1154, 700), (768, 1024), (360, 800))
SCREENSHOT_DIR = Path("/tmp/search-vector-ui")
README_SCREENSHOT = Path(__file__).resolve().parents[2] / "docs/screenshots/atlas-search.png"


def assert_shell(page, width: int) -> None:
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle", timeout=120_000)

    assert page.get_by_role("heading", name="Search × Vector").is_visible()
    assert page.locator(".search-tabs button").count() == 7
    assert page.get_by_role("button", name="Full-text", exact=True).get_attribute("aria-current") == "page"

    overflow = page.evaluate(
        "document.documentElement.scrollWidth - window.innerWidth"
    )
    assert overflow <= 1, f"body overflowed horizontally by {overflow}px at {width}px"

    nav_background = page.locator(".search-tabs").evaluate(
        "element => getComputedStyle(element).backgroundColor"
    )
    assert nav_background != "rgba(0, 0, 0, 0)", "navigation surface CSS was not applied"

    page.screenshot(
        path=str(SCREENSHOT_DIR / f"search-{width}.png"),
        full_page=True,
    )


def assert_keyboard_and_live_search(page) -> None:
    page.evaluate("document.activeElement.blur()")
    page.keyboard.press("Tab")
    assert page.locator(".pov-skip-link").evaluate("element => element === document.activeElement")

    for label in (
        "Search × Vector",
        "Híbrida",
        "Similares",
        "Analytics",
        "Reviews RAG",
        "Agente",
        "Full-text",
    ):
        page.get_by_role("button", name=label, exact=True).click()
        page.wait_for_timeout(120)
        assert page.get_by_role("button", name=label, exact=True).get_attribute("aria-current") == "page"

    assert "Atlas indisponível" not in page.locator("body").inner_text()
    query = page.get_by_placeholder("Ex.: notebook gamer, adidass, samsumg…")
    query.fill("notebook gamer")
    search_button = page.get_by_role("button", name="Buscar catálogo")
    assert search_button.is_enabled()
    with page.expect_response(
        lambda response: response.url.endswith("/search")
        and response.request.method == "POST",
        timeout=90_000,
    ) as response_info:
        search_button.click()
    assert response_info.value.ok, f"/search returned {response_info.value.status}"
    search_payload = response_info.value.json()
    assert search_payload.get("results"), f"/search returned no results: {search_payload}"
    try:
        page.wait_for_function(
            "document.body.innerText.toLowerCase().includes('no índice')",
            timeout=15_000,
        )
    except Exception:
        page.screenshot(path=str(SCREENSHOT_DIR / "search-live-failure.png"), full_page=True)
        print(page.locator("body").inner_text())
        raise
    body = page.locator("body").inner_text().lower()
    assert "falha na busca" not in body
    assert "no índice" in body
    page.screenshot(path=str(SCREENSHOT_DIR / "search-live-1440.png"), full_page=True)
    if os.getenv("UPDATE_README_SCREENSHOT") == "1":
        page.screenshot(path=str(README_SCREENSHOT))


def main() -> None:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for width, height in VIEWPORTS:
            page_errors: list[str] = []
            context = browser.new_context(viewport={"width": width, "height": height})
            page = context.new_page()
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            assert_shell(page, width)
            assert not page_errors, f"page errors at {width}px: {page_errors}"
            context.close()

        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = context.new_page()
        assert_shell(page, 1440)
        assert_keyboard_and_live_search(page)
        context.close()
        browser.close()

    print(f"UI smoke passed; screenshots: {SCREENSHOT_DIR}")


if __name__ == "__main__":
    main()
