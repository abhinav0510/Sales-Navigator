"""
Offline AI - deterministic, rules-based stand-ins for every AI task.

Used when SN_LLM_MODE=offline (the default): local development, CI, and the
golden test. It produces the SAME structured outputs as Claude mode, so every
downstream stage (citation enforcement, Quality Judge, delivery) runs unchanged.

It is intentionally simple keyword matching - good enough to exercise the whole
system and to grade the pipeline logic, not a replacement for the real model.
"""
from __future__ import annotations

import re

from app.modules.ai.text_match import mentions
from app.modules.ai.schemas import FitJudgment, JudgeVerdict, Opener, Reason, ReasonVerdict
from app.modules.icp.schemas import IcpBody, Segment

# ---------------------------------------------------------------------------
# ICP generation: a small table of business archetypes.
# ---------------------------------------------------------------------------
_ARCHETYPES = [
    {   # Software / services sold to online consumer brands
        "match": r"\b(d2c|direct[- ]to[- ]consumer|e-?commerce|shopify|online brands?|marketplace)",
        "icp": dict(
            seller_mode="b2b",
            summary="Direct-to-consumer brands that sell online across several channels.",
            industries=["D2C / e-commerce brands", "Consumer goods sold online"],
            industry_keywords=["direct-to-consumer", "d2c", "online", "shopify", "woocommerce",
                               "amazon", "orders", "marketplaces", "brand"],
            negative_keywords=["business-to-business", "wholesale", "bulk contracts", "manufacturers",
                               "freight", "industrial"],
            target_size=["smb", "mid"],
            technologies=["Shopify", "WooCommerce", "Amazon"],
            personas=["Head of E-commerce", "Head of Operations", "Founder"],
            pains=["oversell", "inventory", "shipping"],
            triggers=["hiring", "operations executive"],
            local_ratio=0.1,
            segments=[
                Segment(label="Multi-channel D2C brands (11-200 staff)",
                        description="Brands selling on their own store plus marketplaces."),
                Segment(label="Shopify-first D2C brands",
                        description="Brands whose main store runs on Shopify."),
            ],
        ),
    },
    {   # Software sold to clinics
        "match": r"\b(dental|dentist|clinic)",
        "icp": dict(
            seller_mode="b2b",
            summary="Owner-run dental clinics.",
            industries=["Dental clinics"],
            industry_keywords=["dental", "clinic", "dentist", "appointment"],
            negative_keywords=["hospital chain"],
            target_size=["micro", "smb"],
            technologies=[],
            personas=["Owner", "Lead Dentist"],
            pains=["appointment", "no-shows"],
            triggers=[],
            local_ratio=0.9,
            place_categories=["dental"],
            segments=[Segment(label="Independent dental clinics",
                              description="Single-location clinics run by the owner dentist.")],
        ),
    },
]


def build_icp(business_name: str, description: str, website_text: str, seeds: list[str]) -> IcpBody:
    text = f"{description} {website_text}".lower()
    for arch in _ARCHETYPES:
        if re.search(arch["match"], text):
            body = IcpBody(**arch["icp"])
            break
    else:
        body = IcpBody(summary=f"Businesses that buy what {business_name} sells.",
                       industries=["General businesses"], industry_keywords=[], target_size=["smb", "mid"],
                       personas=["Founder", "Head of Operations"], local_ratio=0.2)
    if re.search(r"\b(consumers|individual customers|shoppers)\b", description.lower()) and \
            not re.search(r"\bbrands\b", description.lower()):
        body.seller_mode = "b2c"
    body.exclusions = sorted({s.strip().lower() for s in seeds if s.strip()})
    return body


# ---------------------------------------------------------------------------
# Fit scoring
# Every reason quotes the exact term found in the cited evidence, e.g.
#   "Shows a problem the seller solves: 'oversell'."
# so the independent judge can verify it, whatever the industry.
# ---------------------------------------------------------------------------
def _first_hit(evidence: list[dict], words: list[str]) -> tuple[str | None, list[str]]:
    """The first ICP word found in the evidence, and the ids of the items that mention it."""
    for w in words:
        ids = [e["id"] for e in evidence if mentions(e["snippet"], w)]
        if ids:
            return w, ids
    return None, []


def score_fit(icp: IcpBody, card: dict) -> FitJudgment:
    ev = card["evidence"]
    neg_term, neg_ids = _first_hit(ev, icp.negative_keywords)
    if neg_term:
        return FitJudgment(company_id=card["company_id"], fit="no", score_0_40=0,
                           reasons=[Reason(text=f"Business type differs from the ICP: '{neg_term}'.",
                                           evidence_ids=neg_ids[:2])],
                           risks=["icp_mismatch"])

    reasons: list[Reason] = []
    for words, template in (
        (icp.industry_keywords, "Matches the target industry: evidence mentions '{t}'."),
        (icp.technologies, "Uses a platform the ICP targets: '{t}'."),
        (icp.pains, "Shows a problem the seller solves: '{t}'."),
        (icp.triggers, "Shows a buying trigger: '{t}'."),
    ):
        term, ids = _first_hit(ev, words)
        if term:
            reasons.append(Reason(text=template.format(t=term), evidence_ids=ids[:2]))

    signals = len(reasons)
    size_ok = card.get("size_band") in icp.target_size if icp.target_size else True
    if signals >= 3 and size_ok:
        fit, score = "strong", min(40, 32 + 2 * (signals - 3) + 4)
    elif signals >= 2:
        fit, score = "medium", 22 if size_ok else 16
    elif signals == 1:
        fit, score = "weak", 10
    else:
        fit, score = "no", 0
    return FitJudgment(company_id=card["company_id"], fit=fit, score_0_40=score, reasons=reasons,
                       risks=[] if size_ok else ["size_outside_icp"])


# ---------------------------------------------------------------------------
# Independent judge: does the cited evidence actually contain what each reason claims?
# ---------------------------------------------------------------------------
_QUOTED = re.compile(r"'([^']+)'")


def judge(fit: str, reasons: list[dict], evidence: dict[str, str]) -> JudgeVerdict:
    verdicts = []
    for i, r in enumerate(reasons):
        cited_text = " ".join(evidence.get(eid, "") for eid in r["evidence_ids"]).lower()
        claims = [c.lower() for c in _QUOTED.findall(r["text"])]
        if not cited_text:
            supported = "no"                      # cites nothing we hold
        elif claims and all(c in cited_text for c in claims):
            supported = "yes"
        elif claims and any(c in cited_text for c in claims):
            supported = "partial"
        else:
            supported = "partial" if not claims else "no"
        verdicts.append(ReasonVerdict(index=i, supported=supported))
    n_yes = sum(v.supported == "yes" for v in verdicts)
    agrees = (fit == "strong" and n_yes >= 3) or (fit == "medium" and n_yes >= 2) or fit in ("weak", "no")
    return JudgeVerdict(reasons=verdicts, overall_fit_agrees=agrees)


def opener(person_first: str, company: str, evidence_snippet: str, topic: str | None = None) -> Opener:
    snippet = evidence_snippet.split(".")[0][:120]
    question = f"Curious how you handle {topic} today?" if topic else "Would a quick chat be useful?"
    return Opener(text=f"Hi {person_first} - saw that {company}: \"{snippet}\". {question}"[:280])
