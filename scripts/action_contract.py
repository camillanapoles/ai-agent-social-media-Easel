#!/usr/bin/env python3
"""Contrato padrão de retorno para as actions do pipeline (estilo BPMN gate).

Toda action do fluxo de continuidade (`gate_check.py`, `issue_chain.py`, e as
actions de dados retrofitadas: `scripts/db_crud.py`, `functions/*.py`,
`scripts/watchdog.py`) emite o MESMO formato de objeto, para que o próximo
"gate" do pipeline (seja ele o GitHub Actions, o painel, ou uma issue) saiba
exatamente como interpretar o resultado sem precisar conhecer os detalhes de
cada action:

    {
      "id": "<string>              identificador da action/execução",
      "message": "<string>         mensagem humana ou log",
      "attachments": [...] | [],   anexos gerados (paths/filenames), se houver
      "status": 0 | 1 | 2 | ...,   0 = sucesso; 1, 2, ... = tipos de erro
      "status_label": "<string>",  rótulo legível do código de status
      "continuity": true | false,  true = fluxo deve continuar monitorando/
                                    retentando (gate ainda não está positivo);
                                    false = gate positivo, pode avançar
      "next_issue": "<string|null>" mesma issue (retry) ou a próxima indexada
                                    (se sucesso testado)
      "emitted_at": "<ISO 8601>"
    }

`status=0` é sempre "sucesso" (gate positivo). Qualquer valor >= 1 é um tipo
de erro distinto — os scripts definem suas próprias constantes de erro
(STATUS_ERROR_*) mas todas seguem a mesma convenção de "0 é o único feliz".
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

STATUS_SUCCESS = 0

STATUS_LABELS: dict[int, str] = {
    0: "success",
}


def register_status(code: int, label: str) -> None:
    """Permite que cada script registre seus próprios códigos de erro com um
    rótulo legível, mantendo um único vocabulário compartilhado no log/summary."""
    STATUS_LABELS[code] = label


def _now() -> str:
    """Retorna o instante atual em UTC no formato ISO 8601 com sufixo Z."""
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_result(
    id: str,
    message: str,
    status: int,
    *,
    next_issue: str | int | None = None,
    attachments: list[str] | None = None,
    continuity: bool | None = None,
) -> dict[str, Any]:
    """Monta o objeto de retorno padrão. Se `continuity` não for informado,
    é inferido pela convenção: status==0 → continuity=False (gate positivo,
    pode avançar); status!=0 → continuity=True (ainda não está positivo,
    o fluxo deve continuar monitorando/retentando)."""
    if continuity is None:
        continuity = status != STATUS_SUCCESS
    return {
        "id": id,
        "message": message,
        "attachments": attachments or [],
        "status": status,
        "status_label": STATUS_LABELS.get(status, f"error_type_{status}"),
        "continuity": continuity,
        "next_issue": str(next_issue) if next_issue is not None else None,
        "emitted_at": _now(),
    }


def emit(
    result: dict[str, Any],
    *,
    to_summary: bool = True,
    to_output: bool = True,
    to_file: str | Path | None = None,
) -> dict[str, Any]:
    """Imprime o contrato (stdout), opcionalmente grava no Job Summary, nos
    outputs do step (`GITHUB_OUTPUT`) e/ou em um arquivo. Retorna o próprio
    `result` para permitir `return emit(build_result(...))` nos scripts."""
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)

    if to_summary:
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(f"\n### 🔗 Action contract — `{result['id']}`\n\n```json\n{text}\n```\n")

    if to_output:
        gh_output = os.environ.get("GITHUB_OUTPUT")
        if gh_output:
            with open(gh_output, "a", encoding="utf-8") as fh:
                fh.write(f"status={result['status']}\n")
                fh.write(f"continuity={'true' if result['continuity'] else 'false'}\n")
                fh.write(f"next_issue={result['next_issue'] or ''}\n")
                # Delimitador heredoc para permitir JSON multilinha como output.
                fh.write("result<<ACTION_CONTRACT_EOF\n")
                fh.write(json.dumps(result, ensure_ascii=False))
                fh.write("\nACTION_CONTRACT_EOF\n")

    if to_file:
        Path(to_file).write_text(text + "\n", encoding="utf-8")

    return result


def merge_into_status_file(status_path: str | Path, key: str, result: dict[str, Any]) -> None:
    """Mescla `result` em `data/automation-status.json` sob `contracts.<key>`,
    sem apagar as outras chaves já existentes no arquivo (ex.: o resumo
    específico que cada script já grava sob sua própria chave de topo)."""
    status_path = Path(status_path)
    existing: dict[str, Any] = {}
    if status_path.exists():
        try:
            existing = json.loads(status_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    contracts = existing.setdefault("contracts", {})
    contracts[key] = result
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
