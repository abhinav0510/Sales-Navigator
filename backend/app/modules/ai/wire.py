"""
"Wire" schemas: what we ask Claude to return, deliberately SIMPLE.

Why a second set of models: the structured-outputs API rejects schemas that are
too complex ("Schema is too complex"). Enums inside lists, numeric bounds, length
limits and deep nesting all add complexity. So the model fills these plain
shapes (strings, lists, numbers), and `to_*()` below applies the strict rules in
our own code: allowed values, clamped scores, defaults.

Rule for future edits: no Literal/enum, no Field(ge/le/max_length) here. Put
every constraint in the converter functions instead (tests/unit/test_wire.py
checks this).
"""
from __future__ import annotations

from pydantic import BaseModel

from app.modules.ai.schemas import FitJudgment, JudgeVerdict, Opener, Reason, ReasonVerdict
from app.modules.icp.schemas import IcpBody, Segment

_SIZES = {"micro", "smb", "mid", "enterprise"}


# --------------------------------------------------------------------------- ICP
class IcpWire(BaseModel):
    seller_mode: str              # "b2b" or "b2c"
    summary: str
    industries: list[str]
    industry_keywords: list[str]  # words that appear on a matching company's website
    negative_keywords: list[str]  # words that show a company is NOT a fit
    target_size: list[str]        # any of: micro, smb, mid, enterprise
    technologies: list[str]
    personas: list[str]           # decision-maker titles, most important first
    pains: list[str]
    triggers: list[str]
    segments: list[str]           # "Label: one-line description"
    local_ratio: float            # 0 = companies with teams, 1 = local owner-run shops/clinics
    wants_contacts: bool
    place_categories: list[str]


def to_icp(w: IcpWire) -> IcpBody:
    segments = []
    for s in w.segments[:3]:
        label, _, desc = s.partition(":")
        segments.append(Segment(label=label.strip()[:120], description=desc.strip()[:300]))
    sizes = [s.strip().lower() for s in w.target_size if s.strip().lower() in _SIZES]
    return IcpBody(
        seller_mode="b2c" if w.seller_mode.strip().lower() == "b2c" else "b2b",
        summary=w.summary[:500],
        industries=[i for i in w.industries if i][:10],
        industry_keywords=[k.lower() for k in w.industry_keywords if k][:25],
        negative_keywords=[k.lower() for k in w.negative_keywords if k][:15],
        target_size=sizes or ["smb", "mid"],
        technologies=w.technologies[:10],
        personas=w.personas[:8],
        pains=w.pains[:10],
        triggers=w.triggers[:10],
        segments=segments,
        local_ratio=min(1.0, max(0.0, w.local_ratio)),
        wants_contacts=w.wants_contacts,
        place_categories=w.place_categories[:10],
    )


# --------------------------------------------------------------------------- fit
class ReasonWire(BaseModel):
    text: str
    evidence_ids: list[str]


class FitWire(BaseModel):
    company_id: str
    fit: str                      # strong | medium | weak | no
    score_0_40: int
    reasons: list[ReasonWire]
    risks: list[str]


class FitBatchWire(BaseModel):
    judgments: list[FitWire]


def to_fit(w: FitWire) -> FitJudgment:
    fit = w.fit.strip().lower()
    return FitJudgment(
        company_id=w.company_id,
        fit=fit if fit in ("strong", "medium", "weak", "no") else "no",   # unknown -> safest value
        score_0_40=min(40, max(0, w.score_0_40)),
        reasons=[Reason(text=r.text[:300], evidence_ids=r.evidence_ids) for r in w.reasons[:5]],
        risks=w.risks[:5],
    )


# --------------------------------------------------------------------------- judge
class VerdictWire(BaseModel):
    index: int
    supported: str                # yes | partial | no


class JudgeWire(BaseModel):
    reasons: list[VerdictWire]
    overall_fit_agrees: bool


def to_judge(w: JudgeWire) -> JudgeVerdict:
    verdicts = []
    for v in w.reasons:
        s = v.supported.strip().lower()
        verdicts.append(ReasonVerdict(index=v.index, supported=s if s in ("yes", "partial", "no") else "no"))
    return JudgeVerdict(reasons=verdicts, overall_fit_agrees=w.overall_fit_agrees)


# --------------------------------------------------------------------------- opener
class OpenerWire(BaseModel):
    text: str


def to_opener(w: OpenerWire) -> Opener:
    return Opener(text=w.text.strip()[:280])
