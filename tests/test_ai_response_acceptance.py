"""Accepted-response parity at real query entry points with synthetic effects."""

import json
import unittest
from unittest.mock import MagicMock, patch

from agetha.core import ai_engine
from tests import test_ai_response_recovery as fixtures


ROUTES = ('query', 'stream', 'local-stream-fallback')


def envelope(command='speak', **fields):
    return json.dumps(dict(command=command, mood='happy', segments=[{'text': 'Synthetic answer', 'pause': 0}],
                          shutdown=False, **fields))


class AcceptedResponseContracts(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(ai_engine, '_MEMORY_SYSTEM_AVAILABLE', False))

    def engine(self, route, responses):
        if route == 'local-stream-fallback':
            responses = [RuntimeError('synthetic local stream unavailable'), *responses]
        engine = fixtures._query_engine(responses)
        engine._command_execution_enabled = True
        engine._config = {'LOCAL_AI_MODEL': 'synthetic/model', 'LOCAL_AI_TIMEOUT': '17'}
        if route == 'local-stream-fallback':
            engine._use_local_ai, engine._use_openrouter = True, False
        return engine

    def invoke(self, route, engine, *, origin='user', profile='fast_user', user='hello', tokens=None, **extra):
        arguments = dict(user_message=user, request_profile=profile, request_origin=origin, **extra)
        if route == 'query':
            return engine.query(**arguments)
        return engine.query_streaming(on_token=None if tokens is None else tokens.append, **arguments)

    def trace(self, engine, events):
        safety = engine._enforce_profile_response_safety
        persist = engine._persist_profile_memory
        record = engine._record_profile_response
        def checked(result, *args):
            events.append(('safety', result['command']))
            return safety(result, *args)
        def saved(profile, user, raw, result):
            events.append(('persist', result['command']))
            return persist(profile, user, raw, result)
        def recorded(profile, turn, raw, result):
            events.append(('record', result['command']))
            return record(profile, turn, raw, result)
        engine._enforce_profile_response_safety = checked
        engine._persist_profile_memory = saved
        engine._record_profile_response = recorded

    def test_success_orders_policy_persistence_recording_and_direct_stream_publication(self):
        raw = '  ' + envelope(summary_memory='synthetic summary') + '  '
        for route in ROUTES:
            with self.subTest(route=route):
                engine, events = self.engine(route, [raw]), []
                self.trace(engine, events)
                class Tokens:
                    def append(self, text): events.append(('token', text))
                result = self.invoke(route, engine, tokens=Tokens())
                self.assertEqual(result['segments'][0]['text'], 'Synthetic answer')
                self.assertEqual(events[:3], [('safety', 'speak'), ('persist', 'speak'), ('record', 'speak')])
                self.assertEqual([event[0] for event in events],
                                 ['safety', 'persist', 'record'] + ([] if route == 'query' else ['token']))
                expected_raw = raw if route == 'stream' else raw.strip()
                self.assertEqual(engine._record.call_args.args[1], expected_raw)
                if route != 'query':
                    self.assertEqual(events[-1], ('token', expected_raw))
                engine._save_memory.assert_called_once_with('synthetic summary')
                engine._record.assert_called_once()
                self.assertEqual([call['stream'] for call in engine._client.calls],
                                 [False] if route == 'query' else [True] if route == 'stream' else [True, False])

    def test_direct_idle_keeps_mode_wording_and_records_original_raw(self):
        raw = json.dumps({'command': 'idle', 'segments': [], 'shutdown': False})
        for route in ROUTES:
            with self.subTest(route=route):
                engine, events, tokens = self.engine(route, [raw]), [], []
                self.trace(engine, events)
                def choose(pool):
                    events.append(('fallback', pool))
                    return pool[0]
                with patch.object(ai_engine.random, 'choice', side_effect=choose):
                    result = self.invoke(route, engine, tokens=tokens)
                expected = ([{'text': '...', 'pause': 0.5}, {'text': 'I was somewhere else.', 'pause': 0.0}]
                            if route == 'query' else [{'text': 'Mm.', 'pause': 0.0}])
                self.assertEqual(result['segments'], expected)
                self.assertEqual(result['command'], 'speak')
                self.assertEqual([event[0] for event in events], ['safety', 'persist', 'fallback', 'record'])
                self.assertEqual(events[1], ('persist', 'idle'))
                self.assertEqual(events[-1], ('record', 'speak'))
                self.assertEqual(events[2][1], [
                    [{'text': '...', 'pause': 0.5}, {'text': 'I was somewhere else.', 'pause': 0.0}],
                    [{'text': 'Mm.', 'pause': 0.0}],
                    [{'text': '...', 'pause': 0.6}, {'text': 'Say that again.', 'pause': 0.0}],
                    [{'text': 'Hm.', 'pause': 0.0}],
                ] if route == 'query' else [[{'text': 'Mm.', 'pause': 0.0}]])
                self.assertEqual(engine._record.call_args.args[1], raw)
                self.assertEqual(tokens, [] if route == 'query' else [raw])

    def test_tool_continuation_idle_is_not_converted_for_direct_origin(self):
        raw = json.dumps({'command': 'idle', 'segments': [], 'shutdown': False})
        for route in ROUTES:
            with self.subTest(route=route), patch.object(ai_engine.random, 'choice') as choice:
                engine = self.engine(route, [raw])
                result = self.invoke(route, engine, profile='tool_continuation')
                self.assertEqual(result['command'], 'idle')
                choice.assert_not_called()
                engine._save_memory.assert_not_called()
                engine._record.assert_not_called()

    def test_profile_exclusions_reach_real_memory_and_history_gates(self):
        for route in ROUTES:
            for profile in ('fast_ambient', 'fast_command', 'fast_tool_result', 'tool_continuation'):
                with self.subTest(route=route, profile=profile):
                    engine = self.engine(route, [envelope(summary_memory='must not persist')])
                    self.invoke(route, engine, origin='tool_result', profile=profile,
                                user='[internal event: tool_result]')
                    engine._save_memory.assert_not_called()
                    engine._record.assert_not_called()

    def test_safety_changed_result_precedes_persistence_and_recording(self):
        for route in ROUTES:
            with self.subTest(route=route):
                raw = envelope('shutdown', delay=10, summary_memory='must not persist')
                engine, events = self.engine(route, [raw]), []
                self.trace(engine, events)
                result = self.invoke(route, engine, origin='tool_result', profile='tool_continuation')
                self.assertEqual(result['command'], 'idle')
                self.assertFalse(result['shutdown'])
                self.assertNotIn('summary_memory', result)
                self.assertEqual(events, [('safety', 'shutdown'), ('persist', 'idle'), ('record', 'idle')])
                engine._save_memory.assert_not_called()
                engine._record.assert_not_called()

    def test_exact_typing_payload_keeps_memory_exclusion_and_history_redaction(self):
        for route in ROUTES:
            with self.subTest(route=route):
                raw = envelope('type_text', text='synthetic private payload', summary_memory='must not persist')
                engine = self.engine(route, [raw])
                result = self.invoke(route, engine)
                self.assertEqual(result['command'], 'type_text')
                engine._save_memory.assert_not_called()
                engine._record.assert_called_once()
                self.assertNotIn('synthetic private payload', engine._record.call_args.args[1])
                self.assertIn('exact payload omitted', engine._record.call_args.args[1])

    def test_parse_repair_accepts_only_one_memory_and_history_result(self):
        for route in ROUTES:
            with self.subTest(route=route):
                raw = envelope(summary_memory='accepted once')
                engine, events = self.engine(route, ['not JSON', raw]), []
                self.trace(engine, events)
                result = self.invoke(route, engine)
                self.assertEqual(result['segments'][0]['text'], 'Synthetic answer')
                self.assertEqual([event[0] for event in events], ['safety', 'persist', 'record'])
                engine._save_memory.assert_called_once_with('accepted once')
                engine._record.assert_called_once()
                self.assertEqual(len(engine._client.calls), 3 if route == 'local-stream-fallback' else 2)

    def test_provider_authorization_expiry_before_acceptance_keeps_zero_side_effects(self):
        for route in ROUTES:
            with self.subTest(route=route):
                engine, allowed, tokens, events = self.engine(route, [envelope(summary_memory='discard')]), [True], [], []
                self.trace(engine, events)
                create = engine._client.create
                def expire(**kwargs):
                    result = create(**kwargs)
                    allowed[0] = False
                    return result
                engine._client.chat.completions.create = expire
                result = self.invoke(route, engine, tokens=tokens, provider_authorization=lambda: allowed[0])
                self.assertEqual(result['command'], 'idle')
                self.assertEqual(events, [])
                self.assertEqual(tokens, [])
                engine._save_memory.assert_not_called()
                engine._record.assert_not_called()

    def test_acceptance_exception_stays_inside_existing_route_failure_budget(self):
        for route in ROUTES:
            with self.subTest(route=route):
                engine = self.engine(route, [envelope()] * 3)
                engine._enforce_profile_response_safety = MagicMock(side_effect=RuntimeError('synthetic acceptance failure'))
                result = self.invoke(route, engine)
                self.assertEqual(result['command'], 'idle')
                self.assertEqual(len(engine._client.calls), 2 if route == 'local-stream-fallback' else 3)
                engine._save_memory.assert_not_called()
                engine._record.assert_not_called()

    def test_non_user_stream_prefix_remains_before_acceptance_without_extra_final_callback(self):
        for route in ('stream', 'local-stream-fallback'):
            with self.subTest(route=route):
                raw = envelope()
                engine, events = self.engine(route, [raw]), []
                self.trace(engine, events)
                class Tokens:
                    def append(self, text): events.append(('token', text))
                result = self.invoke(route, engine, origin='tool_result', profile='fast_tool_result',
                                     user='[internal event: tool_result]', tokens=Tokens())
                self.assertEqual(result['command'], 'speak')
                self.assertEqual([event[0] for event in events],
                                 (['token'] if route == 'stream' else []) + ['safety', 'persist', 'record'])
                engine._save_memory.assert_not_called()
                engine._record.assert_not_called()
