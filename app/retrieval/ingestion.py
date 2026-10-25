from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.models.document import Document, DocumentChunk


@dataclass(frozen=True, slots=True)
class IngestResult:
    document: Document
    created: bool


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def chunk_markdown(content: str, *, max_chars: int = 700) -> list[tuple[str, str]]:
    """Split on level-2 headings; drop the H1 and blockquote notice from the body."""
    body_content = re.sub(r"(?m)^#\s+.*$", "", content, count=1)
    body_content = re.sub(r"(?m)^>.*$", "", body_content).strip()
    sections = re.split(r"(?m)^##\s+", body_content)
    if not sections:
        return []

    chunks: list[tuple[str, str]] = []

    def pack(section: str, body: str) -> None:
        packed = ""
        for paragraph in re.split(r"\n\s*\n", body):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            if packed and len(packed) + len(paragraph) + 2 > max_chars:
                chunks.append((section, packed))
                packed = paragraph
            else:
                packed = f"{packed}\n\n{paragraph}".strip()
        if packed:
            chunks.append((section, packed))

    preamble = sections[0].strip()
    if preamble:
        pack("概述", preamble)
    for section in sections[1:]:
        if not section.strip():
            continue
        lines = section.strip().splitlines()
        title = lines[0].strip()
        pack(title, "\n".join(lines[1:]).strip())
    return chunks


def ingest_document(
    session: Session,
    *,
    tenant_id: str,
    source_uri: str,
    title: str,
    version: str,
    content: str,
    effective_from: date,
    effective_to: date | None = None,
    status: str = "ACTIVE",
    meta: dict[str, Any] | None = None,
) -> IngestResult:
    digest = content_hash(content)
    existing = session.scalars(
        select(Document).where(Document.source_uri == source_uri, Document.version == version)
    ).first()
    if existing is not None:
        if existing.content_hash != digest:
            raise ConflictError(
                "Document version already exists with different content; bump the version."
            )
        return IngestResult(document=existing, created=False)

    document = Document(
        id=f"doc_{uuid.uuid4().hex[:16]}",
        tenant_id=tenant_id,
        source_uri=source_uri,
        title=title,
        version=version,
        content_hash=digest,
        effective_from=effective_from,
        effective_to=effective_to,
        status=status,
        meta=meta or {},
    )
    session.add(document)
    session.flush()

    for index, (section, chunk_content) in enumerate(chunk_markdown(content)):
        session.add(
            DocumentChunk(
                id=f"chk_{uuid.uuid4().hex[:16]}",
                document_id=document.id,
                document_version=version,
                chunk_index=index,
                section=section,
                content=chunk_content,
                meta={"tenant_id": tenant_id, "source_uri": source_uri},
            )
        )
    session.flush()
    return IngestResult(document=document, created=True)
