# ADR-001: $rankFusion (RRF) como modo padrão da busca híbrida; $scoreFusion (RSF) exposto como modo didático

**Status:** Aceito — atualizado com causa raiz corrigida (2026-09-08)
**Data:** 2026-09-07 (revisão 2026-09-08)
**Contexto do cliente/conta:** POC interna — search-e-vector-marketplace

## Contexto
A aba Híbrida da POC precisava demonstrar as duas técnicas nativas de fusão de busca do MongoDB Atlas: `$rankFusion` (Reciprocal Rank Fusion) e `$scoreFusion` (Relative Score Fusion), já que documentação e materiais de venda do MongoDB apresentam RRF e RSF como as duas abordagens de hybrid search nativo. O cluster usado está na versão mais recente do Atlas, com suporte a ambos os stages. A aplicação já tinha `$rankFusion` implementado (`backend/atlas.py::hybrid_native`) com fallback gracioso para RRF calculado na aplicação quando o stage ou o índice necessário não estão disponíveis — esse é o padrão central do projeto (nunca presumir que um índice/stage existe; introspectar e sinalizar).

## Decisão
Implementar `hybrid_score_fusion()` (`backend/atlas.py:888`) espelhando o mesmo padrão de degradação graciosa de `hybrid_native()`, e manter `$rankFusion` como a opção recomendada por padrão na UI. `$scoreFusion` fica disponível como terceiro modo ("$scoreFusion (RSF)") para fins didáticos.

## ⚠️ Correção de causa raiz (2026-09-08)
A versão original deste ADR (2026-09-07) atribuiu o colapso de score do `$scoreFusion` a uma **limitação do cluster/Atlas**. Investigação posterior (sessão de debug estruturado, ver `docs/screenshots/hybrid-rankfusion-residual.png` e `docs/screenshots/hybrid-scorefusion.png`) mostrou que essa conclusão estava **errada**: o colapso era causado pelo **dado sintético**, não pelo Atlas.

`populate_marketplace.py::get_descricao()` gerava a `descricao` de cada produto a partir de um único template por subcategoria, só substituindo `{marca}`/`{modelo}` — todo o resto do texto (~200 palavras) era **idêntico** entre dezenas de milhares de produtos da mesma subcategoria. Isso empatava o score lexical do Atlas Search em massa (mesmo corpo de texto → mesmo BM25). Dois sintomas seguiam diretamente disso:

1. **`$rankFusion`**: quando todos os documentos do sub-pipeline `textual` têm o mesmo score bruto, o servidor atribui `rank: 1` a todos eles simultaneamente (ranking com empate, não um bug de posição) — confirmado inspecionando `scoreDetails.details[].rank` cru direto no cluster.
2. **`$scoreFusion` com `normalization: minMaxScaler`**: quando todos os `inputPipelineRawScore` de um sub-pipeline empatam, `min == max` no conjunto, e `(x - min) / (max - min)` é `0/0 → 0` para **todo** documento daquele grupo — propriedade matemática do algoritmo, não degeneração do servidor.

### Fix aplicado
- `get_descricao()` agora recebe atributos por unidade (`cor`, `tamanho`, `gênero`, `avaliação`, `preço`) e monta uma cláusula específica por documento.
- Novo `DESC_DIFERENCIAIS`: segundo eixo de variação — uma frase extra escolhida aleatoriamente por documento, com vocabulário de domínio para "Tênis Esportivos" (a subcategoria mais testada) e um pool genérico para as demais 30 subcategorias.
- `produtos_vector` (500k docs) repopulada com o gerador corrigido; índices `produtos_vector_search`/`produtos_vector` recriados (o `drop()` da coleção derruba os índices Atlas Search/Vector vinculados ao namespace — comportamento esperado, não bug).

### Resultado observado, direto do cluster (query "tenis de corrida")
| | Antes | Depois |
|---|---|---|
| `$scoreFusion` — docs com `score = 0` no top 10 | 10/10 (20/20 em lote maior) | 1/10 (caso matematicamente correto: único doc do grupo sem contraparte no outro sub-pipeline) |
| `$scoreFusion` — score combinado | sempre `0.0` | `0.500, 0.459, 0.415, 0.404, 0.246, 0.152, 0.129, 0.128, 0.043, 0.0` |
| `$rankFusion` — `rank_vector` | sempre `NA` (embeddings idênticos, nenhum doc emergia) | rank real aparece (embedding único por doc via voyage-4) |
| `$rankFusion` — `rank_search` | sempre `1` para toda a subcategoria (~15-20k docs) | ainda `1` para o subconjunto que compartilha o mesmo combo de template+diferencial (~1/12 da subcategoria) — ver limitação abaixo |

