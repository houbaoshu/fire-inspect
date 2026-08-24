from __future__ import annotations

from collections.abc import Callable
from typing import Any

Handler = Callable[[Any], None]
_handlers: dict[str, Handler] = {}


def register(task_type: str):
    def decorator(handler: Handler) -> Handler:
        if task_type in _handlers:
            raise RuntimeError(f"handler already registered for {task_type}")
        _handlers[task_type] = handler
        return handler

    return decorator


def get_handler(task_type: str) -> Handler:
    try:
        return _handlers[task_type]
    except KeyError as exc:
        raise LookupError(f"no handler registered for {task_type}") from exc


def registered_task_types() -> tuple[str, ...]:
    return tuple(sorted(_handlers))
