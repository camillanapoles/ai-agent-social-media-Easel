#!/usr/bin/env python3
"""Function serverless-like: indexador de estatísticas com cache.

Lê `data/db.json`, calcula agregados (contagem por status/canal) e grava o
resultado em `data/stats.json`. O ponto central desta function é demonstrar
**cache entre execuções do workflow**: o resultado é persistido em
`.cache/stats/<hash-do-db>.json`, e esse diretório é restaurado via
`actions/cache` antes do job rodar. Se o conteúdo de `data/db.json` não mudou
desde a última execução, o hash bate e a function reaproveita o resultado
("cache hit") em vez de recalcular — o que fica visível tanto no log quanto
em `data/automation-status.json`.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import action_contract as contract  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "db.json"
STATS_PATH = REPO_ROOT / "data" / "stats.json"
STATUS_PATH = REPO_ROOT / "data" / "automation-status.json"
CACHE_DIR = REPO_ROOT / ".cache" / "stats"


def _now() -> str:
    """Retorna o instante atual em UTC no formato ISO 8601 com sufixo Z."""
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash_db(raw: bytes) -> str:
    """Retorna os primeiros 16 caracteres do SHA-256 dos bytes do banco."""
    return hashlib.sha256(raw).hexdigest()[:16]


def compute_stats(db: dict) -> dict:
    """Conta registros por status e canal, incluindo o total e o instante de geração."""
    by_status: dict[str, int] = {}
    by_channel: dict[str, int] = {}
    for r in db.get("records", []):
        by_status[r.get("status", "unknown")] = by_status.get(r.get("status", "unknown"), 0) + 1
        by_channel[r.get("channel", "unknown")] = by_channel.get(r.get("channel", "unknown"), 0) + 1
    return {
        "total_records": len(db.get("records", [])),
        "by_status": by_status,
        "by_channel": by_channel,
        "generated_at": _now(),
    }


def main() -> int:
    """Reutiliza ou calcula estatísticas pelo hash do banco e grava os resultados e o status."""
    raw = DB_PATH.read_bytes() if DB_PATH.exists() else b"{}"
    digest = _hash_db(raw)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{digest}.json"

    cache_hit = cache_file.exists()
    if cache_hit:
        stats = json.loads(cache_file.read_text(encoding="utf-8"))
        stats["cache_hit"] = True
        print(f"[cache] HIT para digest={digest} — reaproveitando stats sem recalcular")
    else:
        db = json.loads(raw.decode("utf-8") or "{}")
        stats = compute_stats(db)
        stats["cache_hit"] = False
        stats["digest"] = digest
        cache_file.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[cache] MISS para digest={digest} — calculado e salvo em cache")

    STATS_PATH.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    text = (
        "## 📊 Function: stats_indexer\n\n"
        f"- **Cache:** {'HIT ✅ (reaproveitado)' if cache_hit else 'MISS 🧮 (recalculado e cacheado)'}\n"
        f"- **Digest do db.json:** `{digest}`\n"
        f"- **Total de registros:** {stats['total_records']}\n\n"
        "```json\n" + json.dumps(stats, ensure_ascii=False, indent=2) + "\n```\n"
    )
    print(text)
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(text)

    existing = {}
    if STATUS_PATH.exists():
        try:
            existing = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    existing["stats_indexer"] = {
        "component": "function:stats_indexer",
        "cache_hit": cache_hit,
        "digest": digest,
        "stats": stats,
        "updated_at": _now(),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
    }
    STATUS_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    contract_result = contract.build_result(
        "function-stats-indexer",
        f"stats recalculados ({'cache hit' if cache_hit else 'cache miss'}), digest={digest}",
        contract.STATUS_SUCCESS, attachments=["data/stats.json"],
    )
    contract.emit(contract_result)
    contract.merge_into_status_file(STATUS_PATH, "stats_indexer", contract_result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