### Limitação residual (esperada, não é bug)
Com templates finitos, sempre existirá **algum** grupo de documentos empatado no topo para uma query fixa — é propriedade do BM25 sobre vocabulário finito, não uma falha de implementação. O fix reduziu o tamanho do grupo empatado em ~12x (2 templates × 6 diferenciais), não a zero. Zerar completamente exigiria texto 100% único por SKU, o que nenhum gerador sintético de catálogo faz de forma econômica (nem a maioria dos catálogos reais). `docs/screenshots/hybrid-rankfusion-residual.png` documenta esse estado honestamente — a UI continua mostrando `rank_search` empatado quando a query cai nesse cenário, em vez de esconder o comportamento.

## Alternativas consideradas
| Opção | Prós | Contras | Por que rejeitada |
|---|---|---|---|
| Só `$rankFusion` (não implementar RSF) | Simples, já funcionava, sem bug conhecido | Não cobre o material de venda que apresenta RRF+RSF como o par de técnicas nativas; perde valor didático | Rejeitada — usuário pediu explicitamente para cobrir os dois, e é a lacuna que motivou este ADR |
| `$scoreFusion` sem detecção de degradação (mostrar score bruto) | Menos código | Mostrava score `0.0` para tudo sem explicação — parecia bug da aplicação; quebrava o padrão de transparência do projeto | Rejeitada — viola o invariante central do projeto (badges honestos sobre o que rodou) |
| Corrigir a normalização manualmente com `$addFields` reexpondo o score do `$vectorSearch` | Contornaria o problema diretamente no pipeline | `$scoreFusion` **proíbe** `$addFields` dentro dos sub-pipelines (`Location12108713`: "input pipelines must not contain $addFields") — testado e confirmado | Rejeitada — tecnicamente inviável com os stages permitidos (`$search`/`$vectorSearch`/`$limit`/`$sort` apenas) |
| Corrigir o dado sintético (texto único por documento) | Ataca a causa raiz real | Trabalho de conteúdo, não de infraestrutura | **Aceita** — era a causa raiz correta, ver seção de correção acima |

## Evidência
Reproduzido diretamente via agregação (fora da aplicação, consultando o cluster real, antes e depois do fix):
- Antes: `scoreDetails` mostrava, para o mesmo documento, `semantico.inputPipelineRawScore: 0.7409...` (score de cosseno real, não-zero) mas `semantico.value: 0` (normalizado) — e `score` final do documento também `0.0`, em 20/20 documentos de um lote.
- Depois: mesmo tipo de consulta, `scoreDetails.details[].value` varia por documento (`1, 0.918, 0.830, ...`), porque `inputPipelineRawScore` do lado semântico agora é distinto por documento (embeddings únicos).
- `$rankFusion`, mesma query, antes vs depois: `rank_vector` saiu de `"NA"` universal para ranks reais.

## Consequências
- Positivas: a aba híbrida cobre as duas técnicas nativas (RRF e RSF) citadas nos materiais de venda do MongoDB, mantendo o invariante de transparência do projeto (MQL exibido, badge de fallback/limitação sempre visível). O bug real (dado sintético colapsado) está corrigido, não mascarado.
- Negativas / trade-offs aceitos: para queries que caem no grupo residual de empate (ver limitação acima), o `$rankFusion` ainda mostra ranks empatados — é o comportamento correto do servidor sobre esse dado, exibido honestamente em vez de escondido.
- Reversibilidade: alta. O fix em `populate_marketplace.py` é aditivo (não quebra o schema); `hybrid_score_fusion()` continua aditiva e não altera `hybrid_native()`.

## Riscos e mitigação
- **Risco:** a heurística de detecção de degradação (`fused and all(x["score"] == 0)`) ainda existe no código para o caso legítimo de um grupo de documentos inteiramente tied — mantida por segurança, mas hoje dispara com muito menos frequência.
- **Mitigação:** se o residual de empate lexical incomodar em demos futuras, o próximo passo é aumentar `DESC_DIFERENCIAIS`/variantes de `DESC_TEMPLATES` para mais subcategorias (hoje só "Tênis Esportivos" tem pool de vocabulário específico de domínio; as outras 30 usam o pool genérico).
