import json
from datetime import date

import pytest
from pydantic import ValidationError
from sqlalchemy import select


def test_iteration_configuration_has_hard_five_ceiling():
    from backend.config import Settings

    assert Settings(_env_file=None).agent_max_iterations == 5
    assert Settings(_env_file=None, agent_max_iterations=2).agent_max_iterations == 2
    for count in (0, 6):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, agent_max_iterations=count)


def test_section_expansion_retains_missing_rule_and_duplicate_heading_identity(monkeypatch):
    from docling_core.types.doc import DoclingDocument

    from backend.sections import build_map, fetch

    doc = DoclingDocument(name="synthetic")
    first = doc.add_heading("Retention", level=1)
    doc.add_text(label="paragraph", text="Retain records for seven years.")
    doc.add_text(label="paragraph", text="Exception: approval is required.")
    second = doc.add_heading("Retention", level=1)
    doc.add_text(label="paragraph", text="Staff records have a different rule.")
    monkeypatch.setattr("backend.sections.storage.get_json", lambda ref: doc.export_to_dict())
    section_map = build_map([{"namespace": "batch-0", "page_offset": 0, "object_ref": {}}])
    section = fetch(section_map, "batch-0:" + first.self_ref)
    assert section["coverage"] == "complete"
    text = "\n".join(item["text"] for item in section["items"])
    assert "approval is required" in text and "Staff records" not in text
    assert fetch(section_map, "batch-0:" + second.self_ref)["section_id"] != section["section_id"]
    assert fetch(section_map, section["section_id"], max_chars=10)["coverage"] == "budget_limited"
    multi = build_map(
        [{"namespace": f"batch-{i}", "page_offset": i, "object_ref": {}} for i in (0, 1)]
    )
    assert fetch(multi, section["section_id"])["coverage"] == "batch_bounded"


def seed_ready(api, *, key="seed", share=False):
    """Real canonical objects, deterministic test-only vectors, no model downloads."""
    from docling_core.types.doc import DoclingDocument

    from backend import embeddings, storage
    from backend.config import settings
    from backend.db import (
        Chunk,
        Document,
        Embedding,
        Permission,
        SourceSpan,
        Version,
        new_id,
        now,
        transaction,
    )
    from backend.sections import build_map

    settings().embedding_backend = "huggingface"
    settings().embedding_dimensions = 3
    settings().embedding_model = "synthetic-test-model"
    settings().embedding_revision = "test"
    canonical = DoclingDocument(name="synthetic")
    heading = canonical.add_heading("Retention", level=1)
    body = canonical.add_text(
        label="paragraph", text="Customer records must be retained for seven years."
    )
    exception = canonical.add_text(
        label="paragraph", text="Exceptions require compliance approval."
    )
    namespace = "batch-0"
    batches = [
        {
            "namespace": namespace,
            "page_offset": 0,
            "status": "success",
            "object_ref": storage.put_json(
                f"tests/{key}/canonical.json", canonical.export_to_dict()
            ),
        }
    ]
    section_map = build_map(batches)
    doc_id, version_id, chunk_id = new_id(), new_id(), new_id()
    with transaction() as session:
        session.add(
            Document(
                document_id=doc_id, owner_subject="demo:analyst", display_filename=key + ".docx"
            )
        )
        session.flush()
        session.add(
            Version(
                version_id=version_id,
                document_id=doc_id,
                revision_number=1,
                checksum="test",
                mime_type="application/test",
                size_bytes=1,
                title="Synthetic " + key,
                document_type="policy",
                department_code="Compliance",
                approval_status="approved",
                effective_from=date(2026, 1, 1),
                recorded_by="demo:analyst",
                status="ready",
                published_at=now(),
                processing_config=embeddings.profile(),
                artifact_manifest={
                    "batches": batches,
                    "section_map": storage.put_json(f"tests/{key}/sections.json", section_map),
                },
            )
        )
        session.add(
            Permission(
                document_id=doc_id,
                subject_id="demo:analyst",
                action="read",
                granted_by="demo:analyst",
            )
        )
        session.add(
            Permission(
                document_id=doc_id,
                subject_id="demo:analyst",
                action="manage",
                granted_by="demo:analyst",
            )
        )
        if share:
            session.add(
                Permission(
                    document_id=doc_id,
                    subject_id="demo:reviewer",
                    action="read",
                    granted_by="demo:analyst",
                )
            )
        session.flush()
        session.add(
            Chunk(
                chunk_id=chunk_id,
                version_id=version_id,
                ordinal=0,
                section_ref=f"{namespace}:{heading.self_ref}",
                heading_path=["Retention"],
                body_text=body.text,
                context_text=body.text,
                body_hash="test",
                context_hash="test",
                token_count=10,
            )
        )
        session.flush()
        session.add(
            SourceSpan(
                chunk_id=chunk_id,
                span_order=0,
                source_item_ref=f"{namespace}:{body.self_ref}",
                source_locator={"item_ref": body.self_ref, "batch": namespace},
            )
        )
        session.add(
            Embedding(
                chunk_id=chunk_id,
                model_name=settings().embedding_model,
                config_hash=embeddings.signature(embeddings.profile()),
                input_hash="test",
                input_text=body.text,
                input_token_count=10,
                dimensions=3,
                vector=[1, 0, 0],
            )
        )
        from sqlalchemy import func, literal_column, update

        session.execute(
            update(Chunk)
            .where(Chunk.chunk_id == chunk_id)
            .values(
                search_vector=func.to_tsvector(literal_column("'english'::regconfig"), body.text)
            )
        )
    return {
        "document_id": doc_id,
        "version_id": version_id,
        "chunk_id": chunk_id,
        "exception_ref": f"{namespace}:{exception.self_ref}",
    }


