/* Shared component module intentionally exports the table, MQL block, and column factory. */
/* eslint-disable react-refresh/only-export-components */
import { useState } from "react";
import { T, fmtBRL } from "../theme";

// Shared product results table, used across all tabs
export default function ProductTable({ rows, columns }) {
  if (!rows || rows.length === 0) {
    return <div style={{ color: T.text3, fontSize: 13, padding: "12px 4px" }}>Sem resultados.</div>;
  }
  return (
    <div style={{ border: `1px solid ${T.border}`, borderRadius: 8, overflow: "hidden" }}>
      <table className="ptable" style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ background: T.surface2 }}>
            {columns.map((c) => (
              <th key={c.key} style={{
                textAlign: c.align || "left", padding: "9px 12px", fontSize: 10, fontWeight: 700,
                color: T.text3, textTransform: "uppercase", letterSpacing: "0.08em",
                borderBottom: `1px solid ${T.border}`,
              }}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} style={{ borderBottom: `1px solid ${T.borderSub}` }}>
              {columns.map((c) => (
                <td key={c.key} style={{
                  textAlign: c.align || "left", padding: "9px 12px", fontSize: 12,
                  color: c.color || T.text2, fontFamily: c.mono ? T.mono : T.font,
                }}>
                  {c.render ? c.render(r) : r[c.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function priceCol() {
  return { key: "preco", label: "Preço", mono: true, color: T.text, render: (r) => fmtBRL(r.preco) };
}

export function MqlBlock({ pipeline, collection }) {
  const [copied, setCopied] = useState(false);
  const copy = (e) => {
    e.preventDefault(); // don't toggle the <details>
    navigator.clipboard.writeText(JSON.stringify(pipeline, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };
  return (
    <details style={{ marginTop: 14, background: T.surface, border: `1px solid ${T.border}`, borderRadius: 12 }}>
      <summary style={{ cursor: "pointer", padding: "10px 14px", fontSize: 13, color: T.text2, listStyle: "none",
                        display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ flex: 1 }}>🔧 Pipeline MQL executado no MongoDB {collection ? `· ${collection}` : ""}</span>
        <button className="copy-btn" onClick={copy}>{copied ? "✓ copiado" : "copiar"}</button>
      </summary>
      <pre style={{ margin: 0, padding: 14, borderTop: `1px solid ${T.border}`, overflow: "auto",
                    fontSize: 12, color: T.green, fontFamily: T.mono, maxHeight: 320, background: T.codeBg }}>
        {JSON.stringify(pipeline, null, 2)}
      </pre>
    </details>
  );
}
