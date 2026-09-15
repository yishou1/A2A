from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class DocumentBlock:
    """A structural unit emitted by a file parser."""

    text: str
    kind: str = "paragraph"
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    heading_path: List[str] = field(default_factory=list)
    order: int = 0


@dataclass
class ParsedDocument:
    """Parsed document plus enough provenance to reproduce its chunks."""

    document_id: str
    filename: str
    mime_type: str
    source_path: str
    text: str
    blocks: List[DocumentBlock]
    content_sha256: str
    parser: str
    parser_version: str = "1"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["blocks"] = [asdict(block) for block in self.blocks]
        return value


@dataclass
class DocumentChunk:
    """A chunk ready for the existing SynapseRAG text-based pipeline."""

    chunk_id: str
    document_id: str
    text: str
    chunk_index: int
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    heading_path: List[str] = field(default_factory=list)
    source_filename: str = ""
    content_sha256: str = ""
    source_segments: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
