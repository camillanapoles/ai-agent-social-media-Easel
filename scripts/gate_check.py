#!/usr/bin/env python3
"""Gate de continuidade: só fica "positivo" quando NÃO há issue bloqueante
aberta, NEM CI/CD com erro, NEM ausência/falha de testes.

Enquanto o gate não for positivo, o resultado sinaliza `continuity: true` —
ou seja, o pipeline deve continuar monitorando/retentando (não deve avançar
para a próxima etapa/issue). Esse é exatamente o papel de "gate" num fluxo
estilo BPMN: uma condição booleana que decide se o token de execução segue
em frente ou permanece no mesmo nó até a condição ser satisfeita.

Códigos de status (contrato em `action_contract.py`):
  0 = positivo — sem issues bloqueantes, CI/CD verde, testes passando
  1 = bloqueado por issue(s) aberta(s) (label `bug` ou `automation-alert`)
  2 = bloqueado por CI/CD com erro (algum workflow monitorado falhando)
  3 = bloqueado por ausência/falha de testes (job pytest não encontrado ou
      não concluído com sucesso na última execução de `ci.yml`)

Uso:
    python scripts/gate_check.py                 # consulta a API real
    python scripts/gate_check.py --no-gh          # modo offline/teste
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import action_contract as contract  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
STATUS_PATH = REPO_ROOT / "data" / "automation-status.json"

BLOCKING_LABELS = ["bug", "automation-alert"]
MONITORED_WORKFLOWS = ["ci.yml", "crud-data.yml", "functions.yml", "watchdog.yml"]
TEST_JOB_HINT = "pytest"

contract.register_status(1, "blocked_open_issue")
contract.register_status(2, "blocked_cicd_failure")
contract.register_status(3, "blocked_missing_or_failing_tests")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    """Executa o comando na raiz do repositório e captura stdout e stderr."""
    return subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)


def gh_json(*args: str):
    """Decodifica a saída JSON de gh; retorna None em erro, saída vazia ou JSON inválido."""
    result = _run(["gh", *args])
    if result.returncode != 0:
        print(f"[gate] gh {' '.join(args)} falhou: {result.stderr.strip()}", file=sys.stderr)
        return None
    out = result.stdout.strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def fetch_blocking_issues() -> list[dict]:
    """Lista issues abertas com labels bloqueantes, sem duplicar números de issue."""
    issues: list[dict] = []
    for label in BLOCKING_LABELS:
        found = gh_json("issue", "list", "--state", "open", "--label", label,
                         "--json", "number,title,url") or []
        issues.extend(found)
    # dedup por número
    seen: set[int] = set()
    unique = []
    for i in issues:
        if i["number"] not in seen:
            seen.add(i["number"])
            unique.append(i)
    return unique


def fetch_workflow_conclusions() -> dict[str, str | None]:
    """Retorna a conclusão da última execução de cada workflow monitorado, ou None."""
    conclusions: dict[str, str | None] = {}
    for wf in MONITORED_WORKFLOWS:
        runs = gh_json("run", "list", "--workflow", wf, "--limit", "1",
                        "--json", "conclusion,status") or []
        conclusions[wf] = runs[0].get("conclusion") if runs else None
    return conclusions


def fetch_tests_passed() -> bool | None:
    """True se o job de pytest da última run de ci.yml concluiu com sucesso;
    False se concluiu sem sucesso; None se não há como saber (sem runs/jobs)."""
    runs = gh_json("run", "list", "--workflow", "ci.yml", "--limit", "1",
                   "--json", "databaseId") or []
    if not runs:
        return None
    run_id = runs[0]["databaseId"]
    jobs = gh_json("run", "view", str(run_id), "--json", "jobs") or {}
    job_list = jobs.get("jobs", [])
    test_jobs = [j for j in job_list if TEST_JOB_HINT in j.get("name", "").lower()]
    if not test_jobs:
        return None
    return all(j.get("conclusion") == "success" for j in test_jobs)


def evaluate_gate(
    blocking_issues: list[dict],
    workflow_conclusions: dict[str, str | None],
    tests_passed: bool | None,
) -> tuple[int, str]:
    """Função pura (sem I/O) — fácil de testar sem precisar do `gh` real."""
    if blocking_issues:
        titles = ", ".join(f"#{i['number']} {i['title']}" for i in blocking_issues)
        return 1, f"{len(blocking_issues)} issue(s) bloqueante(s) aberta(s): {titles}"

    failing = [wf for wf, c in workflow_conclusions.items() if c == "failure"]
    if failing:
        return 2, f"CI/CD com erro em: {', '.join(failing)}"

    if tests_passed is False:
        return 3, "último job de testes (pytest) não concluiu com sucesso"
    if tests_passed is None:
        return 3, "nenhuma execução de testes (pytest) encontrada ainda"

    return contract.STATUS_SUCCESS, "gate positivo: sem issues bloqueantes, CI/CD verde, testes passando"


def main(argv: list[str] | None = None) -> int:
    """Avalia o gate pela CLI e grava o contrato no status da automação."""
    parser = argparse.ArgumentParser(description="Gate de continuidade do pipeline")
    parser.add_argument("--no-gh", action="store_true", help="modo offline: assume tudo positivo (uso em teste)")
    parser.add_argument("--id", default="gate-check", help="id da action para o contrato de retorno")
    args = parser.parse_args(argv)

    if args.no_gh:
        status, message = contract.STATUS_SUCCESS, "modo offline (--no-gh): gate assumido positivo"
    else:
        blocking_issues = fetch_blocking_issues()
        workflow_conclusions = fetch_workflow_conclusions()
        tests_passed = fetch_tests_passed()
        status, message = evaluate_gate(blocking_issues, workflow_conclusions, tests_passed)

    result = contract.build_result(args.id, message, status)
    contract.emit(result)

    existing = {}
    if STATUS_PATH.exists():
        try:
            existing = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    existing["gate"] = result
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return 0


if __name__ == "__main__":
    sys.exit(main())
