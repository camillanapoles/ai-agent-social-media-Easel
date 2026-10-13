# 🔗 Continuity Gate + Issue Chain — pipeline estilo BPMN via GitHub Actions

Este documento descreve as duas peças de automação adicionadas por cima do
[Automation Control Center](automation-dashboard.md): um **gate de
continuidade** e um **orquestrador de pipeline indexado por Issues**. Juntos
formam um fluxo inspirado em **BPMN** (Business Process Model and Notation):
nós de trabalho (issues), um **gateway** booleano (o gate) que decide se o
token de execução avança ou permanece no mesmo nó, e transições automáticas
entre nós — tudo implementado com ferramentas 100% nativas do GitHub
(Actions + Issues), sem infraestrutura externa.

```
 ┌──────────────┐       sucesso        ┌───────────────┐      positivo       ┌──────────────┐
 │ [Pipeline #N]│ ───reportado────────▶│ Continuity    │────(status=0)──────▶│ fecha #N e    │
 │   (issue)    │                      │ Gate           │                     │ abre #N+1    │
 └──────┬───────┘                      └───────┬───────┘                     └──────────────┘
        │  erro / falha                        │ não positivo (status 1/2/3)
        └───────────────┬───────────────────────┘
                         ▼
               permanece em [Pipeline #N]
               (continuity = true: retry/monitorar)
```

## 1. Continuity Gate (`scripts/gate_check.py` + `.github/workflows/gate.yml`)

Um **gateway exclusivo** (XOR gate, na linguagem BPMN): avalia três condições
e só é "positivo" (`status = 0`) quando **nenhuma** delas é verdadeira:

| `status` | Significado | Condição |
|---|---|---|
| `0` | **Positivo** (gate aberto, pode avançar) | sem issues bloqueantes abertas, CI/CD verde, testes passando |
| `1` | Bloqueado por issue(s) | existe issue aberta com label `bug` ou `automation-alert` |
| `2` | Bloqueado por CI/CD | o último run de algum workflow monitorado (`ci.yml`, `crud-data.yml`, `functions.yml`, `watchdog.yml`) concluiu com `failure` |
| `3` | Bloqueado por testes | o job de testes (pytest) da última run de `ci.yml` não existe ou não concluiu com sucesso |

> **Importante — a semântica pedida:** enquanto o gate **não** está no estado
> positivo (`status != 0`), isso **não** é tratado como uma falha definitiva:
> é o sinal de **continuidade** (`continuity: true`). O workflow `gate.yml`
> roda em `schedule` para sempre, então um estado "não positivo" simplesmente
> significa "continue monitorando/retentando até a próxima verificação" — ele
> nunca "desiste" sozinho.

**Disparo:**
```bash
gh workflow run gate.yml                       # manual
gh workflow run gate.yml --ref main            # manual, branch específica
curl -X POST -H "Authorization: token $TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/<owner>/<repo>/dispatches \
  -d '{"event_type":"gate"}'                   # webhook externo
# + schedule automático a cada 6h (cron '15 */6 * * *')
```

**Local/offline (sem `gh`):**
```bash
python scripts/gate_check.py --no-gh   # assume positivo — útil para testar o resto do fluxo
python scripts/gate_check.py           # consulta a API real via gh
```

A função pura `evaluate_gate(blocking_issues, workflow_conclusions,
tests_passed)` em `scripts/gate_check.py` não faz nenhum I/O — é o que torna
o gate testável sem precisar de um repositório/API reais (ver
`scripts/dev_check_issue_chain.py` para exemplos de uso com `unittest.mock`).

## 2. Issue Chain (`scripts/issue_chain.py` + `.github/workflows/issue-chain.yml`)

Convenção: cada "nó" do pipeline é uma **Issue** intitulada
`[Pipeline #N] <descrição>`. O orquestrador:

