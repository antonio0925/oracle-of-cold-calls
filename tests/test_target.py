"""The daily call target.

The BDR says how many calls they want. The route builder stops there, so a
400-contact list does not cost 400 Octave calls to work 50.
"""
import config
from app import _clamp_target as _clamp


def test_default_target():
    assert config.DEFAULT_CALL_TARGET == 50


def test_a_number_is_taken_as_given():
    assert _clamp(25) == 25
    assert _clamp("120") == 120


def test_missing_or_junk_falls_back_to_the_default():
    for raw in [None, "", "abc", {}, []]:
        assert _clamp(raw) == config.DEFAULT_CALL_TARGET


def test_zero_and_negatives_clamp_up_to_one():
    # A target of 0 would build an empty list and read as a broken app.
    assert _clamp(0) == 1
    assert _clamp(-5) == 1


def test_absurd_targets_clamp_to_the_ceiling():
    assert _clamp(999999) == config.MAX_CALL_TARGET


def _simulate_run(list_size, target, dialable=lambda i: True):
    """Mirror of the loop's stop condition. Returns (prepped, scanned)."""
    prepped = scanned = 0
    for i in range(list_size):
        if prepped >= target:
            break
        scanned += 1
        if dialable(i):
            prepped += 1
    return prepped, scanned


def test_the_loop_stops_at_the_target():
    prepped, scanned = _simulate_run(list_size=413, target=50)
    assert prepped == 50
    assert scanned == 50, "must not touch the other 363 contacts"


def test_a_list_smaller_than_the_target_is_fully_worked():
    prepped, scanned = _simulate_run(list_size=12, target=50)
    assert prepped == 12
    assert scanned == 12


def test_skipped_contacts_do_not_count_toward_the_target():
    # Every other contact is filtered out, so reaching 10 costs 20 scans.
    prepped, scanned = _simulate_run(413, target=10, dialable=lambda i: i % 2 == 0)
    assert prepped == 10
    assert scanned == 19


def test_an_all_skipped_list_terminates_instead_of_hanging():
    prepped, scanned = _simulate_run(100, target=50, dialable=lambda i: False)
    assert prepped == 0
    assert scanned == 100
