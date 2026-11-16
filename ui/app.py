import json
from datetime import date
from functools import wraps
from uuid import uuid4

import httpx
import streamlit as st

from backend.config import settings
from ui.client import Api

st.set_page_config(page_title="Opzen · Documents", page_icon="◈", layout="wide")
st.markdown(
    """
<style>
.block-container {max-width:1260px; padding-top:2.5rem;}
[data-testid="stSidebar"] {border-right:1px solid #e0e5db;}
h1 {letter-spacing:-.045em; font-weight:650;}
h2,h3 {letter-spacing:-.025em;}
[data-testid="stMetricValue"] {font-size:1.8rem;}
[data-testid="stMetric"] {background:#f0f1eb; border:1px solid #e0e5db; padding:1rem; border-radius:12px;}
.stButton button {border-radius:8px;}
</style>
""",
    unsafe_allow_html=True,
)

ACTIVE = {"uploading", "queued", "running"}
STATUS_LABEL = {
    "chunked": "Chunked · awaiting embeddings",
    "ready": "Ready for retrieval",
    "review_required": "Needs review",
    "failed": "Failed",
    "cancelled": "Cancelled",
}


def message(exc):
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            detail = exc.response.json().get("detail", "Request failed")
        except ValueError:
            detail = "Request failed"
        st.error(str(detail))
    else:
        st.error("Cannot reach the API. Start it with: uv run uvicorn backend.api:app --port 8000")


