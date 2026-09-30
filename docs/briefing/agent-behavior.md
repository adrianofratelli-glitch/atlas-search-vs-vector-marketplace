# Comportamento do agente de IA

> Arquitetura geral em `architecture.md`, pipelines em detalhe em `queries.md` (seção 12). Este arquivo é sobre o agente em si: como ele decide o que fazer, com que ferramentas, e como falha com segurança.

## O que é

Agente ReAct (raciocina → escolhe ferramenta → observa resultado → repete) implementado com LangGraph (`create_react_agent`), rodando sobre Claude via LangChain (`ChatAnthropic`). Local: `backend/agent.py`. Exposto no endpoint `POST /agent`, consumido pela aba "Agente" (`frontend/src/tabs/AiAgent.jsx`).

## Modelo e configuração

```python
llm = ChatAnthropic(
    model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
    temperature=0,
    max_tokens=1024,
    api_key="dummy",
    anthropic_api_url=os.getenv("ANTHROPIC_BASE_URL"),
    default_headers={"api-key": os.getenv("ANTHROPIC_API_KEY", "")},
    timeout=float(os.getenv("ANTHROPIC_TIMEOUT_SECONDS", "45")),
    max_retries=int(os.getenv("ANTHROPIC_MAX_RETRIES", "2")),
)
```
(`backend/agent.py:25`)

Ponto que costuma gerar dúvida em auditoria: `api_key="dummy"` não é uma chave real — a autenticação de fato acontece via header custom `api-key` (`default_headers`), porque as PoVs deste ambiente falam com o Claude através de um gateway próprio (Grove/APIM), não direto com `api.anthropic.com`. `ANTHROPIC_BASE_URL` aponta pro gateway. `temperature=0` é proposital — o agente precisa ser determinístico o suficiente pra escolher ferramenta de forma consistente, não criativo.

Modelo separado para o RAG de reviews (`backend/reviews.py:17`): `claude-haiku-4-5` por padrão — sumarização de review é tarefa simples, Haiku entrega qualidade equivalente por custo bem menor.

## Ferramentas disponíveis (quatro)

Local: `backend/agent.py:104-157`.

| Ferramenta | Quando o modelo escolhe | Engine | Coleção |
|---|---|---|---|
| `busca_semantica` | consultas por necessidade/uso ("academia em casa", "presente pro dia dos pais") | Vector Search | `produtos_vector` |
| `buscar_produto` | busca por nome de produto | Atlas Search (autocomplete fuzzy) | `produtos` |
| `comparar_categoria` | "quais os melhores X" | Aggregation (`$match`+`$sort`) | `produtos` |
| `produtos_por_faixa_preco` | "X entre R$ A e R$ B" | Aggregation (filtro de faixa) | `produtos` |

Os docstrings das ferramentas e o `SYSTEM_PROMPT` (`backend/agent.py:196`) ficam **intencionalmente em português** — são eles que dirigem a escolha de ferramenta pelo modelo e o idioma da resposta final, já que o público é brasileiro.

Cada pipeline que a ferramenta executa vem de uma função `_pipe_*` compartilhada com a exibição do trace (ver `queries.md` seção 12) — não existe uma versão "que roda" e outra "que aparece na tela".

## Degradação graciosa aplicada ao agente

Antes de rodar, `busca_semantica` e `buscar_produto` checam se o índice necessário está `READY` via `_index_ready()` (`backend/agent.py:79`) — mesma lógica de introspecção via `$listSearchIndexes` usada nas abas manuais. Se o índice não estiver pronto ou o cluster estiver inacessível, a ferramenta devolve uma mensagem de erro legível **para o modelo**, em vez de deixar uma exceção crua do PyMongo estourar dentro do contexto da conversa — um erro de driver dentro do prompt tende a produzir uma resposta alucinada sobre infraestrutura, que é o pior tipo de falha numa demo.

## Escopo — o que o agente recusa responder

O agente é restrito ao domínio do marketplace. Duas camadas:

