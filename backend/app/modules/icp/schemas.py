"""
ICP (Ideal Customer Profile) data shapes.

The ICP is the description of the customer a user wants to find. It drives
every search: which path the planner chooses, what discovery looks for, and
what the fit scorer compares companies against.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Segment(BaseModel):
    """A focused starting segment suggested to the user (pushback P8)."""

    label: str
    description: str


class IcpBody(BaseModel):
    seller_mode: Literal["b2b", "b2c"] = "b2b"
    summary: str = ""
    industries: list[str] = Field(default_factory=list)
    # Words that identify a matching company in search results and evidence.
    industry_keywords: list[str] = Field(default_factory=list)
    # Words that identify a NON-matching company (e.g. "wholesale" for a D2C seller).
    negative_keywords: list[str] = Field(default_factory=list)
    target_size: list[Literal["micro", "smb", "mid", "enterprise"]] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    personas: list[str] = Field(default_factory=list)           # priority order
    pains: list[str] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)         # domains never to target
    segments: list[Segment] = Field(default_factory=list)
    # Planner features (LLD 6.3)
    local_ratio: float = Field(0.0, ge=0, le=1)                 # 1.0 = targets are local shops/clinics
    wants_contacts: bool = True
    place_categories: list[str] = Field(default_factory=list)   # for LOCAL_BUSINESS discovery


class IcpDraftRequest(BaseModel):
    business_name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=10, max_length=4000)
    website_url: str | None = None
    seeds: list[str] = Field(default_factory=list, max_length=50)


class IcpOut(BaseModel):
    id: str
    lineage_id: str
    version: int
    body: IcpBody
    source: dict