def saver_connection():
    import psycopg
    from psycopg.rows import dict_row

    from backend.config import settings

    return psycopg.connect(
        settings().checkpoint_url,
        autocommit=True,
        row_factory=dict_row,
        options="-c search_path=checkpoints,public",
    )


def fake_model(*, repetitive=False, clarification=False):
    from backend.agent import Action, Answer, Citation, Claim, Verdict

    calls = []

    def call(system, payload, schema, run_id, **kwargs):
        calls.append(schema.__name__)
        if schema is Action:
            tool = "clarify" if clarification and not payload["clarification"] else "search"
            if clarification and payload["clarification"]:
                tool = "finish"
            count = sum(name == "Action" for name in calls)
            return Action(
                tool=tool,
                query="retention" if repetitive else f"retention {count}",
                version_id="",
                section_id="",
                missing_facts=["Need remaining evidence"],
                sufficient=False,
                question="Which records?" if tool == "clarify" else "",
            )
        if schema is Answer:
            evidence = payload["evidence"]["evidence"]
            eid, item = next((k, v) for k, v in evidence.items() if "seven years" in v["text"])
            return Answer(
                claims=[
                    Claim(
                        text="Customer records require seven years of retention.",
                        kind="answer",
                        citations=[Citation(evidence_id=eid, quote=item["text"])],
                    )
                ],
                gaps=[],
            )
        return Verdict(supported_claims=[0], gaps=[])

    return call, calls


