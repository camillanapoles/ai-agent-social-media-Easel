#!/usr/bin/env python3
"""Script de validação manual (não pytest) para issue_chain.py usando mocks
de `gh`, sem precisar de um repositório real com Issues habilitado.

Roda vários cenários e verifica o contrato JSON retornado em cada um. Usado
apenas durante o desenvolvimento desta sessão; não faz parte da suíte
automática de testes do repositório.
"""
import json
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import issue_chain as ic  # noqa: E402
import gate_check as gc  # noqa: E402

PASS = []
FAIL = []


def check(name, condition, detail=""):
    """Registra o cenário em PASS ou FAIL, incluindo detalhes quando a condição é falsa."""
    if condition:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


# ---------------------------------------------------------------------------
# Cenário 1: Issues desabilitado no repo -> status 4
with mock.patch.object(ic, "issues_enabled", return_value=False):
    result = ic.advance(1, "success", "msg", None)
check("cenario1_status_4", result["status"] == 4, result)
check("cenario1_next_issue_same", result["next_issue"] == "1", result)
check("cenario1_continuity_true", result["continuity"] is True, result)

# ---------------------------------------------------------------------------
# Cenário 2: issue não encontrada -> status 5
with mock.patch.object(ic, "issues_enabled", return_value=True), \
     mock.patch.object(ic, "find_issue_by_index", return_value=None):
    result = ic.advance(1, "success", "msg", None)
check("cenario2_status_5", result["status"] == 5, result)

# ---------------------------------------------------------------------------
# Cenário 3: execução reportada como failure -> status 6, next_issue igual
with mock.patch.object(ic, "issues_enabled", return_value=True), \
     mock.patch.object(ic, "find_issue_by_index", return_value={"number": 101, "title": "[Pipeline #1] x", "state": "OPEN", "url": "u"}), \
     mock.patch.object(ic, "comment_issue", return_value=True) as m_comment:
    result = ic.advance(1, "failure", "deu ruim", None)
check("cenario3_status_6", result["status"] == 6, result)
check("cenario3_next_issue_same", result["next_issue"] == "1", result)
check("cenario3_continuity_true", result["continuity"] is True, result)
check("cenario3_comment_called", m_comment.called, "comment_issue não foi chamado")
check("cenario3_comment_uses_github_number_not_index",
      m_comment.call_args[0][0] == 101, m_comment.call_args)

# ---------------------------------------------------------------------------
# Cenário 4: success mas gate bloqueado (issue aberta) -> status 1, retry mesma issue
with mock.patch.object(ic, "issues_enabled", return_value=True), \
     mock.patch.object(ic, "find_issue_by_index", return_value={"number": 101, "title": "[Pipeline #1] x", "state": "OPEN", "url": "u"}), \
     mock.patch.object(ic, "comment_issue", return_value=True) as m_comment4, \
     mock.patch.object(gc, "fetch_blocking_issues", return_value=[{"number": 9, "title": "bug y"}]), \
     mock.patch.object(gc, "fetch_workflow_conclusions", return_value={"ci.yml": "success"}), \
     mock.patch.object(gc, "fetch_tests_passed", return_value=True):
    result = ic.advance(1, "success", "feito", None)
check("cenario4_status_1", result["status"] == 1, result)
check("cenario4_next_issue_same", result["next_issue"] == "1", result)
check("cenario4_continuity_true", result["continuity"] is True, result)
check("cenario4_comment_uses_github_number_not_index",
      m_comment4.call_args[0][0] == 101, m_comment4.call_args)

# ---------------------------------------------------------------------------
# Cenário 5: success + gate limpo -> fecha issue N, cria N+1, status 0
with mock.patch.object(ic, "issues_enabled", return_value=True), \
     mock.patch.object(ic, "find_issue_by_index", return_value={"number": 101, "title": "[Pipeline #1] x", "state": "OPEN", "url": "u"}), \
     mock.patch.object(ic, "close_issue_with_comment", return_value=True) as m_close, \
     mock.patch.object(ic, "create_next_issue", return_value=2) as m_create, \
     mock.patch.object(gc, "fetch_blocking_issues", return_value=[]), \
     mock.patch.object(gc, "fetch_workflow_conclusions", return_value={"ci.yml": "success"}), \
     mock.patch.object(gc, "fetch_tests_passed", return_value=True):
    result = ic.advance(1, "success", "feito e testado", ["report.md"])
check("cenario5_status_0", result["status"] == 0, result)
check("cenario5_next_issue_2", result["next_issue"] == "2", result)
check("cenario5_continuity_false", result["continuity"] is False, result)
check("cenario5_attachments_preserved", result["attachments"] == ["report.md"], result)
check("cenario5_close_called_with_github_number_not_index",
      m_close.call_args[0][0] == 101, m_close.call_args)
