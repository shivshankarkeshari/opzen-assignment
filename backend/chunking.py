import tiktoken
from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
from docling_core.transforms.chunker.tokenizer.openai import OpenAITokenizer
from docling_core.types.doc import DoclingDocument

from backend.storage import digest


def chunk_document(
    canonical: dict,
    namespace: str,
    page_offset: int,
    max_tokens: int,
    encoding: str,
    model_name: str | None = None,
    model_revision: str = "",
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
        tokenizer=tokenizer, merge_peers=False, repeat_table_header=True, always_emit_headings=True
    )
    output = []
    for chunk in chunker.chunk(document):
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
