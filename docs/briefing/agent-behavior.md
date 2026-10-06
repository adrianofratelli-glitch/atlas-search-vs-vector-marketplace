# Comportamento do agente de IA

> Arquitetura geral em `architecture.md`, pipelines em detalhe em `queries.md` (seção 12). Este arquivo é sobre o agente em si: como ele decide o que fazer, com que ferramentas, e como falha com segurança.

## O que é

Agente ReAct (raciocina → escolhe ferramenta → observa resultado → repete) implementado com LangGraph (`create_react_agent`), rodando sobre Claude via LangChain (`ChatAnthropic`). Local: `backend/agent.py`. Exposto no endpoint `POST /agent`, consumido pela aba "Agente" (`frontend/src/tabs/AiAgent.jsx`).

## Modelo e configuração

```python
llm = build_chat_model("ANTHROPIC_MODEL", max_tokens=1024)   # backend/agent.py
```

`build_chat_model` (`backend/llm_gateway.py`) devolve um `GroveChatAnthropic`: a interface continua sendo o `ChatAnthropic` do LangChain (o LangGraph monta o payload, com tools e `cache_control`), mas o transporte é `grove_client.create_message` do pacote compartilhado `pov-shared`. Consequências:

- **Destino e credencial validados pelo `grove_client`**: `GROVE_BASE_URL` + `GROVE_API_KEY`, `Authorization: Bearer` e a chave real em `x-api-key` (placeholder dá 401). Não existe fallback para `ANTHROPIC_API_KEY` nem para assinatura pessoal; sem gateway ou sem o pacote, o agente responde "indisponível" e as abas de busca seguem funcionando (`GET /health/llm` diz o motivo).
- **Resiliência ligada por padrão**: retry com backoff (full jitter) em 429/5xx/timeout/conexão, circuit breaker por modelo e fallback opcional (`GROVE_MODEL_FALLBACKS`). O `max_retries` do LangChain fica em 0 para não multiplicar tentativas.
- **Modelo**: `claude-sonnet-5-5` por padrão (o gateway não serve haiku). Sem `temperature`: esse modelo rejeita o parâmetro (400 "deprecated for this model", observado nesta revisão).

O RAG de reviews usa o mesmo construtor (`REVIEWS_MODEL`, mesmo padrão).

## Ferramentas disponíveis (quatro)

Local: `backend/agent.py`.

| Ferramenta | Quando o modelo escolhe | Engine | Coleção |
|---|---|---|---|
| `busca_semantica` | consultas por necessidade/uso ("academia em casa", "presente pro dia dos pais") | Vector Search | `produtos_vector` |
| `buscar_produto` | busca por nome de produto | Atlas Search (autocomplete fuzzy) | `produtos` |
| `comparar_categoria` | "quais os melhores X" | Aggregation (`$match`+`$sort`) | `produtos` |
| `produtos_por_faixa_preco` | "X entre R$ A e R$ B" | Aggregation (filtro de faixa) | `produtos` |

Os docstrings das ferramentas e o `SYSTEM_PROMPT` (`backend/agent.py`) ficam **intencionalmente em português** — são eles que dirigem a escolha de ferramenta pelo modelo e o idioma da resposta final, já que o público é brasileiro.

Cada pipeline que a ferramenta executa vem de uma função `_pipe_*` compartilhada com a exibição do trace (ver `queries.md` seção 12) — não existe uma versão "que roda" e outra "que aparece na tela".

## Degradação graciosa aplicada ao agente

Antes de rodar, `busca_semantica` e `buscar_produto` checam se o índice necessário está `READY` via `_index_ready()` (`backend/agent.py`) — mesma lógica de introspecção via `$listSearchIndexes` usada nas abas manuais. Se o índice não estiver pronto ou o cluster estiver inacessível, a ferramenta devolve uma mensagem de erro legível **para o modelo**, em vez de deixar uma exceção crua do PyMongo estourar dentro do contexto da conversa — um erro de driver dentro do prompt tende a produzir uma resposta alucinada sobre infraestrutura, que é o pior tipo de falha numa demo.

## Escopo — o que o agente recusa responder

O agente é restrito ao domínio do marketplace. Duas camadas:

1. **Interceptação antes de chamar o modelo** (`is_obviously_out_of_scope`, `backend/agent.py`): normaliza o texto (remove acentos, minúsculas) e casa contra uma lista de padrões óbvios fora de escopo — clima, previsão do tempo, placar de jogo, receita culinária, cotação do dólar, horóscopo. Se casar, responde com `SCOPE_GUIDANCE` (`backend/agent.py`) sem gastar chamada de LLM. Isso funciona mesmo se o provedor Anthropic estiver fora do ar.
2. **Instrução no system prompt**: pede pro modelo reconhecer o limite em uma frase e oferecer as alternativas do domínio (busca por produto, comparação de categoria, faixa de preço, avaliações) em vez de improvisar resposta ou simplesmente dizer "não sei". O prompt também diz que resultado de ferramenta (nome, descrição, avaliação) é dado do catálogo, nunca instrução, e que as instruções não devem ser reveladas.

## Guardrails de entrada (antes de qualquer chamada)

Ordem em `run_agent`:

