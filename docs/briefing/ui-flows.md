# Interface, telas e fluxos

> Arquitetura em `architecture.md`, queries e índices em `queries.md`. Este arquivo é sobre a interface: o que cada tela mostra, o que ela chama no backend, e o que precisa aparecer nela pra fazer sentido em demo.

## Stack de frontend

React 18 + Vite + LeafyGreen (design system MongoDB, dark mode). Sem router — `App.jsx` guarda um índice de aba (`useState`) e um array `TABS` decide qual componente renderiza (`frontend/src/App.jsx`). `axios` como cliente HTTP compartilhado (`frontend/src/api.js`), `react-markdown` pra renderizar a resposta do agente.

Duas restrições técnicas que não são preferência de estilo:
- **React 18 fixo** — LeafyGreen não suporta React 19; subir a versão quebra o build.
- **`vite-plugin-node-polyfills` obrigatório** — uma dependência transitiva do LeafyGreen precisa de `Buffer`/`process`/`global`. Removê-lo não quebra o build — deixa a página em branco, o que é bem pior de diagnosticar.

## Layout geral (`App.jsx`)

Header compacto: marca ("MongoDB Atlas · Discovery Lab"), status do cluster (contagem de documentos + índices prontos, ou aviso "Atlas indisponível"), e uma navegação em dois níveis — grupos (`Busca`, `Analytics`, `Reviews RAG`, `Agente`) e, dentro do grupo "Busca", as quatro sub-abas.

Não há mais sidebar persistente nem KPIs globais fixos na tela — foram removidos numa revisão de UI ("modo palco") para que a tela abra direto no cenário, na consulta e no resultado. `GET /stats` continua disponível para status/preflight, só não ocupa espaço permanente.

Estado `offline` é tratado explicitamente: se `GET /stats` falhar ou vier com `degraded: true`, aparece um banner vermelho dizendo se é o backend que não respondeu (`http://localhost:8200` fora do ar) ou o cluster Atlas inacessível (backend no ar, mas sem conexão com o Atlas) — a distinção importa pra saber o que reiniciar.

## As sete abas

| # | Aba | Componente | Endpoint(s) | O que precisa aparecer na tela |
|---|---|---|---|---|
| 1 | Full-text | `frontend/src/tabs/AtlasSearch.jsx` | `POST /search`, `POST /search/facets` | relevância lexical sobre o catálogo inteiro (500K na demo), filtro dentro do `$search` vs. `$match` posterior, facetas, `scoreDetails`, highlight, toggle do sinal de negócio (`boost_business`) |
| 2 | Search × Vector | `frontend/src/tabs/SearchVsVector.jsx` | `POST /compare` | mesma intenção escrita de dois jeitos — lexical erra (às vezes zero resultado), vetorial acerta; coluna de fusão RRF |
| 3 | Híbrida | `frontend/src/tabs/HybridRRF.jsx` | `POST /hybrid`, `POST /hybrid-native`, `POST /hybrid-score-fusion` | rank de cada motor por documento, contagem "só lexical / só vetorial / nos dois", qual modo rodou (nativo ou fallback) e por quê |
| 4 | Similares | `frontend/src/tabs/Similares.jsx` | `POST /similar` | vizinho semântico a partir de um produto (não de texto digitado), pré-filtro dentro do `$vectorSearch` |
| 5 | Analytics | `frontend/src/tabs/Analytics.jsx` | `GET /analytics?full=` | `$facet` rodando no servidor; comparação amostra (12k, instantâneo) vs. full (coleção inteira) |
| 6 | Reviews RAG | `frontend/src/tabs/ReviewsRag.jsx` | `POST /reviews-rag` | resposta fundamentada só nas avaliações de fato recuperadas, produto + nota média |
| 7 | Agente | `frontend/src/tabs/AiAgent.jsx` | `POST /agent` | trace fiel: pipeline exibido = pipeline executado; continuidade de conversa via `thread_id` |

O client de API centraliza todas as chamadas em `frontend/src/api.js` — é o único lugar que sabe a URL do backend (`VITE_API_URL`, default `http://localhost:8200`) e o timeout (60s, ver seção abaixo).

## `MqlBlock` — o componente que sustenta a demo inteira

Renderiza o campo `pipeline` que **todo endpoint devolve junto com os resultados**, sempre nomeando a coleção (`POC.produtos`, `POC.produtos_vector`, ou o par quando o fluxo atravessa duas coleções, como `POC.produtos → POC.avaliacoes` no RAG). Aparece em praticamente toda aba — quem está assistindo a demo pode copiar e rodar a mesma query no Compass.

