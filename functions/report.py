#!/usr/bin/env python3
"""Function serverless-like: relatório consolidado da automação.

Junta `data/db.json` + `data/stats.json` + `data/automation-status.json` em
um relatório Markdown. Serve tanto para o `GITHUB_STEP_SUMMARY` quanto para
alimentar o painel (dashboard) com um texto humano pronto, sem precisar
recalcular nada no navegador.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import action_contract as contract  # noqa: E402

DATA_DIR = REPO_ROOT / "data"
DB_PATH = DATA_DIR / "db.json"
STATS_PATH = DATA_DIR / "stats.json"
STATUS_PATH = DATA_DIR / "automation-status.json"
REPORT_PATH = DATA_DIR / "report.md"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def main() -> int:
    db = _read_json(DB_PATH)
    stats = _read_json(STATS_PATH)
    status = _read_json(STATUS_PATH)

    lines = [
        "# Relatório de automação — Easel",
        "",
        f"_Gerado em {_now()} pela function `report.py`_",
        "",
        f"- Registros no banco: **{len(db.get('records', []))}**",
        f"- Por status: `{json.dumps(stats.get('by_status', {}), ensure_ascii=False)}`",
        f"- Por canal: `{json.dumps(stats.get('by_channel', {}), ensure_ascii=False)}`",
        f"- Cache do indexador: `{'HIT' if stats.get('cache_hit') else 'MISS'}`",
        "",
        "## Últimos componentes executados",
        "",
    ]
    for key, val in status.items():
        lines.append(f"- **{key}** — atualizado em `{val.get('updated_at', '?')}` "
                      f"(run `{val.get('run_id', '?')}`)")

    text = "\n".join(lines) + "\n"
    REPORT_PATH.write_text(text, encoding="utf-8")
    print(text)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write("## 📝 Function: report\n\n" + text)

    status["report"] = {
        "component": "function:report",
        "updated_at": _now(),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
    }
    STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    contract_result = contract.build_result(
        "function-report", "relatório consolidado gerado",
        contract.STATUS_SUCCESS, attachments=["data/report.md"],
    )
    contract.emit(contract_result)
    contract.merge_into_status_file(STATUS_PATH, "report", contract_result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
