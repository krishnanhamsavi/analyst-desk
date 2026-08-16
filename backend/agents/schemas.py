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
    risk_assessment: "RiskAssessment | None" = Field(
        default=None,
        description="Only the Bear fills this in. A direction-agnostic map of what "
        "could hurt a holder regardless of which case is right. Keep it separate "
        "from your argument: this section is not advocacy.",
    )


class Rebuttal(BaseModel):
    """One attack on a specific claim the opponent made."""

    targets_claim: str = Field(
        description="Quote or closely paraphrase the opponent claim you are attacking, "
        "so a reader can tell which one you mean."
    )
    critique_type: Literal[
        "unsupported", "cherry_picked", "misread_evidence", "wrong_horizon", "overstated"
    ] = Field(description="What kind of weakness this is.")
    critique: str = Field(
        description="Why the claim is weak, in plain English. Attack the reasoning or "
        "the evidence, never the other analyst."
    )
    evidence_ref: str | None = Field(
        default=None,
        description="Citation id backing your critique, if it rests on specific data. "
        "Null when the flaw is logical rather than factual.",
    )
    concession: bool = Field(
        default=False,
        description="True if, having examined it, you think this opponent claim is "
        "actually sound and your own case must accommodate it.",
    )


class RebuttalSet(BaseModel):
    """One analyst's rebuttal round."""

    rebuttals: list[Rebuttal] = Field(
        description="Your critiques of the opponent's strongest claims. Target the "
        "load-bearing ones, not the easy ones. 2-4 is usually right."
    )
    strongest_opposing_point: str = Field(
        description="The single best point the opponent made -- the one you cannot "
        "dismiss. Naming it honestly is part of the job."
    )
    position_after_debate: str = Field(
        description="How your view has changed, if at all, having read the other side. "
        "'Unchanged' is a valid answer if you mean it."
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


# ----------------------------------------------------------------- the memo


class MemoPoint(BaseModel):
    """A point in the final memo, rewritten for a non-expert reader."""

    point: str = Field(
        description="The point in plain English, as you'd say it to a friend at dinner. "
        "No unexplained jargon."
    )
    evidence_ref: str = Field(description="Citation id supporting this point.")
    survived_debate: bool = Field(
        description="True if this point went unchallenged or withstood its rebuttal; "
        "false if the other side landed a real hit on it."
    )


class ResearchMemo(BaseModel):
    """The Moderator's synthesis -- the product's actual deliverable.

    Ordered so a casual reader gets everything that matters in 30 seconds and
    can stop, while a serious reader can keep going.
    """

    plain_summary: str = Field(
        description="2-3 sentences anyone can understand. If the reader sees nothing "
        "else, this must leave them genuinely informed rather than hedged into mush."
    )
    confidence: Confidence = Field(
        description="How confident the desk is overall, given evidence quality and how "
        "much the two sides actually disagree."
    )
    confidence_reasoning: str = Field(
        description="One plain sentence on why -- what makes this clear or unclear."
    )
    key_risks: list[str] = Field(
        description="The risks a cautious reader most needs to see, in plain English, "
        "most important first. Front-loaded deliberately."
    )
    bull_case: list[MemoPoint] = Field(description="Strongest surviving points for.")
    bear_case: list[MemoPoint] = Field(description="Strongest surviving points against.")
    bull_needs_to_be_true: list[str] = Field(
        description="Concrete conditions that must hold for the bull case to win."
    )
    bear_needs_to_be_true: list[str] = Field(
        description="Concrete conditions that must hold for the bear case to win."
    )
    how_the_debate_went: str = Field(
        description="Which arguments survived scrutiny and which collapsed, and why. "
        "This is where you show your work as the referee."
    )
    user_view_assessment: str | None = Field(
        default=None,
        description="If the user offered their own view, how it held up against the "
        "evidence -- supported, partly right, or contradicted, and by what. Null if "
        "they gave no view.",
    )
    what_this_means_for_you: str = Field(
        description="ONE plain closing sentence framing the takeaway WITHOUT advice. "
        'E.g. "In short: a solid company, but you would be paying a high price today '
        '-- the real debate is whether its growth justifies that."'
    )


# --------------------------------------------------------- fact-checking


class VerificationFinding(BaseModel):
    verdict: Literal["supported", "unsupported", "misrepresented"] = Field(
        description="supported: the source contains this. unsupported: the source does "
        "not contain it. misrepresented: the source is real but says something "
        "materially different, e.g. a number rounded the wrong way or context dropped."
    )
    claim: str = Field(description="The claim being checked, quoted from the memo.")
    evidence_ref: str = Field(description="The citation id the claim relies on.")
    explanation: str = Field(
        description="What the source actually says, and how it does or does not support "
        "the claim. Quote the relevant figure."
    )


class VerificationReport(BaseModel):
    """The Fact-Checker's independent audit of the finished memo."""

    findings: list[VerificationFinding] = Field(
        description="One finding per checkable claim in the memo."
    )
    summary: str = Field(
        description="One or two plain sentences a reader can trust: how much of this "
        "memo is backed by the retrieved data."
    )
    overall_verdict: Literal["clean", "minor_issues", "significant_issues"] = Field(
        description="clean: everything checks out. minor_issues: small imprecision that "
        "does not change conclusions. significant_issues: at least one claim is "
        "unsupported or materially misrepresented."
    )
