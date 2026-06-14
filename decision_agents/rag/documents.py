"""Document loading and chunking for the local RAG pipeline."""

from __future__ import annotations

import re

from dataclasses import dataclass
from importlib import resources


DEFAULT_KNOWLEDGE_FILES = (
    "rules.md",
    "law_of_war.md",
    "authorization.md",
    "planning_constraints.md",
)
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]+|[\u4e00-\u9fff]+")


@dataclass(frozen=True)
class RagChunk:
    source: str
    rule_id: str
    title: str
    text: str
    tags: tuple[str, ...]

    @property
    def searchable_text(self) -> str:
        return " ".join([self.rule_id, self.title, self.text, *self.tags])


def load_rag_chunks(
    *,
    files: tuple[str, ...] = DEFAULT_KNOWLEDGE_FILES,
) -> list[RagChunk]:
    chunks = []
    for filename in files:
        text = _read_knowledge_file(filename)
        chunks.extend(parse_markdown_chunks(filename, text))
    return chunks


def parse_markdown_chunks(source: str, text: str) -> list[RagChunk]:
    chunks = []
    current_heading = None
    current_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current_heading:
                chunks.append(_build_chunk(source, current_heading, current_lines))
            current_heading = line.removeprefix("## ").strip()
            current_lines = []
        elif current_heading:
            current_lines.append(line)
    if current_heading:
        chunks.append(_build_chunk(source, current_heading, current_lines))
    return chunks


def tokenize(text: str) -> set[str]:
    return {match.group(0).lower() for match in TOKEN_PATTERN.finditer(text)}


def _read_knowledge_file(filename: str) -> str:
    return (
        resources.files("decision_agents.knowledge")
        .joinpath(filename)
        .read_text(encoding="utf-8")
    )


def _build_chunk(source: str, heading: str, lines: list[str]) -> RagChunk:
    if ":" in heading:
        rule_id, title = heading.split(":", 1)
        rule_id = rule_id.strip()
        title = title.strip()
    else:
        rule_id = heading.strip()
        title = heading.strip()

    tags: tuple[str, ...] = ()
    body_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("tags:"):
            tag_text = stripped.split(":", 1)[1]
            tags = tuple(
                tag.strip().lower()
                for tag in tag_text.split(",")
                if tag.strip()
            )
        elif stripped:
            body_lines.append(stripped)

    return RagChunk(
        source=source,
        rule_id=rule_id,
        title=title,
        text=" ".join(body_lines),
        tags=tags,
    )
