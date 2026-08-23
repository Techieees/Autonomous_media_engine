from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ame.contracts.enums import QAVerdict
from ame.db.models import (
    ContentItem,
    MediaAsset,
    Opportunity,
    ProductionManifestRecord,
    ResearchPack,
    Script,
)
from ame.originality.fingerprints import (
    NEAR_DUPLICATE_REJECT,
    NEAR_DUPLICATE_REVIEW,
    TITLE_REJECT,
    TITLE_REVIEW,
    OriginalityReport,
    script_corpus,
    title_is_short,
)
from ame.qa.assets import (
    LocatedAsset,
    asset_size,
    local_media_path,
    min_size_for,
    missing_provenance,
    pick_captions,
    pick_render,
    read_bytes,
    source_urls_ok,
    store_exists,
)
from ame.qa.captions import caption_boundary_errors, parse_caption_bytes
from ame.qa.claims import prediction_findings
from ame.qa.ffprobe import REQUIRED_HEIGHT, REQUIRED_WIDTH, ProbeResult, probe_media
from ame.qa.forbidden import find_forbidden
from ame.storage.base import ObjectStore

CHECK_KEYS = (
    "render_exists",
    "file_non_empty",
    "ffprobe",
    "captions_exist",
    "caption_boundaries",
    "factual_claims",
    "provenance",
    "copyright_risk",
    "duplicate",
    "forbidden_terms",
    "assets_valid",
)

COPYRIGHT_RISK_REJECT = 0.7
SEVERITY_REJECT = "reject"
SEVERITY_REVIEW = "review"
SEVERITY_INFO = "info"


@dataclass
class QABundle:
    content: ContentItem
    script: Script | None
    assets: list[MediaAsset]
    research: ResearchPack | None
    opportunity: Opportunity | None
    manifest: ProductionManifestRecord | None
    store: ObjectStore
    originality: OriginalityReport


def outcome(
    passed: bool | None,
    *,
    severity: str,
    detail: str,
    skipped: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "passed": passed,
        "severity": severity,
        "detail": detail,
        "skipped": skipped,
    }
    payload.update(extra)
    return payload


async def run_checks(bundle: QABundle) -> dict[str, Any]:
    render = pick_render(bundle.assets, bundle.manifest)
    captions = pick_captions(bundle.assets, bundle.manifest)
    checks: dict[str, Any] = {}
    checks["render_exists"] = _check_render_exists(bundle, render)
    checks["file_non_empty"] = _check_file_non_empty(bundle, render)
    checks["ffprobe"] = await _check_ffprobe(bundle, render)
    checks["captions_exist"] = _check_captions_exist(bundle, captions)
    checks["caption_boundaries"] = _check_caption_boundaries(bundle, captions)
    checks["factual_claims"] = _check_factual_claims(bundle)
    checks["provenance"] = _check_provenance(bundle)
    checks["copyright_risk"] = _check_copyright_risk(bundle)
    checks["duplicate"] = _check_duplicate(bundle)
    checks["forbidden_terms"] = _check_forbidden(bundle)
    checks["assets_valid"] = _check_assets_valid(bundle)
    checks["script_similarity"] = _check_script_similarity(bundle)
    checks["title_similarity"] = _check_title_similarity(bundle)
    return checks


def decide_verdict(checks: dict[str, Any]) -> tuple[QAVerdict, list[str]]:
    reasons: list[str] = []
    reject = False
    review = False
    for name, raw in checks.items():
        if not isinstance(raw, dict):
            continue
        if raw.get("skipped") or raw.get("passed") is True:
            continue
        if raw.get("passed") is None:
            continue
        detail = str(raw.get("detail") or "failed")
        reasons.append(f"{name}: {detail}")
        severity = raw.get("severity") or SEVERITY_REJECT
        if severity == SEVERITY_REJECT:
            reject = True
        elif severity == SEVERITY_REVIEW:
            review = True
    if reject:
        return QAVerdict.REJECTED, reasons
    if review:
        return QAVerdict.REQUIRES_REVIEW, reasons
    return QAVerdict.APPROVED, reasons


