import { useState, useEffect } from "react";
import { H3, Body, Subtitle } from "@leafygreen-ui/typography";
import Badge from "@leafygreen-ui/badge";
import Banner from "@leafygreen-ui/banner";
import { getAnalytics } from "../api";
import { T, fmtBRL } from "../theme";
import { MqlBlock } from "../components/ProductTable";

function Bar({ label, value, max, sub, color = T.green }) {
  return (
    <div style={{ marginBottom: 11 }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
        <span style={{ fontSize: 13, color: T.text }}>{label}</span>
        <span style={{ fontSize: 12, color: T.text2, fontFamily: T.mono }}>{sub}</span>
      </div>
      <div style={{ height: 8, background: "#001016", borderRadius: 4, overflow: "hidden" }}>
        <div style={{ width: `${(value / max) * 100}%`, height: "100%",
                      background: `linear-gradient(90deg, ${T.greenDark}, ${color})` }} />
      </div>
    </div>
  );
}

function ModeToggle({ full, loading, onChange }) {
  return (
    <div style={{ display: "inline-flex", borderRadius: 6, overflow: "hidden", border: `1px solid ${T.border}`, marginLeft: 10, verticalAlign: "middle" }}>
      {[[false, "Amostra 12k"], [true, "Base completa"]].map(([value, label]) => (
        <button key={label} onClick={() => onChange(value)} disabled={loading} style={{
          cursor: "pointer", fontSize: 11, fontFamily: T.font, padding: "5px 10px", border: "none",
          background: full === value ? "rgba(0,237,100,0.15)" : T.surface,
          color: full === value ? T.green : T.text3, fontWeight: full === value ? 700 : 400,
        }}>{label}</button>
      ))}
    </div>
  );
}

export default function Analytics() {
  const [full, setFull] = useState(false);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getAnalytics(full).then(setData).catch(() => setData({ error: "falha" })).finally(() => setLoading(false));
  }, [full]);

  const changeMode = (value) => {
    if (value === full) return;
    setLoading(true);
    setFull(value);
  };

  if (loading) {
    return <Body style={{ color: T.text2 }}>
      Rodando aggregation pipeline ($facet) no servidor{full ? " — base completa, pode levar alguns segundos…" : "…"}
    </Body>;
  }
  if (data?.error) {
    return (
      <div>
        <Banner variant="danger" darkMode>{data.error}</Banner>
        <div style={{ marginTop: 12 }}><ModeToggle full={full} loading={loading} onChange={changeMode} /></div>
      </div>
    );
  }

  const cats = data.por_categoria || [];
  const marcas = data.top_marcas || [];
  const faixas = data.faixa_preco || [];
  const meses = data.por_mes || [];
  const maxCat = Math.max(...cats.map(c => c.total), 1);
  const maxMarca = Math.max(...marcas.map(m => m.total), 1);
  const maxFaixa = Math.max(...faixas.map(f => f.total), 1);
  const maxMes = Math.max(...meses.map(m => m.total), 1);

  return (
    <div>
      <div className="section-label">Aggregation · $facet</div>
      <H3 style={{ color: T.text, fontFamily: T.font, letterSpacing: "-0.02em" }}>Analytics em Tempo Real</H3>
      <Body style={{ color: T.text2, marginBottom: 8 }}>
        Um único <code style={{ color: T.green }}>$facet</code> roda vários agregados em paralelo no servidor — MongoDB
        como engine analítico sobre a base de produtos. <Badge variant="green">{data.elapsed_ms} ms</Badge>
        <span style={{ color: T.text3, fontSize: 12 }}>
          {" "}· {data.full
            ? `base completa: ${(data.geral?.amostra || 0).toLocaleString("pt-BR")} docs`
            : `amostra de ${(data.geral?.amostra || 0).toLocaleString("pt-BR")} docs ($sample p/ latência de demo)`}
        </span>
        <ModeToggle full={full} loading={loading} onChange={changeMode} />
      </Body>

      {/* Overview KPIs */}
      <div style={{ display: "flex", gap: 14, margin: "14px 0 22px" }}>
        {[
          ["Preço médio", fmtBRL(data.geral?.preco_medio), T.green],
          ["Desconto médio", `${data.geral?.desconto_medio || 0}%`, T.yellow],
          ["Em estoque", `${data.geral?.em_estoque_pct || 0}%`, T.teal],
        ].map(([l, v, c]) => (
          <div key={l} style={{ flex: 1, background: T.surface, border: `1px solid ${T.borderSub}`,
                                 borderTop: `3px solid ${c}`, borderRadius: "0 0 8px 8px", padding: "14px 16px" }}>
            <div style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.1em", color: T.text3, marginBottom: 6 }}>{l}</div>
            <div style={{ fontSize: 22, fontWeight: 700, color: c, fontFamily: T.mono }}>{v}</div>
          </div>
        ))}
      </div>

      <div className="responsive-two-col" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 22 }}>
        <div>
          <Subtitle style={{ color: T.text, fontSize: 14, marginBottom: 12 }}>Produtos por categoria</Subtitle>
          {cats.slice(0, 8).map((c) => (
            <Bar key={c._id} label={c._id || "—"} value={c.total} max={maxCat}
                 sub={`${c.total.toLocaleString("pt-BR")} · ⭐ ${(c.avaliacao_media || 0).toFixed(1)}`} />
          ))}
        </div>
        <div>
          <Subtitle style={{ color: T.text, fontSize: 14, marginBottom: 12 }}>Top marcas</Subtitle>
          {marcas.map((m) => (
            <Bar key={m._id} label={m._id || "—"} value={m.total} max={maxMarca}
                 sub={m.total.toLocaleString("pt-BR")} color={T.blue} />
          ))}
        </div>
      </div>

      <div className="responsive-two-col" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 22, marginTop: 22 }}>
        <div>
          <Subtitle style={{ color: T.text, fontSize: 14, marginBottom: 12 }}>Distribuição por faixa de preço ($bucket)</Subtitle>
          {faixas.map((f) => (
            <Bar key={f.label} label={f.label} value={f.total} max={maxFaixa}
                 sub={f.total.toLocaleString("pt-BR")} color={T.purple} />
          ))}
        </div>
        {meses.length > 0 && (
          <div>
            <Subtitle style={{ color: T.text, fontSize: 14, marginBottom: 12 }}>Produtos cadastrados por mês ($dateToString)</Subtitle>
            {meses.map((m) => (
              <Bar key={m._id} label={m._id} value={m.total} max={maxMes}
                   sub={m.total.toLocaleString("pt-BR")} color={T.teal} />
            ))}
          </div>
        )}
      </div>

      <div style={{ marginTop: 22 }}>
        <MqlBlock pipeline={data.pipeline} collection="POC.produtos" />
      </div>
    </div>
  );
}
