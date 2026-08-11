"""Focused tests for the local typed observation bus."""

from __future__ import annotations

import sys
import threading
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agetha.core.observation_bus import (  # noqa: E402
    Observation,
    ObservationBus,
    ObservationKind,
    ObservationUse,
    PublishStatus,
    Sensitivity,
    eligibility_for,
)


class FakeClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        self.monotonic_value = 100.0
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return self.current

    def monotonic(self) -> float:
        with self._lock:
            return self.monotonic_value

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.current += timedelta(seconds=seconds)
            self.monotonic_value += seconds


class ObservationMixin:
    def setUp(self) -> None:
        self.clock = FakeClock()

    def observation(
        self,
        summary: str = "user became idle",
        *,
        kind: ObservationKind = ObservationKind.USER_BECAME_IDLE,
        sensitivity: Sensitivity = Sensitivity.INTERNAL,
        expires_in: float | None = 60.0,
        dedup_key: str | None = None,
        local_only: bool = True,
        metadata: dict[str, object] | None = None,
        request_origin: str | None = "ambient",
        confidence: float = 0.8,
        source: str = "test",
    ) -> Observation:
        expires = (
            None
            if expires_in is None
            else self.clock.now() + timedelta(seconds=expires_in)
        )
        return Observation(
            kind=kind,
            source=source,
            summary=summary,
            confidence=confidence,
            sensitivity=sensitivity,
            created_at=self.clock.now(),
            expires_at=expires,
            local_only=local_only,
            dedup_key=dedup_key,
            metadata=metadata or {},
            request_origin=request_origin,
        )

    def bus(self, **kwargs: object) -> ObservationBus:
        return ObservationBus(
            clock=self.clock.now,
            monotonic=self.clock.monotonic,
            **kwargs,
        )


class TestObservationRecord(ObservationMixin, unittest.TestCase):
    def test_record_and_nested_metadata_are_immutable(self) -> None:
        metadata = {"position": {"x": 1}, "labels": ["python", "traceback"]}
        item = self.observation(metadata=metadata)
        metadata["position"] = {"x": 99}

        with self.assertRaises(FrozenInstanceError):
            item.summary = "changed"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            item.metadata["new"] = "value"  # type: ignore[index]
        nested = item.metadata["position"]
        with self.assertRaises(TypeError):
            nested["x"] = 2  # type: ignore[index]
        self.assertEqual(item.metadata["labels"], ("python", "traceback"))
        self.assertEqual(nested["x"], 1)  # type: ignore[index]

    def test_confidence_is_clamped(self) -> None:
        self.assertEqual(self.observation(confidence=-4).confidence, 0.0)
        self.assertEqual(self.observation(confidence=8).confidence, 1.0)
        self.assertEqual(self.observation(confidence=float("nan")).confidence, 0.0)

    def test_naive_datetimes_are_normalized_to_utc(self) -> None:
        item = Observation(
            kind=ObservationKind.TASK_DUE,
            source="tasks",
            summary="task due",
            confidence=1.0,
            sensitivity=Sensitivity.INTERNAL,
            created_at=datetime(2026, 8, 3, 12, 0),
        )
        self.assertEqual(item.created_at.tzinfo, timezone.utc)

    def test_sensitive_metadata_fields_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            self.observation(metadata={"provider_api_key": "do-not-store"})
        with self.assertRaises(ValueError):
            self.observation(metadata={"raw_ocr": "large private capture"})
        with self.assertRaises(ValueError):
            self.observation(metadata={"nested": {"password": "do-not-store"}})

    def test_credential_like_summary_and_values_are_redacted(self) -> None:
        secret = "AKIAABCDEFGHIJKLMNOP"
        item = self.observation(
            summary=f"provider unavailable: {secret}",
            metadata={"detail": f"Bearer abcdefghijklmnop {secret}"},
        )
        self.assertNotIn(secret, item.summary)
        self.assertNotIn(secret, str(item.metadata))
        self.assertNotIn("abcdefghijklmnop", str(item.metadata))
        self.assertIn("[REDACTED", item.summary)

    def test_summary_and_metadata_are_bounded(self) -> None:
        with self.assertRaises(ValueError):
            self.observation(summary="x" * 513)
        with self.assertRaises(ValueError):
            self.observation(metadata={"snippet": "x" * 513})
        with self.assertRaises(ValueError):
            self.observation(metadata={"snippet": b"x" * 513})
        with self.assertRaises(TypeError):
            self.observation(metadata={1: "not a string key"})  # type: ignore[dict-item]

    def test_request_origin_is_preserved_without_interpretation(self) -> None:
        item = self.observation(request_origin="terminal_sentinel_explain")
        bus = self.bus()
        self.assertTrue(bus.publish(item))
        self.assertEqual(bus.drain()[0].request_origin, "terminal_sentinel_explain")