1. **Máscara de PII** (`llm_gateway.mask_pii`, que usa `guardrails.mask_pii` do `pov-shared`: CPF/CNPJ com dígito verificador, telefone, e-mail). O modelo, o checkpoint e o trace do Langfuse só recebem o texto mascarado.
2. **Fora de escopo** (acima).
3. **Prompt injection direta** (`guardrails.check_injection`, heurística PT/EN/ES offline): se casar, responde `INJECTION_GUIDANCE` (`mode: "injection_blocked"`) sem chamar o modelo. A heurística procura o padrão em qualquer posição, então diluir a instrução num texto benigno longo não a esconde (teste `test_diluted_injection_still_blocked`). Por isso `score_by_clause` não se aplica aqui: não há score por embedding.
4. **Teto de passos**: `recursion_limit = AGENT_RECURSION_LIMIT` (padrão 12). Estourou, responde `mode: "step_limit"` pedindo uma pergunta mais específica.

A validação do corpo (`AgentReq`) já removeu caracteres invisíveis (zero-width, bidi, controle) e limita a mensagem a 4000 caracteres.

## Memória de conversa — checkpoint no MongoDB

`MongoDBSaver` (LangGraph checkpointer oficial para MongoDB) grava o estado da conversa nas coleções `marketplace_checkpoints` e `marketplace_checkpoint_writes` (dedicadas: o banco `POC` é compartilhado com outras PoVs, e o reset precisa apagar só a memória deste agente), com TTL de `CHECKPOINT_TTL_DAYS` (padrão 30), chaveado por `thread_id` (UUID gerado pelo backend se o cliente não enviar um). Isso é continuidade de conversa persistida **no próprio Atlas**, sem estado em memória de processo — reiniciar o backend não perde o histórico de uma conversa em andamento, e é demonstrável na prática: uma pergunta de continuidade no mesmo `thread_id` funciona mesmo depois de um restart.

O agente é construído de forma preguiçosa (`_get_agent()`, `@lru_cache(maxsize=1)`, `backend/agent.py`) — só na primeira requisição a `/agent`, porque `MongoDBSaver` cria/checa seus próprios índices na construção, e isso exigiria cluster acessível já no import do módulo (quebraria testes/lint/outros endpoints).

### Corte de histórico (`_trim_history`, `backend/agent.py`)

Sem limite, cada turno reenviaria o histórico completo de tool-calls/resultados pro Claude — caro e lento numa conversa longa. `MAX_AGENT_HISTORY_MESSAGES = 12` corta o que é **enviado ao modelo** (o checkpoint completo continua intacto no MongoDB). O corte respeita a regra da API Anthropic de não começar a janela numa `AIMessage` com tool-calls órfãos nem numa `ToolMessage` sem a mensagem que a solicitou — avança até a primeira `HumanMessage`.

Também aplica um marcador de cache incremental (`cache_control: ephemeral`) na última mensagem da janela, numa cópia (o checkpoint em si guarda o conteúdo como string simples, não é alterado).

## Trace exibido na UI

Cada resposta de `/agent` inclui um array `trace` (`run_agent`) — para cada par tool-call/tool-result **do turno atual** (`_current_turn`: o estado do checkpoint traz a thread inteira; antes desta revisão, o trace e os contadores de token repetiam as ferramentas dos turnos anteriores):

```python
{
    "tool": info["name"], "args": info["args"],
    "engine": meta["engine"], "collection": meta["collection"],
    "mql": build_tool_pipeline(info["name"], info["args"]),
    "result": result,          # string devolvida pra LLM, truncada em 600 chars
    "degraded": degraded,      # True se o result começa com "Erro"
    "reason": result if degraded else None,
}
```

`TOOL_META` (`backend/agent.py`) mapeia cada ferramenta pro seu engine/coleção de exibição. Isso é o que alimenta o "trace MQL" mostrado na aba Agente — o mesmo padrão de transparência de MQL das demais abas, aplicado ao raciocínio do LLM.

## Concorrência e limites

`/agent` e `/reviews-rag` compartilham um semáforo (`BoundedSemaphore`, `AI_MAX_CONCURRENCY`/`AGENT_MAX_CONCURRENCY`, default 4, `backend/main.py`). Saturado, responde HTTP 429 em vez de enfileirar a chamada de LLM — numa demo, uma requisição travada esperando fila é pior que uma recusa explicada.

Falhas viram respostas legíveis, nunca exceção crua:

| `mode` | Quando |
|---|---|
| `agent` | turno normal |
| `scope_redirect` | pedido fora do marketplace |
| `injection_blocked` | heurística de prompt injection casou |
| `step_limit` | `recursion_limit` estourado |
| `atlas_unavailable` | Atlas inacessível (a memória do agente mora no Atlas) |
| `provider_unavailable` | gateway fora/timeout depois das retentativas do `grove_client` |

## Tracing (Langfuse, opcional)

`backend/langfuse_tracing.py`, no padrão do singleagent: SDK v2 (`langfuse>=2,<3`), fail-open (sem `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` vira no-op; Langfuse fora do ar desliga o tracing no processo depois de um `auth_check`). Uma trace por turno (`marketplace.agent`, `session_id = thread_id`), criada **depois** da máscara de PII, com um span por ferramenta e uma generation com o uso de tokens. O RAG de reviews cria `marketplace.reviews_rag`.

## Uso de tokens

`_track_usage()` soma os `usage_metadata` de todas as `AIMessage`s de uma invocação (um ReAct pode ter várias rodadas de tool-call) e alimenta contadores em `/api/metrics`: tokens de entrada, saída, leitura de cache e escrita de cache. `reviews.py` faz o mesmo para as chamadas de sumarização.
