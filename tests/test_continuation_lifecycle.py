"""Pure lifecycle contracts: in-memory collaborators and deterministic calls."""
import unittest
import threading

from agetha.core.continuation_lifecycle import (
    BoundedContinuationAdapter, ContinuationLifecycle, RequestIdentity, RequestState,
)
from agetha.core.continuation import ContinuationEngine, DecisionKind


class LifecycleContracts(unittest.TestCase):
    def setUp(self):
        self.owner = ContinuationLifecycle()
        self.live = True
        self.cleaned, self.delivered, self.released, self.jobs = [], [], [], []

    def admit(self, name='A', generation=1, **kwargs):
        return self.owner.admit(RequestIdentity(name, generation),
            validity=lambda: self.live,
            cleanup=lambda request: self.cleaned.append((request.identity, request.state)), **kwargs)

    def deliver(self, result, validity):
        if validity():
            self.delivered.append(result)

    def start(self, run, failed):
        self.jobs.append((run, failed))
        return object()

    def test_completion_and_duplicate_final_publish_once(self):
        request = self.admit()
        request.publish(RequestState.SUCCESS, 'A', self.deliver)
        request.publish(RequestState.SUCCESS, 'A duplicate', self.deliver)
        self.assertEqual(self.delivered, ['A'])
        self.assertEqual(self.cleaned, [(RequestIdentity('A', 1), RequestState.SUCCESS)])

    def test_cancel_before_callback_cannot_resurrect(self):
        request = self.admit()
        request.handoff(self.start, lambda: request.publish(RequestState.SUCCESS, 'A', self.deliver))
        request.finish(RequestState.CANCELED, 'escape')
        self.jobs[0][0]()
        self.assertEqual(self.delivered, [])
        self.assertEqual(request.state, RequestState.CANCELED)
        self.assertEqual(len(self.cleaned), 1)

    def test_new_request_invalidates_old_and_late_cleanup_cannot_touch_new(self):
        first = self.admit()
        second = self.admit('B')
        first.finish(RequestState.FAILED, 'late failure')
        first.publish(RequestState.SUCCESS, 'A', self.deliver)
        second.publish(RequestState.SUCCESS, 'B', self.deliver)
        self.assertEqual(self.delivered, ['B'])
        self.assertIs(self.owner.current, second)
        self.assertEqual(first.state, RequestState.INVALIDATED)

    def test_same_identifier_new_generation_is_distinct(self):
        first = self.admit('A', 1)
        second = self.admit('A', 2)
        self.assertNotEqual(first.identity, second.identity)
        self.assertFalse(first.delivery_is_current())
        self.assertTrue(second.work_is_current())

    def test_queued_final_predicate_expires_after_invalidation(self):
        request = self.admit()
        retained = []
        request.publish(RequestState.SUCCESS, 'A', lambda value, valid: retained.append((value, valid)))
        self.admit('B')
        self.deliver(*retained[0])
        self.assertEqual(self.delivered, [])
        self.assertEqual(request.state, RequestState.SUCCESS)

    def test_deadline_expires_work_but_not_accepted_final(self):
        within_deadline = [True]
        request = self.admit(work_validity=lambda: within_deadline[0])
        retained = []
        request.publish(RequestState.SUCCESS, 'A', lambda value, valid: retained.append((value, valid)))
        within_deadline[0] = False
        self.deliver(*retained[0])
        self.assertEqual(self.delivered, ['A'])
        expired = self.admit('B', work_validity=lambda: False)
        self.assertFalse(expired.work_is_current())
        self.assertEqual(expired.state, RequestState.FAILED)
        self.assertEqual(expired.reason, 'deadline_exceeded')

    def test_lifetime_failure_finalizes_without_effect(self):
        request = self.admit()
        self.live = False
        request.publish(RequestState.SUCCESS, 'A', self.deliver)
        self.assertEqual(self.delivered, [])
        self.assertEqual(request.state, RequestState.INVALIDATED)
        self.assertEqual(len(self.cleaned), 1)

    def test_worker_start_failure_withdraws_pending_callback(self):
        request = self.admit()
        request.reserve('pending token', lambda: self.released.append('pending token'))
        def refuse(run, failed):
            self.jobs.append((run, failed))
            return None
        request.handoff(refuse, lambda: self.delivered.append('work'))
        self.jobs[0][0]()
        self.jobs[0][1]()
        self.assertEqual(self.delivered, [])
        self.assertEqual(request.state, RequestState.FAILED)
        self.assertEqual(len(self.cleaned), 1)
        self.assertEqual(self.released, ['pending token'])

    def test_worker_start_exception_after_entry_preserves_owned_work(self):
        request = self.admit()
        def entered(run, failed):
            run()
            raise RuntimeError('after callback entry')
        with self.assertRaises(RuntimeError):
            request.handoff(entered, lambda: request.publish(RequestState.SUCCESS, 'A', self.deliver))
        self.assertEqual(request.state, RequestState.SUCCESS)
        self.assertEqual(self.delivered, ['A'])
        self.assertEqual(len(self.cleaned), 1)

    def test_duplicate_worker_callback_runs_once(self):
        request = self.admit()
        request.handoff(self.start, lambda: self.delivered.append('work'))
        self.jobs[0][0]()
        self.jobs[0][0]()
        self.assertEqual(self.delivered, ['work'])
        self.assertEqual(request.state, RequestState.WAITING)

    def test_provider_release_once_for_success_failure_and_pending_cancel(self):
        for state in (RequestState.SUCCESS, RequestState.FAILED, RequestState.CANCELED):
            request = self.admit(state.value)
            token = object()
            lease = request.reserve(token, lambda: self.released.append(token))
            request.finish(state)
            lease.release()
            request.finish(state)
            self.assertEqual(self.released.count(token), 1)

    def test_running_provider_keeps_reservation_until_return_after_cancel(self):
        request = self.admit()
        token = object()
        lease = request.reserve(token, lambda: self.released.append(token), running=True)
        request.finish(RequestState.CANCELED)
        self.assertEqual(self.released, [])
        request.publish(RequestState.SUCCESS, 'late A', self.deliver)
        lease.release()
        lease.release()
        self.assertEqual(self.released, [token])
        self.assertEqual(self.delivered, [])

    def test_old_provider_return_cannot_release_successor_reservation(self):
        first = self.admit()
        token_a, token_b = object(), object()
        a = first.reserve(token_a, lambda: self.released.append(token_a), running=True)
        second = self.admit('B')
        b = second.reserve(token_b, lambda: self.released.append(token_b), running=True)
        a.release()
        self.assertEqual(self.released, [token_a])
        second.finish(RequestState.SUCCESS)
        b.release()
        self.assertEqual(self.released, [token_a, token_b])

    def test_request_context_clears_at_terminal_without_cross_request_state(self):
        first = self.admit()
        first.set_context('A synthetic context')
        second = self.admit('B')
        self.assertIsNone(first.context)
        self.assertIsNone(second.context)
        second.set_context('B synthetic context')
        first.set_context('late A')
        self.assertEqual(second.context, 'B synthetic context')
        self.assertIsNone(first.context)

    def test_delivery_exception_keeps_single_terminal_owner(self):
        request = self.admit()
        def broken(value, valid):
            raise ValueError('synthetic final delivery failure')
        with self.assertRaises(ValueError):
            request.publish(RequestState.SUCCESS, 'A', broken)
        request.publish(RequestState.SUCCESS, 'retry A', self.deliver)
        self.assertEqual(self.delivered, [])
        self.assertEqual(request.state, RequestState.SUCCESS)
        self.assertEqual(len(self.cleaned), 1)

    def test_cleanup_exception_does_not_repeat_cleanup_or_leak_idle_lease(self):
        cleaned = []
        def broken(request):
            cleaned.append(request.identity)
            raise ValueError('synthetic cleanup failure')
        request = self.owner.admit(RequestIdentity('A', 1), validity=lambda: True, cleanup=broken)
        request.reserve('A token', lambda: self.released.append('A token'))
        with self.assertRaises(ValueError):
            request.finish(RequestState.FAILED)
        request.finish(RequestState.FAILED)
        self.assertEqual(cleaned, [RequestIdentity('A', 1)])
        self.assertEqual(self.released, ['A token'])

    def test_shutdown_cancels_and_rejects_new_admission(self):
        request = self.admit()
        self.owner.shutdown()
        self.owner.shutdown()
        request.publish(RequestState.SUCCESS, 'late A', self.deliver)
        self.assertIsNone(self.admit('B'))
        self.assertEqual(request.state, RequestState.CANCELED)
        self.assertEqual(self.delivered, [])
        self.assertEqual(len(self.cleaned), 1)

    def test_bounded_late_cleanup_cannot_cancel_successor_engine_session(self):
        engine = ContinuationEngine()
        adapter = BoundedContinuationAdapter(self.owner, engine)
        a = engine.start('A', authority_origin='user')
        request = adapter.admit(a, validity=lambda: True,
            cleanup=lambda request: self.cleaned.append(request.identity), on_failure=lambda decision, request: None)
        b = engine.start('B', authority_origin='user')
        request.finish(RequestState.CANCELED, 'late A cleanup')
        self.assertEqual(engine.active_snapshot().session_id, b.session_id)
        self.assertEqual(self.cleaned, [RequestIdentity(a.session_id, a.generation)])

    def test_bounded_deadline_cleanup_records_failure_once(self):
        now = [0.0]
        engine = ContinuationEngine(clock=lambda: now[0])
        a = engine.start('A', authority_origin='user')
        failures = []
        request = BoundedContinuationAdapter(self.owner, engine).admit(a, validity=lambda: True,
            cleanup=lambda request: self.cleaned.append(request.identity), on_failure=lambda decision, request: failures.append(decision))
        now[0] = 120.0
        self.assertFalse(request.work_is_current())
        self.assertFalse(request.work_is_current())
        self.assertIsNone(engine.active_snapshot())
        self.assertEqual(request.state, RequestState.FAILED)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].kind, DecisionKind.STOPPED)
        self.assertEqual(failures[0].reason, 'deadline_exceeded')

    def test_bounded_engine_successor_invalidates_old_delivery_without_host_epoch_change(self):
        engine = ContinuationEngine()
        a = engine.start('A', authority_origin='user')
        request = BoundedContinuationAdapter(self.owner, engine).admit(a, validity=lambda: True,
            cleanup=lambda request: self.cleaned.append(request.identity), on_failure=lambda decision, request: None)
        b = engine.start('B', authority_origin='user')
        self.assertFalse(request.delivery_is_current())
        self.assertEqual(request.state, RequestState.INVALIDATED)
        self.assertEqual(engine.active_snapshot().session_id, b.session_id)

    def test_worker_start_failure_after_concurrent_claim_does_not_withdraw_running_work(self):
        request = self.admit()
        entered, returned = threading.Event(), threading.Event()
        errors, threads = [], []
        token = object()
        def work():
            lease = request.reserve(token, lambda: self.released.append(token), running=True)
            try:
                entered.set()
                if not returned.wait(2.0):
                    raise AssertionError('Synthetic provider was not released')
                request.publish(RequestState.SUCCESS, 'A', self.deliver)
            finally:
                lease.release()
        def starter(run, failed):
            def execute():
                try:
                    run()
                except Exception as exc:
                    errors.append(exc)
            thread = threading.Thread(target=execute)
            threads.append(thread)
            thread.start()
            self.assertTrue(entered.wait(2.0))
            failed()
            raise RuntimeError('Synthetic start failed after claim')
        try:
            with self.assertRaises(RuntimeError):
                request.handoff(starter, work)
            self.assertEqual(request.state, RequestState.RUNNING)
            self.assertEqual(self.cleaned, [])
            self.assertEqual(self.released, [])
        finally:
            returned.set()
            for thread in threads:
                thread.join(2.0)
        self.assertFalse(threads[0].is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(request.state, RequestState.SUCCESS)
        self.assertEqual(self.delivered, ['A'])
        self.assertEqual(self.released, [token])
        self.assertEqual(len(self.cleaned), 1)

    def test_invalidation_between_lifetime_check_and_publication_prevents_delivery(self):
        checked, proceed = threading.Event(), threading.Event()
        hold = [False]
        errors = []
        def validity():
            if hold[0]:
                checked.set()
                if not proceed.wait(2.0):
                    raise AssertionError('Validity barrier was not released')
            return True
        request = self.owner.admit(RequestIdentity('A', 1), validity=validity,
            cleanup=lambda request: self.cleaned.append(request.identity))
        hold[0] = True
        def publish():
            try:
                request.publish(RequestState.SUCCESS, 'A', self.deliver)
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=publish)
        thread.start()
        try:
            self.assertTrue(checked.wait(2.0))
            successor = self.admit('B')
        finally:
            proceed.set()
            thread.join(2.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(request.state, RequestState.INVALIDATED)
        self.assertEqual(self.delivered, [])
        successor.publish(RequestState.SUCCESS, 'B', self.deliver)
        self.assertEqual(self.delivered, ['B'])

    def test_cancel_during_terminal_cleanup_revokes_delivery_without_second_terminal(self):
        cleaned, proceed = threading.Event(), threading.Event()
        errors = []
        def cleanup(request):
            self.cleaned.append((request.identity, request.state))
            cleaned.set()
            if not proceed.wait(2.0):
                raise AssertionError('Cleanup barrier was not released')
        request = self.owner.admit(RequestIdentity('A', 1), validity=lambda: True, cleanup=cleanup)
        def publish():
            try:
                request.publish(RequestState.SUCCESS, 'A', self.deliver)
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=publish)
        thread.start()
        try:
            self.assertTrue(cleaned.wait(2.0))
            self.owner.cancel()
        finally:
            proceed.set()
            thread.join(2.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(request.state, RequestState.SUCCESS)
        self.assertFalse(request.delivery_is_current())
        self.assertEqual(self.delivered, [])
        self.assertEqual(self.cleaned, [(RequestIdentity('A', 1), RequestState.SUCCESS)])

    def test_failed_lifetime_predicate_revokes_and_releases_pending_ownership(self):
        broken = [False]
        def validity():
            if broken[0]:
                raise RuntimeError('synthetic lifetime check failure')
            return True
        request = self.owner.admit(RequestIdentity('A', 1), validity=validity,
            cleanup=lambda request: self.cleaned.append(request.identity))
        request.reserve('A token', lambda: self.released.append('A token'))
        broken[0] = True
        self.assertFalse(request.work_is_current())
        self.assertEqual(request.state, RequestState.INVALIDATED)
        self.assertEqual(self.cleaned, [RequestIdentity('A', 1)])
        self.assertEqual(self.released, ['A token'])

    def test_bounded_late_cleanup_rejects_reused_session_id_with_new_generation(self):
        engine = ContinuationEngine(id_factory=lambda: 'reused-id')
        a = engine.start('A', authority_origin='user')
        request = BoundedContinuationAdapter(self.owner, engine).admit(a, validity=lambda: True,
            cleanup=lambda request: self.cleaned.append(request.identity), on_failure=lambda decision, request: None)
        b = engine.start('B', authority_origin='user')
        self.assertEqual(a.session_id, b.session_id)
        self.assertNotEqual(a.generation, b.generation)
        request.finish(RequestState.CANCELED, 'late A cleanup')
        snapshot = engine.active_snapshot()
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.generation, b.generation)

    def test_successor_admission_before_worker_claim_prevents_old_work(self):
        request = self.admit()
        queued, effects, errors, successors = [], [], [], []
        request.handoff(lambda run, failed: queued.append(run) or object(),
            lambda: effects.append('A'))
        checked, admitted, claim, cleanup = (threading.Event() for _ in range(4))
        current, revoke = request.work_is_current, request.revoke
        def held_validation():
            valid = current()
            checked.set()
            if not claim.wait(2.0):
                raise AssertionError('Worker claim barrier was not released')
            return valid
        def held_revocation(*args):
            # Admission has granted B ownership, but A cleanup has not begun.
            admitted.set()
            if not cleanup.wait(2.0):
                raise AssertionError('Revocation barrier was not released')
            return revoke(*args)
        request.work_is_current = held_validation
        request.revoke = held_revocation
        def run(call):
            try:
                call()
            except Exception as error:
                errors.append(error)
        worker = threading.Thread(target=run, args=(queued[0],))
        admission = threading.Thread(target=run, args=(lambda: successors.append(self.admit('B')),))
        worker.start()
        try:
            self.assertTrue(checked.wait(2.0))
            admission.start()
            self.assertTrue(admitted.wait(2.0))
            claim.set()
            worker.join(2.0)
            self.assertFalse(worker.is_alive())
            self.assertEqual(effects, [])
        finally:
            claim.set()
            cleanup.set()
            worker.join(2.0)
            if admission.ident is not None:
                admission.join(2.0)
        self.assertFalse(admission.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(request.state, RequestState.INVALIDATED)
        self.assertTrue(successors[0].work_is_current())
