# ADR-004: máscara de PII em todo texto enviado à Voyage (autoEmbed e `$rerank`)

**Status:** Aceito
**Data:** 2026-11-13
**Relacionado:** [ADR-003](0003-rerank-opcional-apos-rankfusion.md)
**Contexto do cliente/conta:** POC interna — atlas-search-vs-vector-marketplace

## Contexto
O `$vectorSearch` com autoEmbed recebe `query` em texto, e o Atlas chama a Voyage AI para gerar o embedding da consulta. A Voyage é um terceiro: o que o usuário digita na busca sai do cluster. Desde o ADR-003, o `$rerank` mascarava o texto com `mask_pii` antes de enviá-lo, mas a perna vetorial de todas as abas (`/compare`, `/hybrid`, `/hybrid-native`, `/hybrid-score-fusion`, `/similar` e a ferramenta `busca_semantica` do agente) mandava o texto cru. Medido ao vivo em `marketplace_test`, a consulta `fone para viagem, meu email joao.silva@exemplo.com cpf 123.456.789-09` chegava inteira ao `$vectorSearch` de `/hybrid-native`, enquanto o `$rerank` do mesmo pipeline já levava `<EMAIL>` e `<CPF>`.

## Decisão
- `embed_query_text(text)` (`backend/atlas.py`) aplica a mesma `llm_gateway.mask_pii` do caminho do LLM. Com a `pov-shared` instalada (`guardrails.mask_pii`), ela cobre CPF e CNPJ com dígito verificador, telefone, e-mail, cartão com Luhn e chave PIX aleatória. Sem a `pov-shared`, por exemplo num clone público, vale o fallback local de `llm_gateway.py`, que cobre só e-mail e CPF: telefone, cartão, CNPJ e PIX passam sem máscara. Todo texto enviado à Voyage passa por ela: a `query` de cada `$vectorSearch` (inclusive a descrição do produto-base em `/similar`, que é dado do catálogo mas também é embedada) e o texto do `$rerank`, que agora reaproveita o mesmo texto mascarado.
- O pipeline carrega o texto mascarado. Assim o MQL exibido continua sendo o objeto executado e mostra exatamente o que a Voyage recebeu. A resposta traz `embed_query_masked: true` quando a máscara alterou o texto, e as abas Search × Vector e Híbrida mostram um badge.
- O `$search` lexical continua com o texto original: ele roda no próprio cluster e não sai para terceiros. Mascarar ali trocaria números da consulta por rótulos sem proteger nada.
- **Fail-closed.** Se a própria máscara falhar (`MASK_FAILED`), nada vai à Voyage. Em `/compare`, a perna lexical roda e a vetorial volta com o erro `PII_MASK_UNAVAILABLE`, mostrado no banner de degradação. O híbrido (nativo, RSF ou RRF da aplicação) e `/similar` devolvem esse erro sem executar, porque um híbrido sem a perna vetorial não seria o que a aba diz mostrar. A ferramenta `busca_semantica` responde "Erro: …" ao modelo sem consultar o banco. Antes, a falha da máscara só pulava o `$rerank`.

## Resultado (eval, banco da demo, 500K, só leitura, 2026-11-13)
Gabarito validado (`eval/golden.json`, 26 consultas), mesmo banco e mesma sequência de modos:

| Modo | recall@5 antes | recall@5 depois | MRR antes | MRR depois |
|---|---|---|---|---|
| lexical | 0,462 | 0,462 | 0,462 | 0,462 |
| vetorial | 0,846 | 0,846 | 0,853 | 0,853 |
| híbrido (`$rankFusion`) | 0,923 | 0,923 | 0,846 | 0,846 |
| híbrido + `$rerank` | 0,962 | 0,962 | 0,962 | 0,962 |

"Antes" é o código da `main` sem a mudança. "Depois" é esta branch, rodada duas vezes: com a máscara e com `--sem-mascara`, chave só do eval que troca a máscara por identidade no processo para o A/B. As três rodadas deram métricas iguais e o mesmo top 5 nas 26 consultas, em todos os modos. Nenhuma consulta do gabarito validado tem PII (`queries_masked = 0`). O teste `backend/tests/test_embed_pii_mask.py` fixa que a máscara devolve o texto byte a byte igual para as 26 consultas validadas e as 24 do rascunho sem PII, inclusive as com número e unidade (`tv 55 polegadas`, `geladeira 431 litros`, `whey 2kg`). Logo, ela não tem como mover o recall nesses casos.

No gabarito rascunho (`eval/golden_rascunho.json`, sem rótulo humano), as duas consultas com e-mail ou CPF sintéticos (tipo `pii`) mantiveram o relevante na posição 1 em todos os modos, com e sem máscara. O P@5 do híbrido oscilou entre as rodadas (0,761 com máscara, 0,754 sem) em s03 e a01, consultas sem PII cujo texto enviado é idêntico nas duas rodadas. É o empate `1/61` que o servidor decide sozinho na fusão (ADR-003), não a máscara.

Latência: a máscara é regex local em microssegundos. O p50 do vetorial varia mais entre rodadas (456 a 1632 ms) do que qualquer efeito dela.

## Alternativas consideradas
| Alternativa | Por que não |
|---|---|
| Mascarar também o `$search` lexical | Não sai do cluster. Trocaria números (telefone casado por engano, por exemplo) por rótulos e perderia recall sem ganho de privacidade |
| Na falha da máscara, rodar o híbrido só com a perna lexical | Mostraria como "híbrido" uma resposta só lexical. A falha é rara (exceção dentro do `guardrails`), e o banner com a causa é mais honesto |
| Bloquear a busca quando houver PII | A busca com o texto mascarado continua útil (p01 e p02 do rascunho acertam na posição 1); bloquear pioraria a demo sem proteger mais |
| Gerar o embedding no app (`queryVector`) para controlar o envio | Troca o autoEmbed, que é a tese da PoV, por um cliente Voyage na aplicação; a máscara resolve o mesmo risco sem isso |
