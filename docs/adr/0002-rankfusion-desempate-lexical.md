# ADR-002: desempate do sub-pipeline lexical no `$rankFusion` (`$search.sort` por score e `produto_id`)

**Status:** Aceito
**Data:** 2026-11-12
**Substitui:** a seção "Limitação residual" do [ADR-001](0001-rankfusion-vs-scorefusion.md) (o resto do ADR-001 continua valendo)
**Contexto do cliente/conta:** POC interna — atlas-search-vs-vector-marketplace

## Contexto
O eval de qualidade (`eval/run_quality.py`, gabarito rascunho `eval/golden.json`, 26 consultas) mostrou que o híbrido nativo perdia para a busca vetorial pura: recall@5 0,769 contra 0,885, MRR 0,700 contra 0,891. As perdas de recall eram todas em consultas de intenção (i03, i04, i07, i11, i12); em i06 o primeiro relevante caía na posição 5. O RRF calculado na aplicação, com as mesmas duas pernas, chegava a 0,962. O ADR-001 tratava o empate de `rank_search` como "limitação residual esperada" do dado sintético.

## Causa raiz
Medido direto no cluster (banco da demo, só leitura):

1. **O score lexical empata de verdade, e em grupos grandes.** O catálogo tem dezenas de unidades por modelo (uma por cor), com o mesmo nome-base e a mesma família de descrição. Para "relógio para acompanhar treino de corrida e batimentos", os 10 primeiros do `$search` têm todos `searchScore` 9,9035 (são "Lupo Short de Corrida" em cores diferentes). Para "tenis nike corrida leve", os 300 primeiros formam três grupos: 102 documentos com 9,6209, 98 com 9,6119 e 100 com 9,4846. Até "Galaxy S24 Ultra" devolve 10 empatados em 17,7141. O score não é constante nem descartado por algum estágio; o empate é real.
2. **`$rankFusion` dá o mesmo rank a scores empatados** (ranking de competição: 1, 1, 1, ..., 103). Com `limit=300`, `scoreDetails.details[].rank` do pipeline `textual` saiu 1 para os 102 primeiros e 103 para o grupo seguinte. Sem desempate, o sub-pipeline não é posicional.
3. **Consequência no RRF.** Todo o grupo empatado vale `1/(60+1)`, o mesmo que o 1º vetorial, e mais que o 2º vetorial em diante. O `$limit` final pega N documentos de um grupo maior que N, e os vetoriais saem do topo: com `limit=10` o top 10 era 100% lexical, mesmo em consulta de intenção em que o lexical só acha produto errado. O RRF da aplicação não tinha o problema porque numera pela posição (`enumerate`).

Não era dado sintético a corrigir nem peso a ajustar: o sub-pipeline lexical não tinha ordem total.

## Decisão
O `$search` do sub-pipeline `textual` de `hybrid_native()` passa a ter `sort: {score: {$meta: "searchScore", order: -1}, produto_id: 1}` (`RANK_FUSION_LEXICAL_SORT`, `backend/atlas.py`). A relevância continua sendo o critério principal; `produto_id` (UUID único, mapeado como `token` em `produtos_vector_search`, portanto ordenável no mongot) só desempata. O rank volta a ser posicional (1..N), igual ao RRF da aplicação. A tese da PoV fica de pé: lexical e vetorial no mesmo aggregation, fusão no servidor, sem pipeline paralelo.

Degradação graciosa: `rank_fusion_tiebreak_supported()` lê a definição do índice via `$listSearchIndexes`. Se `produto_id` não estiver como `token`, o pipeline roda sem `sort` (o `$search` falharia com "not indexed as sortable") e a resposta traz `degraded_reason`, que a aba Híbrida mostra em banner. O `pipeline` devolvido continua sendo o objeto executado.

## Resultado (eval, banco da demo, 500K, 2026-11-12, gabarito rascunho)

