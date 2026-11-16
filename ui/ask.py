"""Search and durable evidence questions; state is scoped to the selected identity."""

import hashlib
import json
from uuid import uuid4

import httpx
import streamlit as st

from backend.retrieval import today


def source_action(api, item, key):
    version_id = item["version_id"]
    if st.button("Open exact revision", key=f"source-{key}"):
        # Reauthorize now; don't rely on a previous search response.
        api.get(f"/v1/documents/{item['document_id']}/versions")
        st.query_params["document"] = item["document_id"]
        st.query_params["version"] = version_id
        st.session_state[f"version-{item['document_id']}"] = version_id
        st.rerun()


def filters(api):
    docs = api.get("/v1/documents")
    titles = {
        d["document_id"]: (
            d["latest_version"]["title"] if d["latest_version"] else d["display_filename"]
        )
        for d in docs
    }
    a, b = st.columns(2)
    mode = a.selectbox(
        "Evidence scope", ["Current policies", "Effective on date", "Selected revisions"]
    )
    selected = b.multiselect(
        "Documents (optional)", list(titles), format_func=titles.get, max_selections=20
    )
    scope = {"scope": "current", "document_ids": selected}
    if mode == "Effective on date":
        scope.update(
            scope="effective_on",
            effective_on=st.date_input("Effective date", value=today()).isoformat(),
        )
    elif mode == "Selected revisions":
        candidates = {}
        for doc_id in selected:
            for version in api.get(f"/v1/documents/{doc_id}/versions"):
                if version["status"] == "ready":
                    candidates[version["version_id"]] = (
                        f"{version['title']} · revision {version['revision_number']} · {version['approval_status']}"
                    )
        ids = st.multiselect(
            "Ready revisions", list(candidates), format_func=candidates.get, max_selections=10
        )
        scope.update(scope="selected_versions", version_ids=ids)
        st.caption(
            "Historical, draft, and future-effective evidence is explicitly labeled by revision."
        )
    return scope


