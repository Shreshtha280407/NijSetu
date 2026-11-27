"""Regression test: a patient-specific date shift must never land an event
date exactly on a DOB -- their own, or anyone else's in the same release --
since a whole-file leak scanner cannot tell that result apart from a real
DOB leak (see phase2.patient_context._shift_avoiding_dob_collision)."""

from __future__ import annotations

from datetime import date, timedelta

from nijsetu.phase2 import patient_context as pc


def test_colliding_shift_is_replaced_with_a_safe_one(monkeypatch):
    dob = date(2025, 10, 30)
    event = date(2024, 2, 2)
    colliding_shift = (dob - event).days  # the exact bug reproduced from the practice corpus

    calls = iter([colliding_shift, 5])  # first candidate collides, second doesn't
    monkeypatch.setattr(pc.keys, "date_shift_days", lambda *a, **k: next(calls))

    policy = pc.PolicyConfig(name="test")
    shift = pc._shift_avoiding_dob_collision(b"key", "cluster", policy, colliding_shift, [event], frozenset({dob}))

    assert event + timedelta(days=shift) != dob
    assert shift == 5


def test_non_colliding_shift_is_left_untouched():
    dob = date(2025, 10, 30)
    event = date(2024, 2, 2)
    policy = pc.PolicyConfig(name="test")

    shift = pc._shift_avoiding_dob_collision(b"key", "cluster", policy, 3, [event], frozenset({dob}))

    assert shift == 3


def test_gives_up_after_bounded_retries_without_looping_forever(monkeypatch):
    dob = date(2025, 10, 30)
    event = date(2024, 2, 2)
    colliding_shift = (dob - event).days
    monkeypatch.setattr(pc.keys, "date_shift_days", lambda *a, **k: colliding_shift)

    policy = pc.PolicyConfig(name="test")
    shift = pc._shift_avoiding_dob_collision(b"key", "cluster", policy, colliding_shift, [event], frozenset({dob}))

    assert shift == colliding_shift  # every retry collided too; returns the last one rather than looping forever