1. Ao receber um resultado (`success` ou `failure`) para a issue `N`:
   - Se `failure` → comenta o erro na issue `N`, mantém aberta, `next_issue = N`.
   - Se `success` → **consulta o Continuity Gate**. Só se o gate estiver
     positivo é que o fluxo realmente avança:
     - **Gate positivo** → fecha a issue `N` com o contrato de sucesso como
       comentário, cria a issue `N+1` (lendo o template opcional em
       `.github/pipeline-issues/<N+1>.md`), `next_issue = N+1`.
     - **Gate não positivo** → comenta na issue `N` explicando qual condição
       ainda está pendente, mantém aberta, `next_issue = N` (retry).

Isso é exatamente a regra pedida: *"quando uma issue é concluída, testada e
entregue, dispara a instrução da próxima issue indexada; em caso de erro,
retorna erro + continuidade e permanece na issue atual."*

**Disparo:**
```bash
# bootstrap — cria a issue [Pipeline #1] se ainda não existir
gh workflow run issue-chain.yml -f issue_number=1 -f init=true

# reportar sucesso testado da etapa 1 (avança para a 2 se o gate permitir)
gh workflow run issue-chain.yml -f issue_number=1 -f result=success \
  -f message="Testes da etapa 1 passaram, deploy concluído"

# reportar falha (mantém na etapa 1)
gh workflow run issue-chain.yml -f issue_number=1 -f result=failure \
  -f message="Build quebrou no passo X"

# webhook externo (repository_dispatch)
curl -X POST -H "Authorization: token $TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/<owner>/<repo>/dispatches \
  -d '{"event_type":"issue-chain","client_payload":{"issue_number":1,"result":"success","message":"..."}}'

# fechar a issue [Pipeline #N] manualmente no GitHub também dispara o avanço
# (gatilho `issues: closed`, com checagem automática do gate antes de criar a N+1)
```

**Local/offline:**
```bash
python scripts/issue_chain.py --init --issue 1
python scripts/issue_chain.py --issue 1 --result success --message "..."
python scripts/issue_chain.py --issue 1 --result failure --message "..."
```

