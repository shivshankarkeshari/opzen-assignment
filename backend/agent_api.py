"""HTTP boundaries for search, durable answers, and authorized evidence tools."""

import json
from typing import Literal
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend import llm, sections, storage
from backend.config import settings
from backend.db import (
    AuditEvent,
    Chunk,
    Job,
    Result,
    Run,
    new_id,
    now,
    row_dict,
    transaction,
)
from backend.intake import version_for
from backend.retrieval import SearchRequest, authorize_snapshot, eligible, metadata, search
from backend.telemetry import observe
from backend.telemetry import payload as trace_payload


class AnswerRequest(SearchRequest):
    intent: Literal["answer", "compare", "conflicts", "investigate"] = "answer"
    parent_run_id: UUID | None = None


class ResumeRequest(BaseModel):
    interrupt_id: str = Field(min_length=1, max_length=200)
    reply: str = Field(min_length=1, max_length=2000)


class ReviewRequest(BaseModel):
    decision: Literal["confirmed", "dismissed", "unresolved"]
    rationale: str = Field(min_length=1, max_length=2000)
    expected_updated_at: str


def run_for(session, run_id: str, actor: str):
    run = session.get(Run, run_id)
    if not run or run.owner_subject != actor or run.workflow_type != "agent":
        raise HTTPException(404, "Run not found")
    versions = storage.get_json(run.input_json["snapshot_ref"])
    authorize_snapshot(session, actor, versions)
    return run


