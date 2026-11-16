"""Validate source-grounded labels or run a bounded API evaluation; no LLM judge."""

import argparse
import hashlib
import json
import re
import time
from uuid import uuid4

import httpx
import pypdfium2 as pdfium

from backend.config import settings
from scripts.build_golden_dataset import DEST, normalize
from scripts.evaluation_types import GoldenCase
from scripts.financial_test_docs import OUTPUT


def term_normalize(value):
    """Allow spelling variants in diagnostics without changing exact quote validation."""
    value = re.sub(r"[-–—]", " ", normalize(value))
    return normalize(re.sub(r"\byears\b", "year", value))


def validate():
    manifest = json.loads((DEST / "manifest.json").read_text())
    cases = [
        GoldenCase.model_validate_json(line).model_dump()
        for line in (DEST / "golden.jsonl").read_text().splitlines()
    ]
    assert len(cases) == manifest["cases"] == len({c["id"] for c in cases})
    documents = {d["file"]: d for d in manifest["documents"]}
    pages = {}
    for name, doc in documents.items():
        path = OUTPUT / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == doc["sha256"], (
            f"PDF changed: {name}"
        )
        with pdfium.PdfDocument(path) as pdf:
            assert len(pdf) == doc["pages"] == 3
            pages[name] = [page.get_textpage().get_text_range() for page in pdf]
    evidence_count = 0
    for case in cases:
        assert set(case["scope"]["documents"]) <= set(documents)
        if case["parent_case_id"]:
            assert any(
                p["id"] == case["parent_case_id"]
                and p["actor"] == case["actor"]
                and p["scope"] == case["scope"]
                for p in cases
            )
        for fact in case["expected"]["required_facts"]:
            for source in fact["evidence"]:
                assert source["document"] in case["scope"]["documents"]
                assert (
                    case["actor"] != "reviewer"
                    or documents[source["document"]]["reviewer_can_read"]
                )
                assert normalize(source["quote"]) in normalize(
                    pages[source["document"]][source["page"] - 1]
                ), (case["id"], source)
                evidence_count += 1
    return cases, {
        "dataset_id": manifest["dataset_id"],
        "cases": len(cases),
        "pdfs": len(documents),
        "pages": 15,
        "verified_evidence_references": evidence_count,
        "validation": "passed",
    }


