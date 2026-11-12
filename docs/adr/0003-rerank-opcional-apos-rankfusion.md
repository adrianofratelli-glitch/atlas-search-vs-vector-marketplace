# ADR-003: `$rerank` opcional depois do `$rankFusion` (cascata híbrido → rerank)

**Status:** Aceito
**Data:** 2026-11-12
**Relacionado:** [ADR-001](0001-rankfusion-vs-scorefusion.md), [ADR-002](0002-rankfusion-desempate-lexical.md)
**Contexto do cliente/conta:** POC interna — atlas-search-vs-vector-marketplace

## Contexto
Depois do ADR-002, o híbrido nativo (`$rankFusion`) empata ou supera cada modo isolado em recall@5, mas ainda tem dois limites medidos no gabarito validado (`eval/golden.json`, 26 consultas):

1. **MRR instável.** O 1º lexical e o 1º vetorial valem os dois `1/61`, e o servidor decide esse empate da fusão sozinho. Em n02 ("Gel-Kayano 30"), d02 ("ultrabost"), i03 e i04 o primeiro relevante cai na posição 1 ou 2 conforme a execução e o `limit`: o MRR do eval foi 0,846 (duas rodadas, `limit=10`), 0,865 num script com `limit=20`, e o README anterior registrava 0,87.
2. **Fusão só por posição.** O RRF não lê o texto. Quando as duas listas trazem o mesmo produto errado no topo (m01 "tênis Nike para correr maratona" → "Nike Air Max 270" nas duas), a fusão não tem como corrigir.

O cluster da demo (Atlas 9.0.7) aceita o estágio `$rerank` (reranker Voyage executado pelo próprio Atlas), recomendado pela documentação depois de `$search`, `$vectorSearch`, `$rankFusion` ou `$scoreFusion`.

## Decisão
`hybrid_native(query, limit, rerank=True)` (`backend/atlas.py`) acrescenta, depois do `$rankFusion` e antes do `$limit`:

```python
{"$addFields": {"rrf_score": {"$meta": "score"}, "rrf_details": {"$meta": "scoreDetails"}}},
{"$rerank": {"query": {"text": query}, "path": "descricao",
             "numDocsToRerank": 2 * limit, "model": RERANK_MODEL}},   # padrão rerank-2.5
```

- O `$addFields` guarda o score RRF e o `scoreDetails` antes do `$rerank`, porque o `$rerank` substitui `$meta: "score"`. Assim a aba continua mostrando o rank de cada busca e a contribuição RRF, e ganha a coluna Rerank.
- `numDocsToRerank = 2 * limit` cobre a união das duas listas candidatas (cada perna devolve até `limit`).
- **Opcional e desligado por padrão** (`POST /hybrid-native` com `rerank: true`; checkbox na aba Híbrida). Cada consulta faz uma chamada de rerank à Voyage e soma cerca de 250 ms no p50 medido. O híbrido padrão, e o piso do eval semanal sobre ele, não mudam.
- **Degradação:** se o cluster recusar o `$rerank` (versão sem o estágio, reranker limitado), a resposta é o `$rankFusion` puro com `rerank.applied = false` e o motivo, mostrado em banner. Se o problema for índice não pronto, vale o gate de `safe_aggregate` e o fallback normal do híbrido.
- O `pipeline` devolvido é o objeto executado, com o `$rerank` visível no bloco MQL.

## Resultado (eval, banco da demo, 500K, 2026-11-12)
`eval/run_quality.py`, gabarito validado, 26 consultas, duas rodadas seguidas com o mesmo resultado de qualidade:

| Modo | recall@5 | MRR | P@5 | latência p50 (rodada 1 / 2) |
|---|---|---|---|---|
| lexical (`$search`) | 0,462 | 0,462 | 0,462 | 472 / 482 ms |
| vetorial (`$vectorSearch`) | 0,846 | 0,853 | 0,846 | 2302 / 2258 ms |
| híbrido nativo (`$rankFusion`, padrão) | 0,923 | 0,846 | 0,739 | 664 / 610 ms |
| híbrido + `$rerank` (opt-in) | **0,962** | **0,962** | **0,931** | 913 / 848 ms |

O híbrido padrão ficou idêntico ao de antes desta mudança (0,923 / 0,846 na rodada de base, sem rerank), ou seja, sem regressão. Com o rerank, n02, d02, i03 e i04 passam a ter o relevante na posição 1, e m01 sai de "nenhum relevante no top 10" para a posição 1 (Nike Air Zoom Pegasus 41). Por tipo, recall@5 do misto vai de 0,50 a 1,00; nome, digitação e intenção não mudam. i05 ("proteína para ganhar massa muscular") continua sem acerto em todos os modos: nenhum whey ou hipercalórico chega aos candidatos fundidos, e o reranker só reordena o que recebe.

Medição de apoio antes de implementar (script ad hoc, `limit=20`, `numDocsToRerank=20`): 0,923 / 0,865 → 0,962 / 0,962, o que confirma que o ganho não depende do `limit` usado no eval.

## Alternativas consideradas
| Alternativa | Por que não |
|---|---|
| Desempate determinístico final (`$sort` por score e `produto_id` depois do `$rankFusion`) | Torna o MRR reprodutível, mas escolhe o vencedor do empate `1/61` por UUID, sem relação com relevância; não corrige m01 |
| Peso assimétrico (`textual` 1,0 × `semantico` 0,99) | Não medido; pelos ranks do eval, resolveria o empate a favor do lexical, ganhando n02 e d02 e perdendo i03 e i04, em que o 1º lexical é o produto errado |
| `$rerank` sempre ligado | Muda o custo e a latência de toda busca da demo e o número que o eval semanal protege; fica opt-in até haver piso próprio |
| Rerank na aplicação (SDK Voyage) | Sai do banco e exige LLM/SDK fora do gateway; a tese da PoV é a busca inteira numa aggregation |

## Consequências
- Positivas: a aba Híbrida mostra a cascata recomendada (fusão → rerank) com o efeito medido; o eval passa a reportar `hybrid_rerank` e a latência p50 por modo.
- Negativas: uma chamada de rerank por consulta (custo Voyage) e cerca de 250 ms a mais no p50; depende de o cluster aceitar `$rerank` (sem ele a resposta volta ao `$rankFusion` puro e avisa).
- Pendência fora deste repo: piso opcional `hybrid_rerank_recall_at_5 >= hybrid_recall_at_5` em `_shared/workspace/agendado/eval_pisos.json`.
