#!/usr/bin/env python3
"""Backend orientado a objetos, sobre SQLite, para o histórico do pipeline de
issues encadeadas (`[Pipeline #N]`).

O GitHub Issues continua sendo a representação "oficial" (visível ao usuário)
de cada etapa do pipeline — este módulo NÃO o substitui. Ele existe para dar
ao sistema um **backend local, consultável offline, orientado a objetos**
(classe `PipelineStore`) que grava o histórico completo de todo contrato
(`action_contract`) emitido para cada etapa indexada, em
`data/pipeline.db` (SQLite, arquivo único, versionável como os outros
arquivos de `data/`). Isso dá três coisas que o GitHub sozinho não dá de
graça: (1) auditoria local completa mesmo se a issue for editada/apagada,
(2) consulta determinística sem rate limit de API, (3) uma base para
qualquer outra ferramenta (CLI, testes, dashboard) ler o estado do pipeline
sem depender de `gh`.

Uso via CLI:
    python scripts/pipeline_store.py --list
    python scripts/pipeline_store.py --history 1
    python scripts/pipeline_store.py --stage 1
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "pipeline.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS stages (
    index_number INTEGER PRIMARY KEY,
    issue_number INTEGER,
    title TEXT,
    state TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    index_number INTEGER NOT NULL,
    action_id TEXT NOT NULL,
    message TEXT NOT NULL,
    attachments TEXT NOT NULL,
    status INTEGER NOT NULL,
    status_label TEXT NOT NULL,
    continuity INTEGER NOT NULL,
    next_issue TEXT,
    emitted_at TEXT NOT NULL,
    FOREIGN KEY (index_number) REFERENCES stages (index_number)
);
"""


def _now() -> str:
    """Retorna o instante atual em UTC no formato ISO 8601 com sufixo Z."""
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PipelineStore:
    """Backend orientado a objetos sobre um arquivo SQLite. Cada instância é
    uma conexão; use como context manager (`with PipelineStore(path) as s:`)
    ou chame `.close()` manualmente."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        """Abre o banco SQLite no caminho informado e cria as tabelas ausentes."""
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def __enter__(self) -> "PipelineStore":
        """Retorna esta instância para uso como context manager."""
        return self

    def __exit__(self, *_exc: Any) -> None:
        """Fecha a conexão ao sair do bloco with, inclusive em caso de exceção."""
        self.close()

    def close(self) -> None:
        """Fecha a conexão SQLite desta instância."""
        self._conn.close()

    # -- escrita -----------------------------------------------------------
    def upsert_stage(
        self, index_number: int, issue_number: int | None = None, title: str | None = None,
        state: str = "open",
    ) -> None:
        """Cria ou atualiza a etapa `index_number`. `issue_number`/`title`
        só sobrescrevem o valor existente quando informados (não-`None`) —
        isso permite chamadas parciais (ex.: só mudar `state`) sem apagar
        dados já conhecidos da etapa."""
        now = _now()
        row = self._conn.execute(
            "SELECT * FROM stages WHERE index_number = ?", (index_number,)
        ).fetchone()
        if row:
            final_issue_number = issue_number if issue_number is not None else row["issue_number"]
            final_title = title if title is not None else row["title"]
            self._conn.execute(
                "UPDATE stages SET issue_number = ?, title = ?, state = ?, updated_at = ? "
                "WHERE index_number = ?",
                (final_issue_number, final_title, state, now, index_number),
            )
        else:
            self._conn.execute(
                "INSERT INTO stages (index_number, issue_number, title, state, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (index_number, issue_number, title or "", state, now, now),
            )
        self._conn.commit()

    def record_event(self, index_number: int, result: dict[str, Any]) -> None:
        """Registra um contrato (`action_contract.build_result(...)`) no
        histórico da etapa `index_number`. Também atualiza `stages.state`
        para `closed` quando o contrato sinaliza avanço (status 0 e
        next_issue diferente do index atual)."""
        self._conn.execute(
            "INSERT INTO events (index_number, action_id, message, attachments, status, "
            "status_label, continuity, next_issue, emitted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                index_number,
                result.get("id", ""),
                result.get("message", ""),
                json.dumps(result.get("attachments") or [], ensure_ascii=False),
                int(result.get("status", 0)),
                result.get("status_label", ""),
                1 if result.get("continuity") else 0,
                str(result.get("next_issue")) if result.get("next_issue") is not None else None,
                result.get("emitted_at", _now()),
            ),
        )
        self._conn.commit()

        advanced = (
            result.get("status") == 0
            and result.get("next_issue") is not None
            and str(result.get("next_issue")) != str(index_number)
        )
        if advanced:
            self.upsert_stage(index_number, None, state="closed")

    # -- leitura -------------------------------------------------------------
    def get_stage(self, index_number: int) -> dict[str, Any] | None:
        """Retorna a etapa pelo índice, ou None quando não estiver registrada."""
        row = self._conn.execute(
            "SELECT * FROM stages WHERE index_number = ?", (index_number,)
        ).fetchone()
        return dict(row) if row else None

    def history(self, index_number: int) -> list[dict[str, Any]]:
        """Retorna os eventos da etapa em ordem crescente de ID, com anexos em JSON textual."""
        rows = self._conn.execute(
            "SELECT * FROM events WHERE index_number = ? ORDER BY id ASC", (index_number,)
        ).fetchall()
        return [dict(r) for r in rows]

    def all_stages(self) -> list[dict[str, Any]]:
        """Retorna todas as etapas ordenadas pelo índice do pipeline."""
        rows = self._conn.execute("SELECT * FROM stages ORDER BY index_number ASC").fetchall()
        return [dict(r) for r in rows]

    def latest_event(self, index_number: int) -> dict[str, Any] | None:
        """Retorna o evento de maior ID da etapa, ou None quando não houver eventos."""
        row = self._conn.execute(
            "SELECT * FROM events WHERE index_number = ? ORDER BY id DESC LIMIT 1", (index_number,)
        ).fetchone()
        return dict(row) if row else None


def main(argv: list[str] | None = None) -> int:
    """Consulta etapas ou histórico pela CLI e imprime o resultado como JSON."""
    parser = argparse.ArgumentParser(description="Consulta o backend SQLite do pipeline de issues")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="lista todas as etapas conhecidas")
    group.add_argument("--history", type=int, metavar="N", help="histórico de eventos da etapa N")
    group.add_argument("--stage", type=int, metavar="N", help="estado atual da etapa N")
    args = parser.parse_args(argv)

    with PipelineStore(args.db) as store:
        if args.list:
            print(json.dumps(store.all_stages(), ensure_ascii=False, indent=2))
        elif args.history is not None:
            print(json.dumps(store.history(args.history), ensure_ascii=False, indent=2))
        elif args.stage is not None:
            print(json.dumps(store.get_stage(args.stage), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
