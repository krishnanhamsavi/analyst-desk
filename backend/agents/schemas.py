"""Structured outputs every agent must return.

These are enforced by the API's structured-output feature, not by hoping the
model formats things correctly. That matters most for `evidence_ref`: making the
citation a required field of every claim is what turns "please cite your sources"
from a polite request into a schema constraint.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Confidence = Literal["high", "moderate", "low"]
Severity = Literal["high", "medium", "low"]


class Claim(BaseModel):
    """One argued point, tied to the evidence that supports it."""

    claim: str = Field(
        description="A single specific assertion in plain English. One idea per claim."
    )
    evidence_ref: str = Field(
        description="The citation id of the source supporting this claim, e.g. 'S3'. "
        "Must be an id you were actually shown. Never invent one."
    )
    reasoning: str = Field(
        description="Why this evidence supports the claim -- the analytical step, "
        "not a restatement of the number."
    )
    dimension: str = Field(
        description="Which analytical dimension this addresses: valuation, growth, "
        "profitability, financial_health, moat, momentum, or catalysts."
    )


class DirectionalThesis(BaseModel):
    """The Bull's and Bear's shared output shape -- same schema, opposing lenses."""

    thesis: str = Field(
        description="Your core argument in 2-3 sentences of plain English, as you'd "
        "say it to a colleague. No jargon without explanation."
    )
    supporting_points: list[Claim] = Field(
        description="The strongest evidence-backed points for your side, best first. "
        "Aim for 4-6. Quality of evidence beats quantity of claims."
    )
    key_assumption: str = Field(
        description="The single assumption your thesis depends on most. If this turns "
        "out to be wrong, the argument collapses."
    )
    biggest_risk_to_thesis: str = Field(
        description="The strongest honest argument against your own position. State it "
        "at full strength -- a weak strawman here is a failure of the job."
    )
    what_would_change_my_mind: str = Field(
        description="A concrete, observable event or datapoint that would falsify your "
        "thesis. Must be specific enough to check later."
    )
    confidence: Confidence = Field(
        description="How strongly the evidence supports your case: high, moderate, or "
        "low. Be honest -- low confidence is a legitimate finding, not a failure."
    )
    confidence_reasoning: str = Field(
        description="One sentence on why that confidence level, referring to the "
        "strength and completeness of the evidence you actually found."
    )
    evidence_gaps: list[str] = Field(
        default_factory=list,
        description="Data you wanted but could not retrieve, and how its absence "
        "weakens your argument. Empty list if none.",
    )


class RiskItem(BaseModel):
    risk: str = Field(description="What could go wrong, stated concretely.")
    severity: Severity
    evidence_ref: str = Field(description="Citation id supporting that this risk is real.")
    why_it_matters: str = Field(
        description="The mechanism: how this risk would actually damage an investor."
    )


class RiskAssessment(BaseModel):
    """The Risk Manager's output -- direction-agnostic by design."""

    risks: list[RiskItem] = Field(description="Material risks, most severe first.")
    overall_risk_rating: Severity
    rating_reasoning: str = Field(description="One sentence justifying the overall rating.")
    volatility_note: str = Field(
        description="Plain-English translation of the volatility and drawdown numbers: "
        "what a normal bad stretch has historically looked like for this stock."
    )
