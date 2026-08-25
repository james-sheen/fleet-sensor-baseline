"""The five acceptance scenarios, run through the CLI rather than the internals.

Each one is the smallest fleet that can exhibit the failure it names, and each
asserts the EXIT CODE and the NAMES -- not that something was printed. A test
that only checked for a non-zero exit would pass over a report that found the
wrong five units.

**S2 is the one to read first.** It demonstrates, on purpose, that this layer is
blind to an absence the whole cohort shares, and pairs that with the referee
finding the same absence against a manufacturer declaration. The pair of
assertions IS the precedence rule made executable, and it is why anti-goal 3
exists.
"""

from __future__ import annotations

import json

import pytest

from conftest import write_json
from fleet_sensor_baseline import cli
from fleet_sensor_baseline.exits import CLEAN, FINDINGS, INCOMPLETE
from fleet_sensor_baseline.formats import DOWNGRADE_NOTICE

COHORT = ["Fan_CPU_1", "Fan_CPU_2", "Inlet_Temp", "PSU1_Input_Power"]


def run(argv, capsys):
    code = cli.main(argv)
    return code, capsys.readouterr()


def build_baseline(fleet, tmp_path, capsys, **kwargs):
    store = fleet.commit()
    out = tmp_path / "baseline.json"
    code, _ = run(["baseline", "--store", str(store.root), "--model", "tray",
                   "--out", str(out), *_flags(kwargs)], capsys)
    assert code == CLEAN, "the cohort should derive cleanly"
    return out


def _flags(kwargs):
    flags = []
    for key, value in kwargs.items():
        flags += [f"--{key.replace('_', '-')}", str(value)]
    return flags


class TestS1TheFiveOfTwoThousand:
    """Five units in a cohort are missing one sensor. Name the five and it."""

    def test_the_five_units_and_the_sensor_are_named(self, fleet, tmp_path,
                                                     capsys):
        for index in range(25):
            names = list(COHORT)
            if index < 5:
                names.remove("Fan_CPU_1")
            fleet.add(f"h-{index:04d}", names, model="tray")
        # 20 of 25 have it: 0.8, below the 0.99 default, so the sensor would
        # never enter a baseline derived from THIS cohort. The baseline comes
        # from the healthy majority window, which is the real-world shape --
        # derive it before the five drift.
        baseline = build_baseline(fleet, tmp_path, capsys,
                                  present_threshold=0.8)

        code, captured = run(
            ["outliers", "--store", str(fleet.store.root),
             "--baseline", str(baseline)], capsys)

        assert code == FINDINGS, captured.out
        for index in range(5):
            assert f"h-{index:04d}" in captured.out
        assert "Fan_CPU_1" in captured.out
        assert "absent" in captured.out

    def test_the_derivation_line_and_the_notice_are_printed(self, cohort,
                                                            tmp_path, capsys):
        baseline = build_baseline(cohort, tmp_path, capsys)
        _, captured = run(["outliers", "--store", str(cohort.store.root),
                           "--baseline", str(baseline)], capsys)
        assert "derived from 25 unit(s)" in captured.out
        assert "presence threshold of 0.99" in captured.out
        assert DOWNGRADE_NOTICE in captured.out

    def test_the_clean_units_appear_too(self, fleet, tmp_path, capsys):
        """Silence cannot impersonate a pass. A report listing only outliers is
        indistinguishable from a report over a cohort nobody walked."""
        for index in range(25):
            names = list(COHORT)
            if index < 5:
                names.remove("Fan_CPU_1")
            fleet.add(f"h-{index:04d}", names, model="tray")
        baseline = build_baseline(fleet, tmp_path, capsys,
                                  present_threshold=0.8)
        _, captured = run(["outliers", "--store", str(fleet.store.root),
                           "--baseline", str(baseline)], capsys)
        assert "h-0024" in captured.out, (
            "a unit with nothing wrong must still appear; otherwise a run that "
            "walked five units and a run that walked two thousand look alike")