def register(app, identity):
    actor_dependency = Depends(identity)

    @app.post(
        "/v1/search",
        summary="Search authorized document evidence",
        description=(
            "Run hybrid semantic and keyword retrieval without generating an answer. Send query and "
            "scope: current for approved applicable revisions, effective_on with its date, or "
            "selected_versions with explicit ready revision IDs. Optional document/type/department "
            "filters narrow the scope. Returns ranked items, exact source spans, warnings and the "
            "resolved date. RRF is rank fusion, not confidence; semantic scores are cosine distances. "
            "Source access and embedding compatibility are checked. Invalid scope returns 422; "
            "unavailable model/database services may return 503."
        ),
        tags=["Search"],
    )
    def search_endpoint(request: SearchRequest, actor: str = actor_dependency):
        request_id = new_id()
        try:
            with observe(
                "retrieval.search",
                request_id,
                input={
                    "scope": request.scope,
                    "requested_limit": request.limit,
                    "request": trace_payload(request.model_dump(mode="json")),
                },
            ) as observation:
                response = search(actor, request)
                if observation:
                    observation.update(
                        output={
                            "returned": len(response["items"]),
                            "warnings": response["warnings"],
                            "mode": response["retrieval_mode"],
                            "response": trace_payload(response),
                        }
                    )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                503, "Search unavailable; check model and database services"
            ) from None
        with transaction() as session:
            session.add(
                AuditEvent(
                    actor_subject=actor,
                    action="search",
                    resource_refs={},
                    decision="completed",
                    correlation_id=request_id,
                    details={"returned": len(response["items"])},
                )
            )
        return {"request_id": request_id, **response}

    @app.post(
        "/v1/catalog/discover",
        summary="Discover document revisions eligible for a question",
        description=(
            "Return authorized revision metadata matching a SearchRequest scope and filters. A query "
            "is required by the shared request schema, but this operation lists eligible metadata; it "
            "does not encode the query, retrieve chunks or call an LLM. Use it to identify allowed "
            "document/version IDs before requesting outlines or evidence. Returns items, which may be "
            "empty when no ready applicable revision exists. Invalid scopes return 422."
        ),
        tags=["Search"],
    )
    def discover(request: SearchRequest, actor: str = actor_dependency):
        with transaction() as session:
            return {"items": metadata(eligible(session, actor, request))}

    @app.get(
        "/v1/versions/{version_id}/outline",
        summary="Read a revision’s section outline",
        description=(
            "Return the canonical section hierarchy and section IDs for one readable revision. Use "
            "returned IDs with the section endpoint to retrieve complete subsection context instead "
            "of guessing an identifier. This inspects retained extraction without running an LLM. "
            "Source access is checked again after loading artifacts. Unavailable revisions return "
            "404; extraction artifacts must already exist."
        ),
        tags=["Evidence"],
    )
    def outline(version_id: UUID, actor: str = actor_dependency):
        with transaction() as session:
            version = row_dict(version_for(session, str(version_id), actor))
        result = sections.outline(sections.load_map(version))
        with transaction() as session:
            version_for(session, str(version_id), actor)
        return {"sections": result}

    @app.get(
        "/v1/versions/{version_id}/section",
        summary="Read a complete canonical subsection",
        description=(
            "Fetch section_id from the exact readable version, using an ID returned by its outline. "
            "Returns the section’s ordered canonical content and coverage information, including "
            "available continuation material and provenance. Inspect coverage before assuming a "
            "section is complete; batch boundaries or context limits can produce partial coverage. No "
            "answer is generated. Unknown sections or unavailable revisions return 404."
        ),
        tags=["Evidence"],
    )
    def section(version_id: UUID, section_id: str, actor: str = actor_dependency):
        with transaction() as session:
            version = row_dict(version_for(session, str(version_id), actor))
        try:
            result = sections.fetch(sections.load_map(version), section_id)
        except ValueError:
            raise HTTPException(404, "Section not found") from None
        with transaction() as session:
            version_for(session, str(version_id), actor)
        return result

    @app.get(
        "/v1/chunks/{chunk_id}/evidence",
        summary="Expand a chunk into its source subsections",
        description=(
            "Resolve an authorized chunk to its stored source spans and containing canonical "
            "sections. Use this when a retrieved chunk is only part of a rule or when opening "
            "citation context. Returns chunk metadata, sources and expanded sections; internal "
            "full-text vectors are omitted. It does not perform a new similarity search or LLM call. "
            "Missing chunks or inaccessible revisions return 404."
        ),
        tags=["Evidence"],
    )
    def evidence(chunk_id: UUID, actor: str = actor_dependency):
        with transaction() as session:
            chunk = session.get(Chunk, str(chunk_id))
            if not chunk:
                raise HTTPException(404, "Evidence not found")
            version = row_dict(version_for(session, chunk.version_id, actor))
            data = row_dict(chunk)
        section_map = sections.load_map(version)
        from backend.db import SourceSpan

        with transaction() as session:
            version_for(session, version["version_id"], actor)
            spans = [
                row_dict(s)
                for s in session.scalars(
                    select(SourceSpan).where(SourceSpan.chunk_id == str(chunk_id))
                )
            ]
        ids = {
            section_map["items"][s["source_item_ref"]]["section_id"]
            for s in spans
            if s["source_item_ref"] in section_map["items"]
        }
        data.pop("search_vector", None)
        return {
            **data,
            "sources": spans,
            "sections": [sections.fetch(section_map, sid) for sid in sorted(ids)],
        }

    @app.post(
        "/v1/answers",
        status_code=202,
        summary="Start a cited answer or investigation",
        description=(
            "Queue an owned evidence-agent run. Send query, source scope and intent: answer, compare, "
            "conflicts or investigate, plus required Idempotency-Key. compare requires exactly two "
            "explicitly selected ready revisions. For a follow-up, provide parent_run_id with the "
            "same scope and a completed parent result. Returns 202 with run_id and status; poll GET "
            "/v1/answers/{run_id}. The run pins source revisions/model settings, enforces up to five "
            "exploration iterations and may return gaps or request clarification. Reused keys with "
            "changed input or an unfinished parent return 409; invalid/no eligible scope returns 422; "
            "an unconfigured LLM returns 503."
        ),
        tags=["Answers"],
    )
    def submit_answer(
        request: AnswerRequest,
        actor: str = actor_dependency,
        idempotency_key: str = Header(min_length=1, max_length=160),
    ):
        if not llm.configured():
            raise HTTPException(503, "LLM provider is not configured")
        operation_key = f"agent:{actor}:{idempotency_key}"
        request_data = request.model_dump(mode="json")
        request_hash = storage.digest(json.dumps(request_data, sort_keys=True).encode())
        with transaction() as session:
            previous = session.scalar(select(Job).where(Job.operation_key == operation_key))
            if previous:
                if previous.request_hash != request_hash:
                    raise HTTPException(409, "Idempotency key already used for different input")
                run_for(session, previous.run_id, actor)
                return {"run_id": previous.run_id, "status": previous.status}
            versions = eligible(session, actor, request)
            if not versions:
                raise HTTPException(422, "No ready applicable revisions in this scope")
            if request.intent == "compare" and (
                request.scope != "selected_versions" or len(versions) != 2
            ):
                raise HTTPException(
                    422, "Comparison requires two explicitly selected ready revisions"
                )
            prior = []
            if request.parent_run_id:
                parent = run_for(session, str(request.parent_run_id), actor)
                result = session.scalar(select(Result).where(Result.run_id == parent.run_id))
                if not result:
                    raise HTTPException(409, "Previous answer is not ready")
                old = {
                    k: v
                    for k, v in parent.input_json["search_scope"].items()
                    if k not in ("query", "limit")
                }
                new = {
                    k: v
                    for k, v in request_data.items()
                    if k not in ("query", "limit", "intent", "parent_run_id")
                }
                if old != new:
                    raise HTTPException(
                        422, "Follow-up scope changed; start a new question without a parent"
                    )
                prior = [
                    {"question": parent.input_json["question"], "claims": result.body["claims"][:5]}
                ]
            run_id = new_id()
            snapshot_ref = storage.put_json(
                f"runs/{run_id}/snapshot.json", json.loads(json.dumps(versions, default=str))
            )
            scope = request.model_dump(mode="json", exclude={"intent", "parent_run_id"})
            run = Run(
                run_id=run_id,
                owner_subject=actor,
                workflow_type="agent",
                graph_version="evidence-agent-v1",
                status="queued",
                input_json={
                    "question": request.query,
                    "intent": request.intent,
                    "search_scope": scope,
                    "snapshot_ref": snapshot_ref,
                    "prior_context": prior,
                    "parent_run_id": str(request.parent_run_id) if request.parent_run_id else None,
                },
                config_snapshot={
                    "max_iterations": settings().agent_max_iterations,
                    "timeout_seconds": settings().agent_timeout_seconds,
                    "context_chars": settings().agent_context_chars,
                    "groq_model": llm.model_name(),
                    "llm_model": llm.model_name(),
                    "llm_provider": settings().llm_provider,
                    "llm_reasoning_effort": settings().llm_reasoning_effort,
                    "llm_timeout_seconds": settings().llm_timeout_seconds,
                },
            )
            session.add(run)
            session.flush()
            session.add(
                Job(
                    run_id=run_id,
                    job_type="agent",
                    operation_key=operation_key,
                    request_hash=request_hash,
                    status="queued",
                    payload={},
                )
            )
            session.add(
                AuditEvent(
                    actor_subject=actor,
                    action="answer_submitted",
                    resource_refs={"run_id": run_id},
                    decision="queued",
                    correlation_id=run_id,
                )
            )
        return {"run_id": run_id, "status": "queued"}

    @app.get(
        "/v1/answers",
        summary="List your saved questions and investigations",
        description=(
            "Return recent owned agent questions with run ID, question, intent, status and creation "
            "time, newest first. limit defaults to 30 and must be 1–100. Runs with unavailable source "
            "access are omitted. This is a history list; retrieve one answer by run ID for claims, "
            "evidence, gaps and progress."
        ),
        tags=["Answers"],
    )
    def answers(limit: int = Query(30, ge=1, le=100), actor: str = actor_dependency):
        with transaction() as session:
            runs = session.scalars(
                select(Run)
                .where(Run.owner_subject == actor, Run.workflow_type == "agent")
                .order_by(Run.created_at.desc())
                .limit(limit)
            ).all()
            items = []
            for run in runs:
                try:
                    run_for(session, run.run_id, actor)
                except HTTPException:
                    continue
                items.append(
                    {
                        "run_id": run.run_id,
                        "question": run.input_json["question"],
                        "status": run.status,
                        "intent": run.input_json["intent"],
                        "created_at": run.created_at,
                    }
                )
            return items

    @app.get(
        "/v1/answers/{run_id}",
        summary="Get a question’s progress, answer and sources",
        description=(
            "Poll an owned accessible question run. Returns status, progress, question and intent; "
            "result is null until a result exists. Checked generated text is in result.claims[].text, "
            "exact quotes in claims[].citations, and source content in result.evidence. Review gaps, "
            "stop_reason, iterations and freshness_state before relying on the answer. Changed "
            "applicable revisions mark results stale; inaccessible source snapshots withhold the "
            "result. A completed run can still be evidence-limited or provider-blocked, so inspect "
            "its result, not status alone. Unavailable runs return 404."
        ),
        tags=["Answers"],
    )
    def answer(run_id: UUID, actor: str = actor_dependency):
        with transaction() as session:
            run = run_for(session, str(run_id), actor)
            result = session.scalar(select(Result).where(Result.run_id == run.run_id))
            payload = {
                "run_id": run.run_id,
                "status": run.status,
                "progress": run.progress,
                "question": run.input_json["question"],
                "intent": run.input_json["intent"],
                "result": None,
            }
            if result:
                scope = SearchRequest.model_validate(run.input_json["search_scope"])
                current = eligible(session, actor, scope)
                previous = storage.get_json(run.input_json["snapshot_ref"])
                if {v["version_id"] for v in current} != {v["version_id"] for v in previous}:
                    result.freshness_state = "stale"
                body = dict(result.body)
                ledger = (
                    storage.get_json(body["evidence_ref"])
                    if body.get("evidence_ref")
                    else {"evidence": {}}
                )
                authorize_snapshot(session, actor, previous)
                payload["result"] = {
                    "result_id": result.result_id,
                    "freshness_state": result.freshness_state,
                    "updated_at": result.updated_at.isoformat(),
                    **body,
                    "evidence": ledger["evidence"],
                }
            return payload

    @app.post(
        "/v1/runs/{run_id}/resume",
        summary="Answer a pending clarification and resume the investigation",
        description=(
            "Resume an owned agent run that is waiting_for_input. Send the current interrupt_id and a "
            "nonempty reply. Returns queued status and continues the same durable thread; accumulated "
            "exploration/call limits are not reset. A stale interrupt, duplicate resume or "
            "non-pending clarification returns 409. Missing or inaccessible runs return 404. This "
            "operation does not create a new follow-up question."
        ),
        tags=["Run controls"],
    )
    def resume(run_id: UUID, reply: ResumeRequest, actor: str = actor_dependency):
        with transaction() as session:
            job = session.scalar(
                select(Job)
                .where(Job.run_id == str(run_id), Job.job_type == "agent")
                .with_for_update()
            )
            run = run_for(session, str(run_id), actor)
            if (
                not job
                or job.status != "waiting_for_input"
                or run.progress.get("interrupt_id") != reply.interrupt_id
            ):
                raise HTTPException(409, "Clarification is no longer pending")
            job.payload, job.status, job.available_at = {"resume": reply.reply}, "queued", now()
            run.status = "queued"
            session.add(
                AuditEvent(
                    actor_subject=actor,
                    action="agent_resume",
                    resource_refs={"run_id": run.run_id},
                    decision="queued",
                    correlation_id=run.run_id,
                )
            )
        return {"run_id": str(run_id), "status": "queued"}

    @app.post(
        "/v1/answers/{run_id}/review",
        summary="Record a human review of a conflict report",
        description=(
            "Review an owned accessible conflicts result. Send decision (confirmed, dismissed or "
            "unresolved), rationale and expected_updated_at copied from the latest result. Review "
            "applies to the report, not individual clauses, and records an audit event; it does not "
            "amend policies or establish legal precedence. Confirm/dismiss requires a checked "
            "conflict candidate. No conflict result returns 404; a changed review version or missing "
            "confirmable candidate returns 409. Refresh the result before retrying a stale review."
        ),
        tags=["Reviews"],
    )
    def review(run_id: UUID, decision: ReviewRequest, actor: str = actor_dependency):
        with transaction() as session:
            run = run_for(session, str(run_id), actor)
            result = session.scalar(
                select(Result).where(Result.run_id == run.run_id).with_for_update()
            )
            if not result or result.result_kind != "conflicts":
                raise HTTPException(404, "Conflict result not found")
            if decision.decision != "unresolved" and not any(
                claim.get("kind") == "conflict" for claim in result.body.get("claims", [])
            ):
                raise HTTPException(
                    409, "No checked conflict candidate is available for confirmation"
                )
            if result.updated_at.isoformat() != decision.expected_updated_at:
                raise HTTPException(409, "Review changed; refresh first")
            result.body = {
                **result.body,
                "review_state": decision.decision,
                "review": {"actor": actor, "rationale": decision.rationale},
            }
            session.add(
                AuditEvent(
                    actor_subject=actor,
                    action="conflict_review",
                    resource_refs={"result_id": result.result_id},
                    decision=decision.decision,
                    correlation_id=run.run_id,
                )
            )
        return {"review_state": decision.decision}