@pytest.mark.integration
def test_hybrid_search_acl_exact_sources_and_profile_precedence(api, monkeypatch):
    from backend import embeddings
    from backend.db import Version, new_id, row_dict, transaction

    seed = seed_ready(api)
    monkeypatch.setattr(embeddings, "encode", lambda *a, **kw: [[1, 0, 0]])
    response = api.post("/v1/search", json={"query": "retained seven years"})
    assert response.status_code == 200, response.text
    hit = response.json()["items"][0]
    assert hit["chunk_id"] == seed["chunk_id"] and "keyword" in hit["scores"]
    evidence = api.get(f"/v1/chunks/{seed['chunk_id']}/evidence").json()
    assert "Exceptions require" in json.dumps(evidence)
    denied = api.post(
        "/v1/search", headers={"X-API-Key": "reviewer-local-key"}, json={"query": "retained"}
    )
    assert denied.json()["items"] == []
    assert (
        api.get(
            f"/v1/chunks/{seed['chunk_id']}/evidence", headers={"X-API-Key": "reviewer-local-key"}
        ).status_code
        == 404
    )
    with transaction() as session:
        old = session.get(Version, seed["version_id"])
        data = row_dict(old)
        data.update(
            version_id=new_id(),
            revision_number=2,
            processing_config={**old.processing_config, "embedding_revision": "different"},
        )
        session.add(Version(**data))
    response = api.post("/v1/search", json={"query": "retained"})
    assert response.json()["items"] == [] and response.json()["warnings"]


@pytest.mark.integration
@pytest.mark.parametrize(
    "max_iterations,repetitive,expected", [(5, False, 5), (2, False, 2), (5, True, 2)]
)
def test_agent_exits_at_configured_limit_or_no_progress_and_expands_section(
    api, monkeypatch, max_iterations, repetitive, expected
):
    from langgraph.checkpoint.postgres import PostgresSaver

    from backend import embeddings
    from backend.config import settings
    from backend.db import Result, transaction
    from backend.worker import claim, process

    seed_ready(api)
    settings().agent_max_iterations = max_iterations
    monkeypatch.setattr(embeddings, "encode", lambda *a, **kw: [[1, 0, 0]])
    model, calls = fake_model(repetitive=repetitive)
    monkeypatch.setattr("backend.agent.llm.structured", model)
    receipt = api.post(
        "/v1/answers",
        headers={"Idempotency-Key": "bounded-answer"},
        json={"query": "What retention rule and exceptions apply?"},
    )
    assert receipt.status_code == 202, receipt.text
    run_id = receipt.json()["run_id"]
    with saver_connection() as connection:
        process(claim("agent-test"), PostgresSaver(connection))
    result = api.get(f"/v1/answers/{run_id}").json()
    assert result["status"] == "completed", result
    body = result["result"]
    assert body["iterations"] == expected
    assert calls.count("Action") == expected
    assert body["stop_reason"] == ("no_progress" if repetitive else "max_iterations")
    assert body["claims"] and "Exceptions require" in json.dumps(body["evidence"])
    with transaction() as session:
        assert len(session.scalars(select(Result)).all()) == 1
    # A later identity change does not expose question history or sources.
    assert (
        api.get(f"/v1/answers/{run_id}", headers={"X-API-Key": "reviewer-local-key"}).status_code
        == 404
    )
    assert api.get("/v1/answers", headers={"X-API-Key": "reviewer-local-key"}).json() == []


@pytest.mark.integration
def test_clarification_resume_is_single_use_and_permission_is_rechecked(api, monkeypatch):
    from langgraph.checkpoint.postgres import PostgresSaver

    from backend.worker import claim, process

    seed = seed_ready(api, share=True)
    model, _ = fake_model(clarification=True)
    monkeypatch.setattr("backend.agent.llm.structured", model)
    headers = {"X-API-Key": "reviewer-local-key", "Idempotency-Key": "clarify"}
    response = api.post("/v1/answers", headers=headers, json={"query": "What rule applies?"})
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    with saver_connection() as connection:
        process(claim("clarify-worker"), PostgresSaver(connection))
        result = api.get(f"/v1/answers/{run_id}", headers=headers).json()
        assert result["status"] == "waiting_for_input", result
        payload = {"interrupt_id": result["progress"]["interrupt_id"], "reply": "Customer records"}
        assert (
            api.post(f"/v1/runs/{run_id}/resume", headers=headers, json=payload).status_code == 200
        )
        assert (
            api.post(f"/v1/runs/{run_id}/resume", headers=headers, json=payload).status_code == 409
        )
        process(claim("resume-worker"), PostgresSaver(connection))
    assert api.get(f"/v1/answers/{run_id}", headers=headers).json()["status"] == "completed"
    api.put(
        f"/v1/documents/{seed['document_id']}/permissions",
        json={"user": "reviewer", "allow": False},
    )
    assert api.get(f"/v1/answers/{run_id}", headers=headers).status_code == 404


