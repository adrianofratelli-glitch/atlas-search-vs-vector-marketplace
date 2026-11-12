# Mapa do código — atlas-search-vs-vector-marketplace

> Também conhecida como: Search vs Vector (marketplace). Portas: frontend 5273 / backend 8200. Em uma frase: num mesmo cluster Atlas, mostra busca textual (Atlas Search), busca semântica (Vector Search com autoEmbed), híbrida (`$rankFusion` e `$scoreFusion`), analytics, RAG de avaliações e um agente com tools, e em cada tela exibe o MQL que realmente rodou.

Este mapa serve para achar código rápido. Detalhes das queries: [briefing/queries.md](briefing/queries.md). Arquitetura: [briefing/architecture.md](briefing/architecture.md). Telas: [briefing/ui-flows.md](briefing/ui-flows.md). Agente: [briefing/agent-behavior.md](briefing/agent-behavior.md). Decisão `$rankFusion` x `$scoreFusion`: [adr/0001-rankfusion-vs-scorefusion.md](adr/0001-rankfusion-vs-scorefusion.md).

## 1. Árvore anotada

```
backend/                          FastAPI (Python)
  main.py                         Rotas, validação das requisições (Pydantic), limites e middleware (303 linhas)
  atlas.py                        O CORAÇÃO: conexão com o Atlas, todos os pipelines de busca, híbrida, analytics, similares, reviews
  agent.py                        Agente ReAct (LangGraph) com 4 tools e memória no Atlas (MongoDBSaver)
  reviews.py                      Aba Reviews RAG: busca o produto/avaliações e pede o resumo ao Claude
  catalog_sync.py                 Change Stream que espelha `produtos` em `produtos_vector`
  llm_gateway.py                  Cliente do LLM via gateway Grove, máscara de PII e detector de injection
  langfuse_tracing.py             Tracing Langfuse, no-op sem chave
  observability.py                Logs e contadores em /api/metrics e /metrics
  tests/                          Testes offline (ver seção 6)
frontend/                         React + Vite + LeafyGreen
  src/App.jsx                     Casca: 7 abas agrupadas em 4 blocos de navegação; chama /stats ao abrir
  src/api.js                      Todas as chamadas HTTP (axios) e `describeError`
  src/tabs/AtlasSearch.jsx        Aba 1 "Full-text"
  src/tabs/SearchVsVector.jsx     Aba 2 "Search × Vector"
  src/tabs/HybridRRF.jsx          Aba 3 "Híbrida" ($rankFusion x $scoreFusion)
  src/tabs/Similares.jsx          Aba 4 "Similares"
  src/tabs/Analytics.jsx          Aba 5 "Analytics"
  src/tabs/ReviewsRag.jsx         Aba 6 "Reviews RAG"
  src/tabs/AiAgent.jsx            Aba 7 "Agente"
  src/components/ProductTable.jsx Tabela de produtos e bloco de MQL com botão "copiar"
  src/components/Leaf.jsx         Ícone
  src/theme.js                    Cores e tokens
  vite.config.js                  Porta 5273 (sem proxy: o front chama a API direto)
  tests/e2e_demo.py               Roteiro ponta a ponta da demo (Playwright)
  tests/ui_visual_smoke.py        Smoke visual
scripts/
  populate_marketplace.py         Gera o catálogo sintético (produtos, produtos_vector, avaliacoes) e os índices comuns
  setup_search_indexes.py         Cria/atualiza os índices Atlas Search e Vector Search (`--status` lista)
  reset_demo.py                   Prepara a demo: memória do agente, espelho, sinônimos, índices (e catálogo com --rebuild-catalog)
  bench_thesis.py                 Benchmark: híbrida nativa x fusão na aplicação; frescor do espelho
docs/briefing/                    Briefings
docs/adr/                         Decisão de arquitetura (RRF x RSF)
docs/screenshots/                 Capturas
start.sh, docker/, Dockerfile, nginx.conf             Subir e empacotar
```

Collections: `produtos` (catálogo grande, 500 mil na demo), `produtos_vector` (subconjunto espelhado, base da busca vetorial e híbrida), `avaliacoes`, `sinonimos` (fonte do mapeamento de sinônimos), `catalog_sync_state` (token de retomada do espelho), `marketplace_checkpoints` e `marketplace_checkpoint_writes` (memória do agente; nomes em `backend/agent.py:34-35`). Banco padrão: `POC` (`backend/atlas.py:24`, variável `DB_NAME`). Índices: `produtos_search` (Atlas Search em `produtos`), `produtos_vector` (Vector Search com autoEmbed no campo `descricao`, na collection de mesmo nome), e `produtos_vector_search` (índice textual em `produtos_vector`, necessário para `$rankFusion`/`$scoreFusion` na mesma coleção).

