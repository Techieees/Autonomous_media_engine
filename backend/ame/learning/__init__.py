from typing import Any

__all__ = ["handle_learning_update"]


def __getattr__(name: str) -> Any:
    if name == "handle_learning_update":
        from ame.learning.engine import handle_learning_update

        return handle_learning_update
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
