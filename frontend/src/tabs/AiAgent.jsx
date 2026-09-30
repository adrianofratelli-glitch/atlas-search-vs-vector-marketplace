import { useState, useRef, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import TextInput from "@leafygreen-ui/text-input";
import Button from "@leafygreen-ui/button";
import Badge from "@leafygreen-ui/badge";
import { H3, Body, Subtitle } from "@leafygreen-ui/typography";
import { askAgent, getMetrics } from "../api";
import { T } from "../theme";
import Leaf from "../components/Leaf";

const SUGGESTIONS = [
  "Me recomende um notebook para programação até R$ 3.000",
  "Qual o melhor smartphone custo-benefício até R$ 2.500?",
  "Preciso de um presente para alguém que gosta de academia",
  "Compare os melhores tênis de corrida disponíveis",
];

// follow-ups that only make sense with memory — demonstrates the MongoDBSaver
// checkpoints: the agent resolves "deles/o segundo" from the thread history
const FOLLOW_UPS = [
  "E qual deles é o mais barato?",
  "O segundo tem em estoque?",
  "Tem algo parecido, mas até R$ 1.000?",
];

const ENGINE_COLOR = { "Vector Search": T.blue, "Atlas Search": T.green, "Aggregation": T.purple };

export default function AiAgent() {
  const [q, setQ] = useState("");
  const [history, setHistory] = useState([]);
  const [thread, setThread] = useState(null);
  const [loading, setLoading] = useState(false);
  const [tokens, setTokens] = useState(null);
  const endRef = useRef(null);

  // Custo estimado (Sonnet: $3/Mtok entrada, $15/Mtok saída) com tokens reais
  // reportados pelo backend em /api/metrics — inclui leituras de cache.
  const loadTokens = async () => {
    try {
      const m = await getMetrics();
      const c = m.counters || {};
      const inTok = c.anthropic_input_tokens || 0;
      const outTok = c.anthropic_output_tokens || 0;
      if (inTok + outTok > 0) {
        setTokens({
          in: inTok, out: outTok,
          cacheRead: c.anthropic_cache_read_tokens || 0,
          usd: (inTok * 3 + outTok * 15) / 1_000_000,
        });
      }
    } catch { /* badge é opcional */ }
  };
  useEffect(() => { loadTokens(); }, []);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [history, loading]);

  const send = async (text) => {
    const msg = text ?? q;
    if (!msg.trim() || loading) return;
    setQ("");
    setHistory((h) => [...h, { role: "user", content: msg }]);
    setLoading(true);
    try {
      const t0 = performance.now();
      const res = await askAgent({ message: msg, thread_id: thread });
      setThread(res.thread_id);
      setHistory((h) => [...h, { role: "assistant", content: res.answer, trace: res.trace,
                                  ms: Math.round(performance.now() - t0) }]);
      loadTokens();
    } catch (e) {
      setHistory((h) => [...h, { role: "assistant", content: "Erro: " + (e.message || e), error: true }]);
    } finally { setLoading(false); }
  };

  return (
    <div>
      <div className="section-label">AI Agent · LangGraph ReAct</div>
      <H3 id="agent-title" style={{ color: T.text, fontFamily: T.font, letterSpacing: "-0.02em" }}>Recomendações em Linguagem Natural</H3>
      <Body style={{ color: T.text2, marginBottom: 10 }}>
        LangGraph ReAct Agent + Claude — com raciocínio transparente (tool → MQL → resultado).
      </Body>

      <div style={{ display: "flex", gap: 8, marginBottom: 14, flexWrap: "wrap" }}>
        <Badge variant="green">🧠 Memória {thread ? `· #${thread.slice(0, 8)}` : "· nova conversa"}</Badge>
        <Badge variant="blue">⚙️ 4 ferramentas MongoDB</Badge>
        <Badge variant="darkgray">💾 checkpoints @ POC</Badge>
        {tokens && (
          <Badge variant="yellow">
            🪙 {(tokens.in + tokens.out).toLocaleString("pt-BR")} tokens · ~${tokens.usd.toFixed(4)}
            {tokens.cacheRead > 0 && ` · ${tokens.cacheRead.toLocaleString("pt-BR")} via cache`}
          </Badge>
        )}
        {history.length > 0 && (
          <Button size="xsmall" variant="default" darkMode
            onClick={() => { setHistory([]); setThread(null); }}>🔄 Nova conversa</Button>
        )}
      </div>

      {history.length === 0 && (
        <div style={{ textAlign: "center", padding: "16px 0 22px" }}>
          <div style={{ fontSize: 34, marginBottom: 6 }}>🍃</div>
          <Subtitle style={{ color: T.text }}>Pergunte em linguagem natural</Subtitle>
          <Body style={{ color: T.text3, marginTop: 4, marginBottom: 16 }}>
            O agente escolhe a ferramenta certa e mostra o MQL que rodou.
          </Body>
          <div className="responsive-two-col" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, maxWidth: 720, margin: "0 auto" }}>
            {SUGGESTIONS.map((s) => (
              <Button key={s} variant="default" darkMode onClick={() => send(s)}
                style={{ justifyContent: "flex-start" }}>{s}</Button>
            ))}
          </div>
        </div>
      )}

      {/* Chat */}
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {history.map((m, i) => (
          <Msg key={i} msg={m} />
        ))}
        {loading && (
          <div style={{ display: "flex", gap: 10, alignItems: "center", color: T.text3 }}>
            <Leaf size={20} /> <span>🧠 Agente raciocinando e consultando o MongoDB…</span>
          </div>
        )}
        <div ref={endRef} />
      </div>

      {/* Memory demo: follow-ups that depend on the previous turn */}
      {history.length > 0 && !loading && (
        <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap", alignItems: "center" }}>
          <span style={{ fontSize: 11, color: T.text3 }}>🧠 Teste a memória:</span>
          {FOLLOW_UPS.map((s) => (
            <Button key={s} size="xsmall" variant="default" darkMode onClick={() => send(s)}>{s}</Button>
          ))}
        </div>
      )}

      {/* Input */}
      <div style={{ display: "flex", gap: 12, marginTop: 16, alignItems: "flex-end" }}>
        <div style={{ flex: 1 }}>
          <TextInput aria-labelledby="agent-title" placeholder="Pergunte sobre produtos…"
            value={q} onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()} darkMode />
        </div>
        <Button variant="primary" onClick={() => send()} disabled={loading} darkMode>Enviar</Button>
      </div>
    </div>
  );
}

