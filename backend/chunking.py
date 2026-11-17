import tiktoken
from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
from docling_core.transforms.chunker.tokenizer.openai import OpenAITokenizer
from docling_core.types.doc import DoclingDocument

from backend.storage import digest


def _merge_short_chunks(chunks, chunker, min_tokens, max_tokens):
    """Pack related text without crossing unrelated headings or losing item provenance."""
    count = chunker.tokenizer.count_tokens

    def candidate(left, right):
        if any(
            any(item.label in {"table", "picture", "caption"} for item in chunk.meta.doc_items)
            for chunk in (left, right)
        ):
            return None
        a, b = list(left.meta.headings or []), list(right.meta.headings or [])
        common = []
        for first, second in zip(a, b):
            if first != second:
                break
            common.append(first)
        if a != b and not common:
            return None

        def body(chunk, headings):
            suffix = headings[len(common) :]
            return "\n\n".join([*suffix, chunk.text])

        items = {item.self_ref: item for item in [*left.meta.doc_items, *right.meta.doc_items]}
        merged = left.model_copy(
            update={
                "text": body(left, a) + "\n\n" + body(right, b),
                "meta": left.meta.model_copy(
                    update={"headings": common or None, "doc_items": list(items.values())}
                ),
            }
        )
        return merged if count(chunker.contextualize(merged)) <= max_tokens else None

    chunks = list(chunks)
    index = 0
    while index < len(chunks):
        if count(chunks[index].text) >= min_tokens:
            index += 1
            continue
        # Prefer the previous neighbor, then the next; always retain reading order.
        for start in (index - 1, index):
            if start < 0 or start + 1 >= len(chunks):
                continue
            merged = candidate(chunks[start], chunks[start + 1])
            if merged is not None:
                chunks[start : start + 2] = [merged]
                index = start
                break
        else:
            index += 1
    return chunks


def chunk_document(
    canonical: dict,
    namespace: str,
    page_offset: int,
    max_tokens: int,
    encoding: str,
    model_name: str | None = None,
    model_revision: str = "",
    min_tokens: int = 100,
) -> list[dict]:
    document = DoclingDocument.model_validate(canonical)
    if model_name:
        from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer

        from backend.embeddings import tokenizer as hf_tokenizer

        raw = hf_tokenizer(model_name, model_revision)
        if max_tokens + raw.num_special_tokens_to_add() > raw.model_max_length:
            raise ValueError("Chunk budget leaves no room for model special tokens")
        tokenizer = HuggingFaceTokenizer(tokenizer=raw, max_tokens=max_tokens)
    else:
        tokenizer = OpenAITokenizer(
            tokenizer=tiktoken.get_encoding(encoding), max_tokens=max_tokens
        )
    chunker = HybridChunker(
        tokenizer=tokenizer, merge_peers=True, repeat_table_header=True, always_emit_headings=False
    )
    chunks = _merge_short_chunks(
        [chunk for chunk in chunker.chunk(document) if chunk.text.strip()],
        chunker,
        min(min_tokens, max_tokens),
        max_tokens,
    )
    output = []
    for chunk in chunks:
        text = chunker.contextualize(chunk)
        tokens = tokenizer.count_tokens(text)
        if not text.strip():
            continue
        if tokens > max_tokens:
            raise ValueError("Chunk input exceeds token budget; source needs review")
        sources = []
        for item in chunk.meta.doc_items:
            item_ref = item.self_ref
            for prov in item.prov or [None]:
                page = document.pages.get(prov.page_no) if prov else None
                locator = {
                    "kind": "docling_item",
                    "batch": namespace,
                    "item_ref": item_ref,
                    "precision": "item",
                    "page_offset": page_offset,
                }
                if prov:
                    locator["provenance"] = prov.model_dump(mode="json")
                    if page:
                        locator["page_size"] = page.size.model_dump(mode="json")
                sources.append(
                    {
                        "source_item_ref": f"{namespace}:{item_ref}",
                        "page_number": prov.page_no + page_offset if prov else None,
                        "source_locator": locator,
                    }
                )
        if not sources:
            raise ValueError("A chunk has no resolvable source; review extraction")
        headings = list(chunk.meta.headings or [])
        output.append(
            {
                "body_text": chunk.text,
                "context_text": text,
                "body_hash": digest(chunk.text.encode()),
                "context_hash": digest(text.encode()),
                "heading_path": headings,
                "section_ref": sources[0]["source_item_ref"],
                "token_count": tokens,
                "chunk_kind": "table"
                if any("table" in type(i).__name__.lower() for i in chunk.meta.doc_items)
                else "text",
                "sources": sources,
            }
        )
    if not output:
        raise ValueError("No searchable content was extracted")
    return output
