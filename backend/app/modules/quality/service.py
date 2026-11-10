"""
Quality Judge - nothing reaches a customer unless the system is confident in it.

Three layers (SYSTEM_DESIGN 5.14):
  FIELD      checks on each contact value (email status, email domain = company
             domain, LinkedIn slug matches the name, phone format/country).
  ROW        hard checks: company alive, in region, persona matched, reachable,
             not already delivered to this workspace (permanent dedupe).
  REASONING  an INDEPENDENT judge re-reads every fit reason against the evidence
             it cites. Unsupported reasons are removed.

Then a calibrated confidence decides:
    >= 0.80  PASS     (or DEGRADE if a weak field was removed)  - delivered, billed
    0.60-0.80 HOLD    - review tab, not billed unless the user accepts it
    <  0.60  REJECT   - not delivered, not billed, reason recorded
Any hard-check failure is an immediate REJECT.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.core.config import load_yaml
from app.core.db import fetch_one, tenant_tx
from app.core.security import lookup_hash
from app.modules.ai.service import AIScope, judge_reasons
from app.modules.contacts.service import ContactResult
from app.modules.icp.schemas import IcpBody


@dataclass
class CheckResult:
    layer: str
    code: str
    passed: bool
    hard: bool = False
    detail: dict = field(default_factory=dict)


@dataclass
class Verdict:
    decision: str                  # pass | degrade | hold | reject
    confidence: float
    reason_codes: list[str]
    checks: list[CheckResult]
    kept_reasons: list[dict]
    identities: list[tuple[str, str]]   # (identity_kind, normalized value) for the dedupe ledger


def calibrate(raw: float) -> float:
    """
    Map a raw score to a calibrated probability of the row being correct.

    v1 ships the identity mapping. Once >= 300 labelled outcomes exist (gold set +
    feedback + bounces), `scripts/fit_calibration.py` fits isotonic regression and
    this function applies the fitted piecewise-linear curve instead (LLD 6.12).
    """
    return max(0.0, min(1.0, raw))


def row_identities(company: dict, contact: ContactResult, email_value: str | None) -> list[tuple[str, str]]:
    """Stable identity keys for the permanent per-customer dedupe ledger."""
    ids: list[tuple[str, str]] = []
    if company.get("domain"):
        ids.append(("business_domain", company["domain"]))
    if contact.linkedin:
        ids.append(("person_linkedin", contact.linkedin["url"]))
    if email_value and contact.email and contact.email["status"] == "valid":
        ids.append(("person_email", email_value))
    if contact.full_name and company.get("domain"):
        ids.append(("person_namecompany", f"{contact.full_name.lower()}|{company['domain']}"))
    return ids


def judge_row(*, workspace_id: uuid.UUID, search_id: uuid.UUID, icp: IcpBody, company: dict, path: str,
              fit: str, reasons: list[dict], evidence: dict[str, str], contact: ContactResult,
              email_value: str | None, include_previously_delivered: bool) -> Verdict:
    cfg = load_yaml("quality")
    checks: list[CheckResult] = []
    local = path in ("LOCAL_BUSINESS", "B2B2C")

    # ---------------- FIELD layer --------------------------------------------
    email_conf = 0.0
    if contact.email:
        status = contact.email["status"]
        email_conf = 0.95 if status == "valid" else 0.4
        domain_ok = bool(email_value and company.get("domain") and email_value.endswith("@" + company["domain"]))
        checks.append(CheckResult("field", "email_status", status == "valid", detail={"status": status}))
        checks.append(CheckResult("field", "email_domain_matches_company", domain_ok))
        if not domain_ok:
            email_conf = 0.0
    if contact.linkedin and contact.full_name:
        slug = contact.linkedin["url"].rsplit("/", 1)[-1].lower()
        name_ok = all(part in slug for part in contact.full_name.lower().split()[:2])
        checks.append(CheckResult("field", "linkedin_name_match", name_ok))
    phone_conf = 0.0
    if contact.phone:
        phone_conf = 0.7 if local else 0.45
        checks.append(CheckResult("field", "phone_format_valid", True, detail={"type": contact.phone["type"]}))

    # ---------------- ROW layer (hard checks) ---------------------------------
    reachable = bool(contact.email) or bool(contact.phone)
    checks.append(CheckResult("row", "company_alive", bool(company.get("alive")), hard=True))
    checks.append(CheckResult("row", "core_reachable", reachable, hard=True))
    checks.append(CheckResult("row", "persona_match", contact.persona_rank is not None or local, hard=True,
                              detail={"title": contact.title}))
    identities = row_identities(company, contact, email_value)
    duplicate = False
    if not include_previously_delivered and cfg.get("never_redeliver_to_workspace", True):
        with tenant_tx(workspace_id) as conn:
            for kind, value in identities:
                if fetch_one(conn, "SELECT 1 AS x FROM delivered_record WHERE identity_kind = :k AND identity_hmac = :h",
                             {"k": kind, "h": lookup_hash(kind, value)}):
                    duplicate = True
                    break
    checks.append(CheckResult("row", "not_delivered_to_workspace", not duplicate, hard=True))

    # Hard failures end here - no point paying for an AI judge on a row we will reject.
    failed_hard = [c.code for c in checks if c.hard and not c.passed]
    if failed_hard:
        codes = ["duplicate_delivered" if c == "not_delivered_to_workspace" else c for c in failed_hard]
        return Verdict("reject", 0.0, codes, checks, [], identities)

    # ---------------- REASONING layer (independent judge) --------------------
    verdict = judge_reasons(AIScope(workspace_id, search_id), icp, fit=fit, reasons=reasons, evidence=evidence)
    if verdict is None:
        # No judge-tier model answered: the row waits in HOLD, never delivered unjudged.
        checks.append(CheckResult("reasoning", "judge_available", False))
        return Verdict("hold", 0.0, ["judge_unavailable"], checks, reasons, identities)
    supported = [reasons[v.index] for v in verdict.reasons if v.supported == "yes" and v.index < len(reasons)]
    support_ratio = len(supported) / len(reasons) if reasons else 0.0
    checks.append(CheckResult("reasoning", "reasons_supported", len(supported) >= cfg["min_supported_reasons"],
                              detail={"supported": len(supported), "total": len(reasons)}))
    checks.append(CheckResult("reasoning", "judge_agrees_with_fit", verdict.overall_fit_agrees))

    # ---------------- Decision -------------------------------------------------
    w = cfg["weights"]
    company_conf = min(1.0, 0.6 + 0.1 * len(company.get("sources") or []) + (0.2 if company.get("alive") else 0))
    person_conf = 0.9 if contact.linkedin else (0.8 if contact.full_name else 0.3)
    contact_conf = max(email_conf, phone_conf)
    raw = (w["company"] * company_conf + w["person"] * person_conf + w["contact"] * contact_conf
           + w["reasoning"] * support_ratio + w["freshness"] * 1.0)
    confidence = round(calibrate(raw), 3)

    codes = [c.code for c in checks if not c.passed]
    if not verdict.overall_fit_agrees or len(supported) < cfg["min_supported_reasons"]:
        return Verdict("hold", confidence, codes or ["weak_reasoning"], checks, supported, identities)
    if confidence >= cfg["pass_threshold"]:
        degraded = any(not c.passed and c.layer == "field" for c in checks)
        return Verdict("degrade" if degraded else "pass", confidence, codes, checks, supported, identities)
    if confidence >= cfg["hold_threshold"]:
        return Verdict("hold", confidence, codes or ["low_confidence"], checks, supported, identities)
    return Verdict("reject", confidence, codes or ["low_confidence"], checks, supported, identities)
