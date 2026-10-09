"""Request lifecycle ownership, independent of tools, providers, UI and speech.

Engine states describe execution policy. These states describe who may still
run or deliver work. A running provider lease drains even after cancellation.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from .continuation import ContinuationDecision, ContinuationState, DecisionKind


class RequestState(str, Enum):
    WAITING = 'waiting'
    RUNNING = 'running'
    SUCCESS = 'success'
    FAILED = 'failed'
    CANCELED = 'canceled'
    INVALIDATED = 'invalidated'


_TERMINAL = frozenset({RequestState.SUCCESS, RequestState.FAILED,
    RequestState.CANCELED, RequestState.INVALIDATED})


@dataclass(frozen=True)
class RequestIdentity:
    request_id: str
    generation: int


class ContinuationLifecycle:
    """One admitted request and one authority for terminal transitions."""
    def __init__(self):
        self._lock = threading.RLock()
        self._current = None
        self._shutdown = False

    @property
    def current(self):
        with self._lock:
            return self._current

    def find(self, identity: RequestIdentity):
        with self._lock:
            current = self._current
            return current if current is not None and current.identity == identity else None

    def admit(self, identity: RequestIdentity, *, validity: Callable[[], bool],
              work_validity=None, cleanup=None):
        with self._lock:
            if self._shutdown:
                return None
            previous = self._current
            request = ContinuationRequest(self, identity, validity, work_validity, cleanup)
            self._current = request
        if previous is not None:
            previous.revoke(RequestState.INVALIDATED, 'newer_request')
        if not request.delivery_is_current():
            return None
        return request

    def invalidate(self, reason='generation_changed'):
        current = self.current
        if current is not None:
            current.revoke(RequestState.INVALIDATED, reason)

    def cancel(self, reason='escape'):
        current = self.current
        if current is not None:
            current.revoke(RequestState.CANCELED, reason)

    def shutdown(self):
        with self._lock:
            self._shutdown = True
        self.cancel('shutdown')


class _Reservation:
    def __init__(self, request, token, release, running):
        self.request, self.token = request, token
        self._release, self.running = release, running
        self._released = False

    def release(self):
        with self.request._owner._lock:
            if self._released:
                return False
            self._released = True
            if self.request._reservation is self:
                self.request._reservation = None
        self._release()
        return True


class ContinuationRequest:
    def __init__(self, owner, identity, validity, work_validity, cleanup):
        self._owner, self.identity = owner, identity
        self._validity, self._work_validity, self._cleanup = validity, work_validity, cleanup
        self._state, self.reason = RequestState.WAITING, ''
        self._revoked = self._published = False
        self._pending = set()
        self._reservation = self._context = None

    @property
    def state(self):
        with self._owner._lock:
            return self._state

    @property
    def context(self):
        with self._owner._lock:
            return self._context

    def set_context(self, context):
        if not self.work_is_current():
            return False
        with self._owner._lock:
            if self._state in _TERMINAL or self._owner._current is not self:
                return False
            self._context = context
            return True

    def delivery_is_current(self):
        # Host predicates may acquire their own lock. Never call them under ours.
        try:
            valid = bool(self._validity())
        except Exception:
            valid = False
        if not valid:
            self.revoke(RequestState.INVALIDATED, 'lifetime_changed')
            return False
        with self._owner._lock:
            return bool(not self._owner._shutdown and self._owner._current is self
                and not self._revoked and self._state not in {RequestState.CANCELED, RequestState.INVALIDATED})

    def work_is_current(self):
        if not self.delivery_is_current() or self.state in _TERMINAL:
            return False
        if self._work_validity is not None and not self._work_validity():
            self.finish(RequestState.FAILED, 'deadline_exceeded')
            return False
        with self._owner._lock:
            return self._owner._current is self and self._state not in _TERMINAL and not self._revoked

    def finish(self, state: RequestState, reason=''):
        if state not in _TERMINAL:
            raise ValueError('A terminal outcome is required')
        with self._owner._lock:
            if self._state in _TERMINAL:
                return False
            self._state, self.reason = state, reason
            self._context = None
            self._pending.clear()
            reservation = self._reservation
        try:
            if self._cleanup is not None:
                self._cleanup(self)
        finally:
            if reservation is not None and not reservation.running:
                reservation.release()
        return True

    def revoke(self, state, reason):
        with self._owner._lock:
            self._revoked = True
        return self.finish(state, reason)

    def claim_result(self, state, *, reason=''):
        if state not in {RequestState.SUCCESS, RequestState.FAILED}:
            raise ValueError('Only success/failure can publish a terminal result')
        if not self.delivery_is_current():
            return False
        self.finish(state, reason)
        with self._owner._lock:
            if self._state is not state or self._published or self._revoked or self._owner._current is not self:
                return False
            self._published = True
            return True

    def publish(self, state, result, deliver):
        if not self.claim_result(state):
            return False
        deliver(result, self.delivery_is_current)
        return True

    def reserve(self, token, release, *, running=False):
        lease = _Reservation(self, token, release, running)
        current = self.work_is_current()
        with self._owner._lock:
            accepted = (current and self._owner._current is self and self._state not in _TERMINAL
                and not self._revoked and self._reservation is None)
            if accepted:
                self._reservation = lease
        if not accepted:
            lease.release()
            return None
        return lease

    def handoff(self, starter, work, *, on_start_failure=None):
        if not self.work_is_current():
            return None
        ticket, claimed = object(), False
        with self._owner._lock:
            if self._state in _TERMINAL or self._revoked or self._owner._current is not self:
                return None
            self._pending.add(ticket)

        def run():
            nonlocal claimed
            if not self.work_is_current():
                return
            with self._owner._lock:
                if (ticket not in self._pending or self._revoked or self._state in _TERMINAL
                        or self._owner._current is not self or self._owner._shutdown):
                    return
                self._pending.remove(ticket)
                claimed = True
                self._state = RequestState.RUNNING
            try:
                work()
            except Exception as exc:
                self.finish(RequestState.FAILED, type(exc).__name__)
                raise
            finally:
                with self._owner._lock:
                    if self._state is RequestState.RUNNING:
                        self._state = RequestState.WAITING

        def failed():
            with self._owner._lock:
                if claimed:
                    return
                self._pending.discard(ticket)
            if self.finish(RequestState.FAILED, 'worker_start_failed') and on_start_failure is not None:
                on_start_failure()

        try:
            handle = starter(run, failed)
        except Exception:
            failed()
            raise
        if handle is None:
            failed()
        return handle


class LegacyContinuationAdapter:
    """Passive one-requery execution, retaining caller query/result semantics."""
    def __init__(self, lifecycle):
        self.lifecycle = lifecycle

    def admit(self, *, generation, validity):
        return self.lifecycle.admit(RequestIdentity(uuid.uuid4().hex, generation), validity=validity)

    @staticmethod
    def start(request, starter, query, deliver, *, on_start_failure=None):
        def run():
            result = query(request.context)
            if result:
                request.publish(RequestState.SUCCESS, result, deliver)
            elif request.work_is_current():
                request.finish(RequestState.FAILED, 'provider_failed')
        return request.handoff(starter, run, on_start_failure=on_start_failure)


class BoundedContinuationAdapter:
    """Translate engine progression into common request ownership."""
    def __init__(self, lifecycle, engine):
        self.lifecycle, self.engine = lifecycle, engine

    def admit(self, started, *, validity, cleanup, on_failure):
        identity = RequestIdentity(started.session_id, started.generation)
        def lifetime():
            if not validity():
                return False
            snapshot = self.engine.active_snapshot() or self.engine.last_snapshot()
            return bool(snapshot is not None
                and snapshot.session_id == identity.request_id
                and snapshot.generation == identity.generation
                and snapshot.state is not ContinuationState.CANCELLED)
        def finish(request):
            try:
                if request.state is RequestState.FAILED:
                    decision = self.engine.provider_failed(identity.request_id, identity.generation, request.reason)
                    if decision.kind is not DecisionKind.IGNORED:
                        on_failure(decision, request)
                elif request.state in {RequestState.CANCELED, RequestState.INVALIDATED}:
                    self.engine.cancel_active(request.reason, session_id=identity.request_id, generation=identity.generation)
            finally:
                cleanup(request)
        return self.lifecycle.admit(identity, validity=lifetime,
            work_validity=lambda: self.engine.is_current(identity.request_id, identity.generation), cleanup=finish)

    @staticmethod
    def terminal_state(decision: ContinuationDecision):
        if decision.kind is DecisionKind.FINAL:
            return RequestState.SUCCESS
        if decision.kind in {DecisionKind.BLOCKED, DecisionKind.STOPPED}:
            return RequestState.FAILED
        return None
