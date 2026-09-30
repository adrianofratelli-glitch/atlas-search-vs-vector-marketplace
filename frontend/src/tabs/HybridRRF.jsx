import { useState } from "react";
import TextInput from "@leafygreen-ui/text-input";
import Button from "@leafygreen-ui/button";
import Badge from "@leafygreen-ui/badge";
import Banner from "@leafygreen-ui/banner";
import { H3, Body, Subtitle, InlineCode } from "@leafygreen-ui/typography";
import { hybridNative, hybridScoreFusion } from "../api";
import { T } from "../theme";
import ProductTable, { priceCol, MqlBlock } from "../components/ProductTable";

export default function HybridRRF() {
  const [q, setQ] = useState("");
  const [engine, setEngine] = useState("native"); // "native" ($rankFusion) | "score" ($scoreFusion)
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  const run = async (eng) => {
    const useEngine = eng ?? engine;
    if (loading || !q.trim()) return;
    setLoading(true);
    setData(null);
    try {
      const fetchers = {
        native: () => hybridNative(q),
        score: () => hybridScoreFusion(q),
      };
      setData(await fetchers[useEngine]());
    } catch (e) { setData({ error: `Falha na busca híbrida: ${e.message}` }); }
    finally { setLoading(false); }
  };

  const switchEngine = (eng) => {
    if (loading) return;
    setData(null);
    setEngine(eng);
    if (q.trim()) run(eng);
  };

  const isScoreResult = engine === "score" && data?.native !== false;
  const rows = (data?.fused ?? []).map((row, index) => ({ ...row, position: index + 1 }));
  const rankCounts = (key) => rows.reduce((counts, row) => {
    if (Number.isInteger(row[key])) counts.set(row[key], (counts.get(row[key]) ?? 0) + 1);
    return counts;
  }, new Map());
  const searchRanks = rankCounts("rank_search");
  const vectorRanks = rankCounts("rank_vector");
  const hasTies = [...searchRanks.values(), ...vectorRanks.values()].some((count) => count > 1);
  const formatScore = (value) => Number.isFinite(value) ? value.toFixed(5).replace(".", ",") : "—";
  const rankCell = (row, key, contribution, counts) => (
    <div style={{ whiteSpace: "nowrap" }}>
      <span>{row[key] ?? "—"}</span>
      {counts.get(row[key]) > 1 && <span style={{ color: T.text2, fontFamily: T.font, fontSize: 11 }}> · empate</span>}
      <div style={{ color: T.text3, fontSize: 10, marginTop: 3 }}>
        + {formatScore(row[contribution])} RRF
      </div>
    </div>
  );
  const positionCol = { key: "position", label: "Posição final", mono: true, align: "center" };

  const rankCols = [
    positionCol,
    { key: "nome", label: "Produto", color: T.text },
    priceCol(),
    { key: "rank_search", label: "Rank Search", mono: true, align: "center", render: (r) => rankCell(r, "rank_search", "s_score", searchRanks) },
    { key: "rank_vector", label: "Rank Vector", mono: true, align: "center", render: (r) => rankCell(r, "rank_vector", "v_score", vectorRanks) },
    { key: "both", label: "Nos dois", align: "center", render: (r) => (r.both ? "🏆" : "") },
  ];

  const scoreCols = [
    positionCol,
    { key: "nome", label: "Produto", color: T.text },
    priceCol(),
    { key: "score", label: "Score final", mono: true, color: T.green, render: (r) => (
        <span style={{ fontWeight: 700, fontSize: 13 }}>{formatScore(r.score)}</span>
      ) },
    { key: "s_score", label: "Search (norm.)", mono: true, align: "center", color: T.text3, render: (r) => formatScore(r.s_score) },
    { key: "v_score", label: "Vector (norm.)", mono: true, align: "center", color: T.text3, render: (r) => formatScore(r.v_score) },
    { key: "both", label: "Nos dois", align: "center", render: (r) => (r.both ? "🏆" : "") },
  ];

  const cols = isScoreResult ? scoreCols : rankCols;
  const scoreKey = isScoreResult ? "score" : "rrf";
  const isNativeResult = data?.native === true;

  return (
    <div>
      <div className="section-label">Hybrid · RRF vs RSF</div>
      <H3 id="hybrid-title" style={{ color: T.text, fontFamily: T.font, letterSpacing: "-0.02em" }}>Hybrid Search — Rank Fusion vs Score Fusion</H3>
      <Body style={{ color: T.text2, marginBottom: 6 }}>
        Combina Atlas Search + Vector Search num único ranking, por posição (<InlineCode darkMode>$rankFusion</InlineCode>) ou por score (<InlineCode darkMode>$scoreFusion</InlineCode>).
      </Body>

      <div style={{ display: "flex", gap: 12, alignItems: "flex-end", margin: "12px 0" }}>
        <div style={{ flex: 1 }}>
          <TextInput aria-labelledby="hybrid-title" placeholder="tênis de corrida, fone sem fio…"
            value={q} onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && run()} darkMode />
        </div>
        <div style={{ display: "flex", borderRadius: 6, overflow: "hidden", border: `1px solid ${T.border}` }}>
          {[["native", "$rankFusion (RRF)"], ["score", "$scoreFusion (RSF)"]].map(([eng, label]) => (
            <button key={eng} disabled={loading} onClick={() => switchEngine(eng)} style={{
              cursor: "pointer", fontSize: 12, fontFamily: T.font, padding: "9px 12px", border: "none",
              background: engine === eng ? "rgba(0,237,100,0.15)" : T.surface,
              color: engine === eng ? T.green : T.text3, fontWeight: engine === eng ? 700 : 400,
            }}>{label}</button>
          ))}
        </div>
        <Button variant="primary" onClick={() => run()} disabled={loading} darkMode>
          {loading ? "Fundindo…" : "Buscar"}
        </Button>
      </div>

      {engine === "native" && (
        <Body style={{ color: T.text3, fontSize: 12, marginBottom: 16 }}>
          <InlineCode darkMode>$rankFusion</InlineCode>: combina pelo <b>rank</b> de cada documento em cada busca, preservando empates
          (a magnitude do score bruto não entra na soma) — <InlineCode darkMode>score = Σ 1/(k + rank)</InlineCode>. Roda no
          servidor, num único aggregation stage.
        </Body>
      )}
      {engine === "score" && (
        <Body style={{ color: T.text3, fontSize: 12, marginBottom: 16 }}>
          <InlineCode darkMode>$scoreFusion</InlineCode>: combina pelo <b>score real</b> de cada sub-pipeline —
          normaliza os dois para 0-1 (<InlineCode darkMode>minMaxScaler</InlineCode>) e tira a média ponderada.
          Também roda no servidor, num único aggregation stage.
        </Body>
      )}

      {!data && !loading && (
        <div style={{ textAlign: "center", padding: "28px 0", color: T.text3 }}>
          <div style={{ fontSize: 30, marginBottom: 8 }}>🔀</div>
          <Subtitle style={{ color: T.text }}>Digite uma consulta para ver a fusão</Subtitle>
          <Body style={{ color: T.text3, marginTop: 4 }}>Itens nos dois rankings 🏆 sobem ao topo.</Body>
        </div>
      )}

      {data?.error && <Banner variant="danger" darkMode>{data.error}</Banner>}

      {data && data.native === false && (
        <Banner variant="warning" darkMode style={{ marginBottom: 12 }}>{data.reason}</Banner>
      )}
      {engine === "score" && data?.native === true && data?.degraded_reason && (
        <Banner variant="warning" darkMode style={{ marginBottom: 12 }}>{data.degraded_reason}</Banner>
      )}

      {data?.fused?.length > 0 && (
        <>
          <div style={{ display: "flex", gap: 8, marginBottom: 14, flexWrap: "wrap" }}>
            {engine === "native" && (
              <Badge variant={isNativeResult ? "green" : "yellow"}>
                {isNativeResult ? "⚙️ $rankFusion no servidor" : "fallback: RRF na aplicação"}
              </Badge>
            )}
            {engine === "score" && (
              <Badge variant={isNativeResult ? "green" : "yellow"}>
                {isNativeResult ? "⚙️ $scoreFusion no servidor" : "fallback: RRF na aplicação"}
              </Badge>
            )}
            <Badge variant="green">Atlas Search {data.counts.n_search}</Badge>
            <Badge variant="blue">Vector {data.counts.n_vector}</Badge>
            <Badge variant="darkgray">Fusão {data.fused.length}</Badge>
            <Badge variant="lightgray">⏱ {data.elapsed_ms} ms</Badge>
            <Badge variant="yellow">🏆 nos dois: {data.counts.both}</Badge>
            {data.same_corpus === false && (
              <Badge variant="yellow">corpora distintos (crie o índice search em produtos_vector)</Badge>
            )}
          </div>
          {!isScoreResult && hasTies && (
            <Banner variant="info" darkMode style={{ marginBottom: 12 }}>
              Há ranks empatados entre os resultados exibidos. Cada documento com o mesmo rank recebe
              a mesma contribuição daquela busca; a outra busca pode diferenciá-los na fusão.
            </Banner>
          )}
          <Body style={{ color: T.text2, fontSize: 12, marginBottom: 12 }}>
            Posição final é a ordem retornada nesta execução. Posições diferentes não indicam maior
            relevância quando os scores finais empatam.
            {!isScoreResult && " Abaixo de cada rank está sua contribuição para o RRF; — indica ausência naquela lista candidata, com contribuição zero. Valores arredondados a 5 casas decimais."}
          </Body>
          <div style={{ overflowX: "auto" }} role="region" aria-label="Resultados da fusão" tabIndex={0}>
            <ProductTable rows={rows} columns={cols} />
          </div>

          {/* Mini score chart */}
          <Subtitle style={{ color: T.text, margin: "20px 0 10px", fontSize: 14 }}>{isScoreResult ? "Scores normalizados por produto — Top 10" : "Contribuições para o RRF — Top 10"}</Subtitle>
          {data.fused.slice(0, 10).map((x, i) => {
            const max = Math.max(...data.fused.map((f) => (f.s_score ?? 0) + (f.v_score ?? 0))) || 1;
            return (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, margin: "5px 0" }}>
                <span style={{ width: 200, fontSize: 12, color: T.text2, overflow: "hidden",
                               textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{i + 1}. {x.nome}</span>
                <div style={{ flex: 1, display: "flex", height: 14, borderRadius: 3, overflow: "hidden", background: "rgba(255,255,255,0.05)" }}>
                  <div title={`Search: ${formatScore(x.s_score)}${isScoreResult ? " (normalizado)" : " de contribuição RRF"}`} style={{ width: `${(x.s_score / max) * 100}%`, background: T.greenDark }} />
                  <div title={`Vector: ${formatScore(x.v_score)}${isScoreResult ? " (normalizado)" : " de contribuição RRF"}`} style={{ width: `${(x.v_score / max) * 100}%`, background: T.blue }} />
                </div>
                <span style={{ width: 60, fontSize: 11, color: T.green, fontFamily: T.mono, textAlign: "right" }}>{formatScore(x[scoreKey])}</span>
              </div>
            );
          })}
          <div style={{ display: "flex", gap: 16, marginTop: 8, fontSize: 11, color: T.text3 }}>
            <span><span style={{ color: T.greenDark }}>■</span> Atlas Search</span>
            <span><span style={{ color: T.blue }}>■</span> Vector Search</span>
          </div>

          {data.pipeline && (
            <MqlBlock pipeline={data.pipeline} collection="POC.produtos_vector" />
          )}
        </>
      )}
    </div>
  );
}