def _check_render_exists(bundle: QABundle, render: LocatedAsset | None) -> dict[str, Any]:
    if render is None:
        return outcome(
            False,
            severity=SEVERITY_REJECT,
            detail="no render/video media asset or manifest output key",
        )
    exists = store_exists(bundle.store, render.storage_key)
    return outcome(
        exists,
        severity=SEVERITY_REJECT,
        detail=(
            "render found in object store"
            if exists
            else f"missing object: {render.storage_key}"
        ),
        storage_key=render.storage_key,
        kind=render.kind,
    )


def _check_file_non_empty(bundle: QABundle, render: LocatedAsset | None) -> dict[str, Any]:
    if render is None:
        return outcome(False, severity=SEVERITY_REJECT, detail="no render file to measure")
    size = asset_size(bundle.store, render.storage_key)
    if size is None:
        return outcome(
            False,
            severity=SEVERITY_REJECT,
            detail=f"render unreadable: {render.storage_key}",
            bytes=0,
        )
    ok = size >= min_size_for(render.kind, render.storage_key, _mime(render))
    return outcome(
        ok,
        severity=SEVERITY_REJECT,
        detail=f"render size={size} bytes" if ok else f"render empty or too small ({size} bytes)",
        bytes=size,
    )


async def _check_ffprobe(bundle: QABundle, render: LocatedAsset | None) -> dict[str, Any]:
    if render is None:
        return outcome(
            False,
            severity=SEVERITY_REJECT,
            detail="ffprobe skipped because render is missing",
            available=False,
            skipped=False,
            video_stream=False,
            resolution_1080x1920=False,
            audio_stream=False,
            duration_12_90s=False,
        )
    path = local_media_path(bundle.store, render.storage_key)
    if path is None:
        return outcome(
            None,
            severity=SEVERITY_INFO,
            detail="ffprobe skipped: no local path for media",
            available=False,
            skipped=True,
            video_stream=None,
            resolution_1080x1920=None,
            audio_stream=None,
            duration_12_90s=None,
        )
    probe = await probe_media(path)
    if not probe.available:
        return outcome(
            None,
            severity=SEVERITY_INFO,
            detail="ffprobe not available; stream/duration/resolution not verified",
            available=False,
            skipped=True,
            video_stream=None,
            resolution_1080x1920=None,
            audio_stream=None,
            duration_12_90s=None,
        )
    if probe.error:
        return outcome(
            False,
            severity=SEVERITY_REJECT,
            detail=f"ffprobe failed (corrupt or unreadable media): {probe.error}",
            available=True,
            video_stream=False,
            resolution_1080x1920=False,
            audio_stream=False,
            duration_12_90s=False,
            error=probe.error,
        )
    failures = _ffprobe_failures(probe)
    passed = not failures
    return outcome(
        passed,
        severity=SEVERITY_REJECT,
        detail="; ".join(failures) if failures else _ffprobe_ok_detail(probe),
        available=True,
        video_stream=probe.has_video,
        resolution_1080x1920=probe.resolution_ok,
        audio_stream=probe.has_audio,
        duration_12_90s=probe.duration_ok,
        width=probe.width,
        height=probe.height,
        duration_s=probe.duration_s,
    )


def _ffprobe_failures(probe: ProbeResult) -> list[str]:
    failures: list[str] = []
    if not probe.has_video:
        failures.append("no video stream")
    if not probe.resolution_ok:
        failures.append(
            f"resolution {probe.width}x{probe.height} != {REQUIRED_WIDTH}x{REQUIRED_HEIGHT}"
        )
    if not probe.has_audio:
        failures.append("no audio stream")
    if not probe.duration_ok:
        failures.append(f"duration {probe.duration_s}s not in 12-90s")
    return failures


def _ffprobe_ok_detail(probe: ProbeResult) -> str:
    return (
        f"video {probe.width}x{probe.height}, audio present, duration={probe.duration_s:.2f}s"
        if probe.duration_s is not None
        else "video/audio streams accepted"
    )