> Números desta tabela são do **gabarito rascunho**. Depois da validação do gabarito (i04 só câmeras profissionais, i05 só whey e hipercalórico), o mesmo código mede recall@5: híbrido 0,923 / vetorial 0,846 / lexical 0,462; MRR do híbrido 0,846–0,87. O critério do ADR (híbrido ≥ vetorial e ≥ lexical) continua valendo. Ver [ADR-003](0003-rerank-opcional-apos-rankfusion.md) para os números atuais.

| Modo | recall@5 antes | recall@5 depois | MRR antes | MRR depois |
|---|---|---|---|---|
| lexical | 0,500 | 0,500 | 0,500 | 0,500 |
| vetorial | 0,885 | 0,885 | 0,891 | 0,891 |
| híbrido nativo (`$rankFusion`) | 0,769 | **0,962** | 0,700 | **0,885 / 0,904** |
| RRF na aplicação | 0,962 | 0,962 | 0,731 | 0,731 |

Por tipo, recall@5 do híbrido nativo: nome 1,00 → 1,00; digitação 1,00 → 1,00; intenção 0,58 → 1,00; misto 0,50 → 0,50. Nenhuma regressão em nome exato ou digitação. As duas rodadas depois da correção deram o mesmo recall; o MRR oscilou entre 0,885 e 0,904 porque o 1º lexical e o 1º vetorial empatam em `1/61` e o servidor resolve esse empate da fusão por conta própria (i03 e i04 ficam com o primeiro relevante na posição 1 ou 2).

## Alternativas consideradas
| Opção | Resultado medido | Por que rejeitada |
|---|---|---|
| `$sort` depois do `$search` dentro do sub-pipeline (`{score: {$meta: "searchScore"}, produto_id: 1}`) | erro do servidor: "query requires $search score metadata, but it is not available" | inviável dentro do `$rankFusion` |
| `$search.sort` com `_id` como desempate | erro: "_id is not indexed as sortable" | exigiria mudar o índice; `produto_id` já é `token` |
| Peso maior no vetorial (`semantico: 1.2`), sem desempate | recall@5 0,885, MRR 0,891: o híbrido virou o ranking vetorial e perdeu n02 e d02 (nome exato e digitação) | ajuste de peso esconde o empate e troca uma regressão por outra |
| Limitar o lexical em consultas de intenção | não medido | exigiria classificar a consulta por heurística; sem regra objetiva |
| Mais variação no texto sintético (proposta do ADR-001) | não medido | o empate vem de unidades do mesmo modelo, o que catálogo real também tem; atacaria o sintoma |

## Consequências
- Positivas: híbrido nativo ≥ vetorial e ≥ lexical em recall@5; MRR sobe de 0,70 para 0,885 ou mais, praticamente igual ao vetorial (0,891) e acima do RRF da aplicação (0,731). A aba Híbrida mostra `rank_search` 1, 2, 3... intercalado com `rank_vector`.
- Negativas: o `sort` no `$search` custa um pouco de latência no mongot (ordenação por dois campos); não medido em separado. O desempate por `produto_id` é arbitrário entre documentos de mesma relevância, mas determinístico.
- `$scoreFusion` não muda: ele combina scores normalizados, não ranks, e o empate não altera a fusão.
- Reversibilidade: alta. Remover `RANK_FUSION_LEXICAL_SORT` do pipeline volta ao comportamento anterior.

## Evidência e travas
- Teste offline `backend/tests/test_rank_fusion_ties.py`: o sub-pipeline lexical vem ordenado por score e depois `produto_id`; pesos 1:1; trace igual ao pipeline executado; índice sem `token` degrada com `degraded_reason`; erro do servidor cai no RRF da aplicação.
- Piso no eval semanal (`search.quality_retrieval` em `_shared/workspace/agendado/eval_pisos.json`): `hybrid_minus_vector_recall_at_5 >= 0`, `hybrid_recall_at_5 >= 0,92`, `hybrid_mrr >= 0,84`.
