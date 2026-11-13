# Queries, pipelines e índices MongoDB

> Números de linha podem ficar defasados após mudanças: a fonte atualizada é [MAPA_DO_CODIGO.md](../MAPA_DO_CODIGO.md).

> Isto é o arquivo pra abrir quando o gestor perguntar "onde está a query de X". Todo pipeline aqui vem do código real de `backend/atlas.py`, `backend/agent.py`, `scripts/setup_search_indexes.py` e `scripts/populate_marketplace.py` — nada foi inventado. Localização = arquivo:linha na versão lida nesta auditoria; confira a linha exata antes de citar em reunião, código muda.

Índice rápido:
1. [Índices Atlas Search e Vector Search](#1-índices-atlas-search-e-vector-search)
2. [Busca lexical (Atlas Search)](#2-busca-lexical-atlas-search)
3. [Facetas ($searchMeta)](#3-facetas-searchmeta)
4. [Busca vetorial (Vector Search)](#4-busca-vetorial-vector-search)
5. [Produtos similares — pré-filtro vetorial](#5-produtos-similares--pré-filtro-vetorial)
6. [Comparação lexical vs. vetorial + RRF](#6-comparação-lexical-vs-vetorial--rrf)
7. [Híbrida tunável — RRF na aplicação](#7-híbrida-tunável--rrf-na-aplicação)
8. [Híbrida nativa — $rankFusion](#8-híbrida-nativa--rankfusion)
9. [Híbrida nativa — $scoreFusion](#9-híbrida-nativa--scorefusion)
10. [Analytics — $facet](#10-analytics--facet)
11. [RAG de avaliações](#11-rag-de-avaliações)
12. [Pipelines do agente de IA](#12-pipelines-do-agente-de-ia)
13. [Índices B-tree simples](#13-índices-b-tree-simples)
14. [Introspecção de índices ($listSearchIndexes)](#14-introspecção-de-índices-listsearchindexes)

---

## 0. Espelho produtos → produtos_vector (change stream)

`backend/catalog_sync.py`, iniciado no startup da API:

```python
db.produtos.watch([{"$match": {"operationType": {"$in": ["insert", "replace", "update", "delete"]}}}],
                  full_document="updateLookup", full_document_before_change="whenAvailable",
                  resume_after=<token salvo em catalog_sync_state>)
# insert/replace/update: produtos_vector.replace_one({"produto_id": doc["produto_id"]}, doc_sem_id)
#                        ou, se não existe, replace_one({"_id": doc["_id"]}, doc, upsert=True)
# delete:                produtos_vector.delete_many({"$or": [{"_id": key}, {"produto_id": pre_imagem.produto_id}]})
```

**Por que existe**: o catálogo fica em duas coleções (o índice lexical extra e o vetorial precisam estar juntos em `produtos_vector` para `$rankFusion`/`$scoreFusion`), e um produto novo em `produtos` precisa aparecer também na busca vetorial. Índice B-tree `produto_id` em `produtos_vector` sustenta o `replace_one`; pré-imagens em `produtos` (`collMod changeStreamPreAndPostImages`) permitem remover cópias antigas do seed, que têm `_id` diferente.

## 1. Índices Atlas Search e Vector Search

Definidos em `scripts/setup_search_indexes.py`, aplicados de forma **idempotente** (roda quantas vezes quiser; mescla no que já existe em vez de sobrescrever).

### 1.1 `produtos_search` — índice lexical em `produtos`

Local: `scripts/setup_search_indexes.py:82` (definição completa, usada só se o índice ainda não existir) e `scripts/setup_search_indexes.py:75` (patch aplicado num índice já existente).

```python
PRODUTOS_SEARCH_FULL_DEF = {
    "mappings": {
        "dynamic": False,
        "fields": {
            "nome": [
                {"type": "autocomplete", "analyzer": "lucene.standard",
                 "tokenization": "edgeGram", "minGrams": 2, "maxGrams": 15},
                {"type": "string", "analyzer": "lucene.standard"},
            ],
            "descricao":   {"type": "string", "analyzer": "lucene.portuguese"},
            "marca":       {"type": "string"},
            "produto_id":  {"type": "token"},
            "categoria":   [{"type": "stringFacet"}, {"type": "token"}],
            "subcategoria": {"type": "stringFacet"},
            "genero":      {"type": "stringFacet"},
            "em_estoque":  {"type": "boolean"},
            "preco":       [{"type": "numberFacet"}, {"type": "number"}],
            "avaliacao_media": {"type": "number"},
        },
    },
    "synonyms": [
        {"name": "sinonimos_produtos", "analyzer": "lucene.standard",
         "source": {"collection": "sinonimos"}},
    ],
}
```

**Por que existe**: é o índice que sustenta toda a busca full-text (aba "Busca full-text" e o fallback lexical de outras abas). `nome` tem `autocomplete` (edge n-gram 2-15) para digitação incremental, `descricao` usa o analisador `lucene.portuguese` (stemming em português — "tênis" casa com "tênis", "correr" com "corrida" via raiz). `categoria` e `preco` têm **dois tipos cada** (`token`+`stringFacet`/`number`+`numberFacet`) de propósito: o segundo tipo é o que habilita filtro dentro de `compound.filter` e faceta via `$searchMeta` ao mesmo tempo.

O patch (`patch_produtos_search`, `scripts/setup_search_indexes.py:132`) existe porque essa demo evoluiu em cima de um índice que já estava rodando em produção/demo anterior sem os tipos filtráveis — o script detecta o que falta e adiciona sem derrubar sinônimos/analisadores já configurados.

### 1.2 `produtos_vector` — índice Vector Search (autoEmbed) em `produtos_vector`

Local: `scripts/setup_search_indexes.py:45`.

```python
VECTOR_INDEX_DEF = {
    "fields": [
        {"type": "autoEmbed", "modality": "text", "model": "voyage-4", "path": "descricao"},
        {"type": "filter", "path": "categoria"},
        {"type": "filter", "path": "preco"},
        {"type": "filter", "path": "em_estoque"},
    ]
}
```

**Por que existe**: é o coração da busca semântica. `autoEmbed` delega ao Atlas a geração do embedding — tanto no ingest quanto na query — usando o modelo Voyage AI `voyage-4`, sem pipeline de embedding do lado da aplicação. Os três campos `filter` existem para permitir **pré-filtro dentro do próprio `$vectorSearch`** (ver seção 5) em vez de filtrar depois de trazer os resultados — pré-filtro dentro do estágio preserva a qualidade do ranking semântico porque o filtro atua antes do ANN, não depois.

Build desse índice reprocessa a coleção inteira (~30-50 min para 200-500K docs) — é o índice mais lento pra ficar `READY`.

### 1.3 `produtos_vector_search` — índice lexical em `produtos_vector`

Local: `scripts/setup_search_indexes.py:55`.

```python
VECTOR_SEARCH_INDEX_DEF = {
    "mappings": {
        "dynamic": False,
        "fields": {
            "nome": [
                {"type": "autocomplete", "analyzer": "lucene.standard",
                 "tokenization": "edgeGram", "minGrams": 2, "maxGrams": 15},
                {"type": "string", "analyzer": "lucene.standard"},
            ],
            "descricao":  {"type": "string", "analyzer": "lucene.portuguese"},
            "marca":      {"type": "string"},
            "produto_id": {"type": "token"},
            "categoria":  {"type": "token"},
            "em_estoque": {"type": "boolean"},
            "preco":      {"type": "number"},
        },
    }
}
```

**Por que existe**: é um índice lexical "enxuto" (mesma estrutura de `produtos_search`, sem sinônimos), criado na coleção `produtos_vector` **só** para viabilizar `$rankFusion`/`$scoreFusion` nativos — esses estágios exigem que os dois sub-pipelines (textual + semântico) rodem sobre a **mesma coleção**. Sem ele, o híbrido nativo cai automaticamente para RRF calculado na aplicação (ver seção 8).

---

## 2. Busca lexical (Atlas Search)

Local: `backend/atlas.py` (`atlas_search`), monta o pipeline em `build_search_pipeline` (`backend/atlas.py`).

**O que faz**: busca full-text sobre todos os documentos de `produtos` (500K na demo; o gerador vai a 20M), com autocomplete fuzzy em `nome`, texto fuzzy em `descricao`, filtros opcionais de categoria/preço/estoque, highlight e score explicado.

**Por que existe**: é a aba "Busca full-text" — demonstra Atlas Search em escala, o padrão de decidir dinamicamente se o filtro roda dentro ou fora do `$search`, e o sinal de negócio no ranking.

```python
# Operador de busca (backend/atlas.py build_search_op)
{
    "compound": {
        "should": [
            {"autocomplete": {"query": query, "path": "nome",
                              "fuzzy": {"maxEdits": 1}, "score": nome_score}},
            {"text": {"query": query, "path": "descricao", "fuzzy": {"maxEdits": 1}}},
        ],
        "minimumShouldMatch": 1,
    }
}

# Pipeline completo (backend/atlas.py build_search_pipeline)
[
    {"$search": {
        "index": "produtos_search",
        **search_op,   # compound acima, com compound.filter anexado se aplicável
        "count": {"type": "total"},
        "highlight": {"path": ["nome", "descricao"], "maxCharsToExamine": 500, "maxNumPassages": 1},
        "scoreDetails": True,
    }},
    # {"$match": mql_filter}  ← só aparece quando o índice NÃO suporta o filtro inline
    {"$limit": 50},
    {"$addFields": {"_total_matches": "$$SEARCH_META.count.total"}},
    {"$project": {
        "_id": 0,
        "nome": 1, "marca": 1, "categoria": 1, "subcategoria": 1,
        "preco": 1, "preco_original": 1, "desconto_pct": 1,
        "avaliacao_media": 1, "total_avaliacoes": 1,
        "em_estoque": 1, "score": {"$meta": "searchScore"},
        "highlights": {"$meta": "searchHighlights"},
        "scoreDetails": {"$meta": "searchScoreDetails"},
        "_total_matches": 1,
    }},
]
```

**Sinal de negócio no score** (`_business_score`, `backend/atlas.py`) — multiplica a relevância textual pela nota média do produto:

```python
{"function": {
    "multiply": [
        {"score": "relevance"},
        {"path": {"value": "avaliacao_media", "undefined": 3.0}},
    ]
}}
```

**Decisão de onde vai o filtro** (`build_filters`, `backend/atlas.py`): consulta `search_filter_caps()` (baseado no índice vivo lido via `$listSearchIndexes`) e decide, campo a campo:
- `preco` → `compound.filter` com `{"range": {"path": "preco", "gte": ..., "lte": ...}}` se o índice tem tipo `number`; senão vira `$match` pós-`$search`.
- `em_estoque` → `{"equals": {"path": "em_estoque", "value": True}}` se `boolean`; senão `$match`.
- `categoria` → `{"in": {"path": "categoria", "value": [...]}}` se `token`; senão `$match`.

Isso importa porque filtro **dentro** do `$search` mantém `$$SEARCH_META.count.total` correto (a contagem reflete o filtro); filtro **depois** do `$search` filtra resultados já ranqueados e a contagem exibida fica pré-filtro (a API sinaliza isso via `filters_in_search: bool`).

**Caminho de sinônimos**: quando `with_synonyms=True`, o operador vira `{"text": {"query": query, "path": ["nome", "descricao"], "synonyms": "sinonimos_produtos"}}` — como não é `compound`, precisa ser embrulhado num `compound.must` (`_apply_search_filters`, `backend/atlas.py`) antes de receber `compound.filter`, porque operadores não-compound não aceitam filtro direto. Se o analisador de sinônimos não estiver pronto, a resposta sinaliza `synonyms_fallback: true` e reexecuta sem sinônimos.

---

## 3. Facetas ($searchMeta)

Local: `backend/atlas.py` (`search_facets`).

**O que faz**: retorna contagens por categoria e por faixa de preço direto do servidor, sem trazer os documentos.

```python
[{"$searchMeta": {
    "index": "produtos_search",
    "facet": {
        "operator": search_op,
        "facets": {
            "categorias":   {"type": "string", "path": "categoria", "numBuckets": 10},
            "faixas_preco": {"type": "number", "path": "preco",
                             "boundaries": [0, 100, 500, 1000, 3000, 5000, 10000, 15000]},
        },
    },
}}]
```

**Por que existe**: alimenta os filtros de faceta da aba de busca (contadores "Eletrônicos (1.234)" etc.) — calcular isso na aplicação, sobre uma página de 50 resultados, daria números errados; `$searchMeta` calcula sobre o universo inteiro que casa com a query.

---

## 4. Busca vetorial (Vector Search)

Usada em várias abas — a query "crua" mais simples está em `agent.py` (ver seção 12); a versão da aba "Search vs. Vector" está em `compare_search_vector` (seção 6).

```python
{"$vectorSearch": {
    "index": "produtos_vector",
    "path": "descricao",
    "query": embed_query_text(query),   # texto com PII mascarada; o autoEmbed do Atlas chama a Voyage
    "numCandidates": _num_candidates(limit, multiplier),
    "limit": limit,
}}
```

**Texto enviado à Voyage** ([ADR-004](../adr/0004-mascara-pii-no-autoembed.md)): o autoEmbed gera o embedding chamando a Voyage AI, um terceiro. Por isso toda `query` de `$vectorSearch` (e o texto do `$rerank`) passa por `embed_query_text()` (`backend/atlas.py`), que aplica `llm_gateway.mask_pii` (CPF/CNPJ, telefone, e-mail, cartão, chave PIX aleatória). O pipeline exibido carrega o texto mascarado, e a resposta traz `embed_query_masked`. O `$search` lexical fica com o texto original, porque não sai do cluster. Se a máscara falhar, nada vai à Voyage: `/compare` roda só a perna lexical e nomeia o motivo (`PII_MASK_UNAVAILABLE`); híbridos, `/similar` e a ferramenta do agente devolvem o erro sem executar. Sem PII, o texto sai byte a byte igual (`backend/tests/test_embed_pii_mask.py`, sobre todas as consultas do eval), e o eval validado deu métricas e top 5 idênticos antes e depois.

`_num_candidates(limit, multiplier=10)` (`backend/atlas.py`) centraliza o cálculo — por padrão o pool de candidatos é 10x o `limit` (guidance do MongoDB para o ANN/HNSW: candidatos demais custa latência, de menos custa recall). Multiplicadores maiores (15x, 22x) são usados quando há pré-filtro pesado ou a comparação precisa de mais recall, e cada call site documenta por quê.

---

## 5. Produtos similares — pré-filtro vetorial

Local: `backend/atlas.py` (`find_similar`).

**O que faz**: acha o produto base (por id ou por nome, via Atlas Search) e busca vizinhos semânticos, com filtro de categoria/estoque aplicado **dentro** do `$vectorSearch`.

```python
{"$vectorSearch": {
    "index": "produtos_vector",
    "path": "descricao",
    "query": base_descricao,
    "numCandidates": _num_candidates(9, multiplier=22),   # 22x: compensa o descarte do pré-filtro
    "limit": 9,
    "filter": {"categoria": categoria_do_base, "em_estoque": True},   # PRÉ-FILTRO — dentro do estágio
}}
```

**Por que existe**: é a aba "Similares" — demonstra que filtro estruturado (categoria, estoque) e busca semântica podem rodar no **mesmo** estágio, sem pós-filtro na aplicação, sem degradar a qualidade do ranking. É o argumento mais forte contra "vector DB separado do banco + filtro na aplicação depois".

Detalhe de implementação relevante: quando `produtos_vector` tem índice lexical (`produtos_vector_search`), o produto base é buscado **na própria** `produtos_vector` (mesma distribuição textual do corpus vetorizado); sem esse índice, cai numa heurística cross-collection (base vem de `produtos`, vetor roda em `produtos_vector`) — logada como aviso porque a relevância pode degradar silenciosamente (distribuições textuais diferentes entre o catálogo e o subset).

---

## 6. Comparação lexical vs. vetorial + RRF

Local: `backend/atlas.py` (`compare_search_vector`).

**O que faz**: roda a mesma consulta nos dois motores lado a lado (frase exata em `nome` ou o operador compound completo) e funde por RRF pra mostrar onde cada um achou e onde nenhum achou.

```python
# Lexical
[{"$search": {"index": search_index, **search_op}}, {"$limit": 10}, proj]

# Vetorial
[{"$vectorSearch": {"index": "produtos_vector", "path": "descricao", "query": query,
                    "numCandidates": _num_candidates(10, multiplier=15), "limit": 10}}, proj]
```

**Por que existe**: é a aba que prova visualmente "a lexical erra (às vezes zero resultado), a vetorial acerta" — quando `mode="phrase"`, uma busca por frase exata que não existe literalmente no catálogo retorna zero no lexical e ainda assim acha produtos relevantes no vetorial.

---

## 7. Híbrida tunável — RRF na aplicação

Local: `backend/atlas.py` (`hybrid_rrf`), fusão em `_rrf_fuse` (`backend/atlas.py`).

**O que faz**: roda busca textual e vetorial em paralelo (`ThreadPoolExecutor`, 2 workers) e funde por Reciprocal Rank Fusion: `score = Σ 1/(k + rank)`, com `k` ajustável pela UI (10 a 200).

```python
s_pipe = [
    {"$search": {"index": search_index, "compound": {"should": [
        {"autocomplete": {"query": query, "path": "nome", "fuzzy": {"maxEdits": 1}, "score": {"boost": {"value": 3}}}},
        {"text": {"query": query, "path": "descricao", "fuzzy": {"maxEdits": 1}}},
    ]}}},
    {"$limit": n_search},
    {"$project": {"_id": 0, "produto_id": 1, "nome": 1, "categoria": 1, "preco": 1,
                  "search_score": {"$meta": "searchScore"}}},
]

v_pipe = [
    {"$vectorSearch": {"index": "produtos_vector", "path": "descricao", "query": query,
                       "numCandidates": _num_candidates(n_vector), "limit": n_vector}},
    {"$project": {"_id": 0, "produto_id": 1, "nome": 1, "categoria": 1, "preco": 1,
                  "vector_score": {"$meta": "vectorSearchScore"}}},
]
```

**Por que existe**: é o modo híbrido que **sempre funciona** — não depende de versão do Atlas nem de layout de índice específico — e por isso é o fallback de `hybrid_native`/`hybrid_score_fusion`. `k` foi limitado ao intervalo [10, 200] (`HybridReq.k` em `backend/main.py`) porque fora dessa faixa o RRF deixa de discriminar resultados de forma útil (k baixo: domina o rank 1; k alto: achata as diferenças).

---

## 8. Híbrida nativa — $rankFusion

Local: `backend/atlas.py` (`hybrid_native`), parsing do detalhe em `_parse_rank_fusion_details` (`backend/atlas.py`).

```python
[
    {"$rankFusion": {
        "input": {"pipelines": {
            "textual": [
                {"$search": {"index": search_index, "compound": {"should": [
                    {"autocomplete": {"query": query, "path": "nome", "fuzzy": {"maxEdits": 1}}},
                    {"text": {"query": query, "path": "descricao", "fuzzy": {"maxEdits": 1}}},
                ]},
                # RANK_FUSION_LEXICAL_SORT: relevância primeiro, produto_id só desempata
                "sort": {"score": {"$meta": "searchScore", "order": -1}, "produto_id": 1}}},
                {"$limit": limit},
            ],
            "semantico": [
                {"$vectorSearch": {"index": "produtos_vector", "path": "descricao", "query": query,
                                   "numCandidates": _num_candidates(limit), "limit": limit}},
            ],
        }},
        "combination": {"weights": {"textual": 1, "semantico": 1}},
        "scoreDetails": True,
    }},
    {"$limit": limit},
    {"$project": {"_id": 0, "produto_id": 1, "nome": 1, "categoria": 1, "preco": 1,
                  "score": {"$meta": "score"}, "scoreDetails": {"$meta": "scoreDetails"}}},
]
```

**Por que existe**: RRF calculado **no servidor**, numa aggregation só — é a demonstração de que o MongoDB faz fusão híbrida nativamente (MongoDB 8.1+), sem código de fusão na aplicação. Requer o índice lexical na mesma coleção do vetorial (seção 1.3); se ausente ou se o servidor não conhece o estágio, cai para `hybrid_rrf` (seção 7) **com o motivo declarado na resposta** (`reason`).

**Por que o `sort` no `$search`** ([ADR-002](../adr/0002-rankfusion-desempate-lexical.md)): o `$rankFusion` dá o mesmo rank a scores empatados (1, 1, 1, ..., 103), e o catálogo tem dezenas de unidades do mesmo modelo (uma por cor) com score lexical idêntico. Sem desempate, o top N lexical inteiro saía com `rank_search = 1`, cada um valendo `1/61` como o 1º vetorial, e o `$limit` final empurrava os vetoriais para fora do topo. Ordenar por `searchScore` e desempatar por `produto_id` (UUID, mapeado como `token`, portanto ordenável) dá ranks posicionais 1..N, como o RRF da aplicação. Eval (26 consultas, banco da demo, 2026-11-12, **gabarito rascunho**): recall@5 do híbrido nativo 0,769 → 0,962, MRR 0,70 → 0,885-0,904; vetorial 0,885 / 0,891, lexical 0,50 / 0,50. Com o **gabarito validado** (i04 e i05 restritos no mesmo dia), recall@5 atual: híbrido 0,923 / vetorial 0,846 / lexical 0,462; MRR do híbrido 0,846–0,87 (ver 8.1 e a seção de qualidade do README). Pesos ficam 1:1: `semantico: 1.2` sem desempate transformava o híbrido no ranking vetorial e perdia as consultas de nome exato e digitação que o vetorial erra.

`rank_fusion_tiebreak_supported()` (`backend/atlas.py`) confere na definição do índice (`$listSearchIndexes`) se `produto_id` é `token`. Se não for, o pipeline roda sem `sort` e a resposta traz `degraded_reason`, mostrado em banner na aba Híbrida. Sub-pipelines de `$rankFusion` não aceitam `$sort` por `{$meta: "searchScore"}` depois do `$search` ("query requires $search score metadata"), por isso o desempate fica dentro do próprio `$search`.

### 8.1 Cascata opcional: `$rankFusion` → `$rerank`

Local: `rerank_stages()` e `hybrid_native(query, limit, rerank=True)` (`backend/atlas.py`); `POST /hybrid-native` com `"rerank": true`; checkbox na aba Híbrida. Com o rerank, o pipeline acima ganha dois estágios entre o `$rankFusion` e o `$limit`, e o `$project` passa a ler os campos guardados:

```python
{"$addFields": {"rrf_score": {"$meta": "score"}, "rrf_details": {"$meta": "scoreDetails"}}},
{"$rerank": {"query": {"text": query}, "path": "descricao",
             "numDocsToRerank": 2 * limit, "model": RERANK_MODEL}},      # RERANK_MODEL padrão: rerank-2.5
{"$limit": limit},
{"$project": {"_id": 0, "produto_id": 1, "nome": 1, "categoria": 1, "preco": 1,
              "rrf_score": 1, "rerank_score": {"$meta": "score"}, "scoreDetails": "$rrf_details"}},
```

**Por que existe** ([ADR-003](../adr/0003-rerank-opcional-apos-rankfusion.md)): o RRF só vê posições; o reranker lê a descrição de cada candidato fundido. Eval no banco da demo (26 consultas, gabarito validado, 2026-11-12): recall@5 0,923 → 0,962, MRR 0,846–0,865 → 0,962, P@5 0,74 → 0,93; latência p50 664 → 913 ms. O `$addFields` vem antes porque o `$rerank` substitui `$meta: "score"`; sem ele a aba perderia o rank de cada busca. Se o cluster recusar o `$rerank`, a resposta volta ao `$rankFusion` puro com `rerank.applied = false` e `rerank.reason`. Se o Atlas estiver inacessível ou a aggregation estourar o tempo, o erro volta como está, sem fallback e sem culpar o `$rerank`. O texto enviado ao reranker é o mesmo texto mascarado da perna vetorial (`embed_query_text`, ADR-004); se a própria máscara falhar, nada vai à Voyage e a resposta volta com o erro `PII_MASK_UNAVAILABLE` e `rerank.applied = false`. Opt-in: uma chamada de rerank Voyage por consulta.

---

## 9. Híbrida nativa — $scoreFusion

Local: `backend/atlas.py` (`hybrid_score_fusion`), parsing em `_parse_score_fusion_details` (`backend/atlas.py`).

```python
[
    {"$scoreFusion": {
        "input": {
            "pipelines": {
                "textual": [
                    {"$search": {"index": search_index, "compound": {"should": [
                        {"autocomplete": {"query": query, "path": "nome", "fuzzy": {"maxEdits": 1}}},
                        {"text": {"query": query, "path": "descricao", "fuzzy": {"maxEdits": 1}}},
                    ]}}},
                    {"$limit": limit},
                ],
                "semantico": [
                    {"$vectorSearch": {"index": "produtos_vector", "path": "descricao", "query": query,
                                       "numCandidates": _num_candidates(limit), "limit": limit}},
                ],
            },
            "normalization": "minMaxScaler",
        },
        "combination": {"weights": {"textual": 1, "semantico": 1}, "method": "avg"},
        "scoreDetails": True,
    }},
    {"$limit": limit},
    {"$project": {"_id": 0, "produto_id": 1, "nome": 1, "categoria": 1, "preco": 1,
                  "score": {"$meta": "score"}, "scoreDetails": {"$meta": "scoreDetails"}}},
]
```

**Por que existe**: modo didático de Relative Score Fusion — em vez de fundir por rank (como `$rankFusion`), normaliza os scores brutos de cada sub-pipeline (0-1, `minMaxScaler`) e faz média ponderada. Fica exposto como terceiro modo na aba híbrida.

**Limitação observada e documentada** (código, `backend/atlas.py`, e ADR `docs/adr/0001-rankfusion-vs-scorefusion.md`): a normalização `minMaxScaler` pode colapsar o score combinado de **todos** os resultados para `0`, mesmo com `inputPipelineRawScore > 0` em algum sub-pipeline (confirmado inspecionando `scoreDetails` diretamente). O código detecta esse sintoma (`all(x["score"] == 0 for x in fused)`) e retorna `degraded_reason` explicando — a UI mostra um banner em vez de esconder o problema. Causa raiz **não era bug de servidor**: era `scripts/populate_marketplace.py` gerando descrições quase idênticas dentro da mesma subcategoria, empatando o score lexical em massa — corrigido adicionando `DESC_DIFERENCIAIS` como segundo eixo de variação textual.

---

## 10. Analytics — $facet

Local: `backend/atlas.py` (`get_analytics`).

```python
[
    {"$facet": {
        "por_categoria": [
            {"$group": {"_id": "$categoria", "total": {"$sum": 1},
                        "preco_medio": {"$avg": "$preco"},
                        "avaliacao_media": {"$avg": "$avaliacao_media"}}},
            {"$sort": {"total": -1}},
        ],
        "top_marcas": [
            {"$group": {"_id": "$marca", "total": {"$sum": 1}}},
            {"$sort": {"total": -1}}, {"$limit": 8},
        ],
        "faixa_preco": [
            {"$bucket": {"groupBy": "$preco",
                         "boundaries": [0, 100, 500, 1000, 3000, 5000, 10000, 999999],
                         "default": "outros", "output": {"total": {"$sum": 1}}}},
        ],
        "por_mes": [
            {"$match": {"created_at": {"$type": "date"}}},
            {"$group": {"_id": {"$dateToString": {"format": "%Y-%m", "date": "$created_at"}},
                        "total": {"$sum": 1}}},
            {"$sort": {"_id": 1}}, {"$limit": 12},
        ],
        "geral": [
            {"$group": {"_id": None, "total": {"$sum": 1}, "preco_medio": {"$avg": "$preco"},
                        "desconto_medio": {"$avg": "$desconto_pct"},
                        "em_estoque": {"$sum": {"$cond": ["$em_estoque", 1, 0]}}}},
        ],
    }},
]
# full=False (padrão) prefixa com {"$sample": {"size": 12000}} pra responder instantâneo
# full=True roda o MESMO pipeline sobre a coleção inteira — é essa comparação que é a demonstração
```

**Por que existe**: mostra cinco agregações rodando em paralelo, numa única passada no servidor (`$facet`), sem trazer dado bruto pra aplicação. `full=True` versus `full=False` é o argumento de escala: mesmo pipeline, 12 mil docs (instantâneo) vs. a coleção inteira (500K na demo, até 20M no gerador).

Nota de implementação: `$bucket` omite buckets vazios, então os rótulos de faixa de preço são mapeados pelo `_id` (limite inferior do bucket), não pela posição no array — um gap deslocaria os rótulos se fosse por posição.

---

## 11. RAG de avaliações

Local: `backend/atlas.py` (`get_product_and_reviews`), sumarização em `backend/reviews.py` (`summarize_reviews`).

**Passo 1 — achar o produto relevante que TEM avaliação**:

```python
[
    {"$search": {"index": "produtos_search", "compound": {"should": [
        {"autocomplete": {"query": query, "path": "nome", "fuzzy": {"maxEdits": 1}, "score": {"boost": {"value": 3}}}},
        {"text": {"query": query, "path": ["descricao", "marca"], "fuzzy": {"maxEdits": 1}}},
    ], "minimumShouldMatch": 1}}},
    {"$limit": 300},
    {"$project": {"_id": 0, "produto_id": 1, "score": {"$meta": "searchScore"}}},
]
```

Top 300 candidatos do Atlas Search são cruzados em memória com o conjunto de produtos que têm avaliação (só ~2% do catálogo) — o pool precisa ser generoso porque a interseção é pequena.

**Passo 2 — buscar as avaliações do produto escolhido**:

```python
[
    {"$match": {"produto_id": produto["produto_id"]}},
    {"$sort": {"util_count": -1}},
    {"$limit": n_reviews},
    {"$project": {"_id": 0, "nota": 1, "titulo": 1, "texto": 1, "util_count": 1, "verificado": 1, "usuario": 1}},
]
```

**Por que existe**: RAG real sobre dado real — as avaliações vêm de `avaliacoes` de verdade, não sintetizadas na hora, e o LLM (`claude-sonnet-5-5` via gateway Grove, `backend/reviews.py`) só sumariza o que foi de fato recuperado. Avaliações que casam a heurística de prompt injection (`guardrails.check_injection`) ficam fora do prompt e aparecem marcadas na UI (`suspeita_injection`, `injection_dropped`); o resto vai mascarado (PII) e cercado por `<avaliacoes>` como dado. `_get_reviewed()` (`backend/atlas.py`, cache TTL 600s) garante que a demo nunca cai num estado de "0 avaliações" — resolve a busca dentro do subconjunto avaliado desde o início, o que é honesto porque é exatamente o caso de uso real (recomendar baseado em quem já tem opinião formada).

Fallback: se o índice lexical estiver indisponível, cai num scan em memória sobre o catálogo avaliado (cacheado por 30s por query, pra não repetir o scan em rajadas concorrentes durante uma indisponibilidade).

---

## 12. Pipelines do agente de IA

Local: `backend/agent.py` — funções `_pipe_*`, únicas construtoras usadas tanto pelas tools quanto pela exibição do trace (`build_tool_pipeline`, `backend/agent.py`).

| Ferramenta | Coleção | Pipeline |
|---|---|---|
| `busca_semantica` | `produtos_vector` | ```[{"$vectorSearch": {"index": "produtos_vector", "path": "descricao", "query": consulta, "numCandidates": 150, "limit": 10}}, {"$project": {...}}]``` |
| `buscar_produto` | `produtos` | ```[{"$search": {"index": "produtos_search", "autocomplete": {"query": nome, "path": "nome", "fuzzy": {"maxEdits": 1}}}}, {"$limit": 10}, {"$project": {...}}]``` |
| `comparar_categoria` | `produtos` | ```[{"$match": {"categoria": categoria, "em_estoque": True}}, {"$sort": {"avaliacao_media": -1, "total_avaliacoes": -1}}, {"$limit": limite}, {"$project": {...}}]``` |
| `produtos_por_faixa_preco` | `produtos` | ```[{"$match": {"categoria": categoria, "em_estoque": True, "preco": {"$gte": preco_min, "$lte": preco_max}}}, {"$sort": {"avaliacao_media": -1}}, {"$limit": 10}, {"$project": {...}}]``` |

**Por que existe assim**: `build_tool_pipeline()` lê do mesmo dicionário (`PIPELINE_BUILDERS`, `backend/agent.py`) que as tools chamam de verdade — o trace mostrado na UI (aba "Agente") é byte a byte o que rodou, sem reconstrução aproximada.

Antes de rodar, cada tool checa se o índice necessário está `READY` (`_index_ready`, `backend/agent.py`) — mesma lógica de degradação graciosa das abas manuais, aplicada às tools do LLM, pra não deixar um erro cru do PyMongo entrar no contexto do modelo.

---

## 13. Índices B-tree simples

Local: `scripts/setup_search_indexes.py:221-224`.

```python
for _coll in ("produtos", "produtos_vector", "avaliacoes"):
    db[_coll].create_index("produto_id")
```

**Por que existe**: `produto_id` é o campo de lookup exato e de join entre `produtos`/`produtos_vector` e `avaliacoes` (ex.: `get_product_and_reviews` faz `$match` por `produto_id` em `avaliacoes` — sem índice, seria collection scan a cada consulta de reviews). Não é Atlas Search/Vector Search, é índice tradicional do MongoDB.

---

## 14. Introspecção de índices ($listSearchIndexes)

Local: `backend/atlas.py` (`get_search_indexes`) e funções derivadas.

```python
[{"$listSearchIndexes": {}}]
```

Cache com TTL de 60s por coleção (`_index_cache`, `backend/atlas.py`). Uma leitura negativa (índice ausente ou não consultável) e uma resposta vazia fazem `$listSearchIndexes` ser relido, no máximo a cada 5 s (`_INDEX_RECHECK_TTL`), sem esperar o TTL de 60 s; assim um índice que acabou de ficar `READY` não fica bloqueado e um índice removido depois do cache é detectado. Se a listagem falhar, `_index_listing()` devolve `None` ("estado desconhecido"), que não é lido como "sem índice".

**Gate de prontidão em toda consulta de busca** (`check_search_indexes`, chamado por `safe_aggregate`, `backend/atlas.py`): `search_indexes_used()` extrai os nomes dos índices de `$search`, `$searchMeta` e `$vectorSearch`, inclusive nos sub-pipelines de `$rankFusion`/`$scoreFusion`, e `index_unavailable_reason()` devolve o motivo legível quando algum está ausente ou com `queryable: false`. Medido no Atlas 9.0.7 (2026-11-12): índice inexistente devolve `[]` em `$search` e `$vectorSearch` e `{count: {total: 0}}` em `$searchMeta`, **sem erro**; por isso a checagem não pode depender de exceção do driver.

Funções construídas em cima dela:
- `get_index_status()` (`backend/atlas.py`) — status real (`READY`/`BUILDING`/...) de cada índice, usado no `GET /stats`.
- `_field_types(index_doc, path)` (`backend/atlas.py`) — quais tipos um campo tem na definição viva do índice.
- `search_filter_caps()` (`backend/atlas.py`) — que filtros podem rodar dentro do `$search` (usado na seção 2).
- `vector_collection_search_index()` (`backend/atlas.py`) — nome do índice lexical **queryable** em `produtos_vector`, ou `None` — é o gatilho que decide se `$rankFusion`/`$scoreFusion` nativos são possíveis.

**Por que existe**: é a base técnica de todo o padrão de "degradação graciosa" do projeto — o backend nunca assume estado de índice, sempre lê o estado real antes de decidir o caminho de execução.
