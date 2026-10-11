# 🤖 Automation Control Center — Painel de Automação (GitHub Actions + Pages)

Este documento descreve a camada de automação adicionada ao repositório: um
painel publicado via **GitHub Pages** com botões que disparam **pipelines do
GitHub Actions**, um **backend CRUD** versionado, **functions** sob demanda,
**cache** entre execuções e **object storage** usando os recursos nativos do
GitHub — tudo validável diretamente pela aba *Actions* do repositório, sem
infraestrutura externa.

## Por que assim?

O Easel em si é uma ferramenta local/CLI (gateway de IA para social media) —
não uma API em nuvem. Em vez de inventar uma infraestrutura paralela (que
exigiria credenciais de nuvem que este projeto não tem motivo para versionar),
a automação abaixo usa **o próprio GitHub como plataforma completa**:

| Necessidade          | Solução usada                                                        |
|-----------------------|-----------------------------------------------------------------------|
| Backend + CRUD        | `scripts/db_crud.py` operando sobre `data/db.json` (commit automático) |
| Functions             | `functions/*.py`, cada uma acionável por botão separado               |
| Cache                 | `actions/cache` (pip/npm) + cache de índice em `.cache/stats`         |
| Object Storage        | `actions/upload-artifact` (curto prazo) + GitHub Release `data-store` (permanente) |
| Pipelines / CI-CD     | `.github/workflows/*.yml` (CI, CRUD, Functions, Pages)                |
| Publicação            | GitHub Pages via `actions/deploy-pages` (fluxo oficial, sem branch `gh-pages`) |
| Painel com botões     | `dashboard/` — site estático que fala com a API pública do GitHub     |

## Estrutura

```text
data/
├── db.json                   "banco de dados" (JSON versionado)
├── stats.json                gerado por functions/stats_indexer.py
├── report.md                 gerado por functions/report.py
└── automation-status.json    status lido pelo painel (fonte de verdade do dashboard)

functions/
├── stats_indexer.py          agrega contagens por status/canal — demonstra CACHE
├── backup_object.py          empacota data/ em .zip — demonstra OBJECT STORAGE
└── report.py                 relatório consolidado em Markdown

scripts/
└── db_crud.py                CRUD (create/read/update/delete/list) — o "BACKEND"

dashboard/
├── index.html                painel (terminal web + cards de pipelines + tabelas)
└── assets/{style.css,app.js} lógica 100% client-side

.github/workflows/
├── ci.yml                    já existia — testes, skills, frontend, instaladores
├── crud-data.yml             botão "Backend · CRUD Data"
├── functions.yml             botão "Backend · Functions"
└── pages.yml                 publica dashboard/ no GitHub Pages
```

## Como os botões disparam eventos reais

O GitHub **não permite** que uma página estática anônima dispare
`workflow_dispatch` sem autenticação — isso é intencional (senão qualquer
visitante do site poderia rodar pipelines no seu nome). O painel lida com isso
de duas formas, ambas realistas e usadas em produção por outros projetos:

1. **Padrão (sem token):** o botão "Executar" abre a tela oficial do GitHub
   Actions para aquele workflow, onde um clique em **"Run workflow"** já é o
   gatilho de verdade — o evento, o log e o pipeline são 100% reais.
2. **Modo avançado (opcional):** o usuário cola um *fine-grained Personal
   Access Token* (permissão `Actions: Read and write`) no painel. O token é
   salvo **somente no `localStorage` do navegador** e usado para chamar
   `POST /repos/.../actions/workflows/{id}/dispatches` direto da página —
   nesse modo o botão dispara o workflow sem sair do dashboard.

Em ambos os casos, o painel também traz um **terminal web** (`help`, `status`,
`db list`, `db read <id>`, `runs <workflow>`, `open <workflow>`, `token set
<pat>`) que consulta dados reais via `api.github.com` e
`raw.githubusercontent.com` — sem backend próprio.

## Pipelines disponíveis

- **CI** (`ci.yml`) — já existente: pytest, contrato de skills, build/lint do
  frontend, lint dos instaladores (bash/PowerShell), instalação real agendada.
- **Backend · CRUD Data** (`crud-data.yml`) — `workflow_dispatch` com inputs
  `operation` (list/create/read/update/delete), `id`, `title`, `status`,
  `channel`. Também aceita `repository_dispatch` (`crud-data`) para
  integrações externas via `curl`.
- **Backend · Functions** (`functions.yml`) — `workflow_dispatch` com input
  `function` (`stats_indexer` / `backup_object` / `report` / `all`).
- **Deploy do Painel** (`pages.yml`) — builda e publica `dashboard/` sempre
  que a pasta muda, ou manualmente.

Cada execução grava um resumo em **Job Summary** (visível direto na aba
Actions) e atualiza `data/automation-status.json`, que é o que o dashboard lê
para mostrar status de cache, CRUD e object storage sem precisar reconsultar
a API do GitHub a cada detalhe.

## Habilitando o GitHub Pages (passo manual único)

Por segurança, criar/alterar a configuração de Pages exige permissão de
administração explícita que o token de automação deste ambiente não possui.
Para publicar o painel, um mantenedor com acesso de admin precisa, uma única
vez:

1. Abrir **Settings → Pages** no repositório.
2. Em **Build and deployment → Source**, selecionar **GitHub Actions**.
3. Rodar (ou aguardar) o workflow **Deploy · Automation Dashboard (Pages)**.

Depois disso, toda alteração em `dashboard/` publica automaticamente.

## Testando localmente

```bash
# Backend CRUD
python scripts/db_crud.py list
python scripts/db_crud.py create --title "Novo post" --status draft --channel blog
python scripts/db_crud.py update --id 1 --status published
python scripts/db_crud.py delete --id 1

# Functions
python functions/stats_indexer.py   # 1ª vez: cache MISS · repetir: cache HIT
python functions/backup_object.py   # gera dist-objects/*.zip
python functions/report.py

# Painel (dashboard) — qualquer servidor estático
cd dashboard && python3 -m http.server 8080
```
