from __future__ import annotations

import re

from ame.util.text import normalize_text, token_set

# Instructional / incitement phrases — not news nouns.
FORBIDDEN_PHRASES: tuple[str, ...] = (
    "how to make a bomb",
    "how to build a bomb",
    "make a pipe bomb",
    "build a pipe bomb",
    "homemade explosive",
    "build an explosive device",
    "assemble a dirty bomb",
    "how to make ricin",
    "synthesize ricin",
    "weaponize anthrax",
    "build a biological weapon",
    "how to make sarin",
    "how to make nerve agent",
    "poison a water supply",
    "3d printed gun instructions",
    "undetectable firearm",
    "buy fake views",
    "buy fake followers",
    "buy fake likes",
    "purchase fake engagement",
    "guaranteed 100x",
    "guaranteed returns",
    "cannot lose investment",
    "double your money overnight",
    "send bitcoin to",
    "send crypto to this wallet",
    "wire transfer to claim",
    "pay a processing fee to claim",
    "gift card to unlock",
    "share your password",
    "give me your password",
    "send your seed phrase",
    "share your seed phrase",
    "share your pin",
    "verify by sending money",
    "you have won a prize click",
    "advance fee to release funds",
    "kill all jews",
    "kill all muslims",
    "kill all christians",
    "kill all immigrants",
    "gas the jews",
    "death to jews",
    "death to muslims",
    "death to immigrants",
)

FORBIDDEN_TOKENS: frozenset[str] = frozenset(
    {
        "kike",
        "kikes",
        "niggers",
        "nigger",
        "spic",
        "spics",
        "chink",
        "chinks",
        "wetback",
        "wetbacks",
        "faggot",
        "faggots",
        "tranny",
        "trannies",
        "retard",
        "retards",
    }
)

_INCITE = re.compile(
    r"\b(kill|gas|exterminate|lynch)\s+all\s+[a-z0-9]+\b",
    re.IGNORECASE,
)
_WEAPON_HOW_TO = re.compile(
    r"\b(how to|step[- ]by[- ]step|instructions to)\s+"
    r"(make|build|assemble|synthesize|weaponize)\s+"
    r"(a |an )?(bomb|explosive|ricin|sarin|anthrax|nerve agent|bioweapon|firearm)\b",
    re.IGNORECASE,
)


def find_forbidden(text: str) -> list[str]:
    if not text or not text.strip():
        return []
    hits: list[str] = []
    normalized = normalize_text(text)
    tokens = token_set(text)
    for phrase in FORBIDDEN_PHRASES:
        if normalize_text(phrase) in normalized:
            hits.append(phrase)
    for token in sorted(FORBIDDEN_TOKENS & tokens):
        hits.append(token)
    for match in _INCITE.finditer(text):
        hits.append(match.group(0).lower())
    for match in _WEAPON_HOW_TO.finditer(text):
        hits.append(match.group(0).lower())
    # Preserve order, drop duplicates.
    seen: set[str] = set()
    unique: list[str] = []
    for hit in hits:
        if hit not in seen:
            seen.add(hit)
            unique.append(hit)
    return unique