class TestS2CommonModeBlindness:
    """The founding problem of this family, at fleet scale, demonstrated."""

    def test_the_whole_cohort_missing_a_sensor_is_silent(self, fleet, tmp_path,
                                                         capsys):
        # Every unit lost Fan_CPU_1. They agree with each other perfectly.
        names = [n for n in COHORT if n != "Fan_CPU_1"]
        for index in range(25):
            fleet.add(f"h-{index:04d}", names, model="tray")
        baseline = build_baseline(fleet, tmp_path, capsys)

        code, captured = run(["outliers", "--store", str(fleet.store.root),
                              "--baseline", str(baseline)], capsys)

        assert code == CLEAN, (
            "consensus cannot see an absence the whole cohort shares, and this "
            "test exists to hold that fact still rather than to celebrate it")
        assert "Fan_CPU_1" not in captured.out

    def test_and_the_notice_says_so_in_the_same_breath(self, fleet, tmp_path,
                                                       capsys):
        """**The assertion that makes the silence honest.** A clean outlier
        report over a blind baseline, printed WITHOUT this sentence, is a
        machine-readable claim that a fleet is fine."""
        names = [n for n in COHORT if n != "Fan_CPU_1"]
        for index in range(25):
            fleet.add(f"h-{index:04d}", names, model="tray")
        baseline = build_baseline(fleet, tmp_path, capsys)
        code, captured = run(["outliers", "--store", str(fleet.store.root),
                              "--baseline", str(baseline)], capsys)
        assert code == CLEAN
        assert DOWNGRADE_NOTICE in captured.out

    @pytest.mark.seam
    def test_the_declaration_side_of_the_pair_finds_it(self, tmp_path):
        """The other half: a manufacturer declaration is not blind to this.

        Run against the referee, which is where declaration-precedence lives.
        Skipped in prose, and cleanly, where the referee is not installed --
        `could not check` is a different answer from `found nothing`.
        """
        subprocess = pytest.importorskip("subprocess")
        import shutil
        if shutil.which("bmc-sensor-audit") is None:
            pytest.skip("bmc-sensor-audit is not on PATH; the declaration half "
                        "of the precedence pair could not be run here")

        declaration = write_json(tmp_path / "declared.json", {
            "Exposes": [{"Name": n, "Type": "TMP75"} for n in COHORT]})
        walk_file = write_json(tmp_path / "walk.json", _walk_missing_one())
        result = subprocess.run(
            ["bmc-sensor-audit", "coverage", "--walk", str(walk_file),
             "--config", str(declaration)],
            capture_output=True, text=True)
        assert "Fan_CPU_1" in (result.stdout + result.stderr), (
            "the referee, judging against a declaration written by somebody "
            "who knew what the board has, must find what consensus cannot")


def _walk_missing_one():
    from conftest import walk
    return walk([n for n in COHORT if n != "Fan_CPU_1"])