## 2. "Apertei o botão X, o que roda?"

O front chama `http://localhost:8200` direto (`frontend/src/api.js:5`, `VITE_API_URL`). Handlers em `backend/main.py`; lógica em `backend/atlas.py` salvo indicação.

| Tela / ação | Componente (arquivo:linha) | Chamada HTTP | Handler (main.py:linha) | Função de negócio | Onde toca o MongoDB |
|---|---|---|---|---|---|
| Abrir o app (contadores e status dos índices) | `App.jsx:31` (`getStats`) | `GET /stats` | `stats` (211) | `atlas.py::get_stats` (111), `get_index_status` (161) | `get_stats` (`estimated_document_count` em `produtos`, `produtos_vector`, `avaliacoes`); `get_search_indexes` (141, `$listSearchIndexes`) |
| **Full-text**: Enter / botão buscar / chips de exemplo / liga-desliga sinônimos | `AtlasSearch.jsx:81-92`, chips `:115` -> `run` (37) | `POST /search` e, em paralelo, `POST /search/facets` | `search` (226), `facets` (233) | `atlas.py::atlas_search` (580), `build_search_op` (496), `build_filters` (513), `build_search_pipeline` (551); `search_facets` (618) | `atlas_search` (`aggregate` com `$search` em `produtos`); `search_facets` (`$searchMeta` com facetas, em `produtos`) |
| **Full-text**: clicar numa categoria (faceta) | `AtlasSearch.jsx:146` -> `toggleCat` -> `run` | `POST /search` e `POST /search/facets` | idem | idem (com `categorias`) | idem |
| **Search × Vector**: botão buscar / chips / alternar modo `phrase` x `compound` | `SearchVsVector.jsx:76`, chips `:84`, modo `:69` -> `run` (19) | `POST /compare` | `compare` (237) | `atlas.py::compare_search_vector` (669) | `$search` em `produtos` (ou `produtos_vector` se houver índice textual lá) e `$vectorSearch` em `produtos_vector`; fusão RRF feita em Python por `_rrf_fuse` (643) |
| **Híbrida**: Buscar / Enter / alternar `$rankFusion` x `$scoreFusion` | `HybridRRF.jsx:108`, motor `:101` -> `run` (17) | `POST /hybrid-native` ou `POST /hybrid-score-fusion` | `hybrid_native` (245), `hybrid_score_fusion` (250) | `atlas.py::hybrid_native` (829), `hybrid_score_fusion` (940) | um `aggregate` em `produtos_vector` com `$rankFusion` (849) ou `$scoreFusion` (959), cada um com sub-pipelines `$search` e `$vectorSearch`. Se não houver suporte, cai para RRF na aplicação (`hybrid_rrf`, 735) |
| **Similares**: botão | `Similares.jsx:42` -> `run` (18) | `POST /similar` | `similar` (293) | `atlas.py::find_similar` (301) | `aggregate` do produto-base (`$match` por `produto_id` ou `$search`) e `$vectorSearch` com pré-filtro de categoria e estoque em `produtos_vector` |
| **Analytics**: abrir a aba e alternar "amostra" x "completo" | `Analytics.jsx:44` (efeito) e `ModeToggle` (28) | `GET /analytics?full=false|true` | `analytics` (277) (cache de 5 min, uma execução por modo) | `atlas.py::get_analytics` (209) | `aggregate` em `produtos` com `$facet` (`$group`, `$bucket`, `$dateToString`), precedido de `$sample` de 12 mil no modo amostra |
| **Reviews RAG**: botão / chips | `ReviewsRag.jsx:44`, chips `:52` -> `run` (19) | `POST /reviews-rag` | `reviews_rag` (297) (limite de concorrência de IA) | `reviews.py::summarize_reviews` (38) -> `atlas.py::get_product_and_reviews` (415) | `distinct` em `avaliacoes`, `aggregate` em `produtos` (cache), `$search` em `produtos` e `aggregate` em `avaliacoes`; depois o Claude resume (`reviews.py:84`) |
| **Agente**: Enviar / sugestões / "Nova conversa" | `AiAgent.jsx:145` (Enviar), `:108` e `:133` (sugestões), `:95` (nova conversa) -> `send` (57) | `POST /agent` (e `GET /api/metrics` para o custo em tokens, `loadTokens`, 38) | `agent_route` (259) (limite de concorrência de IA) | `agent.py::run_agent` (310) com 4 tools | tools usam `safe_aggregate`: ver seção 3; memória em `marketplace_checkpoints` |

