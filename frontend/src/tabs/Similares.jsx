import { useState } from "react";
import TextInput from "@leafygreen-ui/text-input";
import Button from "@leafygreen-ui/button";
import Badge from "@leafygreen-ui/badge";
import Banner from "@leafygreen-ui/banner";
import Toggle from "@leafygreen-ui/toggle";
import { H3, Body, Subtitle } from "@leafygreen-ui/typography";
import { findSimilar } from "../api";
import { T, fmtBRL } from "../theme";
import { MqlBlock } from "../components/ProductTable";

export default function Similares() {
  const [nome, setNome] = useState("");
  const [filtered, setFiltered] = useState(true);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  const run = async () => {
    if (loading || !nome.trim()) return;
    setLoading(true);
    try { setData(await findSimilar({ nome, same_category: filtered })); }
    catch (e) { setData({ error: `Falha na busca: ${e.message}` }); }
    finally { setLoading(false); }
  };

  return (
    <div>
      <div className="section-label">Vector · Pre-Filtering</div>
      <H3 id="similares-title" style={{ color: T.text, fontFamily: T.font, letterSpacing: "-0.02em" }}>Produtos Similares — Vector "More Like This"</H3>
      <Body style={{ color: T.text2, marginBottom: 16 }}>
        Busca semântica usando a descrição como query (autoEmbed voyage-4). Com o{" "}
        <b style={{ color: T.green }}>pre-filtering</b> ligado, o filtro de categoria roda <b>dentro</b> do{" "}
        <code style={{ color: T.green }}>$vectorSearch</code> — semântico + estruturado numa única operação.
      </Body>

      <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 8, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 280 }}>
          <TextInput aria-labelledby="similares-title" placeholder="Ex: Nike Air Max, Duna, Notebook Dell…"
            value={nome} onChange={(e) => setNome(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && run()} darkMode />
        </div>
        <Button variant="primary" onClick={run} disabled={loading} darkMode>
          {loading ? "Buscando…" : "Encontrar Similares"}
        </Button>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Toggle checked={filtered} onChange={setFiltered} size="small" aria-label="pre-filter" darkMode />
          <span style={{ fontSize: 12, color: T.text2 }}>Pre-filter (mesma categoria)</span>
        </div>
      </div>

      {data?.error && <Banner variant="warning" style={{ marginTop: 12 }}>{data.error}</Banner>}

      {data?.base && (
        <>
          <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 10,
                        padding: "14px 18px", margin: "16px 0 18px" }}>
            <div style={{ fontSize: 11, color: T.text3, textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: 6 }}>Produto base</div>
            <div style={{ fontSize: 16, fontWeight: 700, color: T.text }}>{data.base.nome}</div>
            <div style={{ fontSize: 13, color: T.text2, marginTop: 4 }}>
              <Badge variant="blue">{data.base.categoria}</Badge> · {fmtBRL(data.base.preco)}
              {data.filtered && (
                <Badge variant="green" style={{ marginLeft: 8 }}>
                  pre-filter: categoria = {data.base.categoria} + em_estoque = true
                </Badge>
              )}
            </div>
          </div>

          <Subtitle style={{ color: T.text, fontSize: 14, marginBottom: 12 }}>
            {data.similares.length} produtos semanticamente similares
          </Subtitle>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 12 }}>
            {data.similares.map((s, i) => (
              <div key={i} className="result-card" style={{ background: T.surface, border: `1px solid ${T.borderSub}`, borderRadius: 8, padding: "12px 16px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
                  <div style={{ fontSize: 14, fontWeight: 600, color: T.text }}>{s.nome}</div>
                  <div style={{ fontFamily: T.mono, fontSize: 12, color: T.teal, whiteSpace: "nowrap" }}>
                    {(s.score * 100).toFixed(1)}%
                  </div>
                </div>
                <div style={{ fontSize: 12, color: T.text2, marginTop: 5 }}>
                  {s.categoria} · {fmtBRL(s.preco)} · ⭐ {(s.avaliacao_media || 0).toFixed(1)}
                </div>
              </div>
            ))}
          </div>

          {data.pipeline && <MqlBlock pipeline={data.pipeline} collection="POC.produtos_vector" />}
        </>
      )}
    </div>
  );
}
