# Backend — API de Search & Vector (FastAPI)

Expõe a lógica de busca da POC (Atlas Search, Vector Search, RRF híbrido,
analytics, RAG de reviews e o agente LangGraph) como endpoints REST consumidos
pelo frontend React via axios.

## Endpoints

| Método | Caminho          | Descrição                                                       |
|--------|------------------|-----------------------------------------------------------------|
| GET    | `/health`        | Health check                                                    |
| GET    | `/stats`         | Contagem das coleções e situação dos índices                    |
| POST   | `/search`        | Atlas Search (autocomplete, fuzzy, highlight, contagens, sinônimos) |
| POST   | `/search/facets` | Facetas em tempo real via `$searchMeta`                          |
| POST   | `/compare`       | Full-text vs vetorial vs RRF, lado a lado                        |
| POST   | `/hybrid`        | RRF ajustável (`k`, `n_search`, `n_vector`)                      |
| POST   | `/hybrid-native` | `$rankFusion` nativo (Atlas 8.1+) com fallback para RRF          |
| GET    | `/analytics`     | Agregações paralelas via `$facet` (cache de 5 minutos)           |
| POST   | `/similar`       | "Mais como este" vetorial com pré-filtro nativo                  |
| POST   | `/reviews-rag`   | Recuperação de reviews e sumarização pelo Claude                 |
| POST   | `/agent`         | Agente ReAct LangGraph com trace MQL estruturado                 |

Documentação interativa da API: http://localhost:8200/docs

## Setup

```bash
pip install -r requirements.txt
# Lê o .env da raiz do repositório (MONGODB_URI, DB_NAME, ANTHROPIC_API_KEY)
uvicorn main:app --reload --port 8200
```

## Variáveis de ambiente

| Variável            | Descrição                                                        |
|---------------------|------------------------------------------------------------------|
| `MONGODB_URI`       | String de conexão do Atlas                                       |
| `DB_NAME`           | Nome do banco (padrão: `POC`)                                    |
| `ANTHROPIC_API_KEY` | Exigida por `/agent` e `/reviews-rag`                            |
| `CORS_ORIGINS`      | Origens permitidas separadas por vírgula (padrão: `localhost:5273`) |

## Módulos

```
atlas.py     conexão com o MongoDB e pipelines (search, vetorial, RRF, facetas, analytics)
agent.py     agente ReAct LangGraph com quatro ferramentas e reconstrução do MQL
reviews.py   RAG de reviews: recuperação mais sumarização pelo Claude
main.py      rotas FastAPI, CORS e modelos de request Pydantic
```