class TestS3TheFortyFiveToFortyTwo:
    """One unit, one firmware step, three sensors gone."""

    def test_all_three_and_the_firmware_boundary_are_named(self, fleet, capsys):
        before = [f"Sensor_{i:02d}" for i in range(45)]
        after = [n for n in before if n not in
                 ("Sensor_11", "Sensor_22", "Sensor_33")]
        fleet.add("h-0042", before, captured_at="2026-08-01T00:00:00Z",
                  firmware="GB200-fw-1.4.2", release="1.4.2")
        fleet.add("h-0042", after, captured_at="2026-08-09T00:00:00Z",
                  firmware="GB200-fw-1.5.0", release="1.5.0")
        store = fleet.commit()

        code, captured = run(["drift", "--store", str(store.root),
                              "--unit", "h-0042"], capsys)

        assert code == FINDINGS
        for name in ("Sensor_11", "Sensor_22", "Sensor_33"):
            assert name in captured.out
        assert "GB200-fw-1.4.2 -> GB200-fw-1.5.0" in captured.out

    def test_a_unit_with_one_capture_cannot_be_judged(self, fleet, capsys):
        """*Nothing to compare* is not *nothing changed*."""
        fleet.add("h-0042", ["Fan_CPU_1"], captured_at="2026-08-01T00:00:00Z")
        store = fleet.commit()
        code, captured = run(["drift", "--store", str(store.root),
                              "--unit", "h-0042"], capsys)
        assert code == CLEAN
        assert "needs two to say anything" in captured.out

    def test_an_unwalkable_capture_in_the_history_is_incomplete(self, fleet,
                                                                capsys):
        fleet.add("h-0042", ["Fan_CPU_1"], captured_at="2026-08-01T00:00:00Z")
        fleet.add_failed("h-0042", captured_at="2026-08-05T00:00:00Z")
        fleet.add("h-0042", ["Fan_CPU_1"], captured_at="2026-08-09T00:00:00Z")
        store = fleet.commit()
        code, captured = run(["drift", "--store", str(store.root),
                              "--unit", "h-0042"], capsys)
        assert code == INCOMPLETE, (
            "a gap in the history is not a clean history; 2 beats whatever the "
            "readable pairs concluded")
        assert "could not be walked" in captured.out


class TestS4TheUnitsThatNeverReported:
    """An expectation list is what turns absence into a finding."""

    def _expectations(self, tmp_path, units):
        path = tmp_path / "expected.txt"
        path.write_text("\n".join(units) + "\n")
        return path

    def test_three_absent_units_are_named_and_the_run_is_incomplete(
            self, fleet, tmp_path, capsys):
        for index in range(5):
            fleet.add(f"h-{index:04d}", COHORT)
        store = fleet.commit()
        expected = self._expectations(
            tmp_path, [f"h-{i:04d}" for i in range(8)])

        code, captured = run(["verdict", "--store", str(store.root),
                              "--expect-units", str(expected)], capsys)

        assert code == INCOMPLETE
        assert "units that never reported: h-0005, h-0006, h-0007" in captured.out

    def test_optional_moves_one_without_touching_the_other_two(
            self, fleet, tmp_path, capsys):
        for index in range(5):
            fleet.add(f"h-{index:04d}", COHORT)
        store = fleet.commit()
        expected = self._expectations(
            tmp_path, [f"h-{i:04d}" for i in range(8)])

        code, captured = run(["verdict", "--store", str(store.root),
                              "--expect-units", str(expected),
                              "--optional-unit", "h-0005"], capsys)

        assert code == INCOMPLETE, "two units are still missing"
        assert "units that never reported: h-0006, h-0007" in captured.out
        assert "declared optional and did not report: h-0005" in captured.out

    def test_all_three_optional_makes_the_run_clean(self, fleet, tmp_path,
                                                    capsys):
        """Non-vacuity: the escape hatch has to actually work, or the test
        above is asserting something that could never have been otherwise."""
        for index in range(5):
            fleet.add(f"h-{index:04d}", COHORT)
        store = fleet.commit()
        expected = self._expectations(
            tmp_path, [f"h-{i:04d}" for i in range(8)])
        code, _ = run(["verdict", "--store", str(store.root),
                       "--expect-units", str(expected),
                       "--optional-unit", "h-0005",
                       "--optional-unit", "h-0006",
                       "--optional-unit", "h-0007"], capsys)
        assert code == CLEAN

    def test_a_unit_that_reported_a_failed_walk_is_not_missing(self, fleet,
                                                               tmp_path, capsys):
        """It reported. What it reported is that it could not be walked, and
        the two are different rows for a reason: one names a collector that
        died, the other a machine that did not answer."""
        fleet.add("h-0000", COHORT)
        fleet.add_failed("h-0001")
        store = fleet.commit()
        expected = self._expectations(tmp_path, ["h-0000", "h-0001"])
        code, captured = run(["verdict", "--store", str(store.root),
                              "--expect-units", str(expected)], capsys)
        assert code == INCOMPLETE
        assert "units that never reported" not in captured.out
        assert "the BMC did not answer" in captured.out


