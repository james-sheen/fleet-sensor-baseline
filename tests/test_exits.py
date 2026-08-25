"""The exit contract, including the codes that are not in it.

`127` is the case worth writing down: a gate that dies on *command not found*
exits `127`, and taking `max` over raw codes would return it. Still non-zero, so
a CI job still fails -- and the summary would claim a verdict vocabulary it does
not have, while throwing away the only half of the sentence anybody can act on.
"""

from __future__ import annotations

import pytest

from fleet_sensor_baseline.exits import (CLEAN, FINDINGS, INCOMPLETE, VERDICTS,
                                         normalise, worst)


class TestNormalisation:
    @pytest.mark.parametrize("code", [CLEAN, FINDINGS, INCOMPLETE])
    def test_the_vocabulary_passes_through_unchanged(self, code):
        assert normalise(code) == (code, None)

    @pytest.mark.parametrize("raw", [127, 3, -1, 255])
    def test_anything_else_becomes_two(self, raw):
        code, note = normalise(raw)
        assert code == INCOMPLETE
        assert note is not None

    def test_the_raw_code_survives_in_the_note(self):
        """*exited 127* is the useful half of that sentence."""
        _, note = normalise(127)
        assert "127" in note

    def test_a_boolean_is_not_an_exit_code(self):
        """`True` is an `int` in Python, and would normalise to 1 -- findings,
        from a tool that returned a flag."""
        code, note = normalise(True)
        assert code == INCOMPLETE
        assert note is not None


class TestPrecedence:
    def test_two_beats_one(self):
        """A run that found three outliers and could not reach a fourth unit
        has not found three outliers."""
        assert worst([FINDINGS, INCOMPLETE, CLEAN]) == INCOMPLETE

    def test_one_beats_zero(self):
        assert worst([CLEAN, FINDINGS]) == FINDINGS

    def test_all_clean_is_clean(self):
        assert worst([CLEAN, CLEAN]) == CLEAN

    def test_empty_is_clean_not_incomplete(self):
        """The caller that expected subjects turns an empty run into 2, by
        name, in its *units that never reported* row. Deciding it here would
        make every legitimately empty aggregation incomplete."""
        assert worst([]) == CLEAN


def test_every_code_has_a_word():
    assert set(VERDICTS) == {CLEAN, FINDINGS, INCOMPLETE}
    assert all(isinstance(v, str) and v for v in VERDICTS.values())
