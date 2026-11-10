"""
Structured outputs for every AI task.

The model must return JSON matching one of these Pydantic models (Claude mode
uses the SDK's structured outputs; offline mode builds them directly). Nothing
downstream ever parses free text from a model.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Reason(BaseModel):
    text: str = Field(max_length=300)
    evidence_ids: list[str] = Field(default_factory=list)   # must exist in OUR evidence store


class FitJudgment(BaseModel):
    company_id: str
    fit: Literal["strong", "medium", "weak", "no"]
    score_0_40: int = Field(ge=0, le=40)
    reasons: list[Reason] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class FitBatch(BaseModel):
    """Fit scoring is batched: several companies per call (cost lever 3.3)."""

    judgments: list[FitJudgment]


class ReasonVerdict(BaseModel):
    index: int
    supported: Literal["yes", "partial", "no"]


class JudgeVerdict(BaseModel):
    """Independent Quality Judge output for ONE lead's reasons."""

    reasons: list[ReasonVerdict]
    overall_fit_agrees: bool


class Opener(BaseModel):
    text: str = Field(max_length=280)
