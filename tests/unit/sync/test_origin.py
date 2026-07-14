"""Origin / echo-suppression tests (task T5.2, spec §4.8 echo suppression).

Covers, per this task's DoD and the user's holistic-testing requirement:
a recorded write's exact ``(path, hash)`` event is suppressed exactly
once (first ``is_echo`` True, an immediate second ``is_echo`` for the
same pair False — the record is consumed); a different hash on the same
path is NOT suppressed; the same hash on a different path is NOT
suppressed; an event with no prior recorded write is NOT suppressed;
bounded-memory eviction both by count (oldest dropped past
``max_pending``) and by TTL (dropped once the injected clock advances
past ``ttl_seconds``); multiple distinct pending writes are each
independently suppressed (order-independent, and suppressing one doesn't
disturb the others).
"""

from __future__ import annotations

from akasha.sync.origin import OriginTracker


def test_recorded_write_is_suppressed_exactly_once():
    tracker = OriginTracker()
    tracker.record_write("notes/a.md", "hash-1")

    assert tracker.is_echo("notes/a.md", "hash-1") is True
    # Consumed: an immediate second identical event is NOT suppressed
    # (DoD: "suppressed once" — a repeat is treated as a genuine write).
    assert tracker.is_echo("notes/a.md", "hash-1") is False


def test_different_hash_same_path_is_not_suppressed():
    tracker = OriginTracker()
    tracker.record_write("notes/a.md", "hash-1")

    assert tracker.is_echo("notes/a.md", "hash-2") is False
    # The original record is still pending (not consumed by the miss).
    assert tracker.is_echo("notes/a.md", "hash-1") is True


def test_same_hash_different_path_is_not_suppressed():
    tracker = OriginTracker()
    tracker.record_write("notes/a.md", "hash-1")

    assert tracker.is_echo("notes/b.md", "hash-1") is False
    # The original record is still pending (not consumed by the miss).
    assert tracker.is_echo("notes/a.md", "hash-1") is True


def test_event_with_no_prior_recorded_write_is_not_suppressed():
    tracker = OriginTracker()

    assert tracker.is_echo("notes/never-written.md", "hash-1") is False


def test_multiple_distinct_pending_writes_are_each_independently_suppressed():
    tracker = OriginTracker()
    tracker.record_write("notes/a.md", "hash-a")
    tracker.record_write("notes/b.md", "hash-b")
    tracker.record_write("notes/c.md", "hash-c")

    # Consume in a different order than recorded, each exactly once.
    assert tracker.is_echo("notes/b.md", "hash-b") is True
    assert tracker.is_echo("notes/b.md", "hash-b") is False

    assert tracker.is_echo("notes/a.md", "hash-a") is True
    assert tracker.is_echo("notes/c.md", "hash-c") is True

    # All consumed now; none suppress a second time.
    assert tracker.is_echo("notes/a.md", "hash-a") is False
    assert tracker.is_echo("notes/c.md", "hash-c") is False


def test_bounded_memory_evicts_oldest_past_max_pending_count():
    tracker = OriginTracker(max_pending=2)
    tracker.record_write("notes/a.md", "hash-a")
    tracker.record_write("notes/b.md", "hash-b")
    # Exceeds the bound of 2; "a" (the oldest) must be evicted.
    tracker.record_write("notes/c.md", "hash-c")

    assert tracker.is_echo("notes/a.md", "hash-a") is False
    assert tracker.is_echo("notes/b.md", "hash-b") is True
    assert tracker.is_echo("notes/c.md", "hash-c") is True


def test_bounded_memory_evicts_expired_records_past_ttl():
    tracker = OriginTracker(ttl_seconds=10.0)
    tracker.record_write("notes/a.md", "hash-a", now=0.0)

    # Just under the TTL: still pending.
    assert tracker.is_echo("notes/a.md", "hash-a", now=9.9) is True

    # Re-record, then advance the injected clock past the TTL before the
    # matching event arrives: must be evicted, not suppressed.
    tracker.record_write("notes/a.md", "hash-a", now=0.0)
    assert tracker.is_echo("notes/a.md", "hash-a", now=10.1) is False


def test_ttl_eviction_does_not_disturb_still_fresh_records():
    tracker = OriginTracker(ttl_seconds=10.0)
    tracker.record_write("notes/old.md", "hash-old", now=0.0)
    tracker.record_write("notes/fresh.md", "hash-fresh", now=5.0)

    # At t=10.1, "old" (recorded at t=0) has expired but "fresh"
    # (recorded at t=5, age 5.1s) has not.
    assert tracker.is_echo("notes/old.md", "hash-old", now=10.1) is False
    assert tracker.is_echo("notes/fresh.md", "hash-fresh", now=10.1) is True
