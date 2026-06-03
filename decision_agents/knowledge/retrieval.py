"""Small local keyword retrieval over markdown knowledge files."""

from __future__ import annotations

import re

from dataclasses import dataclass
from importlib import resources

from decision_agents.schemas import RuleEvidence


DEFAULT_KNOWLEDGE_FILES = (
    "rules.md",
    "law_of_war.md",
    "authorization.md",
    "planning_constraints.md",
)
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]+|[\u4e00-\u9fff]+")


@dataclass(frozen=True)
class KnowledgeChunk:
    source: str
    rule_id: str
    title: str
    text: str
    tags: tuple[str, ...]


def retrieve_evidence(
    query: str,
    *,
    top_k: int = 5,
    files: tuple[str, ...] = DEFAULT_KNOWLEDGE_FILES,
) -> list[RuleEvidence]:
    """Return the highest-scoring local knowledge chunks for a query."""
    query_tokens = _tokens(query)
    if not query_tokens:
        return []

    scored = []
    for chunk in load_knowledge_chunks(files=files):
        chunk_tokens = _tokens(
            " ".join([chunk.rule_id, chunk.title, chunk.text, *chunk.tags])
        )
        overlap = query_tokens & chunk_tokens
        if not overlap:
            continue
        tag_overlap = query_tokens & set(chunk.tags)
        score = len(overlap) + len(tag_overlap) * 1.5
        scored.append((score, chunk))

    scored.sort(key=lambda item: (-item[0], item[1].rule_id))
    return [
        RuleEvidence(
            source=chunk.source,
            rule_id=chunk.rule_id,
            title=chunk.title,
            text=chunk.text,
            score=round(score, 3),
            tags=list(chunk.tags),
        )
        for score, chunk in scored[: max(1, top_k)]
    ]


def load_knowledge_chunks(
    *,
    files: tuple[str, ...] = DEFAULT_KNOWLEDGE_FILES,
) -> list[KnowledgeChunk]:
    chunks = []
    for filename in files:
        text = _read_knowledge_file(filename)
        chunks.extend(_parse_markdown_chunks(filename, text))
    return chunks


def _read_knowledge_file(filename: str) -> str:
    return (
        resources.files("decision_agents.knowledge")
        .joinpath(filename)
        .read_text(encoding="utf-8")
    )


def _parse_markdown_chunks(source: str, text: str) -> list[KnowledgeChunk]:
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


def _build_chunk(
    source: str,
    heading: str,
    lines: list[str],
) -> KnowledgeChunk:
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

    return KnowledgeChunk(
        source=source,
        rule_id=rule_id,
        title=title,
        text=" ".join(body_lines),
        tags=tags,
    )


def _tokens(text: str) -> set[str]:
    return {match.group(0).lower() for match in TOKEN_PATTERN.finditer(text)}
