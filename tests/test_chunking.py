import tiktoken
from docling_core.types.doc import DocItemLabel, DoclingDocument

from backend.chunking import chunk_document


def run(doc, maximum=128, minimum=40):
    return chunk_document(
        doc.export_to_dict(), "batch-1", 0, maximum, "cl100k_base", min_tokens=minimum
    )


def test_headings_are_context_not_empty_embedding_records():
    doc = DoclingDocument(name="cv")
    doc.add_heading("WORK EXPERIENCE", level=1)
    doc.add_heading("Company", level=2)
    item = doc.add_text(label=DocItemLabel.TEXT, text="Built an audited document search system.")
    doc.add_heading("EDUCATION", level=1)
    chunks = run(doc)
    assert len(chunks) == 1
    assert chunks[0]["body_text"].strip()
    assert "WORK EXPERIENCE" in chunks[0]["context_text"]
    assert any(s["source_item_ref"] == f"batch-1:{item.self_ref}" for s in chunks[0]["sources"])


def test_related_short_sections_keep_all_facts_and_sources():
    doc = DoclingDocument(name="policy")
    doc.add_heading("Retention", level=1)
    doc.add_heading("Rule", level=2)
    rule = doc.add_text(label=DocItemLabel.TEXT, text="Keep closed records for seven years.")
    doc.add_heading("Exception", level=2)
    exception = doc.add_text(label=DocItemLabel.TEXT, text="A legal hold blocks destruction.")
    chunks = run(doc)
    assert len(chunks) == 1
    context = chunks[0]["context_text"]
    assert "Rule" in context and "Exception" in context
    assert context.index("seven years") < context.index("legal hold")
    refs = {s["source_item_ref"] for s in chunks[0]["sources"]}
    assert {f"batch-1:{rule.self_ref}", f"batch-1:{exception.self_ref}"} <= refs


def test_short_unrelated_section_is_preserved_without_forced_merge():
    doc = DoclingDocument(name="policy")
    for heading, body in [
        ("Retention", "Keep records for seven years."),
        ("Travel", "Flights need approval."),
    ]:
        doc.add_heading(heading, level=1)
        doc.add_text(label=DocItemLabel.TEXT, text=body)
    chunks = run(doc)
    assert len(chunks) == 2
    assert chunks[0]["heading_path"] == ["Retention"]
    assert chunks[1]["heading_path"] == ["Travel"]


def test_contextual_budget_and_source_coverage_survive_long_content():
    doc = DoclingDocument(name="policy")
    doc.add_heading("Detailed retention requirements and exception approval procedure", level=1)
    first = doc.add_text(label=DocItemLabel.TEXT, text="Keep the audit trail. " * 80)
    doc.add_heading("Exceptions", level=2)
    second = doc.add_text(label=DocItemLabel.TEXT, text="Approval must be recorded. " * 50)
    chunks = run(doc, maximum=64, minimum=48)
    encoding = tiktoken.get_encoding("cl100k_base")
    assert len(chunks) > 1
    assert all(c["token_count"] == len(encoding.encode(c["context_text"])) <= 64 for c in chunks)
    refs = {s["source_item_ref"] for c in chunks for s in c["sources"]}
    assert {f"batch-1:{first.self_ref}", f"batch-1:{second.self_ref}"} <= refs