> **Status do pré-requisito neste repositório:** Issues **já está habilitado**
> (confirmado via `gh repo view --json hasIssuesEnabled` → `true`) e o fluxo
> já foi validado de ponta a ponta contra issues reais deste repositório (ver
> seção 4). Se em algum momento for desabilitado novamente, o código
> continua detectando isso e devolvendo o contrato de erro `status=4`
> (`issues_disabled`) em vez de falhar sem contexto, com o passo manual para
> reabilitar explicado em
> [`automation-dashboard.md`](automation-dashboard.md#habilitando-o-github-issues-passo-manual-único-necessário-para-o-issue-chain).

### Backend orientado a objetos (fábrica de issues + SQLite)

Por cima das funções testadas de integração com `gh`, `scripts/issue_chain.py`
expõe duas classes que formam o ponto de entrada real usado pela CLI/produção:

- **`IssueFactory`** — fábrica (*Factory pattern*) orientada a objetos que
  encapsula a criação/consulta/fechamento de issues `[Pipeline #N]`
  (`find`, `create`, `comment`, `close_with_comment`).
- **`IssueChainOrchestrator`** — orquestrador que compõe a `IssueFactory`, o
  gateway do `gate_check` e o backend `PipelineStore` (`scripts/pipeline_store.py`,
  SQLite, em `data/pipeline.db`). É ele quem o `main()`/CLI de fato usa:
  grava, a cada execução, o histórico completo do contrato de cada etapa no
  banco local, além de fechar/criar as issues reais no GitHub.

```python
from issue_chain import IssueChainOrchestrator

orchestrator = IssueChainOrchestrator()
orchestrator.advance(1, "success", "etapa concluída e testada")
orchestrator.close()
```

`PipelineStore` guarda duas tabelas — `stages` (estado atual de cada etapa
indexada: `issue_number`, `title`, `state`, timestamps) e `events` (histórico
completo de todo contrato emitido para aquela etapa) — e pode ser consultado
diretamente, sem depender da API do GitHub:

```bash
python scripts/pipeline_store.py --list            # todas as etapas conhecidas
python scripts/pipeline_store.py --stage 1          # estado atual da etapa 1
python scripts/pipeline_store.py --history 1        # histórico completo de eventos da etapa 1
```

O GitHub Issues continua sendo a representação oficial/visível de cada
etapa — este backend SQLite é um complemento de auditoria local,
determinístico e consultável offline, não um substituto.

### Templates das etapas

Arquivos em `.github/pipeline-issues/<N>.md` definem o título (1ª linha) e o
corpo da issue `N` quando ela precisa ser criada. Ver
`.github/pipeline-issues/README.md` e o exemplo em `1.md`. Sem template, uma
issue placeholder genérica é criada (nada quebra, só fica menos descritivo).

## 3. O contrato de retorno (`scripts/action_contract.py`)

**Toda** execução de **toda** action deste fluxo — `gate_check.py`,
`issue_chain.py`, e (retrofitado) `db_crud.py`, `functions/stats_indexer.py`,
`functions/backup_object.py`, `functions/report.py`, `watchdog.py` — imprime,
no final, exatamente este objeto JSON:

```json
{
  "id": "issue-chain-1",
  "message": "Testes da etapa 1 passaram, deploy concluído",
  "attachments": ["data/report.md"],
  "status": 0,
  "status_label": "success",
  "continuity": false,
  "next_issue": "2",
  "emitted_at": "2026-10-13T14:20:23Z"
}
```

| Campo | Tipo | Significado |
|---|---|---|
| `id` | string | identificador da action/execução (ex.: `"issue-chain-1"`, `"gate-check"`, `"db-crud-create"`) |
| `message` | string | mensagem humana/log — pode ser um texto fixo ou dinâmico |
| `attachments` | lista de strings | caminhos/nomes de arquivos gerados nesta execução, quando aplicável (lista vazia se não houver) |
| `status` | inteiro | `0` = sucesso; `1`, `2`, `3`, ... = tipos de erro distintos (aberto, cada script registra os seus com `register_status`) |
| `status_label` | string | rótulo legível do código acima (ex.: `"blocked_open_issue"`, `"db_integrity_failed_unhealed"`) |
| `continuity` | booleano | `true` = o gate ainda não está positivo, o fluxo deve **continuar monitorando/retentando** o mesmo ponto; `false` = positivo, pode avançar. Inferido automaticamente de `status` (`status != 0 → true`) a menos que explicitado |
| `next_issue` | string ou `null` | mesma issue (em caso de retry) ou a próxima indexada (em caso de sucesso testado) |
| `emitted_at` | string ISO 8601 (UTC) | quando o contrato foi gerado |

`action_contract.emit(result)` imprime o JSON no log, anexa um bloco ao
**Job Summary** (visível direto na aba Actions), grava `status` /
`continuity` / `next_issue` e o JSON completo nos **outputs do step**
(`GITHUB_OUTPUT`, delimitado por heredoc para suportar JSON multilinha) —
assim qualquer step seguinte do workflow, ou outro workflow via
`workflow_run`, pode consumir o resultado sem reprocessar texto livre.
`action_contract.merge_into_status_file(...)` também grava o contrato em
`data/automation-status.json` sob a chave `contracts.<nome>`, para o painel
(dashboard) exibir o histórico de contratos sem precisar ler logs de Actions.

### Por que esse formato

É deliberadamente minimalista e **genérico o suficiente para qualquer
action** do pipeline: um gate BPMN, visto de fora, só precisa saber "deu
certo ou não, e qual o próximo nó" — o resto (`message`, `attachments`) é
contexto para humanos/dashboards, não para a lógica de controle de fluxo.

## 4. Testes e validação

- `scripts/gate_check.py`: a função pura `evaluate_gate(...)` é testada com
  5 cenários fabricados (limpo, issue bloqueante, CI com erro, sem testes,
  testes falhando) sem precisar de rede/API.
- `scripts/issue_chain.py`: `scripts/dev_check_issue_chain.py` cobre, via
  `unittest.mock`, os cenários centrais (Issues desabilitado, issue não
  encontrada, falha reportada, sucesso com gate bloqueado, sucesso com gate
  limpo avançando para N+1, fallback de template, histórico do
  `PipelineStore`, e o `IssueChainOrchestrator` fachada OOP completo) — 30
  asserções, todas verdes.
- **Teste de ponta a ponta contra o GitHub real** (issues deste repositório,
  habilitadas durante esta sessão): `--init --issue 1` criou a issue real
  `[Pipeline #1]`; `--issue 1 --result success` avaliou o gate de verdade e
  avançou corretamente, criando `[Pipeline #2]`. Esse teste real **revelou
  um bug** (corrigido na mesma sessão): o código estava chamando
  `gh issue close`/`gh issue comment` com o **índice lógico do pipeline**
  (`N`) em vez do **número real da issue no GitHub** — os testes mockados
  não pegavam isso porque usavam o mesmo valor para os dois por coincidência.
  Corrigido (agora usa `issue["number"]` explicitamente) e os testes
  mockados foram atualizados para usar números propositalmente diferentes
  (`101`, `202`) do índice do pipeline, para impedir regressão silenciosa
  dessa classe de bug no futuro.
- **Limitação conhecida de teste local:** o token do `gh` usado neste
  ambiente sandbox consegue **criar** issues, mas recebe
  `403 Resource not accessible by integration` ao tentar **comentar**,
  **fechar** ou **editar** issues (mesma classe de limitação de permissão já
  documentada para Pages/dispatch). Por isso o caminho de fechamento
  automático não pôde ser validado com uma chamada real de ponta a ponta
  neste ambiente — ele é coberto pelos testes mockados (que agora verificam
  explicitamente o número correto do argumento) e deve funcionar
  normalmente quando o workflow `issue-chain.yml` rodar de verdade no
  GitHub, pois ali o `GITHUB_TOKEN` nativo do Actions (com
  `permissions: issues: write` declarado) tem permissão plena — esse é um
  comportamento padrão e bem documentado do GitHub Actions, não uma
  suposição.
- Sintaxe de todos os módulos verificada com `python3 -m py_compile`.
- Workflows (`gate.yml`, `issue-chain.yml`) validados com `yaml.safe_load` e
  com `actionlint` (via pacote npm `actionlint`, que empacota o binário via
  WASM) — zero findings.
- Retrofit em `db_crud.py` / `functions/*.py` / `watchdog.py` testado
  manualmente (execução local real de cada comando, incluindo o caminho de
  erro do `db_crud.py read --id <inexistente>`) confirmando que o
  comportamento/CLI pré-existente (exit codes, resumos, `automation-status.json`)
  não mudou — o contrato é **estritamente aditivo**.
- Suíte completa `pytest -q -o faulthandler_timeout=90` (instalação completa
  via `pip install --prefer-binary -e . pytest`, igual ao `ci.yml`)
  re-executada após todas as mudanças: `22 failed, 1241 passed, 11 skipped,
  0 errors` — as 22 falhas vêm só de `tests/test_login_qr.py` /
  `tests/test_account_login_fixes.py` e são causadas por uma biblioteca de
  sistema ausente neste sandbox (`libGL.so.1`, dependência nativa do
  OpenCV), não pelo código desta branch; ignorando esses 2 arquivos dá
  `1062 passed, 11 skipped, 0 failed` — 100% verde. O job real `pytest
  (ubuntu-latest)` do CI do GitHub (onde `libGL` existe na imagem) confirma
  isso, passando 100%. Nenhuma regressão introduzida.
