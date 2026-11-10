"""
Golden end-to-end cases (docs/GOLDEN_TESTCASE.md). Each one runs the whole
pipeline against the demo dataset and must clear every bar on its scorecard.
A scorecard JSON is written to tests/golden/reports/ on every run.
"""
import pytest

from tests.golden.grader import print_card, run_bluecart, run_dentaldesk, write_report

pytestmark = [pytest.mark.golden, pytest.mark.integration]


def _assert(card):
    write_report(card)
    print_card(card)
    failed = [m for m in card["metrics"] if not m["passed"]]
    assert not card["hard_fails"], card["hard_fails"]
    assert not failed, failed


def test_golden_bluecart(db):
    _assert(run_bluecart())


def test_golden_dentaldesk(db):
    _assert(run_dentaldesk())


def test_golden_medireach_asks_question(db):
    """Ambiguous 'healthcare' must trigger a clarifying question, never a guess."""
    import uuid

    from app.core.context import RequestContext
    from app.core.db import tenant_tx
    from app.modules.icp.schemas import IcpBody
    from app.modules.identity.service import signup
    from app.modules.search import service as searches
    from app.core.db import as_json, execute
    from app.core.ids import new_id

    s = signup(email=f"golden-mr-{uuid.uuid4().hex[:6]}@medireach.example", password="golden-pass-1",
               full_name="G", company_name="MediReach")
    ctx = RequestContext(uuid.UUID(s["user"]["id"]), uuid.UUID(s["workspace_id"]), "owner")
    icp_id = new_id()
    body = IcpBody(summary="Supplies for healthcare providers", industries=["Healthcare"], local_ratio=0.5,
                   industry_keywords=["healthcare", "hospital", "clinic"])
    with tenant_tx(ctx.workspace_id) as conn:
        execute(conn, """INSERT INTO icp (id, workspace_id, lineage_id, version, body, source)
                         VALUES (:id, :ws, :id, 1, CAST(:b AS jsonb), '{}'::jsonb)""",
                {"id": icp_id, "ws": ctx.workspace_id, "b": as_json(body.model_dump())})
    search = searches.create(ctx, icp_id=icp_id, region_text="India", target_count=10)
    assert search["status"] == "NEEDS_INPUT" and search["question"]
    answered = searches.answer_question(ctx, uuid.UUID(search["id"]), 0)
    assert answered["status"] == "ESTIMATED"
    assert [p["path"] for p in answered["plan"]["paths"]] == ["LOCAL_BUSINESS"]