1. **Interceptação antes de chamar o modelo** (`is_obviously_out_of_scope`, `backend/agent.py:186`): normaliza o texto (remove acentos, minúsculas) e casa contra uma lista de padrões óbvios fora de escopo — clima, previsão do tempo, placar de jogo, receita culinária, cotação do dólar, horóscopo. Se casar, responde com `SCOPE_GUIDANCE` (`backend/agent.py:174`) sem gastar chamada de LLM. Isso funciona mesmo se o provedor Anthropic estiver fora do ar.
2. **Instrução no system prompt**: pede pro modelo reconhecer o limite em uma frase e oferecer as alternativas do domínio (busca por produto, comparação de categoria, faixa de preço, avaliações) em vez de improvisar resposta ou simplesmente dizer "não sei".

## Memória de conversa — checkpoint no MongoDB

`MongoDBSaver` (LangGraph checkpointer oficial para MongoDB) grava o estado da conversa na coleção `checkpoints`, chaveado por `thread_id` (UUID gerado pelo backend se o cliente não enviar um). Isso é continuidade de conversa persistida **no próprio Atlas**, sem estado em memória de processo — reiniciar o backend não perde o histórico de uma conversa em andamento, e é demonstrável na prática: uma pergunta de continuidade no mesmo `thread_id` funciona mesmo depois de um restart.

O agente é construído de forma preguiçosa (`_get_agent()`, `@lru_cache(maxsize=1)`, `backend/agent.py:232`) — só na primeira requisição a `/agent`, porque `MongoDBSaver` cria/checa seus próprios índices na construção, e isso exigiria cluster acessível já no import do módulo (quebraria testes/lint/outros endpoints).

### Corte de histórico (`_trim_history`, `backend/agent.py:213`)

Sem limite, cada turno reenviaria o histórico completo de tool-calls/resultados pro Claude — caro e lento numa conversa longa. `MAX_AGENT_HISTORY_MESSAGES = 12` corta o que é **enviado ao modelo** (o checkpoint completo continua intacto no MongoDB). O corte respeita a regra da API Anthropic de não começar a janela numa `AIMessage` com tool-calls órfãos nem numa `ToolMessage` sem a mensagem que a solicitou — avança até a primeira `HumanMessage`.

Também aplica um marcador de cache incremental (`cache_control: ephemeral`) na última mensagem da janela, numa cópia (o checkpoint em si guarda o conteúdo como string simples, não é alterado).

## Trace exibido na UI

Cada resposta de `/agent` inclui um array `trace` (`run_agent`, `backend/agent.py:266`) — para cada par tool-call/tool-result:

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

`TOOL_META` (`backend/agent.py:161`) mapeia cada ferramenta pro seu engine/coleção de exibição. Isso é o que alimenta o "trace MQL" mostrado na aba Agente — o mesmo padrão de transparência de MQL das demais abas, aplicado ao raciocínio do LLM.

## Concorrência e limites

`/agent` e `/reviews-rag` compartilham um semáforo (`BoundedSemaphore`, `AI_MAX_CONCURRENCY`/`AGENT_MAX_CONCURRENCY`, default 4, `backend/main.py:175`). Saturado, responde HTTP 429 em vez de enfileirar a chamada de LLM — numa demo, uma requisição travada esperando fila é pior que uma recusa explicada.

Falha de invocação do agente (provedor Anthropic fora do ar, timeout, etc.) é capturada e devolve uma resposta amigável sugerindo as abas manuais como alternativa (`mode: "provider_unavailable"`, `backend/agent.py:276`) — nunca deixa a exceção subir crua pro frontend.

## Uso de tokens

`_track_usage()` (`backend/agent.py:252`) soma os `usage_metadata` de todas as `AIMessage`s de uma invocação (um ReAct pode ter várias rodadas de tool-call) e alimenta contadores em `/api/metrics`: tokens de entrada, saída, leitura de cache e escrita de cache. `reviews.py` faz o mesmo para as chamadas de sumarização.
