"""Handler registration kept separate from static CommandSpec policy."""

from __future__ import annotations

from typing import Callable

from agetha.commands.specs import DispatchKind, get_command_spec


HandlerFn = Callable[[object, dict, object], bool]
HANDLERS: dict[str, HandlerFn] = {}


def register(command: str) -> Callable[[HandlerFn], HandlerFn]:
    def decorator(function: HandlerFn) -> HandlerFn:
        spec = get_command_spec(command)
        if spec is None:
            raise ValueError(f"handler has no CommandSpec: {command}")
        if spec.dispatch_kind is not DispatchKind.HANDLER or spec.handler_key != command:
            raise ValueError(f"command is not handler-backed: {command}")
        if command in HANDLERS:
            raise ValueError(f"duplicate handler registration: {command}")
        HANDLERS[command] = function
        return function
    return decorator
