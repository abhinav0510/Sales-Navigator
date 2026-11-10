"""Wire schemas sent to Claude must stay simple (regression: 'Schema is too complex' 400)."""
import json

from app.modules.ai.wire import (FitBatchWire, FitWire, IcpWire, JudgeWire, OpenerWire, ReasonWire, VerdictWire,
                                 to_fit, to_icp, to_judge)

FORBIDDEN = ("enum", "minimum", "maximum", "maxLength", "minLength", "maxItems", "const", "pattern")


def test_wire_schemas_have_no_complex_constraints():
    for model in (IcpWire, FitBatchWire, JudgeWire, OpenerWire):
        schema = json.dumps(model.model_json_schema())
        assert not [k for k in FORBIDDEN if f'"{k}"' in schema], (model.__name__, schema)


def test_converters_enforce_rules_in_code():
    icp = to_icp(IcpWire(seller_mode="B2B", summary="x", industries=["VC firms"], industry_keywords=["Venture Capital"],
                         negative_keywords=[], target_size=["huge", "SMB"], technologies=[], personas=["Partner"],
                         pains=[], triggers=[], segments=["Seed funds: early stage"], local_ratio=7.0,
                         wants_contacts=True, place_categories=[]))
    assert icp.target_size == ["smb"] and icp.local_ratio == 1.0 and icp.industry_keywords == ["venture capital"]
    assert icp.segments[0].label == "Seed funds"
    fit = to_fit(FitWire(company_id="c", fit="AMAZING", score_0_40=99, reasons=[ReasonWire(text="t", evidence_ids=["e"])], risks=[]))
    assert fit.fit == "no" and fit.score_0_40 == 40           # unknown fit -> safest value; score clamped
    judge = to_judge(JudgeWire(reasons=[VerdictWire(index=0, supported="maybe")], overall_fit_agrees=True))
    assert judge.reasons[0].supported == "no"