Sem tela: `POST /hybrid` (241) -> `atlas.py::hybrid_rrf` (735), RRF ajustável (`k`, `n_search`, `n_vector`) com fusão na aplicação; `api.js` exporta `hybrid`, mas nenhuma aba chama. Também `GET /health` (190, ping), `GET /health/llm` (200), `GET /health/live` (207), `GET /api/metrics` (93, usado só pelo custo de tokens da aba Agente), `GET /metrics` (99, Prometheus).

## 3. Onde estão as queries MongoDB

Detalhe e exemplos em [briefing/queries.md](briefing/queries.md). Linhas de `backend/atlas.py` salvo indicação.

| O que faz | arquivo::função (linha) | Operador / pipeline | Collection(s) |
|---|---|---|---|
| Executar qualquer aggregate com retry e erros legíveis | `safe_aggregate` (52; aggregate em 66) | `aggregate` com `maxTimeMS` de 10 s | todas |
| Contadores da home | `get_stats` (111) | `estimated_document_count` | `produtos`, `produtos_vector`, `avaliacoes` |
| Status real dos índices de busca | `get_search_indexes` (141) | `$listSearchIndexes` | `produtos`, `produtos_vector` |
| Busca full-text com filtros dentro do `$search` | `build_search_pipeline` (551; `$search` em 553), `atlas_search` (580) | `$search` com `compound` (`autocomplete` + `text`, `function score` por `avaliacao_media`), filtros no `compound.filter`, `highlight`, `scoreDetails`; sinônimos opcionais (`sinonimos_produtos`) | `produtos` |
| Facetas | `search_facets` (618; `$searchMeta` em 620) | `$searchMeta` com facet de categoria e faixas de preço | `produtos` |
| Comparação lexical x vetorial | `compare_search_vector` (669; `$search` em 687, `$vectorSearch` em 691) | `$search` (`phrase` ou `compound`) e `$vectorSearch` com `query` em texto (autoEmbed), `numCandidates` por `_num_candidates` (286) | `produtos`/`produtos_vector` |
| Híbrida ajustável (API-only) | `hybrid_rrf` (735; `$search` em 742, `$vectorSearch` em 752) | dois aggregates + `_rrf_fuse` (643) em Python | `produtos_vector` |
| Híbrida nativa por ranks | `hybrid_native` (854; `$rankFusion` em 876; desempate lexical `RANK_FUSION_LEXICAL_SORT` em 837) | `$rankFusion` com `$search` e `$vectorSearch` | `produtos_vector` |
| Híbrida nativa por scores | `hybrid_score_fusion` (940; `$scoreFusion` em 959) | `$scoreFusion` (Relative Score Fusion) | `produtos_vector` |
| Produtos similares | `find_similar` (301; base em 325/331, `$vectorSearch` em 347, execução em 364) | `$match` ou `$search` para o produto-base; `$vectorSearch` com `filter` (categoria, `em_estoque`) | `produtos_vector` (base em `produtos` se não houver índice textual lá) |
| Analytics | `get_analytics` (209; aggregate em 255) | `$sample` (modo amostra) + `$facet` com `$group`, `$bucket`, `$dateToString`, `allowDiskUse` | `produtos` |
| Reviews: quais produtos têm avaliação | `_get_reviewed` (382; `distinct` em 389, aggregate em 395) | `distinct` + `$match $in` + `$project` (cache de 10 min) | `avaliacoes`, `produtos` |
| Reviews: achar o produto da pergunta e as avaliações | `get_product_and_reviews` (415; `$search` em 430, execução em 438, avaliações em 478) | `$search` (`compound` autocomplete + text, fuzzy), depois `$match` + `$sort util_count` + `$limit` | `produtos`, `avaliacoes` |
| Agente: `busca_semantica` | `backend/agent.py::_pipe_busca_semantica` (45; execução em 115) | `$vectorSearch` (150 candidatos, limit 10) | `produtos_vector` |
| Agente: `buscar_produto` | `agent.py::_pipe_buscar_produto` (53; execução em 130) | `$search` `autocomplete` com fuzzy | `produtos` |
| Agente: `comparar_categoria` | `agent.py::_pipe_comparar_categoria` (62; execução em 143) | `$match`, `$sort`, `$limit` | `produtos` |
| Agente: `produtos_por_faixa_preco` | `agent.py::_pipe_produtos_por_faixa_preco` (75; execução em 156) | `$match` com faixa de preço, `$sort`, `$limit` | `produtos` |
| Memória do agente | `agent.py::_get_agent` (240; `MongoDBSaver` em 247) | checkpointer LangGraph com TTL (`CHECKPOINT_TTL_DAYS`) | `marketplace_checkpoints`, `marketplace_checkpoint_writes` |
| Espelho `produtos` -> `produtos_vector` | `backend/catalog_sync.py::CatalogMirror` (97; watch em 143), `apply_event` (85), `plan_event` (52) | Change Stream em `produtos`; `replace_one` (upsert) ou delete no destino; token salvo em `catalog_sync_state` (118) | `produtos`, `produtos_vector`, `catalog_sync_state` |
| Criar índices de busca | `scripts/setup_search_indexes.py::apply_all` (218), `patch_produtos_search` (129), `create_vector_index` (162), `create_vector_search_index` (179), `list_indexes` (106) | `create_search_index` / `update_search_index` (autoEmbed `voyage-4` em `descricao`), `$listSearchIndexes`, `create_index` | `produtos`, `produtos_vector` |
| Gerar catálogo | `scripts/populate_marketplace.py::populate_produtos` (614), `populate_vector_sample` (640), `populate_avaliacoes` (660), `create_indexes` (681) | `insert_many ordered=False`; subconjunto via `$sample` + `$merge`; índices B-tree | `produtos`, `produtos_vector`, `avaliacoes` |
| Reset da demo | `scripts/reset_demo.py::main` (145), `rebuild_catalog` (90), `smoke` (127) | `drop`, `delete_many`/`insert_many` em `sinonimos`, smoke com `$search` e `$vectorSearch` | várias |
| Benchmark | `scripts/bench_thesis.py::latency` (48), `freshness` (68), `freshness_mirror` (113) | compara fusão nativa x aplicação; mede frescor do espelho | `produtos`, `produtos_vector` |

