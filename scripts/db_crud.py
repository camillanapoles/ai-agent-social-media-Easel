#!/usr/bin/env python3
"""Backend CRUD mínimo e auditável para o painel de automação do Easel.

Este script é o "backend" acionado pelo workflow `crud-data.yml`. Ele trata o
arquivo versionado `data/db.json` como um pequeno banco de dados de
documentos, suportando as quatro operações clássicas de CRUD:

    list    -> lê e retorna todos os registros (Read)
    read    -> lê um registro por id (Read)
    create  -> cria um novo registro (Create)
    update  -> atualiza um registro existente (Update)
    delete  -> remove um registro (Delete)

Cada execução:
  * é idempotente e determinística (sem estado oculto fora do próprio repo);
  * grava um resumo legível em `$GITHUB_STEP_SUMMARY` (visível direto na aba
    Actions, sem precisar baixar logs);
  * atualiza `data/automation-status.json`, que é o "contrato" lido pelo
    painel estático (GitHub Pages) via `raw.githubusercontent.com` — assim o
    dashboard mostra dados reais sem precisar de um servidor rodando.

Uso:
    python scripts/db_crud.py list
    python scripts/db_crud.py create --title "Novo post" --status draft --channel blog
    python scripts/db_crud.py read --id 2
    python scripts/db_crud.py update --id 2 --status published
    python scripts/db_crud.py delete --id 2
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "db.json"
STATUS_PATH = REPO_ROOT / "data" / "automation-status.json"

VALID_STATUSES = {"draft", "scheduled", "published", "archived"}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_db() -> dict:
    if not DB_PATH.exists():
        return {"version": 1, "updated_at": _now(), "records": []}
    return json.loads(DB_PATH.read_text(encoding="utf-8"))


def save_db(db: dict) -> None:
    db["updated_at"] = _now()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DB_PATH.write_text(json.dumps(db, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def next_id(db: dict) -> int:
    ids = [r["id"] for r in db["records"]]
    return (max(ids) + 1) if ids else 1


def op_list(db: dict, _args) -> dict:
    return {"op": "list", "count": len(db["records"]), "records": db["records"]}


def op_read(db: dict, args) -> dict:
    rec = next((r for r in db["records"] if str(r["id"]) == str(args.id)), None)
    if rec is None:
        raise SystemExit(f"registro id={args.id} não encontrado")
    return {"op": "read", "record": rec}


def op_create(db: dict, args) -> dict:
    if not args.title:
        raise SystemExit("--title é obrigatório para create")
    if args.status and args.status not in VALID_STATUSES:
        raise SystemExit(f"status inválido: {args.status} (use um de {sorted(VALID_STATUSES)})")
    rec = {
        "id": next_id(db),
        "title": args.title,
        "status": args.status or "draft",
        "channel": args.channel or "geral",
        "created_at": _now(),
        "updated_at": _now(),
    }
    db["records"].append(rec)
    return {"op": "create", "record": rec}


def op_update(db: dict, args) -> dict:
    rec = next((r for r in db["records"] if str(r["id"]) == str(args.id)), None)
    if rec is None:
        raise SystemExit(f"registro id={args.id} não encontrado")
    if args.title:
        rec["title"] = args.title
    if args.status:
        if args.status not in VALID_STATUSES:
            raise SystemExit(f"status inválido: {args.status}")
        rec["status"] = args.status
    if args.channel:
        rec["channel"] = args.channel
    rec["updated_at"] = _now()
    return {"op": "update", "record": rec}


def op_delete(db: dict, args) -> dict:
    before = len(db["records"])
    db["records"] = [r for r in db["records"] if str(r["id"]) != str(args.id)]
    if len(db["records"]) == before:
        raise SystemExit(f"registro id={args.id} não encontrado")
    return {"op": "delete", "deleted_id": args.id}


OPS = {
    "list": op_list,
    "read": op_read,
    "create": op_create,
    "update": op_update,
    "delete": op_delete,
}


def write_summary(result: dict, db: dict) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    lines = [
        "## 🗄️ Backend CRUD — resultado",
        "",
        f"- **Operação:** `{result['op']}`",
        f"- **Registros no banco:** {len(db['records'])}",
        f"- **Executado em:** {_now()}",
        f"- **Run:** {os.environ.get('GITHUB_RUN_ID', 'local')} "
        f"(#{os.environ.get('GITHUB_RUN_NUMBER', '?')})",
        f"- **Ator:** {os.environ.get('GITHUB_ACTOR', 'local')}",
        "",
        "```json",
        json.dumps(result, ensure_ascii=False, indent=2),
        "```",
    ]
    text = "\n".join(lines) + "\n"
    print(text)
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(text)


def write_status(result: dict, db: dict) -> None:
    counts: dict[str, int] = {}
    for r in db["records"]:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    status = {
        "component": "crud-data",
        "last_operation": result["op"],
        "record_count": len(db["records"]),
        "status_breakdown": counts,
        "updated_at": _now(),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_number": os.environ.get("GITHUB_RUN_NUMBER"),
        "actor": os.environ.get("GITHUB_ACTOR"),
        "workflow": os.environ.get("GITHUB_WORKFLOW"),
        "sha": os.environ.get("GITHUB_SHA"),
    }
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if STATUS_PATH.exists():
        try:
            existing = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    existing["crud"] = status
    STATUS_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CRUD de dados do painel de automação Easel")
    parser.add_argument("operation", choices=sorted(OPS))
    parser.add_argument("--id")
    parser.add_argument("--title")
    parser.add_argument("--status")
    parser.add_argument("--channel")
    args = parser.parse_args(argv)

    db = load_db()
    result = OPS[args.operation](db, args)
    if args.operation in {"create", "update", "delete"}:
        save_db(db)
    write_summary(result, db)
    write_status(result, db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