def score(case, response, bindings, http_status=200):
    if (
        http_status == 422
        and case["expected"]["answerability"] == "unanswerable"
        and response.get("detail") == "No ready applicable revisions in this scope"
    ):
        return {
            "outcome": "no_eligible_source",
            "automatic_checks_passed": True,
            "human_review_required": True,
        }
    if http_status != 200:
        return {
            "outcome": "api_error",
            "automatic_checks_passed": False,
            "http_status": http_status,
        }
    if response.get("status") == "waiting_for_input":
        return {
            "outcome": "clarification",
            "automatic_checks_passed": case["expected"]["answerability"] == "clarification",
            "human_review_required": True,
        }
    result = response.get("result") or {}
    if (result.get("stop_reason") or "").startswith("model_"):
        return {
            "outcome": "provider_blocked",
            "automatic_checks_passed": False,
            "reason": result["stop_reason"],
        }
    if result.get("stop_reason") == "deadline":
        return {"outcome": "budget_blocked", "automatic_checks_passed": False}
    if response.get("status") != "completed":
        return {
            "outcome": "execution_incomplete",
            "automatic_checks_passed": False,
            "status": response.get("status"),
        }
    if case["expected"]["answerability"] == "clarification":
        return {
            "outcome": "clarification_not_requested",
            "automatic_checks_passed": False,
            "human_review_required": True,
        }
    claims = result.get("claims", [])
    answer = " ".join(c["text"] for c in claims)
    output = normalize(answer + " " + " ".join(result.get("gaps", [])))
    fact_output = (
        answer if case["expected"]["answerability"] in ("answerable", "partial") else output
    )
    fact_output = term_normalize(fact_output)
    fact_checks = {
        f["id"]: any(term_normalize(term) in fact_output for term in f["answer_terms_any"])
        for f in case["expected"]["required_facts"]
    }
    evidence = result.get("evidence", {})
    exact = True
    cited = set()
    for claim in claims:
        if not claim.get("citations"):
            exact = False
        for citation in claim.get("citations", []):
            item = evidence.get(citation["evidence_id"])
            if (
                not item
                or not citation["quote"].strip()
                or normalize(citation["quote"]) not in normalize(item["text"])
            ):
                exact = False
                continue
            filename = bindings.get(item["document_id"])
            for source in item.get("sources", []):
                if source.get("page_number"):
                    cited.add((filename, source["page_number"]))
    document_names = {name for name, _ in cited if name}
    allowed = set(case["scope"]["documents"])
    scope_ok = all(bindings.get(item["document_id"]) in allowed for item in evidence.values())
    forbidden = [
        word for word in case["expected"]["forbidden_answer_strings"] if normalize(word) in output
    ]
    pages = {
        (source["document"], source["page"])
        for fact in case["expected"]["required_facts"]
        for source in fact["evidence"]
    }
    checks = {
        "term_coverage": all(fact_checks.values()),
        "exact_quotes": exact,
        "minimum_cited_documents": len(document_names)
        >= case["expected"]["minimum_distinct_cited_documents"],
        "scope": scope_ok,
        "no_forbidden_answer_strings": not forbidden,
        "bounded_iterations": result.get("iterations", 0) <= case["checks"]["max_agent_iterations"],
    }
    if case["expected"]["answerability"] == "answerable":
        checks["nonempty_answer"] = bool(claims)
    if case["expected"]["answerability"] == "unanswerable":
        checks["explained_missing_evidence"] = any(
            term in output
            for term in (
                "not provided",
                "not supplied",
                "outside",
                "not available",
                "unavailable",
                "no applicable",
                "not established",
                "cannot determine",
                "does not",
            )
        )
    return {
        "outcome": "scored",
        "automatic_checks_passed": all(checks.values()),
        "checks": checks,
        "fact_term_checks": fact_checks,
        "citation_page_recall": len(pages & cited) / len(pages) if pages else None,
        "forbidden_strings_found": forbidden,
        "human_review_required": True,
        "note": "Automatic integrity/term diagnostics do not establish semantic entailment or answer correctness.",
    }