## 4. Compartilhado × só desta PoV

**Vem de `_shared`** (pacote `pov-shared`):
- `grove_client` -> `backend/llm_gateway.py` (`_import_grove`, linha 42: tenta `import grove_client` e depois a pasta irmã `../../_shared`). `build_chat_model` (96) monta um `ChatAnthropic` que passa pelo gateway; usado por `agent.py:30` (`llm`) e `reviews.py:18` (`_llm`).
- Máscara de PII e detector de injection: `llm_gateway.py::mask_pii` (135) e `check_injection` (153) (usam o módulo de guardrails do pacote quando disponível, com heurística própria de reserva, `_FALLBACK_INJECTION` em 145). Usados em `agent.py::run_agent` e `reviews.py`.
- Não encontrei outro módulo `_shared` importado.

**Variáveis de ambiente** (nomes em `.env.example`):
- Comuns, vindas de `PoVs/.env.shared` via `_shared/sync_env.py`: `MONGODB_URI`, `GROVE_BASE_URL`, `GROVE_API_KEY`.
- Locais desta PoV: `DB_NAME` (padrão `POC`), `AI_MAX_CONCURRENCY`, `AGENT_RECURSION_LIMIT`, `CHECKPOINT_TTL_DAYS`, `MONGODB_MAX_POOL_SIZE`, `MONGODB_APP_RETRIES`; opcionais lidas no código: `MONGODB_SERVER_SELECTION_TIMEOUT_MS`, `AGENT_CHECKPOINT_COLLECTION`, `AGENT_CHECKPOINT_WRITES_COLLECTION`, `CATALOG_SYNC` (só para desligar o espelho), `CORS_ORIGINS`, `MAX_BODY_BYTES` (`main.py`), `ANTHROPIC_MODEL` e `REVIEWS_MODEL` (modelo do agente e das reviews), `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`, e nos scripts `ALLOW_DEMO_DB_WRITE`, `DROP_FIRST`, `TOTAL_DOCS`, `TOTAL_AVAL`, `VECTOR_SAMPLE`, `BATCH_SIZE`.

**Coleções e banco**: todas as coleções acima são exclusivas desta PoV; o banco `POC` é um nome genérico que pode coexistir com outras PoVs no mesmo cluster. Não encontrei no código coleção de outra PoV. Os scripts recusam escrever no banco da demo sem `ALLOW_DEMO_DB_WRITE=1`.

**Chamadas externas**:
- Claude via gateway Grove: agente (`agent.py::run_agent`, `invoke` do grafo em `_get_agent`) e resumo de reviews (`reviews.py:84`).
- Voyage: não é chamado pelo código Python. O embedding é feito dentro do Atlas pelo índice com autoEmbed (`voyage-4`, definido em `scripts/setup_search_indexes.py`), por isso os `$vectorSearch` passam `query` em texto e não `queryVector`.
- Langfuse: `backend/langfuse_tracing.py`, usado em `agent.py` e `reviews.py`; fail-open.