check("cenario5_create_called_with_next_index", m_create.call_args[0][0] == 2, m_create.call_args)

# ---------------------------------------------------------------------------
# Cenário 6: load_template usa arquivo quando existe, senão fallback
tmp_template_dir = ic.TEMPLATES_DIR
title, body = ic.load_template(999999)  # não deve existir
check("cenario6_fallback_title", title == "Etapa 999999", (title, body))
check("cenario6_fallback_body_mentions_path", "999999.md" in body, body)

# ---------------------------------------------------------------------------
# Cenário 7: contrato JSON sempre tem exatamente os campos exigidos pelo usuário
required_top_level = {"id", "message", "attachments", "status", "next_issue"}
check("cenario7_contract_has_required_fields", required_top_level.issubset(result.keys()), result.keys())

# ---------------------------------------------------------------------------
# Cenário 8: PipelineStore (backend OOP/SQLite) registra histórico corretamente
import tempfile  # noqa: E402

import pipeline_store  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    db_path = Path(tmp) / "pipeline.db"
    with pipeline_store.PipelineStore(db_path) as store:
        store.upsert_stage(1, 101, title="[Pipeline #1] x")
        r_fail = {"id": "x", "message": "falhou", "attachments": [], "status": 6,
                   "status_label": "execution_failed", "continuity": True, "next_issue": "1",
                   "emitted_at": "2026-01-01T00:00:00Z"}
        store.record_event(1, r_fail)
        stage_after_fail = store.get_stage(1)
        check("cenario8_stage_stays_open_on_failure", stage_after_fail["state"] == "open", stage_after_fail)

        r_ok = {"id": "x", "message": "ok", "attachments": ["a.md"], "status": 0,
                 "status_label": "success", "continuity": False, "next_issue": "2",
                 "emitted_at": "2026-01-01T00:01:00Z"}
        store.record_event(1, r_ok)
        stage_after_success = store.get_stage(1)
        check("cenario8_stage_closes_on_advance", stage_after_success["state"] == "closed", stage_after_success)
        check("cenario8_history_has_both_events", len(store.history(1)) == 2, store.history(1))
        check("cenario8_latest_event_is_success", store.latest_event(1)["status"] == 0, store.latest_event(1))

# ---------------------------------------------------------------------------
# Cenário 9: IssueChainOrchestrator (fachada OOP) delega para advance()/init_chain()
# e grava no PipelineStore, usando a IssueFactory em vez de `gh` real.
with tempfile.TemporaryDirectory() as tmp:
    fake_store = pipeline_store.PipelineStore(Path(tmp) / "pipeline.db")
    fake_factory = mock.create_autospec(ic.IssueFactory, instance=True)
    fake_factory.issues_enabled.return_value = True
    fake_factory.find.return_value = {"number": 202, "title": "[Pipeline #1] x", "state": "OPEN", "url": "u"}
    fake_factory.close_with_comment.return_value = True
    fake_factory.create.return_value = 203

    with mock.patch.object(ic, "issues_enabled", fake_factory.issues_enabled), \
         mock.patch.object(ic, "find_issue_by_index", fake_factory.find), \
         mock.patch.object(ic, "close_issue_with_comment", fake_factory.close_with_comment), \
         mock.patch.object(ic, "create_next_issue", fake_factory.create), \
         mock.patch.object(gc, "fetch_blocking_issues", return_value=[]), \
         mock.patch.object(gc, "fetch_workflow_conclusions", return_value={"ci.yml": "success"}), \
         mock.patch.object(gc, "fetch_tests_passed", return_value=True):
        orchestrator = ic.IssueChainOrchestrator(factory=fake_factory, store=fake_store)
        orch_result = orchestrator.advance(1, "success", "via orchestrator", None)
    check("cenario9_orchestrator_status_0", orch_result["status"] == 0, orch_result)
    check("cenario9_orchestrator_next_issue_3", orch_result["next_issue"] == "203", orch_result)
    check("cenario9_orchestrator_closed_github_number_not_index",
          fake_factory.close_with_comment.call_args[0][0] == 202, fake_factory.close_with_comment.call_args)
    check("cenario9_store_has_event_for_stage", len(fake_store.history(1)) == 1, fake_store.history(1))
    fake_store.close()

print(f"\n{len(PASS)} passaram, {len(FAIL)} falharam")
if FAIL:
    print("FALHAS:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
print("TODOS OS CENÁRIOS OFFLINE PASSARAM")