class TestObservationBus(ObservationMixin, unittest.TestCase):
    def test_publication_peek_drain_and_fifo_ordering(self) -> None:
        bus = self.bus()
        first = self.observation("first")
        second = self.observation("second", kind=ObservationKind.TASK_DUE)
        self.assertTrue(bus.publish(first))
        self.assertTrue(bus.publish(second))
        self.assertEqual(bus.peek(), (first, second))
        self.assertEqual(bus.peek(1), (first,))
        self.assertEqual(bus.drain(1), (first,))
        self.assertEqual(bus.drain(), (second,))
        self.assertEqual(len(bus), 0)

    def test_bounded_queue_drops_oldest(self) -> None:
        bus = self.bus(max_size=2)
        bus.publish(self.observation("one"))
        bus.publish(self.observation("two"))
        result = bus.publish(self.observation("three"))
        self.assertTrue(result)
        self.assertTrue(result.dropped_oldest)
        self.assertEqual([item.summary for item in bus.peek()], ["two", "three"])

    def test_expiry_is_enforced_on_publish_and_retrieval(self) -> None:
        bus = self.bus()
        soon = self.observation("soon", expires_in=5)
        bus.publish(soon)
        self.clock.advance(6)
        self.assertEqual(bus.peek(), ())

        expired = self.observation("already expired", expires_in=-1)
        result = bus.publish(expired)
        self.assertFalse(result)
        self.assertEqual(result.status, PublishStatus.EXPIRED)

    def test_deduplication_has_kind_source_and_bounded_window(self) -> None:
        bus = self.bus(dedup_window_seconds=10)
        first = self.observation("first", dedup_key="same")
        duplicate = self.observation("updated", dedup_key="same")
        self.assertTrue(bus.publish(first))
        duplicate_result = bus.publish(duplicate)
        self.assertFalse(duplicate_result)
        self.assertEqual(duplicate_result.status, PublishStatus.DUPLICATE)

        other_kind = self.observation(
            "other kind", kind=ObservationKind.TASK_DUE, dedup_key="same"
        )
        self.assertTrue(bus.publish(other_kind))
        self.clock.advance(11)
        self.assertTrue(bus.publish(duplicate))

    def test_dedup_cache_is_bounded_with_the_queue(self) -> None:
        bus = self.bus(max_size=2, dedup_window_seconds=1000)
        for index in range(20):
            bus.publish(self.observation(str(index), dedup_key=str(index)))
        self.assertLessEqual(len(bus._dedup_until), bus.max_size)

    def test_thread_safe_publication(self) -> None:
        bus = self.bus(max_size=1000)
        start = threading.Barrier(9)

        def publisher(worker: int) -> None:
            start.wait()
            for index in range(100):
                bus.publish(self.observation(f"{worker}:{index}"))

        threads = [threading.Thread(target=publisher, args=(index,)) for index in range(8)]
        for thread in threads:
            thread.start()
        start.wait()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())

        queued = bus.peek()
        self.assertEqual(len(queued), 800)
        self.assertEqual(len({item.summary for item in queued}), 800)

    def test_shutdown_is_idempotent_and_rejects_publication(self) -> None:
        bus = self.bus()
        bus.publish(self.observation())
        bus.shutdown()
        bus.shutdown()
        self.assertTrue(bus.is_shutdown)
        self.assertEqual(bus.peek(), ())
        result = bus.publish(self.observation("late"))
        self.assertFalse(result)
        self.assertEqual(result.status, PublishStatus.SHUTDOWN)

    def test_negative_limit_is_rejected(self) -> None:
        bus = self.bus()
        with self.assertRaises(ValueError):
            bus.peek(-1)
        with self.assertRaises(ValueError):
            bus.drain(-1)


class TestObservationEligibility(ObservationMixin, unittest.TestCase):
    def test_publication_never_authorizes_provider_memory_or_command(self) -> None:
        item = self.observation(
            sensitivity=Sensitivity.PUBLIC,
            local_only=False,
        )
        default = eligibility_for(item)
        self.assertTrue(default.local_reaction)
        self.assertTrue(default.notification)
        self.assertFalse(default.provider_context)
        self.assertFalse(default.memory)
        self.assertFalse(default.guarded_action)
        self.assertFalse(default.allows(ObservationUse.GUARDED_ACTION))

        separately_authorized = eligibility_for(
            item,
            provider_authorized=True,
            memory_authorized=True,
        )
        self.assertTrue(separately_authorized.provider_context)
        self.assertTrue(separately_authorized.memory)
        self.assertFalse(separately_authorized.guarded_action)

    def test_local_only_and_sensitivity_remain_authoritative(self) -> None:
        local = self.observation(
            sensitivity=Sensitivity.PUBLIC,
            local_only=True,
        )
        self.assertFalse(
            eligibility_for(local, provider_authorized=True).provider_context
        )

        private = self.observation(
            sensitivity=Sensitivity.PRIVATE,
            local_only=False,
        )
        private_policy = eligibility_for(
            private,
            provider_authorized=True,
            memory_authorized=True,
        )
        self.assertFalse(private_policy.provider_context)
        self.assertFalse(private_policy.memory)

        sensitive = self.observation(sensitivity=Sensitivity.SENSITIVE)
        self.assertFalse(eligibility_for(sensitive).notification)

    def test_expired_observation_has_no_eligible_use(self) -> None:
        item = self.observation(expires_in=1, local_only=False)
        self.clock.advance(2)
        policy = eligibility_for(
            item,
            now=self.clock.now(),
            provider_authorized=True,
            memory_authorized=True,
        )
        self.assertFalse(policy.local_reaction)
        self.assertFalse(policy.notification)
        self.assertFalse(policy.provider_context)
        self.assertFalse(policy.memory)
        self.assertFalse(policy.guarded_action)


if __name__ == "__main__":
    unittest.main(verbosity=2)
