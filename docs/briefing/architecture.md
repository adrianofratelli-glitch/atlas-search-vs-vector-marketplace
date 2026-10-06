# Arquitetura — search-e-vector-marketplace

> Visão rápida pra responder "como isso é feito" sem precisar abrir o código. Queries e índices detalhados estão em `queries.md`. Telas e fluxos em `ui-flows.md`. Comportamento do agente de IA em `agent-behavior.md`.

## O que é

PoC de MongoDB Atlas Search & Vector Search sobre um catálogo sintético de marketplace (estilo Mercado Livre/Shopee). Cobre busca full-text, busca semântica, ranqueamento híbrido (`$rankFusion`/`$scoreFusion` nativos + RRF calculado na aplicação), analytics agregado, RAG sobre avaliações reais e um agente ReAct (LangGraph) com quatro ferramentas MongoDB.

Tese central: um cluster Atlas único cobre busca lexical, busca vetorial e busca híbrida — sem motor de busca externo nem vector DB separado.

## Stack

- **Backend**: Python, FastAPI (`backend/main.py`), PyMongo direto (sem ODM), LangChain/LangGraph para o agente.
- **Frontend**: React 18 + Vite + LeafyGreen (design system MongoDB). React 18 é fixo — LeafyGreen não suporta React 19 ainda. `vite-plugin-node-polyfills` é obrigatório (dependência do LeafyGreen precisa de `Buffer`) — removê-lo não quebra o build, só deixa a página em branco.
- **Banco**: MongoDB Atlas — Atlas Search (lexical), Vector Search (autoEmbed com modelo `voyage-4`), Aggregation Framework.
- **LLM**: Claude (`claude-sonnet-5-5`) só pelo gateway Grove: `backend/llm_gateway.py` mantém a interface `ChatAnthropic` do LangChain e delega a chamada a `grove_client.create_message` (pacote `pov-shared`), com retry, circuit breaker e fallback ligados por padrão. Sem fallback para `ANTHROPIC_API_KEY`.
- **Tracing**: Langfuse v2 opcional e fail-open (`backend/langfuse_tracing.py`), trace criada depois da máscara de PII.
- **Observabilidade**: logging estruturado + `/api/metrics` e `/metrics` (Prometheus) em `backend/observability.py`.

## Componentes

```
frontend/src/tabs/*.jsx ──axios (src/api.js)──► backend/main.py (rotas FastAPI, modelos Pydantic)
                                                   ├── atlas.py          TODOS os pipelines MongoDB (camada de acesso a dados)
                                                   ├── agent.py          agente ReAct LangGraph + 4 tools
                                                   ├── reviews.py        RAG de sumarização de avaliações
                                                   └── observability.py  logging estruturado + /api/metrics
```

Scripts de setup fora do backend:
- `populate_marketplace.py` — gera o catálogo sintético e popula as três coleções.
- `setup_search_indexes.py` — cria/corrige os índices Atlas Search e Vector Search (idempotente).

Onde encontrar cada coisa rapidamente:
- **Toda query e pipeline MongoDB**: `backend/atlas.py` (é a regra do projeto — nada de aggregation espalhada por outros arquivos).
- **Rotas HTTP e validação de request**: `backend/main.py`.
- **Lógica do agente de IA e suas tools**: `backend/agent.py`.
- **RAG de reviews**: `backend/reviews.py`.
- **Definição de índices**: `setup_search_indexes.py`.
- **Geração/seed de dados**: `populate_marketplace.py`.

## Modelo de dados — banco `POC` (padrão; configurável via `DB_NAME`)

| Coleção | Volume | Papel |
|---|---|---|
| `produtos` | 500 mil no banco da demo (o gerador vai até 20 milhões, `TOTAL_DOCS`) | catálogo principal — Atlas Search lexical |
| `produtos_vector` | 500 mil (subset via `$sample`) | Vector Search (autoEmbed voyage-4) **+** índice lexical auxiliar |
| `avaliacoes` | 100 mil no banco da demo | reviews usadas no RAG e pelo agente |
| `sinonimos` | 7 | fonte do mapeamento `sinonimos_produtos` |
| `marketplace_checkpoints` / `marketplace_checkpoint_writes` | — | memória do LangGraph (`MongoDBSaver`), chaveada por `thread_id`, TTL 30 dias |

### Por que duas coleções de produto

