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
├── action_contract.py        contrato JSON padrão (id/message/attachments/status/next_issue)
├── db_crud.py                CRUD (create/read/update/delete/list) — o "BACKEND"
├── watchdog.py               monitora, recupera e cura as pipelines — a "CONTINUIDADE"
├── gate_check.py             gate de continuidade (issues bloqueantes + CI/CD + testes)
└── issue_chain.py            orquestrador de pipeline indexado por Issues (estilo BPMN)

.github/pipeline-issues/
└── <N>.md                    template da issue `[Pipeline #N]` (título na 1ª linha + corpo)

dashboard/
├── index.html                painel (terminal web + cards de pipelines + tabelas)
└── assets/{style.css,app.js} lógica 100% client-side

.github/workflows/
├── ci.yml                    já existia — testes, skills, frontend, instaladores
├── crud-data.yml             botão "Backend · CRUD Data"
├── functions.yml             botão "Backend · Functions"
├── watchdog.yml              roda sozinho (cron) — continuidade & auto-recuperação
├── gate.yml                  roda sozinho (cron) — gate de continuidade (ver docs/issue-chain-pipeline.md)
├── issue-chain.yml           orquestra o pipeline indexado por Issues (ver docs/issue-chain-pipeline.md)
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
- **Watchdog** (`watchdog.yml`) — `schedule` a cada 6h + `workflow_dispatch` +
  `repository_dispatch`. Monitora, reexecuta falhas, cura `data/db.json` e
  alerta via Issue. Ver seção "Continuidade e recuperação" abaixo.
- **Deploy do Painel** (`pages.yml`) — builda e publica `dashboard/` sempre
  que a pasta muda, ou manualmente.
- **Continuity Gate** (`gate.yml`) — `schedule` a cada 6h + `workflow_dispatch`
  + `repository_dispatch`. Só fica positivo (status `0`) sem issues
  bloqueantes abertas, com CI/CD verde e testes passando. Ver
  [`docs/issue-chain-pipeline.md`](issue-chain-pipeline.md).
- **Issue Chain** (`issue-chain.yml`) — orquestrador estilo BPMN: ao fechar
  (ou reportar sucesso testado para) uma issue `[Pipeline #N]`, avança
  automaticamente para a `[Pipeline #N+1]`. Ver
  [`docs/issue-chain-pipeline.md`](issue-chain-pipeline.md) para o schema
  completo do contrato de retorno e exemplos de uso.

Cada execução grava um resumo em **Job Summary** (visível direto na aba
Actions) e atualiza `data/automation-status.json`, que é o que o dashboard lê
para mostrar status de cache, CRUD e object storage sem precisar reconsultar
a API do GitHub a cada detalhe.

## Continuidade e recuperação (sem depender de nenhuma sessão de agente)

Ponto central deste design: **uma vez na branch padrão, nada aqui depende de
uma sessão de chat/agente estar aberta.** GitHub Actions é infraestrutura do
próprio GitHub — os workflows ficam armazenados no repositório e são
executados pelos runners do GitHub para sempre, acionados por qualquer uma
destas 4 formas (todas funcionam com a sessão fechada, com o computador
desligado, etc.):

1. **Botão no painel** — abre a tela oficial de execução no GitHub.
2. **CLI, de qualquer máquina com `gh` autenticado**:
   ```bash
   gh workflow run crud-data.yml --ref main -f operation=list
   gh workflow run functions.yml --ref main -f function=report
   gh workflow run watchdog.yml  --ref main
   ```
3. **Webhook / API, de qualquer sistema externo** (cron de terceiros, outro
   CI, um script agendado no seu próprio computador, Zapier, etc.) — é o
   `repository_dispatch`, já habilitado em `crud-data.yml` e `functions.yml`:
   ```bash
   curl -X POST \
     -H "Authorization: token $SEU_TOKEN" \
     -H "Accept: application/vnd.github+json" \
     https://api.github.com/repos/<owner>/<repo>/dispatches \
     -d '{"event_type":"crud-data"}'
   ```
4. **Agendamento automático (`schedule`)** — zero intervenção: o próprio
   GitHub dispara o cron.

### Watchdog: auto-recuperação de verdade

`.github/workflows/watchdog.yml` + `scripts/watchdog.py` rodam sozinhos a
cada 6 horas (além de sob demanda) e garantem:

