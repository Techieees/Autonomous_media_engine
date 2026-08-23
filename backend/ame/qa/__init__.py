from typing import Any

__all__ = ["handle_qa_check"]


def __getattr__(name: str) -> Any:
    if name == "handle_qa_check":
        from ame.qa.service import handle_qa_check

        return handle_qa_check
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
