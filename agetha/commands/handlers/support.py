"""Small host-facing helpers shared by command handler domains."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, TYPE_CHECKING

from agetha.core.request_context import RequestOrigin
from agetha.core.capabilities import CapabilityAuthorization, CapabilityController
from agetha.utils import logger

if TYPE_CHECKING:
    from main import CompanionApp

CAPABILITY_AUTHORIZATION = object()


@dataclass
class DispatchCtx:
    user_message: str | None
    mood: str
    segments: list
    shutdown_requested: bool
    origin: RequestOrigin = "user"


def command_result_ok(result: str) -> bool:
    value = str(result or "").casefold()
    return any(value.startswith(prefix) for prefix in (
        "[folder created]", "[file created]", "[folder deleted]",
        "[file deleted]", "[file renamed]", "[command completed]",
        "[written:", "[screen locked]", "[shutdown in ", "[restart in ",
    ))


def finish_verified_command(app, ctx: DispatchCtx, result: str) -> None:
    if command_result_ok(result):
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
        return
    message = str(result or "The operation failed.").strip("[]")[:140]
    app._show_op_error(message)
    app._speak_and_continue(
        [{"text": "That didn't work.", "pause": 0.0}], "neutral", False,
    )


def perform_authorized_effect(app, authorization: object,
                              effect: Callable[[], object]) -> tuple[bool, object | None]:
    if authorization is True:
        return True, effect()
    if not isinstance(authorization, CapabilityAuthorization):
        return False, None
    controller = getattr(app, "_capabilities", None)
    if not isinstance(controller, CapabilityController):
        return False, None
    return controller.perform_authorized(authorization, effect)


def start_app_worker(app: "CompanionApp", target: Callable[[], None], name: str) -> None:
    starter = getattr(type(app), "_start_worker", None)
    if callable(starter):
        starter(app, target, name=name)
    else:
        threading.Thread(target=target, daemon=True).start()


def call_app_ui_sync(app: "CompanionApp", callback: Callable[[], object]) -> object | None:
    caller = getattr(type(app), "_call_ui_sync", None)
    if callable(caller):
        return caller(app, callback)
    return callback()


def schedule_app_ui(app: "CompanionApp", callback: Callable[[], None]) -> object | None:
    scheduler = getattr(type(app), "_schedule_ui", None)
    if callable(scheduler):
        return scheduler(app, callback)
    if threading.current_thread() is not threading.main_thread():
        return None
    try:
        return app.root.after(0, callback)
    except Exception as exc:
        logger.debug("Command UI scheduling failed: %s", type(exc).__name__)
        return None
