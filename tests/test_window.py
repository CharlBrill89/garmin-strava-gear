"""Which date range a run covers, given the flags and whether an export is in play."""

import argparse
from datetime import date

import pytest

import compare_gear

SPAN = (date(2021, 9, 13), date(2026, 9, 9))


def _args(**kw):
    base = {"days": None, "all": False, "start": None, "end": None}
    base.update(kw)
    return argparse.Namespace(**base)


def test_export_with_no_flags_means_the_whole_export(capsys):
    assert compare_gear.resolve_window(_args(), SPAN) == SPAN
    assert "whole Strava export" in capsys.readouterr().out


def test_all_flag_spans_export():
    assert compare_gear.resolve_window(_args(all=True), SPAN) == SPAN


def test_all_flag_without_export_goes_back_to_2000():
    start, end = compare_gear.resolve_window(_args(all=True), None)
    assert start == date(2000, 1, 1)
    assert end >= date(2026, 9, 18)


def test_no_flags_without_export_defaults_to_90_days():
    start, end = compare_gear.resolve_window(_args(), None)
    assert (end - start).days == 90


def test_explicit_days_wins_over_export_span():
    start, end = compare_gear.resolve_window(_args(days=30), SPAN)
    assert (end - start).days == 30


def test_explicit_start_and_end():
    got = compare_gear.resolve_window(_args(start="2024-01-01", end="2024-06-30"), SPAN)
    assert got == (date(2024, 1, 1), date(2024, 6, 30))


def test_start_after_end_is_rejected():
    with pytest.raises(SystemExit):
        compare_gear.resolve_window(_args(start="2024-06-30", end="2024-01-01"), None)