function Msg({ msg }) {
  const isUser = msg.role === "user";
  return (
    <div style={{
      background: T.surface, border: `1px solid ${T.border}`, borderRadius: 8, padding: 14,
      borderLeft: `3px solid ${isUser ? T.text3 : T.green}`,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <span style={{ fontSize: 16 }}>{isUser ? "👤" : "🍃"}</span>
        <span style={{ fontSize: 11, color: T.text3, textTransform: "uppercase", letterSpacing: "0.1em" }}>
          {isUser ? "Você" : "Agente"}
        </span>
        {msg.ms != null && <span style={{ fontSize: 11, color: T.text3, fontFamily: T.mono }}>· {msg.ms} ms</span>}
      </div>
      <div style={{ color: msg.error ? T.red : T.text, fontSize: 13, lineHeight: 1.6 }} className="md-body">
        <ReactMarkdown>{msg.content}</ReactMarkdown>
      </div>

      {msg.trace?.length > 0 && (
        <details style={{ marginTop: 10, background: T.codeBg, border: `1px solid ${T.border}`, borderRadius: 10 }}>
          <summary style={{ cursor: "pointer", padding: "8px 12px", fontSize: 12, color: T.text2, listStyle: "none" }}>
            🔍 Raciocínio do agente · {msg.trace.length} ferramenta(s)
          </summary>
          <div style={{ padding: 12, borderTop: `1px solid ${T.border}`, display: "flex", flexDirection: "column", gap: 14 }}>
            {msg.trace.map((t, j) => (
              <div key={j}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
                  <span style={{ fontFamily: T.mono, fontSize: 13, color: T.text, fontWeight: 600 }}>
                    {t.tool}({Object.entries(t.args).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ")})
                  </span>
                  <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 8px", borderRadius: 4,
                    background: (ENGINE_COLOR[t.engine] || T.text3) + "22", color: ENGINE_COLOR[t.engine] || T.text3,
                    border: `1px solid ${ENGINE_COLOR[t.engine] || T.text3}44`, textTransform: "uppercase",
                    letterSpacing: "0.05em" }}>{t.engine}</span>
                  <span style={{ fontSize: 11, color: T.text3, fontFamily: T.mono }}>→ {t.collection}</span>
                  {t.degraded && (
                    <span title={t.reason} style={{ fontSize: 10, fontWeight: 700, padding: "2px 8px", borderRadius: 4,
                      background: `${T.red}22`, color: T.red, border: `1px solid ${T.red}44`,
                      textTransform: "uppercase", letterSpacing: "0.05em" }}>⚠ degradado</span>
                  )}
                </div>
                <div className="responsive-two-col" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                  <div>
                    <div style={{ fontSize: 10, color: T.text3, marginBottom: 4 }}>Pipeline MQL</div>
                    <pre style={{ margin: 0, padding: 10, background: T.surface, borderRadius: 6, fontSize: 11,
                      color: T.green, fontFamily: T.mono, overflow: "auto", maxHeight: 200 }}>
                      {JSON.stringify(t.mql, null, 2)}
                    </pre>
                  </div>
                  <div>
                    <div style={{ fontSize: 10, color: T.text3, marginBottom: 4 }}>Resultado ao LLM</div>
                    <pre style={{ margin: 0, padding: 10, background: T.surface, borderRadius: 6, fontSize: 11,
                      color: T.text2, fontFamily: T.mono, overflow: "auto", maxHeight: 200, whiteSpace: "pre-wrap" }}>
                      {t.result}
                    </pre>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}
