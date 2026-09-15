import json
from pathlib import Path
from typing import Iterable, List

from .models import DocumentChunk, ParsedDocument


def save_document_artifacts(
    document: ParsedDocument,
    chunks: Iterable[DocumentChunk],
    output_dir: str | Path,
) -> dict:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    chunk_list = list(chunks)
    (target / "parsed_document.json").write_text(
        json.dumps(document.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (target / "chunks.jsonl").open("w", encoding="utf-8") as stream:
        for chunk in chunk_list:
            stream.write(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n")
    return {
        "document_id": document.document_id,
        "filename": document.filename,
        "parser": document.parser,
        "chunk_count": len(chunk_list),
        "output_dir": str(target),
    }
