import { useEffect, useRef, useState } from "react";
import TextInput from "@leafygreen-ui/text-input";
import Button from "@leafygreen-ui/button";
import Badge from "@leafygreen-ui/badge";
import Banner from "@leafygreen-ui/banner";
import Toggle from "@leafygreen-ui/toggle";
import { H3, Body, Subtitle } from "@leafygreen-ui/typography";
import { search, facets } from "../api";
import { T, fmtBRL } from "../theme";
import ProductTable, { priceCol, MqlBlock } from "../components/ProductTable";

// highlight the typed prefix (demonstrates autocomplete)
function highlight(text, q) {
  if (!text || !q) return text;
  const i = text.toLowerCase().indexOf(q.toLowerCase());
  if (i < 0) return text;
  return (<>{text.slice(0, i)}<b style={{ color: T.green }}>{text.slice(i, i + q.length)}</b>{text.slice(i + q.length)}</>);
}

const FAIXA_LABELS = { 0: "0–100", 100: "100–500", 500: "500–1K", 1000: "1K–3K", 3000: "3K–5K", 5000: "5K–10K", 10000: "10K–15K" };

const SUGGESTIONS = ["adidass", "samsumg", "notebook gamer"];

export default function AtlasSearch() {
  const [q, setQ] = useState("");
  const requestId = useRef(0);
  const searchPending = useRef(false);
  const [resultQuery, setResultQuery] = useState("");
  useEffect(() => () => { requestId.current += 1; }, []);
  const [synonyms, setSynonyms] = useState(false);
  const [cats, setCats] = useState([]);          // faceted navigation: selected categories
  const [data, setData] = useState(null);
  const [facetData, setFacetData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [facetsLoading, setFacetsLoading] = useState(false);

  const run = async (selectedCats, queryOverride) => {
    const categorias = selectedCats ?? cats;
    const query = queryOverride ?? q;
    if (searchPending.current || !query.trim()) return;
    const current = ++requestId.current;
    searchPending.current = true;
    setLoading(true);
    setFacetsLoading(true);
    setFacetData(null);
    const facetRequest = facets({ query, synonyms }).catch(() => null);
    try {
      const res = await search({ query, synonyms, categorias: categorias.length ? categorias : null });
      if (current !== requestId.current) return;
      setData(res); setResultQuery(query);
      searchPending.current = false;
      setLoading(false);
      const fac = await facetRequest;
      if (current === requestId.current) setFacetData(fac);
    } catch (e) { if (current === requestId.current) setData({ error: `Falha na busca: ${e.message}` }); }
    finally { if (current === requestId.current) { searchPending.current = false; setLoading(false); setFacetsLoading(false); } }
  };

  const toggleCat = (cat) => {
    const next = cats.includes(cat) ? cats.filter((c) => c !== cat) : [...cats, cat];
    setCats(next);
    run(next);
  };

  const cols = [
    { key: "nome", label: "Produto", color: T.text, render: (r) => highlight(r.nome, resultQuery) },
    priceCol(),
    { key: "categoria", label: "Categoria" },
    { key: "avaliacao_media", label: "Avaliação", render: (r) => `⭐ ${(r.avaliacao_media || 0).toFixed(1)}` },
    { key: "em_estoque", label: "Estoque", align: "center", render: (r) => (r.em_estoque ? "✅" : "❌") },
  ];

  return (
    <div>
      <div className="section-label">Atlas Search · Full-Text · Facets</div>
      <H3 id="atlas-search-title" style={{ color: T.text, fontFamily: T.font, letterSpacing: "-0.02em" }}>Busca Inteligente de Produtos</H3>
      <Body style={{ color: T.text2, marginBottom: 16 }}>
        Autocomplete, fuzzy matching, highlight e navegação facetada ($searchMeta) — como um e-commerce real.
      </Body>

      <form className="query-toolbar" aria-label="Buscar no catálogo" aria-busy={loading} onSubmit={(event) => { event.preventDefault(); run(); }}>
        <div className="query-toolbar__field">
          <span className="query-toolbar__label">Consulta</span>
          <TextInput type="search" aria-labelledby="atlas-search-title" placeholder="Ex.: notebook gamer, adidass, samsumg…"
            value={q} onChange={(e) => setQ(e.target.value)}
            darkMode sizeVariant="large" />
        </div>
        <label className="query-toolbar__option">
          <Toggle checked={synonyms} onChange={setSynonyms} aria-label="Sinônimos" size="small" darkMode />
          <span>Sinônimos</span>
        </label>
        <Button type="button" variant="primary" onClick={() => run()} disabled={loading || !q.trim()} darkMode>
          {loading ? "Buscando…" : "Buscar catálogo"}
        </Button>
      </form>

      {facetsLoading && data?.results?.length > 0 && (
        <div className="facet-loading" role="status">
          <span className="pov-skeleton" aria-hidden="true" />
          Resultados prontos · carregando facetas em segundo plano
        </div>
      )}

      {!data && (
        <div className="search-empty">
          <span className="search-empty__glyph" aria-hidden="true" />
          <Subtitle style={{ color: T.text }}>Busque um produto para começar</Subtitle>
          <Body style={{ color: T.text3, marginTop: 4, marginBottom: 14 }}>
            Termos com erro de digitação (adidass, samsumg) testam o fuzzy matching.
          </Body>
          <div className="query-suggestions">
            <span>Experimente:</span>
            {SUGGESTIONS.map((s) => (
              <Button key={s} size="small" variant="default" darkMode
                onClick={() => { setQ(s); run(cats, s); }}>{s}</Button>
            ))}
          </div>
        </div>
      )}

      {data && synonyms && !data.synonyms_fallback && (
        <Banner variant="info" darkMode style={{ marginTop: 12 }}>
          Modo sinônimos usa o operador <code>text</code> com o mapping <code>sinonimos_produtos</code> — sem
          autocomplete/fuzzy/boost de negócio, então o ranking muda em relação ao modo padrão.
        </Banner>
      )}

      {data?.synonyms_fallback && (
        <Banner variant="warning" darkMode style={{ marginTop: 12 }}>
          Sinônimos indisponíveis neste índice (campo usa analyzer <code>lucene.portuguese</code>). Exibindo resultados sem sinônimos.
        </Banner>
      )}

      {data?.error && <Banner variant="danger" darkMode style={{ marginTop: 12 }}>{data.error}</Banner>}

      {/* Faceted navigation — categorias e faixas de preço vindas do $searchMeta */}
      {facetData?.categorias?.length > 0 && (
        <div style={{ margin: "14px 0 4px" }}>
          <div style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.1em", color: T.text3, marginBottom: 6 }}>
            Categorias ($searchMeta · clique para filtrar)
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {facetData.categorias.map((b) => {
              const active = cats.includes(b._id);
              return (
                <button key={b._id} onClick={() => toggleCat(b._id)} style={{
                  cursor: "pointer", fontSize: 12, fontFamily: T.font, padding: "4px 10px", borderRadius: 12,
                  border: `1px solid ${active ? T.green : T.border}`,
                  background: active ? "rgba(0,237,100,0.12)" : T.surface,
                  color: active ? T.green : T.text2,
                }}>
                  {b._id} <span style={{ fontFamily: T.mono, fontSize: 11, opacity: 0.8 }}>{(b.count ?? 0).toLocaleString("pt-BR")}</span>
                </button>
              );
            })}
          </div>
          {facetData.faixas_preco?.length > 0 && (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 6 }}>
              {facetData.faixas_preco.map((b) => (
                <span key={String(b._id)} style={{
                  fontSize: 11, fontFamily: T.mono, padding: "3px 9px", borderRadius: 12,
                  border: `1px solid ${T.borderSub}`, background: T.surface, color: T.text3,
                }}>
                  R$ {FAIXA_LABELS[b._id] ?? b._id}: {(b.count ?? 0).toLocaleString("pt-BR")}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {data?.results?.length > 0 && (
        <>
          <div style={{ display: "flex", gap: 8, margin: "14px 0", flexWrap: "wrap" }}>
            <Badge variant="green">
              {data.total_matches?.toLocaleString("pt-BR")} {data.filters_in_search ? "no índice (com filtros)" : "no índice (antes dos filtros)"}
            </Badge>
            <Badge variant="blue">{data.results.length} exibidos</Badge>
            <Badge variant={data.filters_in_search ? "green" : "yellow"}>
              {data.filters_in_search ? "filtros dentro do $search" : "filtros via $match (pós-busca)"}
            </Badge>
            <Badge variant="lightgray">⏱ {data.elapsed_ms} ms</Badge>
            <Badge variant="lightgray">Menor {fmtBRL(Math.min(...data.results.map((r) => r.preco)))}</Badge>
            <Badge variant="lightgray">Maior {fmtBRL(Math.max(...data.results.map((r) => r.preco)))}</Badge>
          </div>
          <ProductTable rows={data.results.slice(0, 30)} columns={cols} />

          {/* Relevance transparency — why the top results ranked where they did */}
          {data.results[0]?.scoreDetails && (
            <details style={{ marginTop: 14, background: T.surface, border: `1px solid ${T.border}`, borderRadius: 12 }}>
              <summary style={{ cursor: "pointer", padding: "10px 14px", fontSize: 13, color: T.text2, listStyle: "none" }}>
                🔬 scoreDetails — por que os top 3 rankearam assim (relevância × avaliação do produto)
              </summary>
              <pre style={{ margin: 0, padding: 14, borderTop: `1px solid ${T.border}`, overflow: "auto",
                            fontSize: 11, color: T.text2, fontFamily: T.mono, maxHeight: 320, background: T.codeBg }}>
                {JSON.stringify(
                  data.results.slice(0, 3).map((r) => ({ nome: r.nome, score: r.score, scoreDetails: r.scoreDetails })),
                  null, 2)}
              </pre>
            </details>
          )}

          <MqlBlock pipeline={data.pipeline} collection="POC.produtos" />
        </>
      )}
    </div>
  );
}
