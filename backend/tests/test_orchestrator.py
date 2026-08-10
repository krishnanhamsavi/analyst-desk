"""Phase 3 tests: the orchestrator state machine, verification, and persistence.

All offline. Agents are replaced with stand-ins, because what needs testing here
isn't the model's judgement — it's the control flow around it. Specifically:
does a failing agent degrade the run or kill it, does an unverified memo ever
get presented as verified, and is the audit log complete?
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import orchestrator as orch  # noqa: E402
from agents.base import _unescape_model, _unescape_text  # noqa: E402
from agents.factchecker import apply_verification  # noqa: E402
from agents.schemas import (  # noqa: E402
    Claim,
    DirectionalThesis,
    MemoPoint,
    Rebuttal,
    RebuttalSet,
    ResearchMemo,
    RiskAssessment,
    RiskItem,
    VerificationFinding,
    VerificationReport,
)


# ----------------------------------------------------------------- fixtures


def a_thesis(text: str = "the case") -> DirectionalThesis:
    return DirectionalThesis(
        thesis=text,
        supporting_points=[
            Claim(claim="margins improved", evidence_ref="S1", reasoning="r", dimension="profitability")
        ],
        key_assumption="k",
        biggest_risk_to_thesis="b",
        what_would_change_my_mind="w",
        confidence="moderate",
        confidence_reasoning="cr",
    )


def a_risk() -> RiskAssessment:
    return RiskAssessment(
        risks=[RiskItem(risk="concentration", severity="high", evidence_ref="S1", why_it_matters="w")],
        overall_risk_rating="medium",
        rating_reasoning="r",
        volatility_note="v",
    )


def a_rebuttal(conceded: bool = False) -> RebuttalSet:
    return RebuttalSet(
        rebuttals=[
            Rebuttal(
                targets_claim="margins improved",
                critique_type="overstated",
                critique="one quarter is not a trend",
                concession=conceded,
            )
        ],
        strongest_opposing_point="their valuation point",
        position_after_debate="unchanged",
    )


def a_memo(bull_point: str = "margins improved") -> ResearchMemo:
    return ResearchMemo(
        plain_summary="A summary.",
        confidence="moderate",
        confidence_reasoning="because",
        key_risks=["concentration risk"],
        bull_case=[MemoPoint(point=bull_point, evidence_ref="S1", survived_debate=True)],
        bear_case=[MemoPoint(point="priced for perfection", evidence_ref="S2", survived_debate=True)],
        bull_needs_to_be_true=["growth holds"],
        bear_needs_to_be_true=["growth slows"],
        how_the_debate_went="the bear landed a hit",
        what_this_means_for_you="In short: contested.",
    )


class FakeBundle:
    """Stands in for an EvidenceBundle without touching the network."""

    def __init__(self, sources: int = 3, failures: dict | None = None) -> None:
        from datetime import datetime, timezone

        self.ticker = "TEST"
        self.company_name = "Test Corp"
        self.horizon = "medium"
        self.sources = list(range(sources))
        self.failures = failures or {}
        self.results = {}
        self.fetched_at = datetime.now(timezone.utc)

    def evidence_brief(self) -> str:
        return "brief"


@pytest.fixture
def stub_desk(monkeypatch):
    """Replace every network- and model-touching call with a stand-in."""
    calls: list[str] = []

    def bundle(*args, **kwargs):
        calls.append("gather")
        return FakeBundle()

    monkeypatch.setattr(orch, "build_evidence_bundle", bundle)
    monkeypatch.setattr(
        orch,
        "resolve_ticker",
        lambda q: type("R", (), {"resolved": True, "ticker": "TEST", "name": "Test Corp",
                                 "needs_confirmation": False, "candidates": [], "message": "ok"})(),
    )
    monkeypatch.setattr(orch, "run_bull", lambda *a, **k: (calls.append("bull"), a_thesis("bull"))[1])
    monkeypatch.setattr(orch, "run_bear", lambda *a, **k: (calls.append("bear"), a_thesis("bear"))[1])
    monkeypatch.setattr(orch, "run_risk", lambda *a, **k: (calls.append("risk"), a_risk())[1])
    monkeypatch.setattr(
        orch, "run_debate", lambda *a, **k: (calls.append("debate"), (a_rebuttal(), a_rebuttal()))[1]
    )
    monkeypatch.setattr(orch, "run_moderator", lambda *a, **k: (calls.append("memo"), a_memo())[1])
    monkeypatch.setattr(
        orch,
        "run_factchecker",
        lambda *a, **k: (
            calls.append("verify"),
            VerificationReport(findings=[], summary="clean", overall_verdict="clean"),
        )[1],
    )
    return calls


# ------------------------------------------------------------------- tests


class TestStateMachine:
    def test_happy_path_visits_every_stage_in_order(self, stub_desk):
        result = orch.Orchestrator().run("Test Corp")
        assert result.ok
        assert result.stage is orch.Stage.DONE
        assert stub_desk == ["gather", "bull", "bear", "risk", "debate", "memo", "verify"]

        stages = [e.data.get("stage") for e in result.events if e.type == "run_stage"]
        assert stages[:1] == ["resolve"]
        for expected in ("gather", "research", "debate", "synthesize", "verify"):
            assert expected in stages

    def test_unresolvable_company_fails_with_a_readable_message(self, monkeypatch):
        monkeypatch.setattr(
            orch,
            "resolve_ticker",
            lambda q: type("R", (), {"resolved": False, "ticker": None, "message": "Couldn't find that."})(),
        )
        result = orch.Orchestrator().run("zzzz")
        assert not result.ok
        assert result.stage is orch.Stage.FAILED
        assert "Couldn't find" in result.error

    def test_total_data_failure_stops_the_run(self, monkeypatch, stub_desk):
        monkeypatch.setattr(orch, "build_evidence_bundle", lambda *a, **k: FakeBundle(sources=0))
        result = orch.Orchestrator().run("Test Corp")
        assert not result.ok
        assert "nothing to analyse" in result.error


class TestDegradation:
    """One agent falling over must not lose the whole run."""

    def test_bear_failure_still_produces_a_memo(self, monkeypatch, stub_desk):
        def boom(*a, **k):
            raise RuntimeError("bear exploded")

        monkeypatch.setattr(orch, "run_bear", boom)
        result = orch.Orchestrator().run("Test Corp")

        assert result.ok, "a single agent failure should degrade, not fail, the run"
        assert result.bear is None
        assert any("Bear failed" in d for d in result.degraded)

    def test_debate_is_skipped_when_only_one_side_argued(self, monkeypatch, stub_desk):
        monkeypatch.setattr(orch, "run_bear", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
        result = orch.Orchestrator().run("Test Corp")
        assert "debate" not in stub_desk
        assert any("debate skipped" in d for d in result.degraded)

    def test_losing_both_analysts_fails_the_run(self, monkeypatch, stub_desk):
        def boom(*a, **k):
            raise RuntimeError("x")

        monkeypatch.setattr(orch, "run_bull", boom)
        monkeypatch.setattr(orch, "run_bear", boom)
        result = orch.Orchestrator().run("Test Corp")
        assert not result.ok
        assert "no analysis to moderate" in result.error

    def test_failed_verification_is_declared_loudly(self, monkeypatch, stub_desk):
        """An unverified memo must never look like a verified one."""
        monkeypatch.setattr(
            orch, "run_factchecker", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("checker down"))
        )
        result = orch.Orchestrator().run("Test Corp")

        assert result.ok
        assert result.verification is None
        assert any("VERIFICATION DID NOT RUN" in d for d in result.degraded)


class TestVerificationApplication:
    def test_unsupported_claims_are_marked_not_deleted(self):
        """A struck claim teaches the reader something; a vanished one doesn't."""
        memo = a_memo(bull_point="margins improved")
        report = VerificationReport(
            findings=[
                VerificationFinding(
                    verdict="unsupported",
                    claim="margins improved",
                    evidence_ref="S1",
                    explanation="not in the source",
                )
            ],
            summary="one problem",
            overall_verdict="significant_issues",
        )
        applied = apply_verification(memo, report)
        assert "[unverified]" in applied.bull_case[0].point
        assert applied.bull_case[0].survived_debate is False
        assert len(applied.bull_case) == 1, "the claim should be flagged, not removed"

    def test_supported_claims_are_left_alone(self):
        memo = a_memo()
        report = VerificationReport(
            findings=[
                VerificationFinding(
                    verdict="supported", claim="margins improved", evidence_ref="S1", explanation="ok"
                )
            ],
            summary="clean",
            overall_verdict="clean",
        )
        applied = apply_verification(memo, report)
        assert "[unverified]" not in applied.bull_case[0].point
        assert applied.bull_case[0].survived_debate is True

    def test_flagging_is_idempotent(self):
        memo = a_memo()
        report = VerificationReport(
            findings=[
                VerificationFinding(
                    verdict="misrepresented", claim="margins improved", evidence_ref="S1", explanation="e"
                )
            ],
            summary="s",
            overall_verdict="significant_issues",
        )
        once = apply_verification(memo, report)
        twice = apply_verification(once, report)
        assert twice.bull_case[0].point.count("[unverified]") == 1