@pytest.mark.integration
def test_cross_document_hops_collect_separate_sources(api, monkeypatch):
    from langgraph.checkpoint.postgres import PostgresSaver

    from backend import embeddings
    from backend.agent import Action, Answer, Citation, Claim, Verdict
    from backend.worker import claim, process

    first, second = seed_ready(api, key="first"), seed_ready(api, key="second")
    monkeypatch.setattr(embeddings, "encode", lambda *a, **kw: [[1, 0, 0]])
    planning = []

    def model(system, payload, schema, run_id, **kwargs):
        if schema is Action:
            planning.append(payload)
            step = len(planning)
            return Action(
                tool="search" if step < 3 else "finish",
                query="retention",
                version_id=first["version_id"] if step == 1 else second["version_id"],
                section_id="",
                missing_facts=[],
                sufficient=step == 3,
                question="",
            )
        if schema is Answer:
            sources = [
                (eid, row)
                for eid, row in payload["evidence"]["evidence"].items()
                if "seven years" in row["text"]
            ]
            assert len(sources) == 2
            return Answer(
                claims=[
                    Claim(
                        text="Both policies require seven years of retention.",
                        kind="answer",
                        citations=[
                            Citation(evidence_id=eid, quote=row["text"]) for eid, row in sources
                        ],
                    )
                ],
                gaps=[],
            )
        return Verdict(supported_claims=[0], gaps=[])

    monkeypatch.setattr("backend.agent.llm.structured", model)
    receipt = api.post(
        "/v1/answers",
        headers={"Idempotency-Key": "cross-document"},
        json={
            "query": "What do both policies require?",
            "document_ids": [first["document_id"], second["document_id"]],
        },
    ).json()
    with saver_connection() as connection:
        process(claim("cross-doc-worker"), PostgresSaver(connection))
    result = api.get(f"/v1/answers/{receipt['run_id']}").json()["result"]
    assert result["iterations"] == 3 and result["status"] == "supported"
    assert len(result["claims"][0]["citations"]) == 2
    assert len(planning[0]["documents"]) == 2


@pytest.mark.integration
def test_agent_recovers_checkpoint_without_resetting_iteration_budget(api, monkeypatch):
    from datetime import timedelta

    from langgraph.checkpoint.postgres import PostgresSaver

    from backend import embeddings
    from backend.agent import Agent
    from backend.db import Job, now, transaction
    from backend.worker import claim, process

    seed_ready(api)
    monkeypatch.setattr(embeddings, "encode", lambda *a, **kw: [[1, 0, 0]])
    model, calls = fake_model()
    monkeypatch.setattr("backend.agent.llm.structured", model)
    receipt = api.post(
        "/v1/answers",
        headers={"Idempotency-Key": "checkpoint-budget"},
        json={"query": "Retention and exceptions?"},
    ).json()
    with saver_connection() as connection:
        saver = PostgresSaver(connection)
        original = claim("first-worker")
        agent = Agent(original)
        graph = agent.graph(saver)
        config = {"configurable": {"thread_id": agent.thread_id}}
        graph.invoke(
            {"iteration": 0, "started_at": now().isoformat(), "seen": [], "events": []},
            config,
            interrupt_after=["act"],
        )
        state = graph.get_state(config).values
        assert state["iteration"] == 1
        assert "seven years" not in json.dumps(state)
        with transaction() as session:
            session.get(Job, original["job_id"]).lease_until = now() - timedelta(seconds=1)
        recovered = claim("replacement-worker")
        assert recovered["fence"] > original["fence"]
        process(recovered, saver)
    result = api.get(f"/v1/answers/{receipt['run_id']}").json()["result"]
    assert result["iterations"] == 5 and calls.count("Action") == 5