Regra do projeto ao adicionar qualquer aba nova: o endpoint devolve o pipeline, e a aba tem que renderizar o `MqlBlock`. Sem exceção — é a garantia de que a tese "mostrar a query" não vira promessa vazia numa feature nova.

## Badges de fallback / degradação

O backend sempre informa qual caminho executou; a interface é obrigada a mostrar isso, não só o resultado final:

- se o filtro de busca rodou dentro do `$search` ou virou `$match` posterior (`filters_in_search`);
- se a busca híbrida usou `$rankFusion`/`$scoreFusion` nativos ou caiu no RRF calculado na aplicação, e o motivo (`native: false`, `reason`);
- se a busca com sinônimos caiu no fallback sem sinônimo (`synonyms_fallback`);
- se o `$scoreFusion` sofreu o colapso de normalização conhecido (`degraded_reason` — ver `queries.md` seção 9 e o ADR `docs/adr/0001-rankfusion-vs-scorefusion.md`).

Um badge visível custa pouco espaço de tela e compra credibilidade; esconder um fallback é o tipo de coisa que, descoberta na pergunta seguinte do cliente, custa a reunião inteira.

## Timeout e estados de erro

O cliente axios tem 60s de timeout (`frontend/src/api.js`) — não é um número arbitrário: `$vectorSearch` sobre o subset com autoEmbed e a busca lexical sobre milhões de documentos podem legitimamente passar de 5-10s dependendo da query, e um timeout curto cortaria consultas válidas.

Quando o semáforo de concorrência de IA (`AI_MAX_CONCURRENCY`) satura, o backend responde HTTP 429 — a aba de Agente/Reviews RAG mostra "ocupado, tente de novo" em vez de ficar girando indefinidamente.

Toda mensagem de erro passa por `describeError` (`frontend/src/api.js`), que diz o que houve e como resolver: backend fora ("suba com `bash start.sh`"), timeout de 60 s, 422 com o motivo da validação (ex.: query só com caracteres invisíveis), 413, 429, 503 (Atlas) e, nos demais, o `X-Request-Id` para achar no log. Na aba Reviews RAG, avaliações retidas pelo filtro de prompt injection ganham badge, e o resumo indisponível (gateway fora) vira banner com as avaliações do MongoDB ainda visíveis.

## Screenshots existentes (`docs/screenshots/`)

| Arquivo | O que mostra |
|---|---|
| `atlas-search.png` | aba Full-text |
| `search-vs-vector.png` | aba Search × Vector |
| `hybrid-rrf.png` | aba Híbrida, modo RRF |
| `hybrid-rankfusion-residual.png` | aba Híbrida, `$rankFusion` com empate residual (ver ADR-001) |
| `hybrid-scorefusion.png` | aba Híbrida, modo `$scoreFusion` |
| `similares.png` | aba Similares |
| `analytics.png` | aba Analytics |
| `ai-agent.png` | aba Agente |

Catálogo é sintético, então nenhuma captura precisa de mascaramento. Regra do projeto: cada captura deve mostrar o **comportamento distintivo** da aba, não só a aba em si — o lexical retornando zero ao lado do vetorial retornando resultado, o `$rankFusion` com os ranks por engine visíveis, o pré-filtro dentro do `$vectorSearch`, o trace MQL do agente, o banner de degradação do `$scoreFusion`.

## Roteiro de demonstração (ordem recomendada)

1. **Full-text sobre o catálogo inteiro** — mostrar contagem de matches com filtro dentro do `$search`, `scoreDetails` explicando o ranking, pipeline ao lado.
2. **Ligar/desligar o sinal de negócio** no score e observar o ranking mudar — relevância é regra de negócio, e a regra está na query.
3. **A mesma intenção escrita de outro jeito** — lexical erra (às vezes zero resultado), vetorial acerta, mesmo cluster.
4. **Híbrida com `$rankFusion` nativo** — rank de cada motor por documento, quantos vieram só de um lado.
5. **Forçar o fallback** — RRF na aplicação, com o motivo declarado na tela. É o passo que mais impressiona: o próprio produto degradando com honestidade.
6. **RAG de avaliações** — resposta fundamentada nas avaliações de fato recuperadas.
7. **Agente** — uma pergunta de negócio, trace com pipelines reais, e uma pergunta de continuidade no mesmo `thread_id` provando que o checkpoint no Atlas segurou o contexto.
8. **`/api/metrics`** — contadores e latência por rota, se o gestor perguntar sobre observabilidade.

## Antes de apresentar (checklist)

- `python3 setup_search_indexes.py --status` — tudo `READY` (o índice vetorial com autoEmbed demora mais que o de busca lexical).
- `GET /stats` confirmando números reais do cluster, não zeros.
- Uma busca de aquecimento em cada aba que usa IA, para o primeiro clique da demo não pagar cold start do LLM.
