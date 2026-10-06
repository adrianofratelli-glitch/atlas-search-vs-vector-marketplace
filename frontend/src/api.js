import axios from "axios";

// axios client pointing at the FastAPI backend
const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || "http://localhost:8200",
  timeout: 60000,
});

export const getStats   = ()        => api.get("/stats").then((r) => r.data);
export const search     = (body)    => api.post("/search", body).then((r) => r.data);
export const facets     = (body)    => api.post("/search/facets", body).then((r) => r.data);
export const compare    = (query, mode = "phrase") => api.post("/compare", { query, mode }).then((r) => r.data);
export const hybrid     = (body)    => api.post("/hybrid", body).then((r) => r.data);
export const askAgent   = (body)    => api.post("/agent", body).then((r) => r.data);
export const getMetrics = ()        => api.get("/api/metrics").then((r) => r.data);
export const getAnalytics = (full = false) => api.get("/analytics", { params: { full } }).then((r) => r.data);
export const findSimilar  = (body)  => api.post("/similar", body).then((r) => r.data);
export const reviewsRag   = (query) => api.post("/reviews-rag", { query }).then((r) => r.data);
export const hybridNative = (query) => api.post("/hybrid-native", { query }).then((r) => r.data);
export const hybridScoreFusion = (query) => api.post("/hybrid-score-fusion", { query }).then((r) => r.data);

/**
 * Human-readable error: what happened and how to fix it (never a bare
 * "Request failed with status code 422").
 */
export function describeError(e) {
  const status = e?.response?.status;
  const detail = e?.response?.data?.detail;
  if (!e?.response) {
    if (e?.code === "ECONNABORTED") return "o backend demorou mais de 60 s para responder; tente de novo em instantes.";
    return "backend indisponível (porta 8200). Suba com `bash start.sh` e confira /health.";
  }
  if (status === 422) {
    const msg = Array.isArray(detail) ? detail.map((d) => d.msg).filter(Boolean).join("; ") : detail;
    return `consulta inválida${msg ? `: ${msg}` : ""}. Ajuste o texto ou os filtros.`;
  }
  if (status === 413) return "consulta grande demais; encurte o texto.";
  if (status === 429) return "limite de chamadas de IA simultâneas atingido; aguarde alguns segundos e tente de novo.";
  if (status === 503) return "Atlas indisponível no momento; confira /health e a access list do cluster.";
  return `erro ${status} no backend. Consulte o X-Request-Id ${e.response.headers?.["x-request-id"] || ""} no log.`;
}

export default api;