`produtos_vector` existe porque vetorizar o catálogo inteiro não agrega ao PoC quando ele está em escala de milhões e custa caro (embedding é cobrado). Na escala do gerador (20M), a busca lexical roda contra o catálogo inteiro e a vetorial/híbrida contra o subset de 500K; no banco atual da demo as duas coleções têm 500K. O banco `POC` é compartilhado com outras PoVs: este app só lê/escreve as coleções listadas acima.

`produtos_vector` também carrega um **índice lexical próprio** (`produtos_vector_search`), além do vetorial. Motivo: `$rankFusion` e `$scoreFusion` nativos do MongoDB exigem que os dois sub-pipelines (textual e semântico) rodem na **mesma coleção**. Sem esse índice lexical extra, o híbrido nativo não é possível — só sobra o RRF calculado na aplicação.

## Fluxo de dados (exemplo: busca lexical)

1. Usuário digita uma query na aba "Busca full-text" (`frontend/src/tabs/AtlasSearch.jsx`).
2. `src/api.js` faz `POST /search` com query, filtros de categoria/preço/estoque.
3. `main.py` valida via Pydantic (`SearchReq`) e chama `atlas.atlas_search(...)`.
4. `atlas.py` monta o pipeline `$search` (ver `queries.md`), decide se os filtros rodam dentro do `$search` (`compound.filter`) ou viram `$match` posterior — depende dos tipos declarados no índice vivo, lidos via `$listSearchIndexes`.
5. Resultado + pipeline executado + flags de degradação voltam juntos na resposta.
6. O frontend renderiza os resultados **e** o pipeline (`MqlBlock`), além de badges quando algo caiu em modo fallback.

Esse padrão — endpoint sempre devolve o pipeline executado, e a UI sempre renderiza — se repete em todos os oito endpoints principais.

## Decisões de arquitetura relevantes

### 1. Degradação graciosa como padrão central

`atlas.py` inspeciona os índices Atlas Search/Vector Search **em tempo real** via `$listSearchIndexes` (cache com TTL de 60s, `get_search_indexes()` em `backend/atlas.py`), em vez de assumir que um índice existe e está pronto. Consequências práticas:

- `search_filter_caps()` (`backend/atlas.py`) verifica se `categoria`/`preco`/`em_estoque` têm os tipos certos (`token`/`number`/`boolean`) no índice vivo. Se sim, filtros entram em `compound.filter` dentro do `$search`; se não, viram `$match` posterior — e a resposta expõe qual caminho foi usado (`filters_in_search`).
- `hybrid_native()` e `hybrid_score_fusion()` caem para RRF calculado na aplicação, **com o motivo explícito na resposta**, quando o cluster não tem os requisitos (MongoDB 8.1+, índice lexical na mesma coleção).
- Todo fallback é retornado explicitamente no JSON — nunca escondido — e a UI é obrigada a mostrar um badge.

Por que isso importa pro gestor: numa demo ao vivo, um índice ainda em `BUILDING` ou um cluster abaixo da versão mínima não quebra a apresentação — o sistema se adapta e ainda assim mostra o pipeline real que rodou.

### 2. Transparência de MQL

Todo endpoint devolve o pipeline agregado que executou, junto com os resultados (campo `pipeline` na resposta JSON). No agente, as tools e a função que gera o "trace" pra exibição (`build_tool_pipeline`, `backend/agent.py`) usam os **mesmos** construtores `_pipe_*` — não existe uma versão "para rodar" e outra "para mostrar". Isso é enforçado como regra de projeto: se alguém precisar duplicar uma função de pipeline para exibição, é sinal de que a arquitetura escorregou.

### 3. Relevância com sinal de negócio

A busca lexical não usa só o score textual do BM25/Lucene: multiplica a relevância pela nota média do produto (`_business_score()`, `backend/atlas.py`), com fallback de 3.0 quando a nota está ausente. Isso é alternável (`boost_business`) para comparar lado a lado o ranking com e sem regra de negócio — argumento de que tuning de relevância de e-commerce é regra que vive na query, não um serviço de reranking à parte.

### 4. $rankFusion vs $scoreFusion — ver ADR

