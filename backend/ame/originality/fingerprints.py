from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.db.models import ContentItem, MediaAsset, OriginalityFingerprint, Script
from ame.llm import get_llm
from ame.util.text import jaccard, normalize_text, sha256_text, token_set

RECENT_SCRIPT_LIMIT = 50
RECENT_TITLE_LIMIT = 100
NEAR_DUPLICATE_REJECT = 0.85
NEAR_DUPLICATE_REVIEW = 0.70
TITLE_REJECT = 0.95
TITLE_REVIEW = 0.85
SHORT_TITLE_TOKEN_FLOOR = 4


@dataclass
class SimilarityHit:
    content_id: UUID
    score: float
    kind: str


@dataclass
class OriginalityReport:
    script_hash: str
    hook_hash: str
    title_normalized: str
    asset_manifest_hash: str
    embedding: list[float] | None
    max_script_jaccard: float = 0.0
    max_title_jaccard: float = 0.0
    nearest_script: SimilarityHit | None = None
    nearest_title: SimilarityHit | None = None
    duplicate_content_id: UUID | None = None
    hits: list[SimilarityHit] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "script_hash": self.script_hash,
            "hook_hash": self.hook_hash,
            "title_normalized": self.title_normalized,
            "asset_manifest_hash": self.asset_manifest_hash,
            "max_script_jaccard": self.max_script_jaccard,
            "max_title_jaccard": self.max_title_jaccard,
            "duplicate_content_id": (
                str(self.duplicate_content_id) if self.duplicate_content_id else None
            ),
            "nearest_script_content_id": (
                str(self.nearest_script.content_id) if self.nearest_script else None
            ),
            "nearest_title_content_id": (
                str(self.nearest_title.content_id) if self.nearest_title else None
            ),
        }


def script_corpus(script: Script) -> str:
    parts = [script.hook, script.body, script.reveal, script.cta, script.caption or ""]
    if script.on_screen_text:
        parts.extend(str(item) for item in script.on_screen_text)
    return " ".join(part for part in parts if part)


def asset_manifest_hash(assets: list[MediaAsset]) -> str:
    rows = sorted(
        (
            f"{asset.kind}|{asset.storage_key}|{asset.sha256 or ''}|{asset.usage_type}"
            for asset in assets
        )
    )
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


async def compute_fingerprint(
    session: AsyncSession,
    content: ContentItem,
    script: Script | None,
    assets: list[MediaAsset],
) -> OriginalityReport:
    corpus = script_corpus(script) if script else ""
    hook = script.hook if script else ""
    title = content.topic or ""
    report = OriginalityReport(
        script_hash=sha256_text(corpus) if corpus.strip() else "",
        hook_hash=sha256_text(hook) if hook.strip() else "",
        title_normalized=normalize_text(title),
        asset_manifest_hash=asset_manifest_hash(assets),
        embedding=None,
    )
    if corpus.strip():
        try:
            report.embedding = await get_llm().embed(normalize_text(corpus))
        except Exception:  # noqa: BLE001
            report.embedding = None
    await _scan_duplicates(session, content.id, report)
    await _scan_script_similarity(session, content.id, report, corpus)
    await _scan_title_similarity(session, content.id, report)
    return report


async def persist_fingerprint(
    session: AsyncSession,
    content: ContentItem,
    script: Script | None,
    report: OriginalityReport,
) -> OriginalityFingerprint:
    existing = await session.execute(
        select(OriginalityFingerprint).where(OriginalityFingerprint.content_id == content.id)
    )
    row = existing.scalars().first()
    if row is None:
        row = OriginalityFingerprint(content_id=content.id)
        session.add(row)
    row.script_hash = report.script_hash or sha256_text(str(content.id))
    row.hook_hash = report.hook_hash or None
    row.title_normalized = report.title_normalized or None
    row.embedding = report.embedding
    row.asset_manifest_hash = report.asset_manifest_hash or None
    if script is not None and report.script_hash:
        script.normalized_hash = report.script_hash
    await session.flush()
    return row


async def _scan_duplicates(
    session: AsyncSession, content_id: UUID, report: OriginalityReport
) -> None:
    if not report.script_hash:
        return
    found = await session.execute(
        select(OriginalityFingerprint).where(
            OriginalityFingerprint.script_hash == report.script_hash,
            OriginalityFingerprint.content_id != content_id,
        )
    )
    other = found.scalars().first()
    if other is not None:
        report.duplicate_content_id = other.content_id
        report.hits.append(
            SimilarityHit(content_id=other.content_id, score=1.0, kind="script_hash")
        )


async def _scan_script_similarity(
    session: AsyncSession, content_id: UUID, report: OriginalityReport, corpus: str
) -> None:
    if not corpus.strip():
        return
    rows = await session.execute(
        select(Script)
        .where(Script.content_id != content_id)
        .order_by(Script.created_at.desc())
        .limit(RECENT_SCRIPT_LIMIT)
    )
    best: SimilarityHit | None = None
    for other in rows.scalars():
        score = jaccard(corpus, script_corpus(other))
        if best is None or score > best.score:
            best = SimilarityHit(content_id=other.content_id, score=score, kind="script_jaccard")
    if best is not None:
        report.nearest_script = best
        report.max_script_jaccard = best.score
        report.hits.append(best)


async def _scan_title_similarity(
    session: AsyncSession, content_id: UUID, report: OriginalityReport
) -> None:
    if not report.title_normalized:
        return
    rows = await session.execute(
        select(OriginalityFingerprint)
        .where(
            OriginalityFingerprint.content_id != content_id,
            OriginalityFingerprint.title_normalized.is_not(None),
        )
        .order_by(OriginalityFingerprint.created_at.desc())
        .limit(RECENT_TITLE_LIMIT)
    )
    best: SimilarityHit | None = None
    for other in rows.scalars():
        other_title = other.title_normalized or ""
        score = jaccard(report.title_normalized, other_title)
        if best is None or score > best.score:
            best = SimilarityHit(content_id=other.content_id, score=score, kind="title_jaccard")
    if best is not None:
        report.nearest_title = best
        report.max_title_jaccard = best.score
        report.hits.append(best)


def title_is_short(title: str) -> bool:
    return len(token_set(title)) < SHORT_TITLE_TOKEN_FLOOR
