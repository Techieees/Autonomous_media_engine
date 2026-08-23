import hashlib
import re


_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def normalize_text(value: str) -> str:
    lowered = value.casefold().strip()
    lowered = _PUNCT.sub(" ", lowered)
    return _WS.sub(" ", lowered)


def sha256_text(value: str) -> str:
    return hashlib.sha256(normalize_text(value).encode("utf-8")).hexdigest()


def token_set(value: str) -> set[str]:
    return {token for token in normalize_text(value).split(" ") if token}


def jaccard(a: str, b: str) -> float:
    left, right = token_set(a), token_set(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)
