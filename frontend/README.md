# Frontend — Search & Vector (React + LeafyGreen)

A interface da POC, construída com React 18 e Vite usando a biblioteca oficial de
componentes da MongoDB (LeafyGreen), consumindo o backend FastAPI via axios.

## Shell de apresentação

O header segue a assinatura MongoDB Dark v3 do portfólio: marca compacta, status
real do Atlas e uma capability rail com estado ativo explícito. Em desktop as
sete jornadas ficam em uma linha; em tablet reorganizam-se em duas linhas; em
mobile a rail tem scroll horizontal próprio e nunca provoca overflow da página.

Na aba Full-text, a busca é a única ação dominante. O resultado de `/search` é
renderizado assim que chega; `/search/facets` continua em segundo plano com um
estado de carregamento independente. Assim, uma faceta lenta não congela os
resultados que o Atlas já devolveu.

## Stack

| Camada       | Tecnologia                                                        |
|--------------|-------------------------------------------------------------------|
| Framework de UI | React 18 + Vite                                                |
| Componentes  | `@leafygreen-ui/*` (design system da MongoDB)                     |
| Tipografia   | Outfit + JetBrains Mono (Google Fonts)                            |
| HTTP         | axios                                                             |
| Polyfills    | `vite-plugin-node-polyfills` (Buffer/process para deps do LeafyGreen) |

## Pré-requisitos

- Node 18+ (testado no Node 18–26)
- Backend rodando (veja [`../backend/README.md`](../backend/README.md))

## Rodar junto com o backend

A partir da raiz do repositório:

```bash
bash start.sh        # Sobe o FastAPI :8200 e o Vite :5273, e então verifica os dois
```

## Rodar só o frontend

```bash
npm install
cp .env.example .env        # Ajuste VITE_API_URL se o backend não estiver em localhost:8200
npm run dev                 # http://localhost:5273
```

## Variáveis de ambiente

| Variável       | Padrão                    | Descrição                    |
|----------------|---------------------------|------------------------------|
| `VITE_API_URL` | `http://localhost:8200`   | URL base do backend FastAPI  |

## Estrutura

```
src/
├── api.js                 cliente axios e endpoints
├── theme.js               tokens de cor do MongoDB Atlas e formatadores
├── App.jsx                LeafyGreenProvider, layout e abas
├── components/
│   ├── Leaf.jsx           logo da folha MongoDB
│   ├── Sidebar.jsx        componente legado, fora do shell atual
│   ├── KpiCard.jsx        componente legado, fora do shell atual
│   └── ProductTable.jsx   tabela de resultados e bloco MQL
└── tabs/
    ├── AtlasSearch.jsx    busca full-text (autocomplete, fuzzy, highlight)
    ├── SearchVsVector.jsx comparação léxica vs semântica
    ├── HybridRRF.jsx      RRF ajustável com gráfico de score
    ├── Similares.jsx      "mais como este" vetorial com pré-filtro
    ├── Analytics.jsx      agregações com $facet
    ├── ReviewsRag.jsx     recuperação e sumarização de reviews
    └── AiAgent.jsx        chat ReAct com trace ferramenta → MQL → resultado
```

## Notas técnicas

- React 18 (não 19): os componentes LeafyGreen ainda não suportam React 19.
- Polyfills do Node: uma dependência do LeafyGreen depende de globais do Node (`Buffer`).
  O `vite-plugin-node-polyfills` fornece esses globais; sem ele a página renderiza em branco.
- Os textos da UI estão intencionalmente em português, já que a demo é voltada a um
  público brasileiro.

## Build de produção

```bash
npm run build      # Gera em dist/
npm run preview    # Serve o build localmente
```

## Smoke visual e fluxo real

Com o `.env` configurado e acesso ao Atlas, o teste sobe backend e frontend,
percorre as sete jornadas, valida teclado/overflow e captura 1440, 1154, 768 e
360 px. A consulta ao Atlas exige rede liberada.

```bash
python3 /caminho/para/with_server.py \
  --server "cd backend && ../.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8200" --port 8200 \
  --server "cd frontend && env VITE_API_URL=http://127.0.0.1:8200 npm run dev -- --host 127.0.0.1 --port 5273 --strictPort" --port 5273 \
  -- .venv/bin/python frontend/tests/ui_visual_smoke.py
```

Defina `UPDATE_README_SCREENSHOT=1` para atualizar o screenshot público da aba
Full-text em 1600×1000, somente com o cluster real e dados anonimizados.
