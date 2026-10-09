# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Cost-match API routes.

Mounted at ``/api/v1/cost-match/`` by the module loader.

Endpoint groups:
    /runs/                          - submit a batch, list a project's runs
    /runs/{id}                      - run header with aggregated counts
    /runs/{id}/results              - paged results of one run
    /runs/{id}/review-queue         - the lines still waiting for a person
    /runs/{id}/validate             - the ``cost_match`` rule set, whole run
    /results/{id}                   - one line with its evidence and rulings
    /results/{id}/decision          - confirm, override or reject the line
    /results/{id}/validate          - the ``cost_match`` rule set, one line

Tenant scoping follows the platform's IDOR posture: a request for an object
the caller cannot see returns **404, never 403**, so probing never leaks the
existence of a row.

One route here is deliberately odd. ``_health`` keeps its literal
``/cost-match/_health`` path, which combines with the loader's own
``/api/v1/cost-match`` prefix into a doubled URL. That doubled URL is where the
module-loader health roll-up already looks, and moving it would be a silent
break in an unrelated subsystem for a cosmetic gain. New routes are declared
without the redundant segment, so the business surface is clean.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.permissions import Role, permission_registry
from app.dependencies import CurrentUserId, RequirePermission, SessionDep, verify_project_access
from app.modules.cost_match.manifest import manifest
from app.modules.cost_match.models import (
    DECISION_STATES,
    TIERS,
    MatchResult,
    MatchRun,
)
from app.modules.cost_match.schemas import (
    BoqConfirmRequest,
    BoqConfirmResponse,
    BoqMatchJobResponse,
    BoqMatchRunCreate,
    BoqMatchRunResponse,
    CostMatchValidationReport,
    MatchDecisionCreate,
    MatchDecisionResponse,
    MatchResultPage,
    MatchResultResponse,
    MatchRunCreate,
    MatchRunResponse,
    MatchRunUpdate,
    WebVerifyJobResponse,
    WebVerifyRequest,
    WebVerifyResult,
)
from app.modules.cost_match.service import (
    BoqLockedError,
    CostMatchService,
    DecisionPayloadError,
    NoSuggestionToConfirmError,
    RunClosedError,
)
from app.modules.cost_match.webverify import estimate_signature

router = APIRouter(tags=["Cost Match"])

logger = logging.getLogger(__name__)

# ── Async whole-BOQ match jobs ───────────────────────────────────────────
#
# ``POST /boq/{id}/run`` scores the whole bill inside the request - minutes
# on a large BOQ, well past every proxy and browser timeout on the way, so
# the UI could only ever report failure while the server kept working.
# ``/run-async`` instead spawns the same pipeline as a task and returns a
# job handle the caller polls.  State lives in process memory: the runs and
# position write-back are durable rows either way, only the *handle* is
# volatile - a restart mid-run shows up as a lost job, never as lost data.
#
# ``_ACTIVE_BOQ_JOBS`` pins one running job per BOQ, so a retry (the very
# thing a client does after its request timed out) returns the existing
# job instead of stacking a second match on the same positions.
_BOQ_JOBS: dict[uuid.UUID, dict] = {}
_ACTIVE_BOQ_JOBS: dict[uuid.UUID, uuid.UUID] = {}
_JOB_TASKS: set[asyncio.Task] = set()
_JOB_TTL = timedelta(hours=1)


def _job_response(job: dict) -> BoqMatchJobResponse:
    return BoqMatchJobResponse(
        job_id=job["job_id"],
        boq_id=job["boq_id"],
        status=job["status"],
        started_at=job["started_at"],
        finished_at=job["finished_at"],
        lines_done=job.get("lines_done"),
        lines_total=job.get("lines_total"),
        result=job["result"],
        error=job["error"],
    )


def _prune_jobs() -> None:
    cutoff = datetime.now(UTC) - _JOB_TTL
    for job_id in [
        j
        for j, job in _BOQ_JOBS.items()
        if job["status"] != "running" and job["started_at"] < cutoff
    ]:
        job = _BOQ_JOBS.pop(job_id, None)
        if job and _ACTIVE_BOQ_JOBS.get(job["boq_id"]) == job_id:
            _ACTIVE_BOQ_JOBS.pop(job["boq_id"], None)


