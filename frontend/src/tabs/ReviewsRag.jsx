import { useState } from "react";
import TextInput from "@leafygreen-ui/text-input";
import Button from "@leafygreen-ui/button";
import Badge from "@leafygreen-ui/badge";
import Banner from "@leafygreen-ui/banner";
import { H3, Body, Subtitle } from "@leafygreen-ui/typography";
import ReactMarkdown from "react-markdown";
import { reviewsRag } from "../api";
import { T } from "../theme";
import { MqlBlock } from "../components/ProductTable";

const SUGESTOES = ["ASUS ZenBook", "Royal Canin", "Garmin Fenix", "Sony Alpha", "Adidas Techfit"];

export default function ReviewsRag() {
  const [q, setQ] = useState("");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  const run = async (text) => {
    const query = text ?? q;
    if (loading || !query.trim()) return;
    setQ(query);
    setLoading(true);
    try { setData(await reviewsRag(query)); }
    catch (e) { setData({ error: `Falha ao resumir avaliações: ${e.message}` }); }
    finally { setLoading(false); }
  };

  return (
    <div>
      <div className="section-label">RAG · Claude + Atlas</div>
      <H3 id="rag-title" style={{ color: T.text, fontFamily: T.font, letterSpacing: "-0.02em" }}>Reviews RAG — Resumo de Avaliações Reais</H3>
      <Body style={{ color: T.text2, marginBottom: 16 }}>
        Atlas Search acha o produto → puxa avaliações reais do MongoDB (coleção <code style={{ color: T.green }}>avaliacoes</code>) →
        Claude resume <b>apenas com base nos dados reais</b>. RAG operacional, tudo num cluster só.
      </Body>

      <div style={{ display: "flex", gap: 12, marginBottom: 16, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 280 }}>
          <TextInput aria-labelledby="rag-title" placeholder="Ex: ASUS ZenBook, Royal Canin, Garmin…"
            value={q} onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && run()} darkMode />
        </div>
        <Button variant="primary" onClick={() => run()} disabled={loading} darkMode>
          {loading ? "Analisando avaliações…" : "Resumir Avaliações"}
        </Button>
      </div>

      {!data && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 16 }}>
          {SUGESTOES.map((s) => (
            <Button key={s} size="xsmall" variant="default" darkMode onClick={() => run(s)}>{s}</Button>
          ))}
        </div>
      )}

      {data?.error && <Banner variant="warning">{data.error}</Banner>}

      {data?.produto && (
        <div className="responsive-split" style={{ display: "grid", gridTemplateColumns: "3fr 2fr", gap: 20 }}>
          {/* LLM summary */}
          <div>
            <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 10, padding: "16px 20px" }}>
              <div style={{ fontSize: 16, fontWeight: 700, color: T.text, marginBottom: 4 }}>{data.produto.nome}</div>
              <div style={{ marginBottom: 12 }}>
                <Badge variant="blue">{data.produto.categoria}</Badge>{" "}
                <Badge variant="green">⭐ {data.nota_media} média</Badge>{" "}
                <Badge variant={data.via === "atlas_search" ? "green" : "yellow"}>
                  {data.via === "atlas_search" ? "🔍 produto via Atlas Search" : "produto via catálogo (fallback)"}
                </Badge>{" "}
                <span style={{ fontSize: 12, color: T.text3 }}>· {data.total_analisado} reviews analisados</span>
              </div>
              <div className="md" style={{ color: T.text, fontSize: 14, lineHeight: 1.6 }}>
                <ReactMarkdown>{data.summary}</ReactMarkdown>
              </div>
            </div>
          </div>

          {/* Real reviews */}
          <div>
            <Subtitle style={{ color: T.text, fontSize: 13, marginBottom: 10 }}>Avaliações usadas (top por utilidade)</Subtitle>
            {(data.reviews || []).slice(0, 6).map((r, i) => (
              <div key={i} style={{ background: T.surface, border: `1px solid ${T.borderSub}`, borderRadius: 8, padding: "10px 14px", marginBottom: 8 }}>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ fontSize: 12, fontWeight: 600, color: T.text }}>{"★".repeat(r.nota)}<span style={{ color: T.text3 }}>{"★".repeat(5 - r.nota)}</span></span>
                  <span style={{ fontSize: 11, color: T.text3, fontFamily: T.mono }}>👍 {r.util_count || 0}</span>
                </div>
                {r.titulo && <div style={{ fontSize: 13, fontWeight: 600, color: T.text, marginTop: 4 }}>{r.titulo}</div>}
                <div style={{ fontSize: 12, color: T.text2, marginTop: 3 }}>{r.texto}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {data?.pipeline && (
        <div style={{ marginTop: 16 }}>
          <MqlBlock pipeline={data.pipeline} collection="POC.produtos → POC.avaliacoes" />
        </div>
      )}
    </div>
  );
}
