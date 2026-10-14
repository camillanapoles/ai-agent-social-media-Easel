#!/usr/bin/env python3
"""Watchdog de continuidade e auto-recuperação do CI/CD.

Este script existe para uma garantia específica: **nenhuma automação deste
repositório depende de uma sessão de agente/chat estar aberta**. Uma vez que
este workflow esteja na branch padrão, ele roda sozinho, nos servidores do
GitHub, para sempre — disparado por `schedule` (cron), por botão
(`workflow_dispatch`) ou por webhook externo (`repository_dispatch`).

O que ele faz a cada execução:

1. **Monitora** o último run de cada pipeline crítica (`crud-data.yml`,
   `functions.yml`, `pages.yml`) via `gh run list`.
2. **Recupera automaticamente** runs que falharam, reexecutando só os jobs
   que falharam (`gh run rerun --failed`) — sem intervenção humana.
3. **Verifica a integridade** de `data/db.json` e, se estiver corrompido,
   tenta restaurá-lo a partir do histórico do git ou, em último caso, do
   asset permanente da release `data-store` (object storage).
4. **Alerta** abrindo/atualizando uma Issue única quando uma pipeline falha
   de forma persistente (N vezes seguidas), para que um humano seja
   notificado mesmo que não exista nenhum painel sendo observado.

Tudo isso é idempotente e seguro de rodar repetidamente: não há estado fora
do próprio repositório (`data/automation-status.json`) e do GitHub (runs,
issues, releases).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import action_contract as contract  # noqa: E402

contract.register_status(1, "transient_failure_auto_recovered")
contract.register_status(2, "db_integrity_failed_unhealed")
contract.register_status(3, "persistent_cicd_failure_alert_opened")

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "db.json"
STATUS_PATH = REPO_ROOT / "data" / "automation-status.json"

MONITORED_WORKFLOWS = ["crud-data.yml", "functions.yml", "pages.yml"]
FAILURE_THRESHOLD = 3  # falhas consecutivas antes de abrir/atualizar a issue de alerta
ALERT_TITLE = "🔴 Automation Watchdog: pipeline(s) falhando de forma persistente"


def _now() -> str:
    """Retorna o instante atual em UTC no formato ISO 8601 com sufixo Z."""
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    """Executa o comando na raiz do repositório e captura stdout e stderr."""
    return subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)


def gh_json(*args: str):
    """Roda `gh <args>` e decodifica a saída como JSON. Retorna None em erro."""
    result = _run(["gh", *args])
    if result.returncode != 0:
        print(f"[watchdog] gh {' '.join(args)} falhou: {result.stderr.strip()}", file=sys.stderr)
        return None
    out = result.stdout.strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def check_workflow(workflow: str) -> dict:
    """Resume a última execução do workflow, indicando no-runs quando não houver dados."""
    runs = gh_json(
        "run", "list", "--workflow", workflow, "--limit", "1",
        "--json", "databaseId,conclusion,status,url",
    )
    if not runs:
        return {"workflow": workflow, "status": "no-runs", "conclusion": None, "run_id": None, "html_url": None}
    r = runs[0]
    return {
        "workflow": workflow,
        "status": r.get("status"),
        "conclusion": r.get("conclusion"),
        "run_id": r.get("databaseId"),
        "html_url": r.get("url"),
    }


def attempt_recovery(run_id: int) -> bool:
    """Solicita a reexecução dos jobs falhos da run e retorna se gh aceitou o pedido."""
    print(f"[watchdog] tentando auto-recuperação: reexecutando jobs que falharam na run {run_id}")
    result = _run(["gh", "run", "rerun", str(run_id), "--failed"])
    if result.returncode != 0:
        print(f"[watchdog] rerun falhou: {result.stderr.strip()}", file=sys.stderr)
        return False
    return True


def validate_db_integrity() -> bool:
    """Verifica se DB_PATH contém JSON com records do tipo lista; retorna False em erro."""
    try:
        data = json.loads(DB_PATH.read_text(encoding="utf-8"))
        return isinstance(data.get("records"), list)
    except Exception as exc:  # noqa: BLE001 - queremos capturar qualquer corrupção
        print(f"[watchdog] data/db.json corrompido: {exc}", file=sys.stderr)
        return False


def heal_db() -> bool:
    """Tenta restaurar data/db.json a partir do histórico git (até 10 commits
    atrás); se nada servir, baixa o backup permanente da release data-store."""
    for i in range(1, 11):
        result = _run(["git", "show", f"HEAD~{i}:data/db.json"])
        if result.returncode != 0:
            continue
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            continue
        if isinstance(data.get("records"), list):
            DB_PATH.write_text(result.stdout, encoding="utf-8")
            print(f"[watchdog] data/db.json restaurado a partir de HEAD~{i}")
            return True

    result = _run([
        "gh", "release", "download", "data-store",
        "--pattern", "db.json", "--output", str(DB_PATH), "--clobber",
    ])
    if result.returncode == 0 and validate_db_integrity():
        print("[watchdog] data/db.json restaurado a partir da release data-store (object storage)")
        return True

    print("[watchdog] não foi possível recuperar data/db.json automaticamente", file=sys.stderr)
    return False


def manage_alert_issue(failing: list[dict]) -> bool:
    """Comenta o alerta existente ou tenta criar uma issue para as falhas persistentes."""
    existing = gh_json(
        "issue", "list", "--search", f"{ALERT_TITLE} in:title",
        "--state", "open", "--json", "number",
    ) or []
    body = "Pipelines com falha persistente detectada pelo watchdog:\n\n" + "\n".join(
        f"- `{f['workflow']}` — [run]({f['html_url']})" for f in failing
    ) + f"\n\n_Última verificação: {_now()}_\n"

    if existing:
        number = existing[0]["number"]
        _run(["gh", "issue", "comment", str(number), "--body", body])
        return True

    result = _run(["gh", "issue", "create", "--title", ALERT_TITLE, "--body", body, "--label", "automation-alert"])
    if result.returncode != 0:
        # Label pode não existir no repo ainda — tenta de novo sem ela.
        result = _run(["gh", "issue", "create", "--title", ALERT_TITLE, "--body", body])
    return result.returncode == 0


def load_status() -> dict:
    """Lê o status da automação; retorna um dicionário vazio se ausente ou JSON inválido."""
    if not STATUS_PATH.exists():
        return {}
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_status(status: dict) -> None:
    """Cria o diretório necessário e grava o status recebido como JSON."""
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Monitora workflows e banco, aplica a recuperação permitida e grava o status e o contrato."""
    parser = argparse.ArgumentParser(description="Watchdog de continuidade/recuperação do CI/CD")
    parser.add_argument("--no-recovery", action="store_true", help="só diagnostica, não reexecuta nem cura nada")
    parser.add_argument("--no-gh", action="store_true", help="pula chamadas de gh (uso em teste local offline)")
    args = parser.parse_args(argv)

    status = load_status()
    history = status.get("watchdog", {}).get("consecutive_failures", {})

    if args.no_gh:
        results = [{"workflow": wf, "status": "skipped", "conclusion": None, "run_id": None, "html_url": None}
                   for wf in MONITORED_WORKFLOWS]
    else:
        results = [check_workflow(wf) for wf in MONITORED_WORKFLOWS]

    failing_now = [r for r in results if r["conclusion"] == "failure"]

    recovered: list[str] = []
    if not args.no_recovery and not args.no_gh:
        for f in failing_now:
            if f["run_id"] and attempt_recovery(f["run_id"]):
                recovered.append(f["workflow"])

    for r in results:
        wf = r["workflow"]
        history[wf] = history.get(wf, 0) + 1 if r["conclusion"] == "failure" else 0

    db_ok = validate_db_integrity()
    healed = False
    if not db_ok and not args.no_recovery and not args.no_gh:
        healed = heal_db()

    persistent_failures = [r for r in results if history.get(r["workflow"], 0) >= FAILURE_THRESHOLD]
    alert_opened = False
    if persistent_failures and not args.no_gh:
        alert_opened = manage_alert_issue(persistent_failures)

    status["watchdog"] = {
        "component": "watchdog",
        "checked_at": _now(),
        "results": results,
        "recovered": recovered,
        "db_integrity_ok": db_ok,
        "db_healed": healed,
        "consecutive_failures": history,
        "alert_opened": alert_opened,
    }
    save_status(status)

    lines = ["## 🐕 Watchdog — continuidade & recuperação do CI/CD", ""]
    for r in results:
        mark = " → reexecutado automaticamente ✅" if r["workflow"] in recovered else ""
        lines.append(f"- `{r['workflow']}`: **{r['conclusion'] or r['status']}**{mark}")
    lines.append(
        f"- integridade de `data/db.json`: "
        + ("OK ✅" if db_ok else ("restaurada automaticamente 🩹" if healed else "FALHOU ❌ (requer intervenção manual)"))
    )
    if alert_opened:
        lines.append(f"- ⚠️ issue de alerta aberta/atualizada: `{ALERT_TITLE}`")
    text = "\n".join(lines) + "\n"
    print(text)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(text)

    if persistent_failures:
        wf_status, wf_message = 3, f"falha persistente em: {', '.join(r['workflow'] for r in persistent_failures)}"
    elif not db_ok and not healed:
        wf_status, wf_message = 2, "integridade de data/db.json falhou e não foi possível restaurar"
    elif recovered:
        wf_status, wf_message = 1, f"falha(s) transitória(s) reexecutada(s) automaticamente: {', '.join(recovered)}"
    else:
        wf_status, wf_message = contract.STATUS_SUCCESS, "tudo verde: sem falhas, db íntegro"

    contract_result = contract.build_result(
        "watchdog", wf_message, wf_status,
        attachments=["data/automation-status.json"],
    )
    contract.emit(contract_result)
    contract.merge_into_status_file(STATUS_PATH, "watchdog", contract_result)

    return 0


if __name__ == "__main__":
    sys.exit(main())
