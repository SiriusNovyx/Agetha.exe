"""Provider-slot bridge for isolated Computer Use planner requests."""

from __future__ import annotations

import threading
from typing import Callable, Mapping


class StructuredAIClient:
    """Adapt ``AIEngine.request_structured`` to the planner client protocol.

    Reservation and release callbacks keep the application as the sole owner
    of provider concurrency.  This object has no access to personality,
    conversation history, memory, or exact payload values beyond the compact
    planner payload it is explicitly handed.
    """

    def __init__(
        self,
        engine: object,
        *,
        route: str,
        model: str = "",
        max_tokens: int = 480,
        reserve: Callable[[], object | None],
        release: Callable[[object], None],
    ) -> None:
        self._engine = engine
        self._route = str(route or "inherit").strip().lower()
        self._model = str(model or "").strip()[:300]
        self._max_tokens = max(64, min(1200, int(max_tokens)))
        self._reserve = reserve
        self._release = release

    def request(
        self,
        system_prompt: str,
        payload: Mapping[str, object],
        cancel_event: threading.Event,
    ) -> str:
        if cancel_event.is_set():
            return ""
        token = self._reserve()
        if token is None:
            raise RuntimeError("provider slot unavailable")
        try:
            if cancel_event.is_set():
                return ""
            method = getattr(self._engine, "request_structured", None)
            if not callable(method):
                raise RuntimeError("structured provider requests are unavailable")
            result = method(
                route=self._route,
                system_prompt=system_prompt,
                payload=dict(payload),
                model=self._model,
                max_tokens=self._max_tokens,
                cancel_event=cancel_event,
            )
            return "" if cancel_event.is_set() else str(result or "")
        finally:
            self._release(token)


__all__ = ["StructuredAIClient"]
