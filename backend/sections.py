"""Canonical subsection maps. Batch boundaries are explicit, never guessed away."""

from difflib import SequenceMatcher

from docling_core.types.doc import DoclingDocument
from docling_core.types.doc.items.text import SectionHeaderItem, TitleItem

from backend import storage


def build_map(batches: list[dict]) -> dict:
    sections, items = [], {}
    for batch in batches:
        doc = DoclingDocument.model_validate(storage.get_json(batch["object_ref"]))
        namespace = batch["namespace"]
        stack = []
        root = {
            "section_id": f"{namespace}:root",
            "title": "Document",
            "parent_id": None,
            "item_ids": [],
            "boundary": "batch_bounded" if len(batches) > 1 else "complete",
        }
        sections.append(root)
        stack.append((-1, root))
        for item, _ in doc.iterate_items():
            if isinstance(item, (SectionHeaderItem, TitleItem)):
                level = item.level if isinstance(item, SectionHeaderItem) else 0
                while len(stack) > 1 and stack[-1][0] >= level:
                    stack.pop()
                section = {
                    "section_id": f"{namespace}:{item.self_ref}",
                    "title": item.text,
                    "parent_id": stack[-1][1]["section_id"],
                    "item_ids": [],
                    "boundary": "complete" if len(batches) == 1 else "batch_bounded",
                }
                sections.append(section)
                stack.append((level, section))
            text = getattr(item, "text", None)
            if text is None and hasattr(item, "export_to_markdown"):
                text = item.export_to_markdown(doc=doc)
            if not text or not text.strip():
                continue
            item_id = f"{namespace}:{item.self_ref}"
            sources = []
            for prov in item.prov or [None]:
                sources.append(
                    {
                        "source_item_ref": item_id,
                        "page_number": prov.page_no + batch["page_offset"] if prov else None,
                        "source_locator": {
                            "batch": namespace,
                            "item_ref": item.self_ref,
                            "page_offset": batch["page_offset"],
                            "provenance": prov.model_dump(mode="json") if prov else None,
                        },
                    }
                )
            items[item_id] = {
                "text": text,
                "sources": sources,
                "section_id": stack[-1][1]["section_id"],
            }
            for _, section in stack:
                section["item_ids"].append(item_id)
    return {"schema_version": 1, "sections": sections, "items": items}


def load_map(version: dict) -> dict:
    manifest = version["artifact_manifest"]
    if ref := manifest.get("section_map"):
        return storage.get_json(ref)
    # Legacy evidence is read through a derived map without rewriting published chunks.
    return build_map(manifest.get("batches", []))


def outline(section_map: dict) -> list[dict]:
    return [
        {k: row[k] for k in ("section_id", "title", "parent_id", "boundary")}
        for row in section_map["sections"]
    ]


def fetch(section_map: dict, section_id: str, max_chars: int = 16000) -> dict:
    section = next((s for s in section_map["sections"] if s["section_id"] == section_id), None)
    if section is None:
        raise ValueError("Section not found")
    rows, count = [], 0
    for item_id in section["item_ids"]:
        item = section_map["items"][item_id]
        if count + len(item["text"]) > max_chars:
            break
        rows.append({"item_id": item_id, **item})
        count += len(item["text"])
    complete = len(rows) == len(section["item_ids"])
    return {
        **section,
        "items": rows,
        "coverage": section["boundary"] if complete else "budget_limited",
        "examined_items": len(rows),
        "total_items": len(section["item_ids"]),
    }


def compare_maps(left: dict, right: dict) -> dict:
    """Deterministic reading-order changes; semantic clause alignment is separate."""

    def ordered_items(mapping):
        return [
            item_id
            for section in mapping["sections"]
            if section["parent_id"] is None
            for item_id in section["item_ids"]
        ]

    left_ids, right_ids = ordered_items(left), ordered_items(right)
    left_text = [" ".join(left["items"][key]["text"].split()) for key in left_ids]
    right_text = [" ".join(right["items"][key]["text"].split()) for key in right_ids]
    changes = [
        {"kind": kind, "left_item_ids": left_ids[a:b], "right_item_ids": right_ids[c:d]}
        for kind, a, b, c, d in SequenceMatcher(
            None, left_text, right_text, autojunk=False
        ).get_opcodes()
        if kind != "equal"
    ]
    return {
        "method": "deterministic reading-order text diff",
        "changes": changes[:40],
        "changes_limited": len(changes) > 40,
        "boundary_certain": all(
            s["boundary"] == "complete" for s in [*left["sections"], *right["sections"]]
        ),
    }