@pytest.mark.integration
def test_model_failure_returns_safe_partial_without_retry_loop(api, monkeypatch):
    from langgraph.checkpoint.postgres import PostgresSaver

    from backend.worker import claim, process

    seed_ready(api)
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(1)
        raise ValueError("Synthetic invalid model response")

    monkeypatch.setattr("backend.agent.llm.structured", unavailable)
    receipt = api.post(
        "/v1/answers", headers={"Idempotency-Key": "model-failure"}, json={"query": "What policy?"}
    ).json()
    with saver_connection() as connection:
        process(claim("model-failure-worker"), PostgresSaver(connection))
    result = api.get(f"/v1/answers/{receipt['run_id']}").json()
    assert result["status"] == "completed", result
    assert result["result"]["claims"] == [] and result["result"]["status"] == "partial"
    assert result["result"]["stop_reason"] == "model_unavailable" and len(calls) == 1


@pytest.mark.integration
def test_empty_conflict_report_cannot_be_confirmed(api, monkeypatch):
    from langgraph.checkpoint.postgres import PostgresSaver

    from backend.worker import claim, process

    seed_ready(api)

    def unavailable(*args, **kwargs):
        raise ValueError("Synthetic provider failure")

    monkeypatch.setattr("backend.agent.llm.structured", unavailable)
    receipt = api.post(
        "/v1/answers",
        headers={"Idempotency-Key": "empty-conflict"},
        json={"query": "Check conflict", "intent": "conflicts"},
    ).json()
    with saver_connection() as connection:
        process(claim("empty-conflict-worker"), PostgresSaver(connection))
    body = api.get(f"/v1/answers/{receipt['run_id']}").json()["result"]
    assert not body["claims"]
    response = api.post(
        f"/v1/answers/{receipt['run_id']}/review",
        json={
            "decision": "confirmed",
            "rationale": "Must not confirm empty evidence",
            "expected_updated_at": body["updated_at"],
        },
    )
    assert response.status_code == 409


@pytest.mark.integration
def test_comparison_preserves_side_order_and_enforces_paired_claims(api, monkeypatch):
    from langgraph.checkpoint.postgres import PostgresSaver

    from backend.agent import Action, Answer, Citation, Claim, Verdict
    from backend.worker import claim, process

    first, second = seed_ready(api, key="old"), seed_ready(api, key="new")
    calls = []

    def model(system, payload, schema, run_id, **kwargs):
        if schema is Action:
            calls.append(1)
            return Action(
                tool="compare" if len(calls) == 1 else "finish",
                query="",
                version_id="",
                section_id="",
                missing_facts=[],
                sufficient=len(calls) > 1,
                question="",
            )
        if schema is Answer:
            ledger = payload["evidence"]
            assert ledger["comparison"]["left_version_id"] == first["version_id"]
            assert ledger["comparison"]["right_version_id"] == second["version_id"]
            assert ledger["comparison"]["changes"] == []
            eid, item = next(
                (k, v) for k, v in ledger["evidence"].items() if "seven years" in v["text"]
            )
            return Answer(
                claims=[
                    Claim(
                        text="The retention period changed.",
                        kind="change",
                        citations=[Citation(evidence_id=eid, quote=item["text"])],
                    )
                ],
                gaps=[],
            )
        return Verdict(supported_claims=[0], gaps=[])

    monkeypatch.setattr("backend.agent.llm.structured", model)
    receipt = api.post(
        "/v1/answers",
        headers={"Idempotency-Key": "comparison"},
        json={
            "query": "Compare these revisions",
            "intent": "compare",
            "scope": "selected_versions",
            "version_ids": [first["version_id"], second["version_id"]],
        },
    ).json()
    with saver_connection() as connection:
        process(claim("comparison-worker"), PostgresSaver(connection))
    result = api.get(f"/v1/answers/{receipt['run_id']}").json()
    assert result["status"] == "completed", result
    assert result["result"]["claims"] == []
    assert any("paired sources" in gap for gap in result["result"]["gaps"])