@st.fragment(run_every="3s")
def answer_view(api, run_id):
    try:
        data = api.get(f"/v1/answers/{run_id}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            st.session_state.pop("agent_run", None)
            st.warning("This run is unavailable with current access.")
            return
        raise
    st.subheader(data["question"])
    progress = data["progress"]
    st.caption(f"{data['status'].replace('_', ' ').capitalize()} · {progress.get('stage', '')}")
    if iteration := progress.get("iteration", progress.get("iterations")):
        st.caption(f"Exploration {iteration} / {progress.get('max_iterations', 5)}")
    if data["status"] in ("queued", "running"):
        if st.button("Cancel question", key=f"cancel-{run_id}"):
            api.post(f"/v1/runs/{run_id}/cancel")
        return
    if data["status"] == "waiting_for_input":
        st.info(progress["question"])
        with st.form(f"clarification-{run_id}"):
            reply = st.text_area("Clarification")
            if st.form_submit_button("Continue") and reply.strip():
                api.post(
                    f"/v1/runs/{run_id}/resume",
                    json={"interrupt_id": progress["interrupt_id"], "reply": reply},
                )
                st.rerun()
        return
    if data["status"] in ("failed", "cancelled"):
        if st.button("Retry from checkpoint", key=f"retry-{run_id}"):
            api.post(f"/v1/runs/{run_id}/retry")
            st.rerun()
    result = data.get("result")
    if not result:
        return
    if result["freshness_state"] != "current":
        st.warning(
            "Source scope changed. This result uses an earlier evidence snapshot; rerun for current findings."
        )
    for i, claim in enumerate(result["claims"]):
        with st.container(border=True):
            st.write(claim["text"])
            if claim["kind"] != "answer":
                st.caption(claim["kind"].replace("_", " ").capitalize())
            for j, citation in enumerate(claim["citations"]):
                item = result["evidence"][citation["evidence_id"]]
                with st.expander(f"Source: {item['title']}"):
                    st.text(citation["quote"])
                    pages = sorted({s["page_number"] for s in item["sources"] if s["page_number"]})
                    st.caption(
                        f"Pages: {', '.join(map(str, pages))}"
                        if pages
                        else "Structural source anchor · no physical page"
                    )
                    st.caption(f"Context coverage: {item['coverage']}")
                    source_action(api, item, f"{run_id}-{i}-{j}")
    if result["gaps"]:
        with st.expander("Evidence gaps and limits", expanded=True):
            for gap in result["gaps"]:
                st.write("• " + gap.replace("_", " "))
    if not result["claims"]:
        st.info("There is not enough supported evidence to answer this question.")
    with st.expander("Investigation steps"):
        st.dataframe(result["events"], hide_index=True)
        st.caption(
            f"{result['iterations']} / {result['max_iterations']} exploration iterations · {result['validation']}"
        )
    if data["intent"] == "conflicts" and any(c["kind"] == "conflict" for c in result["claims"]):
        st.caption(f"Review: {result['review_state']}")
        with st.form(f"review-{run_id}"):
            decision = st.selectbox("Decision", ["unresolved", "confirmed", "dismissed"])
            rationale = st.text_area("Review rationale")
            if st.form_submit_button("Save review") and rationale.strip():
                api.post(
                    f"/v1/answers/{run_id}/review",
                    json={
                        "decision": decision,
                        "rationale": rationale,
                        "expected_updated_at": result["updated_at"],
                    },
                )
                st.rerun()


def workspace(api, search_only=False):
    st.title("Search evidence" if search_only else "Ask your documents")
    st.write(
        "Find source passages by wording or meaning."
        if search_only
        else "Ask across documents, compare revisions, or investigate a policy question with cited evidence."
    )
    scope = filters(api)
    intent = "answer"
    if not search_only:
        label = st.selectbox(
            "Task",
            ["Answer question", "Compare revisions", "Check policy conflicts", "Investigate"],
        )
        intent = {
            "Answer question": "answer",
            "Compare revisions": "compare",
            "Check policy conflicts": "conflicts",
            "Investigate": "investigate",
        }[label]
    query = st.text_area(
        "Question",
        placeholder="Which retention rule applies, and who can approve an exception?",
        max_chars=2000,
    )
    follow = False
    if not search_only and st.session_state.get("agent_run"):
        follow = st.checkbox("Follow up on the open answer (same evidence scope)")
    if st.button("Search" if search_only else "Start", type="primary"):
        if not query.strip():
            st.warning("Enter a question.")
        elif scope["scope"] == "selected_versions" and not scope.get("version_ids"):
            st.warning("Choose ready revisions.")
        else:
            payload = {"query": query, **scope}
            if search_only:
                st.session_state["agent_search_request"] = payload
            else:
                payload["intent"] = intent
                if follow:
                    payload["parent_run_id"] = st.session_state["agent_run"]
                fingerprint = hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode()
                ).hexdigest()
                keys = st.session_state.setdefault("agent_keys", {})
                receipt = api.post(
                    "/v1/answers",
                    json=payload,
                    headers={"Idempotency-Key": keys.setdefault(fingerprint, str(uuid4()))},
                )
                st.session_state["agent_run"] = receipt["run_id"]
                st.session_state["agent_saved_run"] = receipt["run_id"]
                # Preserve the key only until a successful acknowledgment.
                keys.pop(fingerprint, None)
    if search_only:
        # Re-run searches when identity/scope changes; each source action reauthorizes.
        response = None
        if request := st.session_state.get("agent_search_request"):
            with st.spinner("Finding evidence…"):
                response = api.post("/v1/search", json=request)
        if response:
            for warning in response["warnings"]:
                st.warning(warning)
            if not response["items"]:
                st.info("No eligible evidence found.")
            for item in response["items"]:
                with st.container(border=True):
                    st.subheader(item["document"]["title"])
                    st.caption(
                        f"Revision {item['document']['revision_number']} · {' / '.join(item['heading_path'])}"
                    )
                    st.text(item["body_text"])
                    if st.button("Read subsection", key=f"expand-{item['chunk_id']}"):
                        evidence = api.get(f"/v1/chunks/{item['chunk_id']}/evidence")
                        for section in evidence["sections"]:
                            st.caption(section["coverage"])
                            st.text("\n\n".join(i["text"] for i in section["items"]))
                    source_action(api, {**item["document"]}, item["chunk_id"])
    else:
        runs = api.get("/v1/answers", limit=100)
        if runs:
            labels = {
                r["run_id"]: f"{r['question'][:65]} · {r['status']} · {r['run_id'][:8]}"
                for r in runs
            }
            options = list(labels)
            active = st.session_state.get("agent_run")
            chosen = st.selectbox(
                "Saved questions",
                options,
                index=options.index(active) if active in options else 0,
                format_func=labels.get,
                key="agent_saved_run",
            )
            st.session_state["agent_run"] = chosen
            answer_view(api, chosen)
