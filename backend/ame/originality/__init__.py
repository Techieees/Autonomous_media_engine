from typing import Any

__all__ = [
    "OriginalityReport",
    "compute_fingerprint",
    "persist_fingerprint",
    "script_corpus",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from ame.originality import fingerprints

        return getattr(fingerprints, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