## 5. "Quero mexer em..." (atalhos)

- Texto de uma tela -> `frontend/src/tabs/<Aba>.jsx` (títulos e chips de exemplo ficam no próprio arquivo); nomes das abas em `frontend/src/App.jsx` (`TAB_LABELS`, `NAV_GROUPS`).
- Mudar o que a busca full-text pesa (nome x descrição, boost de nota) -> `backend/atlas.py::build_search_op` (496) e `_business_score` (487).
- Mudar filtros da busca (preço, categoria, estoque) -> `backend/atlas.py::build_filters` (513) e os campos de `SearchReq` em `backend/main.py` (127).
- Mudar a híbrida (pesos, candidatos, motor) -> `atlas.py::hybrid_native` (854) / `hybrid_score_fusion` (969); critério em `docs/adr/0001-rankfusion-vs-scorefusion.md` e `docs/adr/0002-rankfusion-desempate-lexical.md`.
- Mudar `numCandidates` da busca vetorial -> `atlas.py::_num_candidates` (286) e os `limit` em cada pipeline.
- Adicionar uma rota -> `backend/main.py` (modelo Pydantic + `@app.get/post`), lógica em `atlas.py` e função em `frontend/src/api.js`.
- Prompt do agente -> `backend/agent.py` (`SYSTEM_PROMPT`, linha 201); adicionar uma tool: criar o `_pipe_*`, a função `@tool`, registrar em `PIPELINE_BUILDERS` (99), `TOOL_META` (166) e na lista de `create_react_agent` (`_get_agent`).
- Prompt do resumo de avaliações -> `backend/reviews.py` (`PROMPT`, linha 20).
- Limite de passos do agente e de concorrência de IA -> `AGENT_RECURSION_LIMIT` e `AI_MAX_CONCURRENCY` (`main.py:255`).
- Definição dos índices (campos, analisadores, sinônimos) -> `scripts/setup_search_indexes.py`; depois `scripts/setup_search_indexes.py` de novo (ou `reset_demo.py`).
- Dados do catálogo sintético (marcas, descrições, preços) -> `scripts/populate_marketplace.py` (`make_produto` 527, `make_avaliacao` 569, `get_descricao` 498).
- Sinônimos -> `SYNONYMS` em `scripts/reset_demo.py` (grava em `sinonimos`).
- Mudar porta -> `frontend/vite.config.js` (5273), `CORS_ORIGINS`, `VITE_API_URL`, `start.sh` e `PORTS.md` na raiz do workspace.

## 6. Testes

Rodar (do README): `.venv/bin/python -m unittest discover -s backend/tests` (offline: sem Atlas, gateway nem Langfuse); `.venv/bin/python frontend/tests/e2e_demo.py` (roteiro completo contra o app rodando); `.venv/bin/python scripts/bench_thesis.py --db marketplace_test --freshness` (evidência medida, não é teste).

| Arquivo | O que cobre |
|---|---|
| `backend/tests/test_hardening_adversarial.py` | Entradas hostis (operadores, vazio, caracteres invisíveis, números absurdos, corpo grande, request-id), falhas do Mongo viram erro legível, adaptador Grove sem fallback direto, heurística de reserva |
| `backend/tests/test_catalog_sync.py` | Regras puras do espelho: planejar evento, pular o que é seed, aplicar, interruptor |
| `backend/tests/test_agent_pipelines.py` | Os pipelines das tools usam `$vectorSearch` e `autocomplete` com fuzzy |
| `backend/tests/test_request_models.py` | `SimilarReq` exige `produto_id` ou `nome` |
| `backend/tests/test_index_readiness.py` | Índice ausente ou não consultável vira erro, não lista vazia: extração dos índices (inclusive dentro de `$rankFusion`), releitura do cache negativo, resposta vazia relida, fail-open sem listagem, `degraded` no `/compare`, motivo do fallback híbrido |
| `backend/tests/test_rerank.py` | Cascata `$rankFusion` → `$rerank` (ADR-003): estágios, paridade do trace, volta ao `$rankFusion` puro se o `$rerank` for recusado, métricas novas do eval |
| `backend/tests/test_observability.py` | Contadores de requisição e de negócio |
| `frontend/tests/e2e_demo.py` | Roteiro ponta a ponta da demo |
| `frontend/tests/ui_visual_smoke.py` | Smoke visual das abas |