def run_cases(cases, ids, max_cases, pause):
    unknown = ids - {case["id"] for case in cases}
    if unknown:
        raise ValueError(f"Unknown case IDs: {sorted(unknown)}")
    if max_cases < 1:
        raise ValueError("--max-cases must be positive")
    selected = [case for case in cases if not ids or case["id"] in ids]
    if not ids:
        selected = selected[:max_cases]
    needed = {c["id"] for c in selected}
    for c in list(selected):
        if c["parent_case_id"]:
            needed.add(c["parent_case_id"])
    selected = [c for c in cases if c["id"] in needed]
    if len(selected) > max_cases:
        raise ValueError("Selected cases plus parents exceed --max-cases")
    receipts = json.loads((OUTPUT / "receipts.json").read_text())
    by_file = {r["filename"]: r for r in receipts}
    bindings = {r["document_id"]: r["filename"] for r in receipts}
    results = {}
    out = DEST / ("results-" + time.strftime("%Y%m%d-%H%M%S") + ".jsonl")
    s = settings()
    reference_date = json.loads((DEST / "manifest.json").read_text())["reference_date"]
    with httpx.Client(base_url=s.api_url, timeout=45) as client:
        health = client.get("/health").json()
        for case in selected:
            parent = case.get("parent_case_id")
            if parent and (
                results[parent]["score"]["outcome"] == "provider_blocked"
                or not results[parent].get("response", {}).get("result")
            ):
                row = {
                    "id": case["id"],
                    "score": {"outcome": "dependency_blocked", "automatic_checks_passed": False},
                }
            else:
                scope = case["scope"]
                payload = {
                    "query": case["question"],
                    "scope": scope["mode"],
                    "document_ids": [by_file[name]["document_id"] for name in scope["documents"]],
                    "intent": "compare"
                    if case["category"] == "comparison"
                    else "conflicts"
                    if case["category"] == "true_conflict"
                    else "investigate"
                    if case["category"] == "multi_hop"
                    else "answer",
                }
                if scope["mode"] == "selected_versions":
                    payload["version_ids"] = [
                        by_file[name]["version_id"] for name in scope["documents"]
                    ]
                if scope["effective_on"]:
                    payload["effective_on"] = scope["effective_on"]
                elif scope["mode"] == "current":
                    payload["scope"], payload["effective_on"] = "effective_on", reference_date
                if parent:
                    payload["parent_run_id"] = results[parent]["response"]["run_id"]
                headers = {
                    "X-API-Key": s.identities[case["actor"]],
                    "Idempotency-Key": "golden-" + case["id"] + "-" + str(uuid4()),
                }
                response = client.post("/v1/answers", headers=headers, json=payload)
                body = response.json()
                status = response.status_code
                if status == 202:
                    for _ in range((s.agent_timeout_seconds + 60) // 2):
                        response = client.get("/v1/answers/" + body["run_id"], headers=headers)
                        body = response.json()
                        status = response.status_code
                        if status != 200 or body["status"] in (
                            "completed",
                            "failed",
                            "cancelled",
                            "waiting_for_input",
                        ):
                            break
                        time.sleep(2)
                row = {
                    "id": case["id"],
                    "provider": health.get("llm_provider"),
                    "model": health.get("llm_model"),
                    "reference_date": reference_date,
                    "runner_config": {
                        "llm_reasoning_effort": s.llm_reasoning_effort,
                        "llm_timeout_seconds": s.llm_timeout_seconds,
                        "agent_timeout_seconds": s.agent_timeout_seconds,
                        "max_iterations": s.agent_max_iterations,
                    },
                    "response": body,
                    "score": score(case, body, bindings, status),
                }
            results[case["id"]] = row
            with out.open("a") as f:
                f.write(json.dumps(row, default=str, ensure_ascii=False) + "\n")
            print(case["id"], row["score"], flush=True)
            if pause:
                time.sleep(min(45, pause))
    print("Results:", out)


def publish_langfuse(cases):
    from uuid import NAMESPACE_URL, uuid5

    from backend.telemetry import client

    name = "financial-ui-golden-v1"
    manifest = json.loads((DEST / "manifest.json").read_text())
    dataset = client().create_dataset(
        name=name,
        description="30 source-grounded synthetic financial cases across five three-page PDFs.",
        metadata={
            "schema_version": 1,
            "reference_date": manifest["reference_date"],
            "source_manifest": manifest["documents"],
            "label_origin": manifest["label_origin"],
        },
    )
    for case in cases:
        client().create_dataset_item(
            dataset_name=name,
            id=str(uuid5(NAMESPACE_URL, name + "/" + case["id"])),
            input={
                "question": case["question"],
                "actor": case["actor"],
                "scope": case["scope"],
                "parent_case_id": case.get("parent_case_id"),
            },
            expected_output=case["expected"],
            metadata={
                "case_id": case["id"],
                "category": case["category"],
                "checks": case["checks"],
                "reference_date": manifest["reference_date"],
                "review_notes": case["review_notes"],
            },
        )
    saved = client().get_dataset(name)
    assert len(saved.items) == len(cases)
    record = {
        "name": name,
        "id": dataset.id,
        "items": len(saved.items),
        "project_id": "opzen-documents",
    }
    (DEST / "langfuse-dataset.json").write_text(json.dumps(record, indent=2))
    print("Langfuse golden dataset:", record)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run",
        action="store_true",
        help="Call the configured application/model; default is offline validation only",
    )
    parser.add_argument("--ids", default="", help="Comma-separated case IDs")
    parser.add_argument("--max-cases", type=int, default=5)
    parser.add_argument("--pause", type=int, default=30)
    parser.add_argument(
        "--publish-langfuse",
        action="store_true",
        help="Upsert validated gold items into the configured local Langfuse project; no model calls",
    )
    args = parser.parse_args()
    cases, report = validate()
    (DEST / "validation.json").write_text(json.dumps(report, indent=2))
    print(report)
    if args.publish_langfuse:
        publish_langfuse(cases)
    if args.run:
        run_cases(cases, set(filter(None, args.ids.split(","))), args.max_cases, max(0, args.pause))
