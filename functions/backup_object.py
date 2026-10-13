#!/usr/bin/env python3
"""Function serverless-like: gera um "objeto" de backup para object storage.

O GitHub não nos dá um bucket S3/GCS dentro do sandbox, mas o próprio GitHub
já oferece dois mecanismos nativos de armazenamento de objetos binários
versionados por execução:

  1. **Actions Artifacts** — anexados à run, expiram (padrão 90 dias), bons
     para auditoria de curto/médio prazo de cada pipeline.
  2. **Release assets** — presos a uma tag (`data-store`), não expiram,
     funcionam como um "bucket" simples e permanente dentro do repositório.

Esta function empacota `data/` inteiro em um `.zip` com timestamp — esse zip
é o "objeto" que o workflow sobe como artifact e também publica como asset da
release `data-store` (ver `.github/workflows/crud-data.yml` e
`functions.yml`).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import action_contract as contract  # noqa: E402

DATA_DIR = REPO_ROOT / "data"
OBJECTS_DIR = REPO_ROOT / "dist-objects"
STATUS_PATH = DATA_DIR / "automation-status.json"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    OBJECTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    object_name = f"easel-data-backup-{stamp}.zip"
    object_path = OBJECTS_DIR / object_name

    with zipfile.ZipFile(object_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in DATA_DIR.rglob("*"):
            if path.is_file():
                zf.write(path, arcname=path.relative_to(DATA_DIR.parent))

    size_bytes = object_path.stat().st_size

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    text = (
        "## 📦 Function: backup_object (object storage)\n\n"
        f"- **Objeto gerado:** `{object_name}`\n"
        f"- **Tamanho:** {size_bytes} bytes\n"
        "- **Destinos:** Actions Artifact (curto prazo) + GitHub Release "
        "`data-store` (permanente)\n"
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
    existing["backup_object"] = {
        "component": "function:backup_object",
        "object_name": object_name,
        "size_bytes": size_bytes,
        "updated_at": _now(),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
    }
    STATUS_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Caminho exposto para o próximo step do workflow via GITHUB_OUTPUT.
    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a", encoding="utf-8") as fh:
            fh.write(f"object_name={object_name}\n")
            fh.write(f"object_path={object_path}\n")

    contract_result = contract.build_result(
        "function-backup-object",
        f"objeto de backup gerado: {object_name} ({size_bytes} bytes)",
        contract.STATUS_SUCCESS, attachments=[str(object_path.relative_to(REPO_ROOT))],
    )
    contract.emit(contract_result)
    contract.merge_into_status_file(STATUS_PATH, "backup_object", contract_result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
