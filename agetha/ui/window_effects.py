"""Non-blocking, cancellable window close effects."""

from __future__ import annotations

from collections.abc import Callable


class CRTCloseController:
    """Run a short CRT power-off sequence, then invoke graceful shutdown once."""

    def __init__(
        self,
        root,
        graceful_shutdown: Callable[[], None],
        *,
        enabled: bool = True,
        reduced_motion: bool = False,
        cancel_geometry: Callable[[], None] = lambda: None,
        disable_input: Callable[[], None] = lambda: None,
    ) -> None:
        self.root = root
        self._graceful_shutdown = graceful_shutdown
        self.enabled = bool(enabled)
        self.reduced_motion = bool(reduced_motion)
        self._cancel_geometry = cancel_geometry
        self._disable_input = disable_input
        self._closing = False
        self._finished = False
        self._job_ids: set = set()

    @property
    def closing(self) -> bool:
        return self._closing

    @property
    def job_ids(self) -> frozenset:
        return frozenset(self._job_ids)

    def request_close(self) -> bool:
        if self._closing:
            return False
        self._closing = True
        try:
            self._disable_input()
        except Exception:
            pass
        try:
            self._cancel_geometry()
        except Exception:
            pass
        if not self.enabled or self.reduced_motion:
            self._finish()
            return True
        try:
            self.root.update_idletasks()
            width = max(1, int(self.root.winfo_width()))
            height = max(1, int(self.root.winfo_height()))
            x = int(self.root.winfo_x())
            y = int(self.root.winfo_y())
            center_x = x + width / 2
            center_y = y + height / 2

            widened = max(width + 10, round(width * 1.03))
            self._set_geometry(widened, height, center_x, center_y)
            self._schedule(60, lambda: self._set_geometry(widened, 4, center_x, center_y))
            self._schedule(130, lambda: self._set_geometry(8, 4, center_x, center_y))
            self._schedule(190, lambda: self.root.attributes("-alpha", 0.35))
            self._schedule(240, self._fade_and_finish)
        except Exception:
            self.cancel()
            self._finish()
        return True

    def _set_geometry(self, width: int, height: int, center_x: float, center_y: float) -> None:
        x = round(center_x - width / 2)
        y = round(center_y - height / 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _schedule(self, delay_ms: int, callback: Callable[[], None]) -> None:
        holder: list = [None]

        def _run() -> None:
            self._job_ids.discard(holder[0])
            if not self._finished:
                try:
                    callback()
                except Exception:
                    self.cancel()
                    self._finish()

        holder[0] = self.root.after(delay_ms, _run)
        self._job_ids.add(holder[0])

    def _fade_and_finish(self) -> None:
        self.root.attributes("-alpha", 0.0)
        self._finish()

    def cancel(self) -> None:
        for job_id in tuple(self._job_ids):
            try:
                self.root.after_cancel(job_id)
            except Exception:
                pass
        self._job_ids.clear()

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self.cancel()
        self._graceful_shutdown()


__all__ = ["CRTCloseController"]