def _check_captions_exist(bundle: QABundle, captions: LocatedAsset | None) -> dict[str, Any]:
    if captions is None:
        return outcome(False, severity=SEVERITY_REJECT, detail="no captions/srt asset")
    exists = store_exists(bundle.store, captions.storage_key)
    return outcome(
        exists,
        severity=SEVERITY_REJECT,
        detail=(
            "captions found in object store"
            if exists
            else f"missing object: {captions.storage_key}"
        ),
        storage_key=captions.storage_key,
        kind=captions.kind,
    )


def _check_caption_boundaries(bundle: QABundle, captions: LocatedAsset | None) -> dict[str, Any]:
    if captions is None:
        return outcome(False, severity=SEVERITY_REJECT, detail="no caption file to parse")
    data = read_bytes(bundle.store, captions.storage_key)
    if data is None:
        return outcome(False, severity=SEVERITY_REJECT, detail="caption file unreadable")
    if len(data.strip()) == 0:
        return outcome(False, severity=SEVERITY_REJECT, detail="caption file empty")
    cues = parse_caption_bytes(data)
    errors = caption_boundary_errors(cues)
    return outcome(
        not errors,
        severity=SEVERITY_REJECT,
        detail="; ".join(errors) if errors else f"{len(cues)} caption cue(s) with valid bounds",
        cue_count=len(cues),
    )


def _check_factual_claims(bundle: QABundle) -> dict[str, Any]:
    if bundle.script is None:
        return outcome(False, severity=SEVERITY_REJECT, detail="no script to inspect claims")
    findings = prediction_findings(bundle.script)
    as_fact = [item for item in findings if item.presented_as_fact]
    ambiguous = [item for item in findings if item.ambiguous_fact]
    serialized = [
        {
            "claim": item.claim,
            "overlap": item.overlap,
            "in_hook": item.in_hook,
            "hedged": item.hedged,
            "question": item.question,
        }
        for item in findings
    ]
    if as_fact:
        return outcome(
            False,
            severity=SEVERITY_REJECT,
            detail="prediction claim presented as fact in hook",
            predictions=serialized,
        )
    if ambiguous:
        return outcome(
            False,
            severity=SEVERITY_REVIEW,
            detail="prediction in hook mixes hedging with factual certainty language",
            predictions=serialized,
        )
    return outcome(
        True,
        severity=SEVERITY_INFO,
        detail="no prediction-as-fact in hook",
        predictions=serialized,
    )


def _check_provenance(bundle: QABundle) -> dict[str, Any]:
    pack = bundle.research
    if pack is None:
        return outcome(False, severity=SEVERITY_REJECT, detail="research pack missing")
    urls = source_urls_ok(pack.source_urls)
    if not urls:
        return outcome(
            False,
            severity=SEVERITY_REJECT,
            detail="research pack has no http(s) source_urls",
            source_url_count=0,
        )
    return outcome(
        True,
        severity=SEVERITY_INFO,
        detail=f"research pack has {len(urls)} source url(s)",
        source_url_count=len(urls),
    )


def _check_copyright_risk(bundle: QABundle) -> dict[str, Any]:
    risk = _copyright_risk_value(bundle.opportunity)
    over = risk > COPYRIGHT_RISK_REJECT
    return outcome(
        not over,
        severity=SEVERITY_REJECT,
        detail=(
            f"copyright_risk {risk:.3f} > {COPYRIGHT_RISK_REJECT}"
            if over
            else f"copyright_risk {risk:.3f} within cap"
        ),
        copyright_risk=risk,
        threshold=COPYRIGHT_RISK_REJECT,
    )


def _copyright_risk_value(opportunity: Opportunity | None) -> float:
    if opportunity is None:
        return 0.0
    features = opportunity.features or {}
    raw = features.get("copyright_risk", 0.0)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _check_duplicate(bundle: QABundle) -> dict[str, Any]:
    other = bundle.originality.duplicate_content_id
    if other is not None:
        return outcome(
            False,
            severity=SEVERITY_REJECT,
            detail=f"script_hash already used by content {other}",
            script_hash=bundle.originality.script_hash,
            other_content_id=str(other),
        )
    if bundle.script is None or not bundle.originality.script_hash:
        return outcome(False, severity=SEVERITY_REJECT, detail="cannot fingerprint empty script")
    return outcome(
        True,
        severity=SEVERITY_INFO,
        detail="script_hash is unique among originality fingerprints",
        script_hash=bundle.originality.script_hash,
    )


