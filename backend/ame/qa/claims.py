from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from ame.contracts.enums import ClaimKind
from ame.util.text import normalize_text, token_set


class _ScriptLike(Protocol):
    hook: str
    claims: list[Any] | None

_HEDGE = re.compile(
    r"\b("
    r"may|might|could|possibly|maybe|perhaps|predicted|predicts|forecast|"
    r"expected|allegedly|reportedly|rumored|estimate|estimated|likely|"
    r"unlikely|theoretically|hypothetically|if|unless"
    r")\b",
    re.IGNORECASE,
)
_STRONG_FACT = re.compile(
    r"\b("
    r"confirmed|proven|definitely|certainly|undeniably|factually|"
    r"scientists confirmed|has already|will happen|is certain|is a fact"
    r")\b",
    re.IGNORECASE,
)
_CLAIM_OVERLAP = 0.6


@dataclass(frozen=True)
class ClaimFinding:
    claim: str
    kind: str
    overlap: float
    in_hook: bool
    hedged: bool
    strong_fact: bool
    question: bool

    @property
    def presented_as_fact(self) -> bool:
        return self.in_hook and not self.question and not self.hedged

    @property
    def ambiguous_fact(self) -> bool:
        return self.in_hook and not self.question and self.hedged and self.strong_fact


def prediction_findings(script: _ScriptLike) -> list[ClaimFinding]:
    hook = script.hook or ""
    hook_norm = normalize_text(hook)
    hook_tokens = token_set(hook)
    findings: list[ClaimFinding] = []
    for raw in script.claims or []:
        kind, text = _claim_fields(raw)
        if kind != ClaimKind.PREDICTION.value or not text.strip():
            continue
        claim_tokens = token_set(text)
        if not claim_tokens:
            continue
        overlap = len(claim_tokens & hook_tokens) / len(claim_tokens)
        contained = normalize_text(text) in hook_norm if hook_norm else False
        in_hook = contained or overlap >= _CLAIM_OVERLAP
        findings.append(
            ClaimFinding(
                claim=text,
                kind=kind,
                overlap=round(overlap, 4),
                in_hook=in_hook,
                hedged=bool(_HEDGE.search(hook)),
                strong_fact=bool(_STRONG_FACT.search(hook)),
                question="?" in hook,
            )
        )
    return findings


def _claim_fields(raw: Any) -> tuple[str, str]:
    if isinstance(raw, str):
        return "", raw
    if not isinstance(raw, dict):
        return "", ""
    kind = str(raw.get("kind") or "")
    text = str(raw.get("claim") or raw.get("text") or "")
    return kind, text
