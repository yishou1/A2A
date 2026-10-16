import hashlib
from typing import List

from .models import DocumentBlock, DocumentChunk, ParsedDocument


def _token_count(text: str) -> int:
    try:
        import tiktoken

        return len(tiktoken.get_encoding("cl100k_base").encode(text))
    except Exception:
        return max(1, len(text))


def _split_long_text(text: str, max_tokens: int, overlap_tokens: int) -> List[str]:
    if _token_count(text) <= max_tokens:
        return [text.strip()]
    # Character windows are deterministic and keep this layer independent of
    # the tokenizer used by the remote OpenIE deployment.
    chars_per_token = 2 if any("\u4e00" <= char <= "\u9fff" for char in text) else 4
    window = max(1, max_tokens * chars_per_token)
    overlap = min(window // 2, max(0, overlap_tokens * chars_per_token))
    step = max(1, window - overlap)
    return [text[start:start + window].strip() for start in range(0, len(text), step) if text[start:start + window].strip()]


def chunk_document(
    document: ParsedDocument,
    max_tokens: int = 700,
    overlap_tokens: int = 100,
) -> List[DocumentChunk]:
    if max_tokens <= 0 or overlap_tokens < 0 or overlap_tokens >= max_tokens:
        raise ValueError("Require max_tokens > overlap_tokens >= 0")
    chunks: List[DocumentChunk] = []
    pending: List[DocumentBlock] = []
    pending_text: List[str] = []
    source_search_start = 0

    def flush() -> None:
        nonlocal source_search_start
        if not pending_text:
            return
        raw = "\n\n".join(pending_text).strip()
        for part in _split_long_text(raw, max_tokens, overlap_tokens):
            index = len(chunks)
            first, last = pending[0], pending[-1]
            heading_path = list(last.heading_path or first.heading_path)
            context = f"标题：{' / '.join(heading_path)}\n{part}" if heading_path else part
            chunk_hash = hashlib.sha256(context.encode("utf-8")).hexdigest()
            source_start = document.text.find(part, source_search_start)
            if source_start < 0:
                source_start = document.text.find(part)
            source_end = source_start + len(part) if source_start >= 0 else None
            if source_start >= 0:
                # Advance by one character so overlapping windows can still
                # resolve against the same source region.
                source_search_start = source_start + 1
            chunks.append(DocumentChunk(
                chunk_id=f"{document.document_id}-chunk-{index:05d}",
                document_id=document.document_id,
                text=context,
                chunk_index=index,
                page_start=first.page_start,
                page_end=last.page_end or first.page_end,
                heading_path=heading_path,
                source_filename=document.filename,
                content_sha256=chunk_hash,
                source_segments=[{
                    "source_start": source_start if source_start >= 0 else None,
                    "source_end": source_end,
                    "block_start_order": first.order,
                    "block_end_order": last.order,
                    "page_start": first.page_start,
                    "page_end": last.page_end or first.page_end,
                }],
            ))
        pending.clear()
        pending_text.clear()

    for block in document.blocks:
        # A heading starts a new traceability scope.  Do not merge adjacent
        # Markdown sections merely because their token count fits one window.
        if block.kind == "heading" and pending_text:
            flush()
        candidate = "\n\n".join(pending_text + [block.text]).strip()
        if pending_text and _token_count(candidate) > max_tokens:
            flush()
        pending.append(block)
        pending_text.append(block.text)
    flush()
    return chunks
