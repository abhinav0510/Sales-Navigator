"""
Golden-case runner + grader.

Runs a golden case end to end through the REAL services (signup -> ICP ->
suppression -> plan -> run worker -> leads -> export -> second search) and
grades the result against the case file. Produces a scorecard dict:

    {"case": ..., "passed": bool, "metrics": [{name, value, bar, passed}], "hard_fails": [...]}

Used by tests/golden/test_golden.py (pytest) and by `python -m tests.golden.grader`
(prints the scorecard and writes tests/golden/reports/<case>_latest.json).
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.core.context import RequestContext
from app.core.db import fetch_all, fetch_one, system_tx, tenant_tx
from app.modules.compliance.service import add_suppressions
from app.modules.icp.schemas import IcpDraftRequest
from app.modules.icp.service import create_draft
from app.modules.identity.service import signup
from app.modules.leads.service import export_csv, list_for_search
from app.modules.providers.demo_data import DEMO_COMPANIES, DEMO_MAILBOXES
from app.modules.search import service as searches
from app.workers.worker import run_pending

HERE = Path(__file__).parent


def _metric(name, value, bar, passed) -> dict:
    return {"name": name, "value": value, "bar": bar, "passed": bool(passed)}


def run_bluecart() -> dict:
    case = json.loads((HERE / "case_bluecart.json").read_text())
    inp, th = case["input"], case["thresholds"]

    # --- set up a customer ----------------------------------------------------
    s = signup(email=f"golden-{uuid.uuid4().hex[:8]}@bluecart.example", password="golden-pass-1",
               full_name="Golden", company_name=inp["business_name"])
    ctx = RequestContext(uuid.UUID(s["user"]["id"]), uuid.UUID(s["workspace_id"]), "owner")
    with tenant_tx(ctx.workspace_id) as conn:
        from app.modules.billing.service import change_plan
        change_plan(conn, ctx.workspace_id, "growth")               # enough quota for two searches
    founder = DEMO_COMPANIES[inp["suppress_founder_email_of"]].people[0].real_email
    add_suppressions(ctx.workspace_id, "email", [founder])

    icp = create_draft(ctx, IcpDraftRequest(business_name=inp["business_name"], description=inp["description"],
                                            website_url=inp["website_url"], seeds=inp["seeds"]))
    search = searches.create(ctx, icp_id=uuid.UUID(icp["id"]), region_text=inp["region_text"],
                             target_count=inp["target_count"])
    plan_paths = [p["path"] for p in search["plan"]["paths"]]
    searches.run(ctx, uuid.UUID(search["id"]))
    run_pending()
    search = searches.get(ctx, uuid.UUID(search["id"]))
    sid = uuid.UUID(search["id"])

    leads = list_for_search(ctx, sid, None)
    delivered = [l for l in leads if l["decision"] in ("pass", "degrade")]
    held = {l["company"]["domain"]: l["reason_codes"] for l in leads if l["decision"] == "hold"}
    with tenant_tx(ctx.workspace_id) as conn:
        cand = {r["domain"]: r["dropped_reason"] for r in fetch_all(conn, """
            SELECT co.domain, ca.dropped_reason FROM candidate ca JOIN company co ON co.id = ca.company_id
            WHERE ca.search_id = :s""", {"s": sid})}
    metrics, hard = [], []

    # --- plan + ICP -------------------------------------------------------------
    ep, ei = case["expected_plan"], case["expected_icp"]
    metrics.append(_metric("plan.primary_path", plan_paths[0], ep["primary_path"], plan_paths[0] == ep["primary_path"]))
    metrics.append(_metric("plan.required_paths", plan_paths, ep["required_paths"],
                           all(p in plan_paths for p in ep["required_paths"])))
    metrics.append(_metric("plan.forbidden_paths_absent", plan_paths, ep["forbidden_paths"],
                           not any(p in plan_paths for p in ep["forbidden_paths"])))
    metrics.append(_metric("plan.no_clarifying_question", search["question"], None, search["question"] is None))
    metrics.append(_metric("geo.admin1", search["scopes"][0]["admin1_code"], ep["geo_admin1"],
                           search["scopes"][0]["admin1_code"] == ep["geo_admin1"]))
    body = icp["body"]
    inds = " ".join(body["industries"]).lower()
    icp_checks = [
        body["seller_mode"] == ei["seller_mode"],
        any(k in inds for k in ei["industries_must_include_any"]),
        not any(k in inds for k in ei["industries_must_not_include"]),
        set(body["target_size"]) <= set(ei["target_size_allowed"]),
        any(t in body["technologies"] for t in ei["technologies_must_include_any"]),
        any(p in body["personas"] for p in ei["personas_must_include_any"]),
        all(d in body["exclusions"] for d in ei["exclusions_must_include"]),
    ]
    metrics.append(_metric("icp.field_overlap", round(sum(icp_checks) / len(icp_checks), 2), 0.80,
                           sum(icp_checks) / len(icp_checks) >= 0.8))

    # --- leads ----------------------------------------------------------------
    d_domains = [l["company"]["domain"] for l in delivered]
    found = [d for d in case["must_find"] if d in d_domains]
    recall = len(found) / len(case["must_find"])
    metrics.append(_metric("recall_must_find", round(recall, 3), th["recall_must_find_min"],
                           recall >= th["recall_must_find_min"]))
    ok_set = set(case["must_find"]) | set(case["acceptable"])
    precision = sum(d in ok_set for d in d_domains) / len(d_domains) if d_domains else 0
    metrics.append(_metric("precision_delivered", round(precision, 3), th["precision_delivered_min"],
                           precision >= th["precision_delivered_min"]))
    dm = sum(1 for l in delivered if any(k in (l["person"]["title"] or "").lower()
                                         for k in ("founder", "head", "owner", "ceo"))) / max(len(delivered), 1)
    metrics.append(_metric("decision_maker_accuracy", round(dm, 3), th["decision_maker_accuracy_min"],
                           dm >= th["decision_maker_accuracy_min"]))
    valid = [l["contacts"]["email"]["value"] for l in delivered
             if l.get("contacts", {}).get("email", {}).get("status") == "valid"]
    rate = len(valid) / max(len(delivered), 1)
    metrics.append(_metric("email_valid_rate", round(rate, 3), th["email_valid_rate_min"],
                           rate >= th["email_valid_rate_min"]))
    wrong = [e for e in valid if e not in DEMO_MAILBOXES]
    metrics.append(_metric("wrong_valid_emails", len(wrong), th["wrong_valid_emails_max"], not wrong))
    if wrong:
        hard.append("wrong email marked valid")
    for dom, code in case["expected_hold"].items():
        metrics.append(_metric(f"hold.{dom}", held.get(dom), code, code in (held.get(dom) or [])))

    # --- negatives ------------------------------------------------------------
    neg_ok = 0
    for dom, code in case["negatives"].items():
        good = dom not in d_domains and cand.get(dom) == code
        neg_ok += good
        if dom in d_domains:
            hard.append(f"negative delivered: {dom}")
    metrics.append(_metric("negatives_rejected_with_code", f"{neg_ok}/{len(case['negatives'])}", "all",
                           neg_ok == len(case["negatives"])))

    # --- guardrails -------------------------------------------------------------
    disc = (search["quality_report"] or {}).get("discovery", {})
    tr = case["injection_traps"]
    metrics.append(_metric("injections_removed", disc.get("injections_removed"), f">= {tr['min_injections_removed']}",
                           (disc.get("injections_removed") or 0) >= tr["min_injections_removed"]))
    metrics.append(_metric("unsafe_links_blocked", disc.get("unsafe_links_blocked"),
                           f">= {tr['min_unsafe_links_blocked']}",
                           (disc.get("unsafe_links_blocked") or 0) >= tr["min_unsafe_links_blocked"]))
    bad_cites = 0
    with system_tx() as conn:
        for l in leads:
            for r in l["reasons"]:
                for eid in r["evidence_ids"]:
                    if eid in tr["forbidden_evidence_ids"]:
                        bad_cites += 1
                        continue
                    ev = fetch_one(conn, "SELECT snippet, company_id::text AS c FROM evidence WHERE id::text = :e",
                                   {"e": eid})
                    if ev is None or ev["c"] != l["company"]["id"] or \
                            any(t in ev["snippet"].lower() for t in tr["forbidden_text_in_cited_evidence"]):
                        bad_cites += 1
    metrics.append(_metric("evidence_grounding_bad_cites", bad_cites, 0, bad_cites == 0))
    if bad_cites:
        hard.append("injection or invalid citation changed the output")
    all_contact_values = {c["value"] for l in leads for c in l.get("contacts", {}).values()}
    leaked = [v for v in tr["forbidden_contacts"] + [founder] if v in all_contact_values]
    csv_text = export_csv(ctx, sid)
    leaked += [founder] if founder in csv_text else []
    metrics.append(_metric("suppressed_or_forbidden_contacts_in_output", len(leaked), 0, not leaked))
    if leaked:
        hard.append("suppressed contact in output")

    # --- cost -------------------------------------------------------------------
    cpl = search["quality_report"].get("cost_per_delivered_lead_paise")
    metrics.append(_metric("cost_per_delivered_lead_paise", cpl, th["cost_per_delivered_lead_paise_max"],
                           cpl is not None and cpl <= th["cost_per_delivered_lead_paise_max"]))

    # --- no duplicates: a second, overlapping search in the same workspace ---------
    second = searches.create(ctx, icp_id=uuid.UUID(icp["id"]), region_text="Pune, Mumbai", target_count=10)
    searches.run(ctx, uuid.UUID(second["id"]))
    run_pending()
    second = searches.get(ctx, uuid.UUID(second["id"]))
    second_delivered = [l for l in list_for_search(ctx, uuid.UUID(second["id"]), "delivered")]
    dupes = [l["company"]["domain"] for l in second_delivered if l["company"]["domain"] in d_domains]
    # The second search must genuinely run (no vacuous pass) and re-find the first search's companies...
    refound = (second["quality_report"] or {}).get("discovery", {}).get("dropped", {}).get("duplicate_delivered", 0)
    metrics.append(_metric("second_search_completed", second["status"], "COMPLETED", second["status"] == "COMPLETED"))
    metrics.append(_metric("second_search_refound_delivered_companies", refound, "> 0", refound > 0))
    # ...and still deliver none of them again.
    metrics.append(_metric("duplicates_on_second_search", len(dupes), th["duplicates_on_second_search_max"],
                           second["status"] == "COMPLETED" and not dupes))
    if dupes:
        hard.append("record delivered twice")

    passed = all(m["passed"] for m in metrics) and not hard
    return {
        "case": case["case_id"], "run_at": datetime.now(UTC).isoformat(), "passed": passed, "hard_fails": hard,
        "summary": {"status": search["status"], "delivered": search["delivered_count"], "held": search["held_count"],
                    "rejected": search["rejected_count"], "target": search["target_count"],
                    "spent_paise": search["spent_paise"], "second_search_delivered": len(second_delivered)},
        "metrics": metrics, "quality_report": search["quality_report"],
    }


def run_dentaldesk() -> dict:
    """LOCAL_BUSINESS path: clinic software sold to Pune dental clinics within 25 km."""
    s = signup(email=f"golden-dd-{uuid.uuid4().hex[:8]}@dentaldesk.example", password="golden-pass-1",
               full_name="Golden", company_name="DentalDesk")
    ctx = RequestContext(uuid.UUID(s["user"]["id"]), uuid.UUID(s["workspace_id"]), "owner")
    icp = create_draft(ctx, IcpDraftRequest(business_name="DentalDesk",
                                            description="Appointment and billing software for dental clinics."))
    search = searches.create(ctx, icp_id=uuid.UUID(icp["id"]), region_text="within 25 km of Pune", target_count=5)
    paths = [p["path"] for p in search["plan"]["paths"]]
    searches.run(ctx, uuid.UUID(search["id"]))
    run_pending()
    search = searches.get(ctx, uuid.UUID(search["id"]))
    delivered = list_for_search(ctx, uuid.UUID(search["id"]), "delivered")
    clinic_domains = {f"{c.key}.example" for c in DEMO_COMPANIES.values() if c.fit == "clinic"}
    metrics = [
        _metric("plan.primary_path", paths[0], "LOCAL_BUSINESS", paths[0] == "LOCAL_BUSINESS"),
        _metric("geo.radius", search["scopes"][0]["level"], "radius", search["scopes"][0]["level"] == "radius"),
        _metric("delivered", len(delivered), 5, len(delivered) == 5),
        _metric("all_delivered_are_clinics", all(l["company"]["domain"] in clinic_domains for l in delivered), True,
                bool(delivered) and all(l["company"]["domain"] in clinic_domains for l in delivered)),
        # `bool(delivered) and ...` so an empty result can never pass these checks vacuously.
        _metric("has_business_phone", sum("phone" in l.get("contacts", {}) for l in delivered), 5,
                bool(delivered) and all("phone" in l.get("contacts", {}) for l in delivered)),
        _metric("owner_identified", sum((l["person"] or {}).get("name") not in (None, "Owner") for l in delivered), 5,
                bool(delivered) and all((l["person"] or {}).get("name") not in (None, "Owner") for l in delivered)),
    ]
    return {"case": "dentaldesk", "run_at": datetime.now(UTC).isoformat(),
            "passed": all(m["passed"] for m in metrics), "hard_fails": [],
            "summary": {"status": search["status"], "delivered": search["delivered_count"],
                        "held": search["held_count"], "rejected": search["rejected_count"]},
            "metrics": metrics}


def write_report(card: dict) -> Path:
    out = HERE / "reports"
    out.mkdir(exist_ok=True)
    path = out / f"{card['case']}_latest.json"
    path.write_text(json.dumps(card, indent=2, default=str))
    return path


def print_card(card: dict) -> None:
    print(f"\nGOLDEN CASE: {card['case']}")
    for m in card["metrics"]:
        print(f"  {m['name']:<45} {str(m['value']):<28} bar={str(m['bar']):<22} {'PASS' if m['passed'] else 'FAIL'}")
    print(f"  HARD FAILS: {card['hard_fails'] or 'none'}")
    print(f"  CASE RESULT: {'PASS' if card['passed'] else 'FAIL'}")


if __name__ == "__main__":
    import os

    import tests.conftest  # noqa: F401  (points the app at the TEST database)
    from app.cli import reset_db

    assert "test" in os.environ["SN_DATABASE_URL"]
    reset_db()
    for runner in (run_bluecart, run_dentaldesk):
        card = runner()
        print_card(card)
        print("  report ->", write_report(card))