`docs/adr/0001-rankfusion-vs-scorefusion.md` documenta por que `$rankFusion` (RRF nativo) é o modo padrão da aba híbrida, e `$scoreFusion` (RSF) fica exposto como modo didático com um bug observado: a normalização `minMaxScaler` pode colapsar o score combinado de todos os resultados para `0` mesmo com score bruto não-zero em algum sub-pipeline. Isso é detectado (`hybrid_score_fusion`, `backend/atlas.py`) e reportado via `degraded_reason` — a causa raiz não era o cluster, era texto quase idêntico entre produtos gerados sinteticamente (empatando o score lexical em massa); corrigido em `populate_marketplace.py` adicionando um segundo eixo de variação textual (`DESC_DIFERENCIAIS`).

### 5. Inicialização preguiçosa do agente

O agente LangGraph e seu checkpointer MongoDB (`MongoDBSaver`) só são construídos na primeira requisição a `/agent` (`_get_agent()`, `backend/agent.py`, decorado com `@lru_cache`). Isso mantém os imports de módulo livres de efeito colateral — testes unitários, lint e endpoints que não usam o agente não exigem cluster Atlas alcançável.

### 6. Concorrência de IA limitada

`/agent` e `/reviews-rag` compartilham um semáforo (`AI_MAX_CONCURRENCY`, default 4, `backend/main.py`). Saturado, responde HTTP 429 em vez de enfileirar — numa demo, uma requisição de LLM travada é pior que uma recusa explicada.

## Como rodar

```bash
.venv/bin/python scripts/reset_demo.py --db marketplace_test --rebuild-catalog \
  --products 20000 --vector 3000 --reviews 20000      # banco de teste do zero (~2 min)
ALLOW_DEMO_DB_WRITE=1 .venv/bin/python scripts/reset_demo.py   # demo: limpa memória do agente + confere índices
bash start.sh                          # backend 127.0.0.1:8200 + frontend 127.0.0.1:5273
DB_NAME=marketplace_test bash start.sh # mesmo app no banco de teste (variável de ambiente vence o .env)
python3 setup_search_indexes.py --status   # só mostra status dos índices (leitura)

.venv/bin/python -m unittest discover -s backend/tests   # offline, inclui a suíte adversarial
.venv/bin/python frontend/tests/e2e_demo.py              # roteiro completo no navegador
.venv/bin/python scripts/bench_thesis.py --db marketplace_test --freshness
```

`.env` na raiz: `MONGODB_URI`, `DB_NAME` (default `POC`), `GROVE_BASE_URL` e `GROVE_API_KEY` (só para `/agent` e `/reviews-rag`). `populate_marketplace.py`, `setup_search_indexes.py` e `reset_demo.py` recusam bancos que não terminam em `_test` sem `ALLOW_DEMO_DB_WRITE=1`.

## Resiliência

- **MongoDB** (`backend/atlas.py`): `serverSelectionTimeoutMS`/`connectTimeoutMS` 5 s, `socketTimeoutMS` 15 s, `waitQueueTimeoutMS` 5 s, `maxTimeMS` 10 s por agregação. Cluster inacessível vira "Atlas inacessível…" (sem host nem mensagem crua do driver); `AutoReconnect`/`NetworkTimeout` e throttling do autoEmbed (Voyage, 429/5xx) ganham uma retentativa (`MONGODB_APP_RETRIES`, 0 desliga). `/stats` e `/compare` não esperam o timeout duas vezes quando o cluster já caiu.
- **Voyage**: o embedding da query roda dentro do Atlas (autoEmbed), então o timeout dele é o `maxTimeMS` da agregação e a retentativa é a de cima. Não há cliente Voyage no app.
- **LLM**: `grove_client` (timeout 30 s por tentativa, 2 retries, breaker 5/30 s) + teto de passos ReAct.
- **Entrada**: queries normalizadas (NFC, sem zero-width/bidi/controle), filtros e preços com limite, `NaN`/`Infinity` recusados, corpo acima de 64 KB → 413, 422 sem eco do payload, `X-Request-Id` saneado, refresh do analytics em single-flight.

## Fronteiras do PoC (dito explicitamente pra evitar mal-entendido em demo)

- Catálogo é sintético, não dado de cliente.
- Vetorial e híbrida rodam sobre `produtos_vector` (500K), mostrado na tela.
- RAG responde só sobre o subconjunto de produtos que tem avaliação (~2% do catálogo).
- Sem autenticação nos endpoints.
- Métricas ficam em memória do processo — resetam no restart.