class TestUnescaping:
    def test_double_escaped_unicode_is_repaired(self):
        assert _unescape_text("a business \\u2014 huge margins") == "a business — huge margins"

    def test_repairs_nested_model_fields(self):
        memo = a_memo(bull_point="growth \\u2014 strong")
        _unescape_model(memo)
        assert "—" in memo.bull_case[0].point
        assert "\\u" not in memo.bull_case[0].point

    def test_plain_text_is_untouched(self):
        assert _unescape_text("nothing to fix here") == "nothing to fix here"


class TestPersistence:
    def test_run_round_trips_through_the_database(self, stub_desk, tmp_path, monkeypatch):
        from core import db

        monkeypatch.setattr(db, "_engine", None)
        monkeypatch.setattr(db, "_SessionFactory", None)
        monkeypatch.setattr(db, "_database_url", lambda: f"sqlite:///{(tmp_path / 't.db').as_posix()}")

        result = orch.Orchestrator().run("Test Corp")
        db.save_run(result)

        listed = db.list_runs(5)
        assert listed and listed[0]["run_id"] == result.run_id

        loaded = db.load_run(result.run_id)
        assert loaded["ticker"] == "TEST"
        assert "memo" in loaded["artifacts"]
        assert "sources" in loaded["artifacts"]
        assert len(loaded["events"]) == len(result.events), "audit log must be complete"

    def test_saving_never_raises_into_the_caller(self, stub_desk, monkeypatch):
        from core import db

        monkeypatch.setattr(db, "get_session", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
        result = orch.Orchestrator().run("Test Corp")
        db.save_run(result)  # must not raise — a good memo outlives a broken database