def _check_forbidden(bundle: QABundle) -> dict[str, Any]:
    if bundle.script is None:
        return outcome(
            False, severity=SEVERITY_REJECT, detail="no script to scan for forbidden terms"
        )
    hits = find_forbidden(script_corpus(bundle.script))
    return outcome(
        not hits,
        severity=SEVERITY_REJECT,
        detail="forbidden terms found: " + ", ".join(hits) if hits else "no forbidden terms",
        terms=hits,
    )


def _check_assets_valid(bundle: QABundle) -> dict[str, Any]:
    problems: list[str] = []
    review_notes: list[str] = []
    if not bundle.assets:
        problems.append("no media assets recorded")
    for asset in bundle.assets:
        if not store_exists(bundle.store, asset.storage_key):
            problems.append(f"missing {asset.kind}:{asset.storage_key}")
            continue
        size = asset_size(bundle.store, asset.storage_key)
        needed = min_size_for(asset.kind, asset.storage_key, asset.mime_type)
        if size is None:
            problems.append(f"unreadable {asset.kind}:{asset.storage_key}")
        elif size < needed:
            problems.append(f"empty/corrupt {asset.kind}:{asset.storage_key} ({size} bytes)")
        if missing_provenance(asset):
            review_notes.append(
                f"external asset missing source_url: {asset.kind}:{asset.storage_key}"
            )
    if problems:
        return outcome(
            False,
            severity=SEVERITY_REJECT,
            detail="; ".join(problems),
            problems=problems,
            review_notes=review_notes,
        )
    if review_notes:
        return outcome(
            False,
            severity=SEVERITY_REVIEW,
            detail="; ".join(review_notes),
            problems=[],
            review_notes=review_notes,
        )
    return outcome(True, severity=SEVERITY_INFO, detail=f"{len(bundle.assets)} asset(s) readable")


def _check_script_similarity(bundle: QABundle) -> dict[str, Any]:
    score = bundle.originality.max_script_jaccard
    nearest = bundle.originality.nearest_script
    extra = {
        "jaccard": score,
        "nearest_content_id": str(nearest.content_id) if nearest else None,
    }
    if score >= NEAR_DUPLICATE_REJECT:
        return outcome(
            False,
            severity=SEVERITY_REJECT,
            detail=f"script token Jaccard {score:.3f} >= {NEAR_DUPLICATE_REJECT}",
            **extra,
        )
    if score >= NEAR_DUPLICATE_REVIEW:
        return outcome(
            False,
            severity=SEVERITY_REVIEW,
            detail=f"script token Jaccard {score:.3f} >= {NEAR_DUPLICATE_REVIEW}",
            **extra,
        )
    return outcome(True, severity=SEVERITY_INFO, detail=f"script Jaccard {score:.3f}", **extra)


def _check_title_similarity(bundle: QABundle) -> dict[str, Any]:
    score = bundle.originality.max_title_jaccard
    nearest = bundle.originality.nearest_title
    title = bundle.originality.title_normalized
    extra = {
        "jaccard": score,
        "nearest_content_id": str(nearest.content_id) if nearest else None,
        "title_normalized": title,
    }
    short = title_is_short(title)
    if short and score < 1.0:
        return outcome(
            True,
            severity=SEVERITY_INFO,
            detail=f"short title Jaccard {score:.3f} ignored for reject",
            **extra,
        )
    if score >= TITLE_REJECT and not short:
        return outcome(
            False,
            severity=SEVERITY_REJECT,
            detail=f"title Jaccard {score:.3f} >= {TITLE_REJECT}",
            **extra,
        )
    if score >= TITLE_REVIEW:
        return outcome(
            False,
            severity=SEVERITY_REVIEW,
            detail=f"title Jaccard {score:.3f} >= {TITLE_REVIEW}",
            **extra,
        )
    return outcome(True, severity=SEVERITY_INFO, detail=f"title Jaccard {score:.3f}", **extra)


def _mime(located: LocatedAsset) -> str | None:
    if located.asset is None:
        return None
    return located.asset.mime_type
