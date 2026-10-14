#!/usr/bin/env python3
"""Orquestrador de pipeline indexado por GitHub Issues (fluxo estilo BPMN).

Cada "nó" do fluxo é uma Issue intitulada `[Pipeline #N] <título>`. A regra:

    sucesso testado  →  fecha a issue N (comentário com o contrato) e cria/
                         aponta para a issue N+1 (a "seguinte indexada")
    erro / gate não  →  mantém a issue N aberta, comenta o contrato de erro,
       positivo          e sinaliza `continuity: true` (continua retentando
                         o MESMO nó em vez de avançar)

O "gate" de `gate_check.py` é sempre consultado antes de avançar: mesmo que a
execução em si tenha sido reportada como sucesso, só se avança se não houver
issue bloqueante aberta, CI/CD verde e testes passando.

Pré-requisito: Issues precisa estar habilitado no repositório (Settings →
General → Features → Issues). Se estiver desabilitado, este script detecta e
devolve um contrato de erro autoexplicativo em vez de falhar sem contexto.

Uso:
    python scripts/issue_chain.py --issue 1 --result success --message "..."
    python scripts/issue_chain.py --issue 1 --result failure --message "..."
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import action_contract as contract  # noqa: E402
import gate_check  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
STATUS_PATH = REPO_ROOT / "data" / "automation-status.json"
TEMPLATES_DIR = REPO_ROOT / ".github" / "pipeline-issues"
TITLE_RE = re.compile(r"^\[Pipeline #(\d+)\]\s*(.*)$")
ISSUE_URL_RE = re.compile(r"/issues/(\d+)\s*$")

contract.register_status(4, "issues_disabled")
contract.register_status(5, "issue_not_found")
contract.register_status(6, "execution_failed")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)


def gh_json(*args: str):
    result = _run(["gh", *args])
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def issues_enabled() -> bool:
    data = gh_json("repo", "view", "--json", "hasIssuesEnabled")
    return bool(data and data.get("hasIssuesEnabled"))


def find_issue_by_index(index: int) -> dict | None:
    needle = f"[Pipeline #{index}]"
    data = gh_json("issue", "list", "--state", "all", "--search", needle,
                   "--json", "number,title,state,url") or []
    for issue in data:
        m = TITLE_RE.match(issue["title"])
        if m and int(m.group(1)) == index:
            return issue
    return None


def load_template(index: int) -> tuple[str, str]:
    """(título_sufixo, corpo) para a issue N: usa `.github/pipeline-issues/<N>.md`
    se existir (1ª linha = título, resto = corpo); senão gera um placeholder."""
    template_path = TEMPLATES_DIR / f"{index}.md"
    if template_path.is_file():
        lines = template_path.read_text(encoding="utf-8").splitlines()
        title = lines[0].lstrip("# ").strip() if lines else f"Etapa {index}"
        body = "\n".join(lines[1:]).strip() or "_(sem descrição adicional)_"
        return title, body
    return (
        f"Etapa {index}",
        f"_(template não encontrado em `.github/pipeline-issues/{index}.md` — "
        "adicione um arquivo com o título na 1ª linha e as instruções no "
        "corpo para personalizar esta etapa)_",
    )


def close_issue_with_comment(number: int, body: str) -> bool:
    r1 = _run(["gh", "issue", "comment", str(number), "--body", body])
    r2 = _run(["gh", "issue", "close", str(number)])
    return r1.returncode == 0 and r2.returncode == 0


def comment_issue(number: int, body: str) -> bool:
    return _run(["gh", "issue", "comment", str(number), "--body", body]).returncode == 0


def create_next_issue(index: int) -> int | None:
    title_suffix, body = load_template(index)
    title = f"[Pipeline #{index}] {title_suffix}"
    result = _run(["gh", "issue", "create", "--title", title, "--body", body])
    if result.returncode != 0:
        return None
    m = ISSUE_URL_RE.search(result.stdout.strip())
    if m:
        return int(m.group(1))
    found = find_issue_by_index(index)
    return found["number"] if found else None


def save_status(result: dict) -> None:
    existing = {}
    if STATUS_PATH.exists():
        try:
            existing = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    existing["issue_chain"] = result
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def advance(issue_index: int, outcome: str, message: str, attachments: list[str] | None) -> dict:
    action_id = f"issue-chain-{issue_index}"
    attachments = attachments or []

    if not issues_enabled():
        result = contract.build_result(
            action_id,
            "Issues está desabilitado neste repositório — habilite em "
            "Settings → General → Features → Issues para o pipeline avançar.",
            4, next_issue=issue_index, attachments=attachments,
        )
        save_status(result)
        return contract.emit(result)

    issue = find_issue_by_index(issue_index)
    if issue is None:
        result = contract.build_result(
            action_id, f"issue [Pipeline #{issue_index}] não encontrada", 5,
            next_issue=issue_index, attachments=attachments,
        )
        save_status(result)
        return contract.emit(result)

    github_number = issue["number"]

    if outcome != "success":
        body = (
            f"❌ **Execução reportada como falha.**\n\n{message}\n\n"
            "O fluxo permanece nesta etapa (continuidade ativa) até um novo "
            "sinal de sucesso testado."
        )
        comment_issue(github_number, body)
        result = contract.build_result(action_id, message, 6, next_issue=issue_index, attachments=attachments)
        save_status(result)
        return contract.emit(result)

    gate_status, gate_message = gate_check.evaluate_gate(
        gate_check.fetch_blocking_issues(),
        gate_check.fetch_workflow_conclusions(),
        gate_check.fetch_tests_passed(),
    )
    if gate_status != 0:
        body = (
            "⏳ **Sucesso reportado, mas o gate de continuidade ainda não está "
            f"positivo:**\n\n{gate_message}\n\nO fluxo permanece nesta etapa até o gate abrir."
        )
        comment_issue(github_number, body)
        result = contract.build_result(action_id, gate_message, gate_status,
                                        next_issue=issue_index, attachments=attachments)
        save_status(result)
        return contract.emit(result)

    next_index = issue_index + 1
    close_body = (
        f"✅ **Concluída com sucesso testado.**\n\n{message}\n\n"
        f"Gate de continuidade positivo: {gate_message}\n\n"
        f"Avançando para `[Pipeline #{next_index}]`."
    )
    close_issue_with_comment(github_number, close_body)
    next_ref = create_next_issue(next_index) or next_index

    result = contract.build_result(
        action_id, message, contract.STATUS_SUCCESS,
        next_issue=next_ref, attachments=attachments, continuity=False,
    )
    save_status(result)
    return contract.emit(result)


def init_chain(index: int) -> dict:
    """Bootstrap: cria a issue indexada N se ela ainda não existir (uso típico:
    `--init --issue 1`, para dar o primeiro passo do pipeline)."""
    action_id = f"issue-chain-init-{index}"

    if not issues_enabled():
        result = contract.build_result(
            action_id,
            "Issues está desabilitado neste repositório — habilite em "
            "Settings → General → Features → Issues antes de iniciar o pipeline.",
            4, next_issue=index,
        )
        save_status(result)
        return contract.emit(result)

    existing = find_issue_by_index(index)
    if existing is not None:
        result = contract.build_result(
            action_id, f"issue [Pipeline #{index}] já existe (#{existing['number']}), nada a fazer",
            contract.STATUS_SUCCESS, next_issue=existing["number"], continuity=False,
        )
        save_status(result)
        return contract.emit(result)

    created = create_next_issue(index)
    if created is None:
        result = contract.build_result(
            action_id, f"falha ao criar a issue [Pipeline #{index}]", 6, next_issue=index,
        )
        save_status(result)
        return contract.emit(result)

    result = contract.build_result(
        action_id, f"issue [Pipeline #{index}] criada (#{created})",
        contract.STATUS_SUCCESS, next_issue=created, continuity=False,
    )
    save_status(result)
    return contract.emit(result)


class IssueFactory:
    """Fábrica orientada a objetos (padrão *Factory*) para as issues
    `[Pipeline #N]`. É uma fachada fina sobre as funções de integração com
    `gh` já testadas acima (`find_issue_by_index`, `create_next_issue`,
    `comment_issue`, `close_issue_with_comment`), para que o orquestrador
    (`IssueChainOrchestrator`) dependa de uma interface orientada a objetos
    em vez de funções soltas — sem duplicar nem reescrever a lógica já
    coberta pelos testes de `dev_check_issue_chain.py`."""

    def issues_enabled(self) -> bool:
        return issues_enabled()

    def find(self, index: int) -> dict | None:
        return find_issue_by_index(index)

    def create(self, index: int) -> int | None:
        return create_next_issue(index)

    def comment(self, github_number: int, body: str) -> bool:
        return comment_issue(github_number, body)

    def close_with_comment(self, github_number: int, body: str) -> bool:
        return close_issue_with_comment(github_number, body)


class IssueChainOrchestrator:
    """Orquestrador orientado a objetos: compõe a `IssueFactory` (criação/
    fechamento de issues reais no GitHub), o `gate_check` (gateway estilo
    BPMN) e o `PipelineStore` (backend local em SQLite, ver
    `scripts/pipeline_store.py`) para registrar o histórico completo de cada
    etapa — além de produzir o mesmo contrato JSON padrão. É o ponto de
    entrada usado pela CLI/produção (`main()`); a lógica unitária em si
    continua nas funções puras `advance()`/`init_chain()`, já testadas com
    `unittest.mock` em `dev_check_issue_chain.py`."""

    def __init__(self, factory: IssueFactory | None = None, store=None):
        self.factory = factory or IssueFactory()
        if store is None:
            import pipeline_store  # import tardio: evita custo p/ quem só usa advance()/init_chain()

            store = pipeline_store.PipelineStore()
        self.store = store

    def advance(self, issue_index: int, outcome: str, message: str,
                attachments: list[str] | None = None) -> dict:
        issue = self.factory.find(issue_index)
        if issue is not None:
            self.store.upsert_stage(issue_index, issue["number"], title=issue["title"])
        result = advance(issue_index, outcome, message, attachments)
        self.store.record_event(issue_index, result)
        return result

    def init(self, index: int) -> dict:
        result = init_chain(index)
        self.store.record_event(index, result)
        issue_number = None
        try:
            issue_number = int(result["next_issue"]) if result.get("next_issue") else None
        except (TypeError, ValueError):
            issue_number = None
        self.store.upsert_stage(index, issue_number, title=f"[Pipeline #{index}]")
        return result

    def close(self) -> None:
        self.store.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Avança o pipeline indexado por Issues")
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--result", choices=["success", "failure"], default="success",
                         help="ignorado quando --init é usado")
    parser.add_argument("--message", default="")
    parser.add_argument("--attachment", action="append", default=[])
    parser.add_argument("--init", action="store_true",
                         help="cria a issue indexada se ela ainda não existir, em vez de avançar")
    args = parser.parse_args(argv)

    orchestrator = IssueChainOrchestrator()
    try:
        if args.init:
            orchestrator.init(args.issue)
        else:
            orchestrator.advance(args.issue, args.result, args.message, args.attachment)
    finally:
        orchestrator.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