- **Reexecução automática de falhas** — se `crud-data.yml` ou
  `functions.yml` falharem, o watchdog reexecuta só os jobs que falharam
  (`gh run rerun --failed`), sem precisar de ninguém clicando em nada.
- **Auto-cura de `data/db.json`** — se o "banco" ficar corrompido (JSON
  inválido), o watchdog tenta restaurá-lo a partir do histórico do git (até
  10 commits atrás) e, em último caso, a partir do backup permanente na
  release `data-store` (object storage). Testado de ponta a ponta
  corrompendo o arquivo de propósito em um clone descartável.
- **Alerta humano quando a auto-recuperação não resolve** — se uma pipeline
  falhar 3 vezes seguidas mesmo após tentativas de recuperação, o watchdog
  abre (ou atualiza, sem duplicar) uma Issue rotulada `automation-alert`.
- **Estado persistido no próprio repositório** — `data/automation-status.json`
  guarda o histórico de falhas consecutivas entre execuções, já que cada run
  do Actions é uma VM nova e descartável; o painel lê esse arquivo para
  mostrar a saúde do sistema sem precisar consultar a API a cada detalhe.

Em outras palavras: mergear este PR e fechar a sessão do agente **não
interrompe nada** — pelo contrário, é exatamente o estado em que o sistema
foi desenhado para operar.

## Habilitando o GitHub Pages (passo manual único)

Por segurança, criar/alterar a configuração de Pages exige permissão de
administração explícita que o token de automação deste ambiente não possui.
Para publicar o painel, um mantenedor com acesso de admin precisa, uma única
vez:

1. Abrir **Settings → Pages** no repositório.
2. Em **Build and deployment → Source**, selecionar **GitHub Actions**.
3. Rodar (ou aguardar) o workflow **Deploy · Automation Dashboard (Pages)**.

Depois disso, toda alteração em `dashboard/` publica automaticamente.

## Habilitando o GitHub Issues (passo manual único, necessário para o Issue Chain)

O repositório está hoje com **Issues desabilitado** nas configurações
(`Settings → General → Features`), e o token de automação deste ambiente não
tem permissão (`administration:write`) para ligar essa chave remotamente —
mesma limitação de origem do item acima sobre Pages. O **Continuity Gate**
(`gate.yml`) funciona normalmente sem Issues (ele só reporta `status=1` se
encontrar uma pendente), mas o **Issue Chain** (`issue-chain.yml`) depende de
Issues para existir: sem elas, qualquer execução retorna o contrato de erro
`status=4` (`issues_disabled`) explicando exatamente este passo.

Para habilitar, uma única vez, um mantenedor com acesso de admin precisa:

1. Abrir **Settings → General** no repositório.
2. Em **Features**, marcar a caixa **Issues**.
3. (Opcional) rodar `gh workflow run issue-chain.yml -f issue_number=1 -f
   init=true` para criar a primeira issue do pipeline (`[Pipeline #1]`).

Depois disso, o fluxo descrito em
[`docs/issue-chain-pipeline.md`](issue-chain-pipeline.md) funciona de ponta a
ponta sem nenhuma intervenção manual adicional.

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

# Watchdog (requer `gh` autenticado; --no-gh roda só a parte offline)
python scripts/watchdog.py
python scripts/watchdog.py --no-gh          # só valida integridade local, sem chamar a API
python scripts/watchdog.py --no-recovery    # diagnostica sem reexecutar/curar nada

# Continuity gate (requer `gh` autenticado; --no-gh assume positivo, uso em teste)
python scripts/gate_check.py
python scripts/gate_check.py --no-gh

# Issue chain (requer `gh` autenticado + Issues habilitado no repo)
python scripts/issue_chain.py --init --issue 1
python scripts/issue_chain.py --issue 1 --result success --message "Etapa concluída e testada"
python scripts/issue_chain.py --issue 1 --result failure --message "Build quebrou em X"

# Painel (dashboard) — qualquer servidor estático
cd dashboard && python3 -m http.server 8080
```

Detalhes completos do gate de continuidade e do orquestrador de Issues — o
schema exato do contrato JSON de retorno, os códigos de `status`, e o
blueprint estilo BPMN do fluxo — estão em
[`docs/issue-chain-pipeline.md`](issue-chain-pipeline.md).