def _job_progress(job: dict):
    """Return the service's on_progress callback bound to this job dict."""

    def _update(done: int, total: int) -> None:
        job["lines_done"] = done
        job["lines_total"] = total

    return _update


async def _run_boq_match_job(
    job: dict,
    data: BoqMatchRunCreate,
    created_by: uuid.UUID | None,
) -> None:
    """Run the ordinary match pipeline on its own session, then commit.

    Same service, same runs, same write-back as the sync endpoint - the only
    difference is the session: request sessions are committed by their
    dependency after the response, a task has no request so it commits its
    own.
    """
    from app.database import async_session_factory

    try:
        async with async_session_factory() as session:
            service = CostMatchService(session)
            result = await service.run_boq_match(
                boq_id=job["boq_id"],
                data=data,
                created_by=created_by,
                on_progress=_job_progress(job),
            )
            await session.commit()
        job["status"] = "done"
        job["result"] = result
    except Exception as exc:  # noqa: BLE001 - the job row is where it lands
        logger.exception("boq match job %s failed: %s", job["job_id"], exc)
        job["status"] = "failed"
        job["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        job["finished_at"] = datetime.now(UTC)
        if _ACTIVE_BOQ_JOBS.get(job["boq_id"]) == job["job_id"]:
            _ACTIVE_BOQ_JOBS.pop(job["boq_id"], None)


# ── Async web-verify jobs ────────────────────────────────────────────────
#
# Same in-process handle pattern as the match jobs above, keyed by run: the
# estimate rows are durable either way, only the progress handle is volatile.
_WV_JOBS: dict[uuid.UUID, dict[str, Any]] = {}
_ACTIVE_WV_JOBS: dict[uuid.UUID, uuid.UUID] = {}


def _wv_job_response(job: dict[str, Any]) -> WebVerifyJobResponse:
    return WebVerifyJobResponse(
        job_id=job["job_id"],
        run_id=job["run_id"],
        status=job["status"],
        started_at=job["started_at"],
        finished_at=job["finished_at"],
        lines_done=job.get("lines_done"),
        lines_total=job.get("lines_total"),
        result=job["result"],
        error=job["error"],
    )


def _wv_prune_jobs() -> None:
    cutoff = datetime.now(UTC) - _JOB_TTL
    for job_id in [
        j
        for j, job in _WV_JOBS.items()
        if job["status"] != "running" and job["started_at"] < cutoff
    ]:
        job = _WV_JOBS.pop(job_id, None)
        if job and _ACTIVE_WV_JOBS.get(job["run_id"]) == job_id:
            _ACTIVE_WV_JOBS.pop(job["run_id"], None)


async def _run_web_verify_job(job: dict[str, Any], scope: str) -> None:
    from app.database import async_session_factory

    try:
        async with async_session_factory() as session:
            service = CostMatchService(session)
            run = await session.get(MatchRun, job["run_id"])
            if run is None:
                raise LookupError("match run not found")

            def _update(done: int, total: int) -> None:
                job["lines_done"] = done
                job["lines_total"] = total

            result = await service.run_web_verify(run, scope=scope, on_progress=_update)
            await session.commit()
        job["status"] = "done"
        job["result"] = WebVerifyResult(**result)
    except Exception as exc:  # noqa: BLE001 - the job row is where it lands
        logger.exception("web-verify job %s failed: %s", job["job_id"], exc)
        job["status"] = "failed"
        job["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if job["status"] == "running":
            # A cancelled task never reaches the except: mark it instead of
            # leaving a job that polls as "running" forever.
            job["status"] = "failed"
            job["error"] = "job interrupted"
        job["finished_at"] = datetime.now(UTC)
        if _ACTIVE_WV_JOBS.get(job["run_id"]) == job["job_id"]:
            _ACTIVE_WV_JOBS.pop(job["run_id"], None)

# Registered at import time, like every other module's permission block: the
# loader imports this router during ``load_all`` (after the core permissions
# are in place) and the registry is never cleared afterwards.
#
# Reading a run is a viewer-level action. Submitting a batch and ruling on a
# line are not: a ruling is the human confirmation the whole workflow rests
# on, so it is gated at EDITOR rather than left open to anyone who can read
# the project.
permission_registry.register_module_permissions(
    "cost_match",
    {
        "cost_match.read": Role.VIEWER,
        "cost_match.run": Role.EDITOR,
        "cost_match.review": Role.EDITOR,
    },
)

_TIER_VALUES = set(TIERS)
_DECISION_STATE_VALUES = set(DECISION_STATES)


def _as_uuid(raw: str | None) -> uuid.UUID | None:
    """Parse an authenticated subject id into a UUID, or ``None``.

    Tokens carry the subject as a string. A deployment that issues a
    non-UUID subject must not crash the review endpoint; the missing
    attribution is then reported by ``cost_match.decision_has_reviewer``
    rather than swallowed.
    """
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        return None


async def _load_run_or_404(session, run_id: uuid.UUID) -> MatchRun:
    run = await session.get(MatchRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Match run not found")
    return run


async def _verify_run_access(session, run_id: uuid.UUID, user_id: str) -> MatchRun:
    run = await _load_run_or_404(session, run_id)
    await verify_project_access(run.project_id, user_id, session)
    return run


async def _verify_result_access(
    session,
    result_id: uuid.UUID,
    user_id: str,
) -> tuple[MatchRun, MatchResult]:
    """Load one result plus its run, with the project access check applied.

    The result carries its own ``project_id``, but the run is needed anyway to
    scope an override to the right cost base and to refuse rulings on a closed
    run, so both are resolved here once.
    """
    result = await session.get(MatchResult, result_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Match result not found")
    run = await _load_run_or_404(session, result.run_id)
    await verify_project_access(result.project_id, user_id, session)
    return run, result


def _validate_tier(tier: str | None) -> str | None:
    if tier and tier not in _TIER_VALUES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"unknown tier '{tier}'; expected one of {', '.join(sorted(_TIER_VALUES))}",
        )
    return tier


def _validate_decision_state(state: str | None) -> str | None:
    if state and state not in _DECISION_STATE_VALUES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"unknown decision state '{state}'; expected one of {', '.join(sorted(_DECISION_STATE_VALUES))}",
        )
    return state


@router.get("/cost-match/_health", include_in_schema=False)
async def module_health() -> dict[str, str]:
    return {
        "module": manifest.name,
        "version": manifest.version,
        "status": "healthy",
    }


# ── Runs ─────────────────────────────────────────────────────────────────


@router.post(
    "/runs/",
    response_model=MatchRunResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(RequirePermission("cost_match.run"))],
)
async def create_run(
    data: MatchRunCreate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> MatchRunResponse:
    """Match a pasted batch of descriptions against one cost base.

    Scored synchronously: a few hundred lines against a bounded candidate
    pool is a request, not a job, and the surveyor gets the queue back in the
    same round trip. Nothing is applied - every line comes back pending and
    waits for a person, whatever tier it landed in.
    """
    await verify_project_access(data.project_id, user_id, session)
    service = CostMatchService(session)
    run = await service.create_run(data, created_by=_as_uuid(user_id))
    return await service.run_response(run)


@router.post(
    "/boq/{boq_id}/run",
    response_model=BoqMatchRunResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(RequirePermission("cost_match.run"))],
)
async def run_boq_match(
    boq_id: uuid.UUID,
    session: SessionDep,
    user_id: CurrentUserId,
    data: BoqMatchRunCreate | None = None,
) -> BoqMatchRunResponse:
    """The one-click path: match a whole BOQ and price its positions.

    Runs are created through the ordinary run pipeline in 500-line chunks and
    each suggestion is written back onto its position with the evidence tier
    as ``price_basis``. Lines with no offer stay unresolved - nothing is
    guessed. Reviewer rulings re-sync the position afterwards.
    """
    from app.modules.boq.models import BOQ

    boq = await session.get(BOQ, boq_id)
    if boq is None:
        raise HTTPException(status_code=404, detail="BOQ not found")
    await verify_project_access(boq.project_id, user_id, session)
    service = CostMatchService(session)
    try:
        return await service.run_boq_match(
            boq_id=boq_id, data=data or BoqMatchRunCreate(), created_by=_as_uuid(user_id)
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="BOQ not found") from None
    except BoqLockedError:
        raise HTTPException(status_code=409, detail="BOQ is locked and cannot be repriced") from None


@router.post(
    "/boq/{boq_id}/run-async",
    response_model=BoqMatchJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(RequirePermission("cost_match.run"))],
)
async def run_boq_match_async(
    boq_id: uuid.UUID,
    session: SessionDep,
    user_id: CurrentUserId,
    data: BoqMatchRunCreate | None = None,
) -> BoqMatchJobResponse:
    """The one-click path, off the request clock.

    Same pipeline as ``POST /boq/{id}/run`` but the match runs as a task, so
    a large bill no longer dies inside the proxy/browser timeout while the
    server keeps working.  Returns a job handle; poll
    ``GET /boq/{id}/run-jobs/{job_id}`` until ``status`` is ``done`` or
    ``failed``.  A second call while a job for this BOQ is already running
    returns that job - retrying a timed-out click can never stack two
    matches on the same positions.
    """
    from app.modules.boq.models import BOQ

    boq = await session.get(BOQ, boq_id)
    if boq is None:
        raise HTTPException(status_code=404, detail="BOQ not found")
    await verify_project_access(boq.project_id, user_id, session)
    if boq.is_locked:
        raise HTTPException(status_code=409, detail="BOQ is locked and cannot be repriced")

    _prune_jobs()
    active_id = _ACTIVE_BOQ_JOBS.get(boq_id)
    if active_id is not None and (job := _BOQ_JOBS.get(active_id)) is not None:
        return _job_response(job)

    job_id = uuid.uuid4()
    job = {
        "job_id": job_id,
        "boq_id": boq_id,
        "project_id": boq.project_id,
        "status": "running",
        "started_at": datetime.now(UTC),
        "finished_at": None,
        "lines_done": None,
        "lines_total": None,
        "result": None,
        "error": None,
    }
    _BOQ_JOBS[job_id] = job
    _ACTIVE_BOQ_JOBS[boq_id] = job_id
    task = asyncio.create_task(
        _run_boq_match_job(job, data or BoqMatchRunCreate(), _as_uuid(user_id))
    )
    # Keep a reference - an unreferenced task may be garbage-collected.
    _JOB_TASKS.add(task)
    task.add_done_callback(_JOB_TASKS.discard)
    return _job_response(job)


