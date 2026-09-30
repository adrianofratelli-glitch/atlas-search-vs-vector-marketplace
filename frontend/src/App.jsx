import { lazy, Suspense, useEffect, useState } from "react";
import LeafyGreenProvider from "@leafygreen-ui/leafygreen-provider";
import { getStats } from "./api";
import { T, fmtCount } from "./theme";
import Leaf from "./components/Leaf";

const AtlasSearch = lazy(() => import("./tabs/AtlasSearch"));
const SearchVsVector = lazy(() => import("./tabs/SearchVsVector"));
const HybridRRF = lazy(() => import("./tabs/HybridRRF"));
const AiAgent = lazy(() => import("./tabs/AiAgent"));
const Analytics = lazy(() => import("./tabs/Analytics"));
const Similares = lazy(() => import("./tabs/Similares"));
const ReviewsRag = lazy(() => import("./tabs/ReviewsRag"));

const TABS = [AtlasSearch, SearchVsVector, HybridRRF, Similares, Analytics, ReviewsRag, AiAgent];
const TAB_LABELS = ["Full-text", "Search × Vector", "Híbrida", "Similares", "Analytics", "Reviews RAG", "Agente"];

const NAV_GROUPS = [
  { label: "Busca", tabs: [0, 1, 2, 3] },
  { label: "Analytics", tabs: [4] },
  { label: "Reviews RAG", tabs: [5] },
  { label: "Agente", tabs: [6] },
];

export default function App() {
  const [tab, setTab] = useState(0);
  const [stats, setStats] = useState(null);
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    getStats()
      .then((s) => { setStats(s); setOffline(!!s.degraded); })
      .catch(() => setOffline(true));
  }, []);

  const group = NAV_GROUPS.find((item) => item.tabs.includes(tab));
  const ActiveTab = TABS[tab];
  const c = stats?.collections || {};
  const indices = stats?.indices || [];
  const readyCount = indices.filter((i) => i.status === "READY").length;

  return (
    <LeafyGreenProvider darkMode>
      <div className="search-shell" data-pov-shell>
        <a className="pov-skip-link" href="#conteudo-principal">Pular para o conteúdo</a>
        <main id="conteudo-principal" tabIndex={-1} className="search-main">
          <header className="search-header">
            <div className="search-header__topline">
              <div className="search-brand">
                <span className="search-brand__mark" aria-hidden="true"><Leaf size={30} /></span>
                <span>
                  <span className="search-brand__eyebrow">MongoDB Atlas · Discovery Lab</span>
                  <h1>Search <span>×</span> Vector</h1>
                </span>
              </div>
              <div className={`search-status ${offline ? "is-offline" : ""}`} role="status" aria-live="polite">
                <span className="search-status__dot" aria-hidden="true" />
                <span>{offline ? "Atlas indisponível" : `${fmtCount(c.produtos)} documentos`}</span>
                {!offline && <span className="search-status__detail">{readyCount}/{indices.length || "—"} índices prontos</span>}
              </div>
            </div>

            <nav className="search-tabs" aria-label="Capacidades">
              {NAV_GROUPS.map((item) => (
                <button key={item.label} type="button" className={group === item ? "active" : ""}
                  aria-current={group === item ? "page" : undefined} onClick={() => setTab(item.tabs[0])}>
                  {item.label}
                </button>
              ))}
            </nav>
            {group.tabs.length > 1 && <nav className="search-tabs" aria-label="Cenários de busca">
              {group.tabs.map((index) => (
                <button
                  key={TAB_LABELS[index]}
                  type="button"
                  className={tab === index ? "active" : ""}
                  aria-current={tab === index ? "page" : undefined}
                  onClick={() => setTab(index)}
                >
                  <span className="search-tab__index" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
                  <span>{TAB_LABELS[index]}</span>
                </button>
              ))}
            </nav>}
          </header>

          {offline && (
            <div style={{ background: "rgba(255,105,96,0.1)", border: `1px solid ${T.red}44`,
                          borderRadius: 8, padding: 16, color: T.red, marginBottom: 18 }}>
              {stats
                ? <>Backend no ar, mas o cluster Atlas está inacessível — verifique a conexão/IP access list. Os dados abaixo estão zerados.</>
                : <>Backend não respondeu em <code>http://localhost:8200</code>. Rode{" "}
                    <code>bash start.sh</code> na raiz, ou <code>uvicorn main:app --port 8200</code> dentro de <code>backend/</code>.</>}
            </div>
          )}

          <div className="fade-up search-workspace" key={tab}>
            <Suspense fallback={<div className="search-module-loading"><span className="pov-skeleton" />Carregando módulo…</div>}>
              <ActiveTab />
            </Suspense>
          </div>
        </main>
      </div>
    </LeafyGreenProvider>
  );
}