class TestS5ThePrefixChange:
    """An aggregation prefix moved across a firmware step."""

    def _shifted(self, fleet):
        plain = ["Fan_CPU_1", "Fan_CPU_2", "Inlet_Temp"]
        fleet.add("h-0042", plain, captured_at="2026-08-01T00:00:00Z",
                  firmware="fw-1", release="1.0")
        payload_names = [f"HMC0_{n}" for n in plain]
        fleet.add("h-0042", payload_names, captured_at="2026-08-09T00:00:00Z",
                  firmware="fw-2", release="2.0")
        return fleet.commit()

    def test_undeclared_the_shift_is_reported_and_still_counted(self, fleet,
                                                                capsys):
        """**Reported, never applied.** A rename this tool inferred is a rename
        nobody declared. Pairing through it silently is how a genuine
        mass-disappearance gets absorbed into a footnote."""
        store = self._shifted(fleet)
        code, captured = run(["drift", "--store", str(store.root),
                              "--unit", "h-0042"], capsys)
        assert code == FINDINGS
        assert "undeclared prefix" in captured.out
        assert "'HMC0_'" in captured.out

    #: The declaration an operator has to write, stem by stem, because the
    #: referee's dialect refuses an empty OLD. One entry would do it if a prefix
    #: being ADDED were expressible; see `docs/upstream-asks.md`.
    DECLARED = ("--aggregation-prefix", "Fan_CPU_=HMC0_Fan_CPU_",
                "--aggregation-prefix", "Inlet_=HMC0_Inlet_")

    def test_declared_it_pairs_with_a_note_and_finds_nothing(self, fleet,
                                                             capsys):
        store = self._shifted(fleet)
        code, captured = run(["drift", "--store", str(store.root),
                              "--unit", "h-0042", *self.DECLARED], capsys)
        assert code == CLEAN, captured.out
        assert "paired through a declared prefix" in captured.out

    def test_adding_a_prefix_cannot_be_declared_in_one_entry(self, fleet,
                                                             capsys):
        """**The limitation, pinned rather than described.**

        The referee allows `HMC0_=` (the prefix was dropped) and refuses
        `=HMC0_` (the prefix was added). Aggregation appearing where there was
        none is the common direction, and it is the one that cannot be said in a
        single declaration. This test fails the day that changes upstream, which
        is when the workaround above should be removed rather than kept out of
        habit.
        """
        store = self._shifted(fleet)
        code, captured = run(["drift", "--store", str(store.root),
                              "--unit", "h-0042",
                              "--aggregation-prefix", "=HMC0_"], capsys)
        assert code == INCOMPLETE
        assert "is not OLD=NEW" in captured.err

    def test_a_declared_prefix_does_not_hide_a_real_disappearance(self, fleet,
                                                                  capsys):
        """The guard on the guard. If declaring a rename could also absorb a
        sensor that genuinely vanished, the declaration would be a way to make
        findings disappear."""
        plain = ["Fan_CPU_1", "Fan_CPU_2", "Inlet_Temp"]
        fleet.add("h-0042", plain, captured_at="2026-08-01T00:00:00Z")
        fleet.add("h-0042", [f"HMC0_{n}" for n in plain if n != "Inlet_Temp"],
                  captured_at="2026-08-09T00:00:00Z")
        store = fleet.commit()
        code, captured = run(["drift", "--store", str(store.root),
                              "--unit", "h-0042", *self.DECLARED], capsys)
        assert code == FINDINGS
        assert "HMC0_Inlet_Temp" in captured.out
