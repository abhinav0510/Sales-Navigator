"""Search Planner rules and the offline AI scorer/judge."""
from app.modules.ai import offline
from app.modules.icp.schemas import IcpBody
from app.modules.planner.service import plan_search

ALL = {"web_search", "website", "place", "smtp"}


def _d2c_icp() -> IcpBody:
    return offline.build_icp("BlueCart", "SaaS for D2C brands selling on Shopify and Amazon", "", [])


def test_d2c_seller_gets_b2b_plus_lookalike():
    plan = plan_search(_d2c_icp(), seeds=["a.example", "b.example", "c.example"], available_sources=ALL,
                       target_count=20)
    paths = [p["path"] for p in plan.paths]
    assert paths[0] == "B2B_COMPANY"
    assert "LOOKALIKE" in paths and "LOCAL_BUSINESS" not in paths
    assert plan.question is None and plan.confidence >= 0.6
    assert abs(sum(p["share"] for p in plan.paths) - 1.0) < 0.02


def test_clinic_seller_gets_local_business():
    icp = offline.build_icp("DentalDesk", "Appointment software for dental clinics", "", [])
    plan = plan_search(icp, seeds=[], available_sources=ALL, target_count=5)
    assert plan.paths[0]["path"] == "LOCAL_BUSINESS"


def test_ambiguous_healthcare_asks_question():
    icp = IcpBody(summary="We sell to healthcare providers", industries=["Healthcare"], local_ratio=0.5)
    plan = plan_search(icp, seeds=[], available_sources=ALL, target_count=10)
    assert plan.question is not None and plan.confidence < 0.6
    assert {o["path"] for o in plan.question["options"]} == {"LOCAL_BUSINESS", "B2B_COMPANY"}


def test_b2c_without_contacts_is_audience_only():
    icp = IcpBody(seller_mode="b2c", wants_contacts=False)
    plan = plan_search(icp, seeds=[], available_sources=ALL, target_count=10)
    assert [p["path"] for p in plan.paths] == ["AUDIENCE_INSIGHT"]


def test_b2c_with_contacts_targets_businesses_not_consumers():
    icp = IcpBody(seller_mode="b2c", wants_contacts=True)
    plan = plan_search(icp, seeds=[], available_sources=ALL, target_count=10)
    assert plan.paths[0]["path"] == "B2B2C"


def test_local_path_dropped_without_place_source():
    icp = offline.build_icp("DentalDesk", "Appointment software for dental clinics", "", [])
    plan = plan_search(icp, seeds=[], available_sources={"web_search", "website"}, target_count=5)
    assert "LOCAL_BUSINESS" not in [p["path"] for p in plan.paths]
    assert any("Skipped LOCAL_BUSINESS" in r for r in plan.rationale)


def test_forced_path_wins():
    plan = plan_search(_d2c_icp(), seeds=[], available_sources=ALL, target_count=5, forced_path="LOCAL_BUSINESS")
    assert [p["path"] for p in plan.paths] == ["LOCAL_BUSINESS"] and plan.decided_by == "user"


def _card(snippets):
    return {"company_id": "c1", "size_band": "smb",
            "evidence": [{"id": f"e{i}", "kind": "website", "snippet": s} for i, s in enumerate(snippets)]}


def test_offline_fit_strong_with_citations():
    icp = _d2c_icp()
    j = offline.score_fit(icp, _card([
        "A direct-to-consumer skincare brand. We sell online and ship orders across India.",
        "Online store runs on Shopify.",
        "Items can oversell before inventory syncs across marketplaces.",
        "We're hiring: Operations Executive.",
    ]))
    assert j.fit == "strong" and j.score_0_40 >= 32
    assert all(r.evidence_ids for r in j.reasons)


def test_offline_fit_mismatch():
    j = offline.score_fit(_d2c_icp(), _card(["A business-to-business fabric wholesale company with bulk contracts."]))
    assert j.fit == "no" and j.score_0_40 == 0


def test_offline_judge_flags_unsupported_reason():
    verdict = offline.judge("strong",
                            [{"text": "Currently hiring for operations", "evidence_ids": ["e1"]}],
                            {"e1": "We sell cookies."})
    assert verdict.reasons[0].supported != "yes"
    assert verdict.overall_fit_agrees is False