@router.get(
    "/boq/{boq_id}/run-jobs/active",
    response_model=BoqMatchJobResponse,
)
async def get_active_boq_match_job(
    boq_id: uuid.UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> BoqMatchJobResponse:
    """The currently running job for this BOQ, if any.

    Lets a page that was reloaded mid-run re-attach its progress instead of
    asking the user to guess whether the click they made before the reload
    is still working.  404 when nothing is running - same posture as any
    other invisible object.
    """
    job_id = _ACTIVE_BOQ_JOBS.get(boq_id)
    job = _BOQ_JOBS.get(job_id) if job_id is not None else None
    if job is None:
        raise HTTPException(status_code=404, detail="No active match job")
    await verify_project_access(job["project_id"], user_id, session)
    return _job_response(job)


@router.get(
    "/boq/{boq_id}/run-jobs/{job_id}",
    response_model=BoqMatchJobResponse,
)
async def get_boq_match_job(
    boq_id: uuid.UUID,
    job_id: uuid.UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> BoqMatchJobResponse:
    """Poll one async BOQ match job.

    404 for an unknown or expired job - same as any object the caller cannot
    see, and what a restart mid-run looks like from the client's side.  The
    underlying runs and prices are durable rows regardless.
    """
    job = _BOQ_JOBS.get(job_id)
    if job is None or job["boq_id"] != boq_id:
        raise HTTPException(status_code=404, detail="Match job not found")
    await verify_project_access(job["project_id"], user_id, session)
    return _job_response(job)


@router.post(
    "/boq/{boq_id}/confirm",
    response_model=BoqConfirmResponse,
    dependencies=[Depends(RequirePermission("cost_match.review"))],
)
async def confirm_boq_positions(
    boq_id: uuid.UUID,
    session: SessionDep,
    user_id: CurrentUserId,
    data: BoqConfirmRequest | None = None,
) -> BoqConfirmResponse:
    """Bulk-confirm the machine's priced suggestions on a bill.

    The grid's "confirm selected / confirm all" path. A suggestion written by
    a match run stays a suggestion until a person rules on it - this applies
    the ``confirmed`` ruling through the same per-line decision flow the
    review queue uses, so every bulk confirmation lands in the append-only
    decision history with the reviewer attributed and the position marked
    ``corpus_confirmed``.  Rows already ruled, never suggested, or without a
    match result are skipped, never guessed at.

    ``learn_to_corpus`` (default on) additionally persists each confirmed
    price as an ``estimate_confirmed`` cost item keyed ``CONF-<position
    id>`` - a confirmed price is company evidence, and re-confirming the
    same position updates its row instead of duplicating it.
    """
    from app.modules.boq.models import BOQ

    boq = await session.get(BOQ, boq_id)
    if boq is None:
        raise HTTPException(status_code=404, detail="BOQ not found")
    await verify_project_access(boq.project_id, user_id, session)
    if boq.is_locked:
        raise HTTPException(
            status_code=409, detail="BOQ is locked and cannot take new rulings"
        )
    service = CostMatchService(session)
    counts = await service.confirm_boq_positions(
        boq,
        position_ids=data.position_ids if data else None,
        decided_by=_as_uuid(user_id),
        learn_to_corpus=data.learn_to_corpus if data else True,
    )
    return BoqConfirmResponse(boq_id=boq_id, **counts)


@router.get("/runs/", response_model=list[MatchRunResponse])
async def list_runs(
    session: SessionDep,
    user_id: CurrentUserId,
    project_id: uuid.UUID = Query(...),
    run_status: str | None = Query(default=None, alias="status"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[MatchRunResponse]:
    """Match runs on one project, newest first, each with its live counts.

    The counts come from one grouped query over every listed run's results,
    not from stored columns, so the queue badge on the list can never
    disagree with the queue itself.
    """
    await verify_project_access(project_id, user_id, session)
    service = CostMatchService(session)
    runs = await service.run_repo.list_for_project(
        project_id,
        status=run_status,
        offset=offset,
        limit=limit,
    )
    counts = await service.counts_for_runs([run.id for run in runs])
    responses: list[MatchRunResponse] = []
    for run in runs:
        response = MatchRunResponse.model_validate(run)
        if run.id in counts:
            response.counts = counts[run.id]
        responses.append(response)
    return responses


@router.get("/runs/{run_id}", response_model=MatchRunResponse)
async def get_run(
    run_id: uuid.UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> MatchRunResponse:
    run = await _verify_run_access(session, run_id, user_id)
    service = CostMatchService(session)
    return await service.run_response(run)


@router.patch(
    "/runs/{run_id}",
    response_model=MatchRunResponse,
    dependencies=[Depends(RequirePermission("cost_match.review"))],
)
async def update_run(
    run_id: uuid.UUID,
    data: MatchRunUpdate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> MatchRunResponse:
    """Rename a run, re-label its source, or open and close its review.

    Closing is how the surveyor says the pricing is settled: a closed run
    refuses further rulings until it is re-opened, so a late edit cannot move
    a bill that has already been quoted from.
    """
    run = await _verify_run_access(session, run_id, user_id)
    service = CostMatchService(session)
    run = await service.update_run(run, data)
    return await service.run_response(run)


@router.delete(
    "/runs/{run_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(RequirePermission("cost_match.run"))],
)
async def delete_run(
    run_id: uuid.UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> None:
    """Delete a run with its results and their whole ruling history."""
    await _verify_run_access(session, run_id, user_id)
    service = CostMatchService(session)
    await service.run_repo.delete(run_id)


@router.get("/runs/{run_id}/results", response_model=MatchResultPage)
async def list_run_results(
    run_id: uuid.UUID,
    session: SessionDep,
    user_id: CurrentUserId,
    tier: str | None = Query(default=None),
    decision_state: str | None = Query(default=None),
    locale: str = Query(default="en"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> MatchResultPage:
    """One page of a run's results in submission order.

    Filterable by tier and by decision state, which together are how the
    review screens slice the same run: "show me the exact ones", "show me
    what I have already overridden".
    """
    run = await _verify_run_access(session, run_id, user_id)
    _validate_tier(tier)
    _validate_decision_state(decision_state)
    service = CostMatchService(session)
    total = await service.result_repo.count_for_run(
        run.id,
        tier=tier,
        decision_state=decision_state,
    )
    items = await service.result_repo.list_for_run(
        run.id,
        tier=tier,
        decision_state=decision_state,
        offset=offset,
        limit=limit,
    )
    estimates = await service.estimates_for_results(items)
    return MatchResultPage(
        run_id=run.id,
        total=total,
        offset=offset,
        limit=limit,
        items=[
            service.result_response(
                item,
                locale=locale,
                web_estimate=estimates.get(
                    estimate_signature(item.source_description, item.source_unit)
                ),
            )
            for item in items
        ],
    )


@router.get("/runs/{run_id}/review-queue", response_model=MatchResultPage)
async def list_review_queue(
    run_id: uuid.UUID,
    session: SessionDep,
    user_id: CurrentUserId,
    locale: str = Query(default="en"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> MatchResultPage:
    """The lines that still need a person before anything can be priced.

    The queue is the ``needs_review`` and ``unmatched`` tiers that nobody has
    ruled on yet - the two where the machine is explicitly not claiming an
    answer. Confident and exact lines still require confirmation before they
    are adopted, but they belong in the bulk-review lists, not here; ask for
    them with ``/results?decision_state=pending``.
    """
    run = await _verify_run_access(session, run_id, user_id)
    service = CostMatchService(session)
    total = await service.result_repo.count_for_run(run.id, queue_only=True)
    items = await service.result_repo.list_for_run(
        run.id,
        queue_only=True,
        offset=offset,
        limit=limit,
    )
    estimates = await service.estimates_for_results(items)
    return MatchResultPage(
        run_id=run.id,
        total=total,
        offset=offset,
        limit=limit,
        items=[
            service.result_response(
                item,
                locale=locale,
                web_estimate=estimates.get(
                    estimate_signature(item.source_description, item.source_unit)
                ),
            )
            for item in items
        ],
    )


# ── Web-verify ────────────────────────────────────────────────────────────


@router.post(
    "/runs/{run_id}/web-verify",
    response_model=WebVerifyJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(RequirePermission("cost_match.review"))],
)
async def start_web_verify(
    run_id: uuid.UUID,
    data: WebVerifyRequest,
    session: SessionDep,
    user_id: CurrentUserId,
) -> WebVerifyJobResponse:
    """Extract AI market estimates for a run's lines, as a background job.

    One call per unique line; the caller polls the returned job for
    ``lines_done / lines_total`` progress. The estimates are advisory
    records - applying one is still a ``manual`` ruling by a person.
    A repeat start while a job for the run is running returns that job.
    """
    run = await _verify_run_access(session, run_id, user_id)
    from app.config import get_settings

    s = get_settings()
    configured = (
        bool(s.web_verify_key and s.web_verify_gemini_model)
        if s.web_verify_provider == "gemini"
        else bool(s.web_verify_url and s.web_verify_model)
    )
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Web-verify is not configured (OE_WEB_VERIFY_* for the chosen provider)",
        )
    _wv_prune_jobs()
    existing_id = _ACTIVE_WV_JOBS.get(run.id)
    if existing_id is not None and existing_id in _WV_JOBS:
        return _wv_job_response(_WV_JOBS[existing_id])
    job: dict[str, Any] = {
        "job_id": uuid.uuid4(),
        "run_id": run.id,
        "status": "running",
        "started_at": datetime.now(UTC),
        "finished_at": None,
        "lines_done": None,
        "lines_total": None,
        "result": None,
        "error": None,
    }
    _WV_JOBS[job["job_id"]] = job
    _ACTIVE_WV_JOBS[run.id] = job["job_id"]
    task = asyncio.create_task(_run_web_verify_job(job, data.scope))
    _JOB_TASKS.add(task)
    task.add_done_callback(_JOB_TASKS.discard)
    return _wv_job_response(job)


@router.get("/runs/{run_id}/web-verify-jobs/active", response_model=WebVerifyJobResponse)
async def get_active_web_verify_job(
    run_id: uuid.UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> WebVerifyJobResponse:
    """The running verify job for this run, or 404 when nothing is running."""
    run = await _verify_run_access(session, run_id, user_id)
    job_id = _ACTIVE_WV_JOBS.get(run.id)
    if job_id is None or job_id not in _WV_JOBS:
        raise HTTPException(status_code=404, detail="No web-verify job is running for this run")
    return _wv_job_response(_WV_JOBS[job_id])


@router.get("/runs/{run_id}/web-verify-jobs/{job_id}", response_model=WebVerifyJobResponse)
async def get_web_verify_job(
    run_id: uuid.UUID,
    job_id: uuid.UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> WebVerifyJobResponse:
    """One verify job's state, for the poller the button drives."""
    run = await _verify_run_access(session, run_id, user_id)
    job = _WV_JOBS.get(job_id)
    if job is None or job["run_id"] != run.id:
        raise HTTPException(status_code=404, detail="Web-verify job not found")
    return _wv_job_response(job)


@router.post("/runs/{run_id}/validate", response_model=CostMatchValidationReport)
async def validate_run(
    run_id: uuid.UUID,
    session: SessionDep,
    user_id: CurrentUserId,
    locale: str = Query(default=""),
) -> CostMatchValidationReport:
    """Run the ``cost_match`` rule set over a whole run.

    Both scopes at once: what the batch shows as a batch (rates adopted in
    two currencies, one scope priced two ways, how much queue is left) and
    every line's own findings folded in.
    """
    run = await _verify_run_access(session, run_id, user_id)
    service = CostMatchService(session)
    return await service.validate_run(run, locale=locale)


# ── Results ──────────────────────────────────────────────────────────────


@router.get("/results/{result_id}", response_model=MatchResultResponse)
async def get_result(
    result_id: uuid.UUID,
    session: SessionDep,
    user_id: CurrentUserId,
    locale: str = Query(default="en"),
) -> MatchResultResponse:
    """One line with its suggestion, its runners-up and its ruling history."""
    _run, result = await _verify_result_access(session, result_id, user_id)
    service = CostMatchService(session)
    loaded = await service.result_repo.get_with_decisions(result.id)
    if loaded is None:
        raise HTTPException(status_code=404, detail="Match result not found")
    estimates = await service.estimates_for_results([loaded])
    return service.result_response(
        loaded,
        locale=locale,
        web_estimate=estimates.get(
            estimate_signature(loaded.source_description, loaded.source_unit)
        ),
    )


@router.post(
    "/results/{result_id}/decision",
    response_model=MatchDecisionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(RequirePermission("cost_match.review"))],
)
async def decide_result(
    result_id: uuid.UUID,
    data: MatchDecisionCreate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> MatchDecisionResponse:
    """Confirm, override or reject one suggested match.

    This is the only way a suggestion becomes something the project uses.
    The ruling is appended to the line's history rather than replacing it, so
    a change of mind stays visible, and it records who ruled and what the
    machine was showing at that moment.
    """
    run, result = await _verify_result_access(session, result_id, user_id)
    service = CostMatchService(session)
    try:
        decision = await service.record_decision(
            run,
            result,
            data,
            decided_by=_as_uuid(user_id),
        )
    except RunClosedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except NoSuggestionToConfirmError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except DecisionPayloadError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except LookupError as exc:
        # Same IDOR rationale as everywhere else: an id the caller may not use
        # is a 404, never a 403 and never a 422.
        raise HTTPException(status_code=404, detail="Cost item not found in this run's cost base") from exc
    return MatchDecisionResponse.model_validate(decision)


@router.get(
    "/results/{result_id}/decisions",
    response_model=list[MatchDecisionResponse],
)
async def list_result_decisions(
    result_id: uuid.UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> list[MatchDecisionResponse]:
    """The full, ordered ruling history of one line."""
    _run, result = await _verify_result_access(session, result_id, user_id)
    service = CostMatchService(session)
    loaded = await service.result_repo.get_with_decisions(result.id)
    if loaded is None:
        raise HTTPException(status_code=404, detail="Match result not found")
    return [MatchDecisionResponse.model_validate(d) for d in loaded.decisions]


@router.post("/results/{result_id}/validate", response_model=CostMatchValidationReport)
async def validate_result(
    result_id: uuid.UUID,
    session: SessionDep,
    user_id: CurrentUserId,
    locale: str = Query(default=""),
) -> CostMatchValidationReport:
    """Run the result-scope ``cost_match`` rules over one line."""
    _run, result = await _verify_result_access(session, result_id, user_id)
    service = CostMatchService(session)
    loaded = await service.result_repo.get_with_decisions(result.id)
    if loaded is None:
        raise HTTPException(status_code=404, detail="Match result not found")
    return await service.validate_result(loaded, locale=locale)