def api_errors(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except httpx.HTTPError as exc:
            message(exc)

    return wrapped


@st.dialog("Add documents", width="large")
def upload_dialog(api: Api, user: str, document_id=None):
    st.caption(
        "Add a revision to this document."
        if document_id
        else "Originals are retained. Processing continues if you leave this page."
    )
    with st.form("upload", clear_on_submit=False):
        files = st.file_uploader(
            "Choose files",
            type=["pdf", "docx", "png", "jpg", "jpeg"],
            accept_multiple_files=not bool(document_id),
        )
        title = st.text_input("Title (optional)", placeholder="Uses the filename when left blank")
        a, b = st.columns(2)
        kind = a.selectbox("Document type", ["policy", "procedure", "contract", "guideline"])
        department = b.selectbox(
            "Department", ["General", "Operations", "Compliance", "Legal", "Finance", "HR"]
        )
        a, b = st.columns(2)
        approval = a.selectbox("Approval", ["draft", "approved"])
        author = b.text_input("Recorded author (optional)")
        a, b = st.columns(2)
        start = a.date_input("Effective from", value=date.today())
        end = b.date_input("Effective until (optional)", value=None)
        share = (
            st.multiselect(
                "Also allow read access", [u for u in settings().identities if u != user]
            )
            if not document_id
            else []
        )
        st.caption(
            f"Up to {settings().max_upload_mb} MB per file. PDF limit: {settings().max_pages} pages. Password-protected files need an unencrypted copy."
        )
        submitted = st.form_submit_button("Upload and process", type="primary", width="stretch")
    if not submitted:
        return
    items = files if isinstance(files, list) else ([files] if files else [])
    if not items:
        st.warning("Choose at least one file.")
        return
    if end and end <= start:
        st.error("Effective end must be after effective start.")
        return
    metadata = {
        "title": title,
        "document_type": kind,
        "department_code": department,
        "approval_status": approval,
        "recorded_author": author,
        "effective_from": start.isoformat(),
        "effective_to": end.isoformat() if end else None,
        "share_with": share,
    }
    successes = []
    keys = st.session_state.setdefault("upload_keys", {})
    for file in items:
        # Keep the key stable across retries, including changed metadata in the fingerprint.
        import hashlib

        fingerprint = hashlib.sha256(
            file.getvalue()
            + json.dumps(metadata, sort_keys=True).encode()
            + (document_id or "").encode()
            + file.name.encode()
        ).hexdigest()
        key = keys.setdefault(fingerprint, str(uuid4()))
        path = f"/v1/documents/{document_id}/versions" if document_id else "/v1/documents"
        try:
            receipt = api.post(
                path,
                headers={"Idempotency-Key": key},
                files={"file": (file.name, file.getvalue(), file.type)},
                data={"metadata": json.dumps(metadata)},
            )
            successes.append(receipt)
            st.success(f"{file.name} is queued. You can close this panel.")
        except httpx.HTTPError as exc:
            message(exc)
    if successes:
        st.session_state["last_upload"] = successes[-1]
        st.query_params["document"] = successes[-1]["document_id"]
        st.session_state["upload_notice"] = f"{len(successes)} document(s) queued for processing."
        st.rerun()


@st.fragment(run_every=3)
@api_errors
def activity(api: Api):
    rows = api.get("/v1/runs")
    if not rows:
        st.info("Your uploads and processing runs will appear here.")
        return
    for run in rows:
        with st.container(border=True):
            a, b, c = st.columns([6, 2, 2])
            a.write(f"**{run['workflow_type'].capitalize()}** · {run['status'].replace('_', ' ')}")
            a.caption(run.get("progress", {}).get("stage", "Waiting for worker"))
            a.caption(f"Run {run['run_id'][:8]} · {run['created_at'][:19].replace('T', ' ')} UTC")
            if b.button("Open document", key=f"open-run-{run['run_id']}"):
                st.query_params["document"] = run["document_id"]
                st.rerun()
            if run["status"] in ACTIVE:
                if c.button("Cancel", key=f"cancel-{run['run_id']}"):
                    api.post(f"/v1/runs/{run['run_id']}/cancel")
                    st.rerun(scope="fragment")
            elif run["status"] in {"failed", "cancelled"}:
                if c.button("Retry", key=f"retry-{run['run_id']}"):
                    api.post(f"/v1/runs/{run['run_id']}/retry")
                    st.rerun(scope="fragment")


@st.fragment(run_every=4)
@api_errors
def document_detail(api: Api, doc_id: str, user: str):
    document = api.get(f"/v1/documents/{doc_id}")
    versions = api.get(f"/v1/documents/{doc_id}/versions")
    if not versions:
        st.info("Upload is being registered.")
        return
    a, b = st.columns([8, 2])
    a.subheader(document["display_filename"])
    if document["owner_subject"] == f"demo:{user}" and b.button("Add revision", key="add-revision"):
        upload_dialog(api, user, doc_id)
    version_id = st.selectbox(
        "Revision",
        [v["version_id"] for v in versions],
        format_func=lambda x: next(
            f"Revision {v['revision_number']} · {STATUS_LABEL.get(v['status'], v['status'])}"
            for v in versions
            if v["version_id"] == x
        ),
        key=f"version-{doc_id}",
    )
    version = next(v for v in versions if v["version_id"] == version_id)
    if document["owner_subject"] == f"demo:{user}" and version["status"] in {
        "chunked",
        "ready",
        "review_required",
        "failed",
    }:
        if st.button("Reprocess with current settings", key=f"reprocess-{version_id}"):
            key = st.session_state.setdefault(f"reprocess-key-{version_id}", str(uuid4()))
            receipt = api.post(
                f"/v1/versions/{version_id}/reprocess", headers={"Idempotency-Key": key}
            )
            st.session_state["last_upload"] = receipt
            st.toast("New processing revision queued. Original bytes are reused.")
            st.rerun()
    st.caption(
        f"{version['document_type'].capitalize()} · {version['department_code']} · {version['approval_status'].capitalize()}"
    )
    if version["status"] == "chunked":
        st.info(
            "Extraction and citations are available. Embeddings are not configured, so this revision is not published for retrieval."
        )
    elif version["status"] in {"failed", "review_required"}:
        st.warning(
            "This revision is staged and needs attention. See Ingestion activity and extraction warnings."
        )
    elif version["status"] not in {"ready", "cancelled"}:
        st.info(
            f"Processing: {version['status'].replace('_', ' ')}. This panel refreshes automatically."
        )
    overview, source, extraction, assets, chunks, history = st.tabs(
        ["Overview", "Source", "Extraction", "Tables & images", "Chunks & citations", "History"]
    )
    with overview:
        a, b, c = st.columns(3)
        a.metric("Revision", version["revision_number"])
        b.metric("Chunks", version["readiness_manifest"].get("chunks", "—"))
        c.metric("Original size", f"{version['size_bytes'] / 1024:.1f} KB")
        st.write("**Title:**", version["title"])
        st.write(
            "**Applicable dates:**",
            f"{version['effective_from'] or 'Unknown'} → {version['effective_to'] or 'No end specified'}",
        )
        st.caption(f"Document {doc_id}\n\nSource checksum {version['checksum']}")
        if document["owner_subject"] == f"demo:{user}":
            with st.expander("Read access"):
                target = st.selectbox(
                    "Identity",
                    [u for u in settings().identities if u != user],
                    key=f"grant-{doc_id}",
                )
                grant, revoke = st.columns(2)
                for column, label, allowed in [
                    (grant, "Grant read access", True),
                    (revoke, "Revoke read access", False),
                ]:
                    if column.button(label, key=f"grant-{allowed}-{doc_id}"):
                        api.request(
                            "PUT",
                            f"/v1/documents/{doc_id}/permissions",
                            json={"user": target, "allow": allowed},
                        )
                        st.success("Access updated.")
    with source:
        cache_key = f"source-{user}-{version_id}"
        if st.button("Load original", key=f"load-{version_id}"):
            st.session_state[cache_key] = api.request(
                "GET", f"/v1/versions/{version_id}/content"
            ).content
        if data := st.session_state.get(cache_key):
            st.download_button(
                "Download original",
                data,
                file_name=document["display_filename"],
                mime=version["mime_type"],
            )
            if version["mime_type"].startswith("image/"):
                st.image(data)
            elif version["mime_type"] == "application/pdf" and hasattr(st, "pdf"):
                try:
                    st.pdf(data, height=550)
                except Exception:
                    st.caption("Use Download original to inspect the PDF in your viewer.")
    manifest = version["artifact_manifest"]
    with extraction:
        for warning in version["quality_report"].get("warnings", []):
            st.warning(warning)
        if manifest.get("markdown"):
            preview = api.request(
                "GET", f"/v1/versions/{version_id}/content", params={"kind": "markdown"}
            ).text
            st.text_area(
                "Extracted Markdown · source preview",
                preview,
                height=420,
                disabled=True,
                key=f"md-{version_id}",
            )
        else:
            st.caption("Extracted text appears after conversion.")
        if manifest.get("batches"):
            st.caption(
                f"{len(manifest['batches'])} canonical artifact(s). These retain structure and provenance; Markdown is a preview."
            )
            with st.expander("Inspect artifact manifest"):
                st.json(manifest)
    with assets:
        for i, table in enumerate(manifest.get("tables", [])):
            with st.expander(f"Table {i + 1} · {table['namespace']}"):
                content = api.get(f"/v1/versions/{version_id}/content", kind="tables", index=i)
                if content.get("caption"):
                    st.caption(content["caption"])
                st.dataframe(content["rows"], width="stretch")
        for i, picture in enumerate(manifest.get("pictures", [])):
            with st.expander(f"Image {i + 1}"):
                if st.button("Load image", key=f"picture-{version_id}-{i}"):
                    st.image(
                        api.request(
                            "GET",
                            f"/v1/versions/{version_id}/content",
                            params={"kind": "pictures", "index": i},
                        ).content,
                        caption=picture.get("caption", ""),
                    )
        if not manifest.get("tables") and not manifest.get("pictures"):
            st.caption("No exported tables or images yet.")
    with chunks:
        result = api.get(f"/v1/versions/{version_id}/chunks", limit=30)
        st.caption(
            f"Showing up to 30 of {result['total']} chunks. Citations resolve to canonical source items."
        )
        for chunk in result["items"]:
            heading = " › ".join(chunk["heading_path"]) or "Document body"
            with st.expander(
                f"{chunk['ordinal'] + 1:02d} · {heading} · {chunk['token_count']} tokens"
            ):
                st.text(chunk["context_text"])
                for span in chunk["sources"]:
                    label = (
                        f"Page {span['page_number']}"
                        if span["page_number"]
                        else "Structural anchor · no physical page"
                    )
                    st.caption(f"{label} · {span['source_item_ref']}")
                st.json([s["source_locator"] for s in chunk["sources"]], expanded=False)
    with history:
        st.dataframe(
            [
                {
                    "Revision": v["revision_number"],
                    "Title": v["title"],
                    "Status": v["status"],
                    "Approval": v["approval_status"],
                    "Effective from": v["effective_from"],
                    "Published": v["published_at"],
                    "Created": v["created_at"],
                }
                for v in versions
            ],
            hide_index=True,
            width="stretch",
        )


def library(api: Api, user: str):
    a, b = st.columns([8, 2])
    a.caption("YOUR KNOWLEDGE WORKSPACE")
    if b.button("＋ Add documents", type="primary", width="stretch"):
        upload_dialog(api, user)
    st.title("Documents")
    st.write(
        "Bring your policies, contracts, and procedures into one place. Inspect every source before it becomes evidence."
    )
    search = st.text_input(
        "Search filenames", placeholder="Find a document…", label_visibility="collapsed"
    )
    rows = api.get("/v1/documents", search=search)
    a, b, c = st.columns(3)
    a.metric("Documents shown", len(rows))
    b.metric(
        "Ready revisions",
        sum(r["latest_version"]["status"] == "ready" for r in rows if r["latest_version"]),
    )
    c.metric(
        "Needs attention",
        sum(
            r["latest_version"]["status"] in {"failed", "review_required"}
            for r in rows
            if r["latest_version"]
        ),
    )
    st.write("")
    if not rows:
        with st.container(border=True):
            st.subheader("Your evidence starts here")
            st.write(
                "Upload a PDF, Word document, or scan. You’ll be able to inspect extracted text, tables, and source citations."
            )
            if st.button("Upload your first document"):
                upload_dialog(api, user)
        return
    st.caption("Latest 50 authorized documents · revisions may be staged or future-effective")
    for doc in rows:
        v = doc["latest_version"]
        with st.container(border=True):
            a, b, c = st.columns([6, 3, 1])
            a.write(f"**{v['title'] if v else doc['display_filename']}**")
            a.caption(f"{doc['display_filename']} · {doc['revision_count']} revision(s)")
            if v:
                b.write(STATUS_LABEL.get(v["status"], v["status"].replace("_", " ").capitalize()))
                b.caption(f"{v['department_code']} · {v['approval_status']}")
            if c.button("Open", key=doc["document_id"]):
                st.query_params["document"] = doc["document_id"]
                st.rerun()


def main():
    if notice := st.session_state.pop("upload_notice", None):
        st.toast(notice)
    with st.sidebar:
        st.markdown("## ◈ opzen")
        st.caption("DOCUMENT INTELLIGENCE")
        page = st.radio(
            "Workspace",
            ["Documents", "Search", "Ask & investigate", "Ingestion activity"],
            label_visibility="collapsed",
            on_change=st.query_params.clear,
        )
        st.divider()
        user = st.selectbox(
            "Demo identity",
            list(settings().identities),
            index=list(settings().identities).index(settings().ui_demo_user),
        )
        st.caption(
            "Local development identities. Access is enforced by the API; this is not production sign-in."
        )
    if st.session_state.get("active_identity") != user:
        for key in list(st.session_state):
            if key.startswith(("agent_", "source-")):
                del st.session_state[key]
        st.session_state["active_identity"] = user
    api = Api(user)
    try:
        health = api.get("/health")
        if not health["database"] or not health["storage"]:
            st.warning(
                "Local services need setup. See README: start Docker services, then run bootstrap."
            )
            return
        doc_id = st.query_params.get("document")
        if doc_id:
            if st.button("← All documents"):
                st.query_params.clear()
                st.rerun()
            document_detail(api, doc_id, user)
        elif page in ("Search", "Ask & investigate"):
            from ui.ask import workspace

            workspace(api, search_only=page == "Search")
        elif page == "Ingestion activity":
            st.title("Ingestion activity")
            st.write("Durable processing runs, with real stage updates and recovery controls.")
            activity(api)
        else:
            library(api, user)
    except httpx.HTTPError as exc:
        message(exc)


if __name__ == "__main__":
    main()
