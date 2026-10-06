# Backend — API de Search & Vector (FastAPI)

Expõe a lógica de busca da POC (Atlas Search, Vector Search, RRF híbrido,
analytics, RAG de reviews e o agente LangGraph) como endpoints REST consumidos
pelo frontend React via axios.

## Endpoints

| Método | Caminho          | Descrição                                                       |
|--------|------------------|-----------------------------------------------------------------|
| GET    | `/health`        | Health check do Atlas (503 quando inacessível)                  |
| GET    | `/health/llm`    | Gateway Grove configurado? (sem expor host ou chave)            |
| GET    | `/stats`         | Contagem das coleções e situação dos índices                    |
| POST   | `/search`        | Atlas Search (autocomplete, fuzzy, highlight, contagens, sinônimos) |
| POST   | `/search/facets` | Facetas em tempo real via `$searchMeta`                          |
| POST   | `/compare`       | Full-text vs vetorial vs RRF, lado a lado                        |
| POST   | `/hybrid`        | RRF ajustável (`k`, `n_search`, `n_vector`)                      |
| POST   | `/hybrid-native` | `$rankFusion` nativo (Atlas 8.1+) com fallback para RRF          |
| POST   | `/hybrid-score-fusion` | `$scoreFusion` nativo (RSF) com fallback para RRF          |
| GET    | `/analytics`     | Agregações paralelas via `$facet` (cache de 5 minutos)           |
| POST   | `/similar`       | "Mais como este" vetorial com pré-filtro nativo                  |
| POST   | `/reviews-rag`   | Recuperação de reviews e sumarização pelo Claude                 |
| POST   | `/agent`         | Agente ReAct LangGraph com trace MQL estruturado                 |

Documentação interativa da API: http://localhost:8200/docs

## Setup

Use o setup do [README da raiz](../README.md#setup) (venv, `pov-shared`, `.env`,
`scripts/reset_demo.py`). Só o backend:

```bash
cd backend && ../.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8200   # lê ../.env; variável de ambiente vence o .env
```

## Variáveis de ambiente

| Variável            | Descrição                                                        |
|---------------------|------------------------------------------------------------------|
| `MONGODB_URI`       | String de conexão do Atlas                                       |
| `DB_NAME`           | Nome do banco (padrão: `POC`); `marketplace_test` para testes    |
| `GROVE_BASE_URL`, `GROVE_API_KEY` | Gateway Grove: única rota de LLM (`/agent`, `/reviews-rag`) |
| `ANTHROPIC_MODEL`, `REVIEWS_MODEL` | Modelos (padrão `claude-sonnet-5-5`; o gateway não serve haiku) |
| `GROVE_TIMEOUT_SECONDS`, `GROVE_RETRIES`, `GROVE_CB_THRESHOLD`, `GROVE_MODEL_FALLBACKS` | Resiliência do LLM (ligada por padrão; ver `_shared/README.md`) |
| `AGENT_RECURSION_LIMIT` | Teto de passos ReAct por turno (padrão 12)                   |
| `CHECKPOINT_TTL_DAYS` | TTL da memória do agente (padrão 30)                          |
| `AI_MAX_CONCURRENCY` | Chamadas de IA simultâneas; acima disso, 429 (padrão 4)        |
| `MONGODB_APP_RETRIES` | Retentativa para falha transitória/throttling do autoEmbed (padrão 1; 0 desliga) |
| `MAX_BODY_BYTES`    | Limite do corpo da requisição (padrão 64 KB → 413)               |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | Tracing opcional, fail-open                |
| `CORS_ORIGINS`      | Origens permitidas separadas por vírgula (padrão: `localhost:5273`) |

## Módulos

```
atlas.py            conexão com o MongoDB (timeouts explícitos, retry transitório) e pipelines
agent.py            agente ReAct LangGraph: 4 ferramentas, máscara de PII, filtro de injection, trace MQL
reviews.py          RAG de reviews: avaliações suspeitas de injection ficam fora do prompt
llm_gateway.py      ChatAnthropic cujo transporte é grove_client.create_message (Grove, resiliência)
langfuse_tracing.py tracing Langfuse v2 fail-open (trace criada depois da máscara de PII)
main.py             rotas FastAPI, validação de entrada, limites, CORS e métricas
observability.py    logs estruturados + /api/metrics e /metrics
```

## Testes

```bash
../.venv/bin/python -m unittest discover -s tests   # offline; inclui test_hardening_adversarial.py
```
