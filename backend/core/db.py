"""Persistence: runs, agent outputs, memos, and the full audit log.

SQLite locally via SQLAlchemy, so the same code moves to Postgres by changing
one URL. Three things are stored:

  * **runs** — one row per analysis, with its final state and timings.
  * **artifacts** — each agent's structured output, verbatim.
  * **events** — every event the run emitted, in order.

The events table is the audit log, and it is the answer to "why did the memo say
that?". Because the UI renders the same events, there is no privileged internal
view: what a reviewer can reconstruct afterwards is exactly what the user saw
happen.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

from core.config import REPO_ROOT, settings

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Run(Base):
    __tablename__ = "runs"

    id = Column(String(16), primary_key=True)
    created_at = Column(DateTime, default=_utcnow, index=True)
    query = Column(String(200), nullable=False)
    ticker = Column(String(16), index=True)
    company_name = Column(String(200))
    horizon = Column(String(16))
    user_view = Column(Text)

    stage = Column(String(20))
    error = Column(Text)
    degraded = Column(JSON, default=list)
    elapsed_s = Column(Float)

    confidence = Column(String(16))
    verification_verdict = Column(String(24))
    claims_flagged = Column(Integer, default=0)

    artifacts = relationship("Artifact", back_populates="run", cascade="all, delete-orphan")
    events = relationship("EventRow", back_populates="run", cascade="all, delete-orphan")


class Artifact(Base):
    """One agent's structured output, stored verbatim."""

    __tablename__ = "artifacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(16), ForeignKey("runs.id"), index=True)
    kind = Column(String(32))  # bull | bear | risk | bull_rebuttal | memo | verification | sources
    payload = Column(JSON)
    created_at = Column(DateTime, default=_utcnow)

    run = relationship("Run", back_populates="artifacts")


class EventRow(Base):
    """The audit log: every observable step, in order."""

    __tablename__ = "events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(16), ForeignKey("runs.id"), index=True)
    seq = Column(Integer)
    ts = Column(DateTime)
    type = Column(String(32), index=True)
    agent = Column(String(32))
    data = Column(JSON)

    run = relationship("Run", back_populates="events")


# ------------------------------------------------------------------ engine

_engine = None
_SessionFactory = None


def _database_url() -> str:
    """Resolve a relative sqlite path against the repo root, not the cwd."""
    url = settings.database_url
    prefix = "sqlite:///./"
    if url.startswith(prefix):
        return f"sqlite:///{(REPO_ROOT / url[len(prefix):]).as_posix()}"
    return url


def get_engine():
    global _engine, _SessionFactory
    if _engine is None:
        _engine = create_engine(_database_url(), future=True)
        Base.metadata.create_all(_engine)
        _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_session() -> Session:
    get_engine()
    assert _SessionFactory is not None
    return _SessionFactory()


# ------------------------------------------------------------ persistence


def _dump(model: Any) -> Any:
    if model is None:
        return None
    if hasattr(model, "model_dump"):
        return json.loads(model.model_dump_json())
    return model


def save_run(result: Any) -> None:
    """Persist a completed (or failed) run. Never raises into the caller.

    A run that produced a good memo must not be lost because the database was
    locked, so storage failures are logged and swallowed.
    """
    try:
        with get_session() as session:
            flagged = 0
            if result.verification:
                flagged = sum(1 for f in result.verification.findings if f.verdict != "supported")

            session.merge(
                Run(
                    id=result.run_id,
                    query=result.query,
                    ticker=result.ticker,
                    company_name=result.company_name,
                    horizon=result.horizon,
                    user_view=result.user_view,
                    stage=result.stage.value,
                    error=result.error,
                    degraded=list(result.degraded),
                    elapsed_s=round(result.elapsed_s, 2),
                    confidence=result.memo.confidence if result.memo else None,
                    verification_verdict=(
                        result.verification.overall_verdict if result.verification else None
                    ),
                    claims_flagged=flagged,
                )
            )

            artifacts = {
                "bull": result.bull,
                "bear": result.bear,
                "risk": result.risk,
                "bull_rebuttal": result.bull_rebuttal,
                "bear_rebuttal": result.bear_rebuttal,
                "memo": result.memo,
                "verification": result.verification,
            }
            for kind, model in artifacts.items():
                if model is not None:
                    session.add(Artifact(run_id=result.run_id, kind=kind, payload=_dump(model)))

            # Sources are stored too: a citation is only auditable if the thing
            # it points at was captured at the time, not re-fetched later.
            session.add(
                Artifact(
                    run_id=result.run_id,
                    kind="sources",
                    payload=[json.loads(s.model_dump_json()) for s in result.registry.all()],
                )
            )

            for seq, event in enumerate(result.events):
                session.add(
                    EventRow(
                        run_id=result.run_id,
                        seq=seq,
                        ts=event.ts,
                        type=event.type,
                        agent=event.agent,
                        data=event.data,
                    )
                )

            session.commit()
            log.info("Saved run %s (%d events)", result.run_id, len(result.events))
    except Exception:
        log.exception("Could not persist run %s", getattr(result, "run_id", "?"))


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    """Recent runs, newest first — backs the History panel."""
    try:
        with get_session() as session:
            rows = (
                session.query(Run)
                .order_by(Run.created_at.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "run_id": r.id,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "ticker": r.ticker,
                    "company_name": r.company_name,
                    "horizon": r.horizon,
                    "stage": r.stage,
                    "confidence": r.confidence,
                    "verification_verdict": r.verification_verdict,
                    "claims_flagged": r.claims_flagged,
                    "elapsed_s": r.elapsed_s,
                }
                for r in rows
            ]
    except Exception:
        log.exception("Could not list runs")
        return []


def load_run(run_id: str) -> dict[str, Any] | None:
    """Rehydrate one run: its artifacts and full audit trail."""
    try:
        with get_session() as session:
            run = session.get(Run, run_id)
            if run is None:
                return None
            return {
                "run_id": run.id,
                "created_at": run.created_at.isoformat() if run.created_at else None,
                "query": run.query,
                "ticker": run.ticker,
                "company_name": run.company_name,
                "horizon": run.horizon,
                "user_view": run.user_view,
                "stage": run.stage,
                "error": run.error,
                "degraded": run.degraded or [],
                "elapsed_s": run.elapsed_s,
                "artifacts": {a.kind: a.payload for a in run.artifacts},
                "events": [
                    {
                        "seq": e.seq,
                        "ts": e.ts.isoformat() if e.ts else None,
                        "type": e.type,
                        "agent": e.agent,
                        "data": e.data,
                    }
                    for e in sorted(run.events, key=lambda e: e.seq or 0)
                ],
            }
    except Exception:
        log.exception("Could not load run %s", run_id)
        return None
