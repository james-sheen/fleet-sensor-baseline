"""Derivation: the floor, the denominator, the scope, and the notice.

**The floor is the important one.** A baseline over nine units is an anecdote
wearing a format key: at that size one unlucky machine moves every ratio past
any threshold worth setting. The refusal says so, and lowering it takes an
explicit flag, so a nine-unit baseline is always somebody's decision on the
record rather than a default nobody read.
"""

from __future__ import annotations

import json

import pytest

from conftest import write_json
from fleet_sensor_baseline import cli
from fleet_sensor_baseline.baseline import (BaselineError, DEFAULT_FLOOR,
                                            derive, in_range, parse_range,
                                            select)
from fleet_sensor_baseline.exits import CLEAN, INCOMPLETE
from fleet_sensor_baseline.formats import DOWNGRADE_NOTICE, validate_baseline


def _presence(count, names):
    return {f"h-{i:04d}": set(names) for i in range(count)}


class TestTheFloor:
    def test_a_cohort_below_the_floor_is_refused(self):
        with pytest.raises(BaselineError) as caught:
            derive(_presence(9, ["Fan_1"]), {}, scope={})
        message = str(caught.value)
        assert "9 unit(s)" in message and str(DEFAULT_FLOOR) in message
        assert "anecdote" in message

    def test_the_refusal_says_how_to_proceed_deliberately(self):
        with pytest.raises(BaselineError) as caught:
            derive(_presence(9, ["Fan_1"]), {}, scope={})
        assert "--floor" in str(caught.value)

    def test_an_explicit_floor_lets_it_through(self):
        artifact = derive(_presence(9, ["Fan_1"]), {}, scope={}, floor=5)
        assert artifact["derived"]["units"] == 9

    def test_an_empty_cohort_is_refused_differently(self):
        """Not a weak baseline. No measurement at all, and the message says
        which -- an operator reading *below the floor* would go looking for more
        machines when the scope matched none."""
        with pytest.raises(BaselineError) as caught:
            derive({}, {}, scope={})
        assert "no measurement" in str(caught.value)

    def test_the_cli_exits_two_below_the_floor(self, fleet, tmp_path, capsys):
        for index in range(3):
            fleet.add(f"h-{index:04d}", ["Fan_CPU_1"], model="tray")
        store = fleet.commit()
        code = cli.main(["baseline", "--store", str(store.root),
                         "--model", "tray", "--out", str(tmp_path / "b.json")])
        assert code == INCOMPLETE
        assert "anecdote" in capsys.readouterr().err


class TestTheDenominator:
    def test_a_unit_captured_many_times_counts_once(self, fleet, tmp_path,
                                                    capsys):
        """Otherwise a rack that reports hourly outvotes one that reports
        weekly, and the baseline describes the collection schedule."""
        for index in range(20):
            fleet.add(f"h-{index:04d}", ["Fan_CPU_1"], model="tray",
                      captured_at="2026-08-01T00:00:00Z")
        for day in range(2, 12):
            fleet.add("h-0000", ["Fan_CPU_1"], model="tray",
                      captured_at=f"2026-08-{day:02d}T00:00:00Z")
        store = fleet.commit()
        out = tmp_path / "b.json"
        assert cli.main(["baseline", "--store", str(store.root),
                         "--model", "tray", "--out", str(out)]) == CLEAN
        artifact = json.loads(out.read_text())
        assert artifact["derived"]["units"] == 20

    def test_the_ratio_is_units_with_over_units_total(self):
        present = _presence(100, ["Everywhere"])
        for index in range(4):
            present[f"h-{index:04d}"] = {"Everywhere", "Rare"}
        # `absent_threshold=0` because a present-threshold of 0.01 leaves no
        # room beneath the default one, and `derive` refuses that rather than
        # quietly producing a baseline with no divergent band. This test is
        # about ratio arithmetic, so it says so explicitly.
        artifact = derive(present, {}, scope={}, threshold=0.01,
                          absent_threshold=0.0)
        ratios = {s["name"]: s["present_ratio"] for s in artifact["sensors"]}
        assert ratios == {"Everywhere": 1.0, "Rare": 0.04}

    def test_a_sensor_below_the_threshold_is_not_in_the_baseline(self):
        present = _presence(100, ["Everywhere"])
        present["h-0000"] = {"Everywhere", "Rare"}
        artifact = derive(present, {}, scope={})
        assert [s["name"] for s in artifact["sensors"]] == ["Everywhere"]


class TestTheUriSuffix:
    def test_it_is_written_when_the_cohort_agrees(self):
        present = _presence(20, ["Fan_1"])
        paths = {unit: {"Fan_1": "/Sensors/Fan_1"} for unit in present}
        artifact = derive(present, paths, scope={})
        assert artifact["sensors"][0]["uri_suffix"] == "/Sensors/Fan_1"

    def test_it_is_omitted_when_the_cohort_disagrees(self):
        """A baseline asserting one URI while the cohort reports several is
        asserting something no unit said. Advisory metadata that is wrong is
        worse than absent -- matching is by name either way."""
        present = _presence(20, ["Fan_1"])
        paths = {unit: {"Fan_1": f"/Chassis/{i}/Sensors/Fan_1"}
                 for i, unit in enumerate(present)}
        artifact = derive(present, paths, scope={})
        assert "uri_suffix" not in artifact["sensors"][0]


class TestTheScopeIsDeclaredNeverInferred:
    @pytest.mark.parametrize("text,release,expected", [
        (">=1.4,<1.5", "1.4.2", True),
        (">=1.4,<1.5", "1.5.0", False),
        (">=1.4,<1.5", "1.3.9", False),
        (">=1.4", "1.4", True),
        ("==1.4.2", "1.4.2", True),
        ("==1.4.2", "1.4.20", False),
    ])
    def test_ranges_compare_as_written(self, text, release, expected):
        assert in_range(release, parse_range(text)) is expected

    def test_a_short_bound_compares_against_a_long_release(self):
        """`>=1.4` must accept `1.4.2`. Without padding, the answer depends on
        how many components somebody typed."""
        assert in_range("1.4.2", parse_range(">=1.4"))

    def test_a_range_with_no_comparison_is_refused(self):
        with pytest.raises(BaselineError):
            parse_range("1.4")

    def test_a_record_with_no_release_is_excluded_and_named(self):
        """**Not counted as absent, and not silently dropped.** A unit excluded
        for being unjudgeable is not a unit that failed, and a denominator that
        quietly shrank is the defect this whole family is pointed at."""
        records = [{"unit_key": "h-0000", "firmware": {"version": "fw-1.4.2"}}]
        selection = select(records, firmware_range=">=1.4,<1.5")
        assert selection.records == []
        assert selection.excluded[0][0] == "h-0000"
        assert "not a version" in selection.excluded[0][1]

    def test_a_vendor_string_is_never_parsed_for_a_version(self):
        """The rule that keeps a hardcoded assumption about one vendor's
        formatting out of a tool that claims not to have any."""
        records = [{"unit_key": "h", "firmware": {"version": "GB200-fw-1.4.2"}}]
        assert select(records, firmware_range=">=1.4,<1.5").records == []

    def test_a_declared_release_is_used(self):
        records = [{"unit_key": "h", "firmware": {"version": "GB200-fw-1.4.2",
                                                  "release": "1.4.2"}}]
        assert len(select(records, firmware_range=">=1.4,<1.5").records) == 1

    def test_the_model_is_matched_exactly(self):
        records = [{"unit_key": "a", "model": "tray"},
                   {"unit_key": "b", "model": "tray-v2"}]
        assert [r["unit_key"] for r in select(records, model="tray").records] == ["a"]


class TestTheNoticeIsPartOfTheFormat:
    def test_a_derived_baseline_always_carries_it(self):
        artifact = derive(_presence(20, ["Fan_1"]), {}, scope={})
        assert artifact["notice"] == DOWNGRADE_NOTICE
        assert validate_baseline(artifact) == []

    def test_the_derivation_line_carries_the_denominator(self, cohort,
                                                         tmp_path, capsys):
        out = tmp_path / "b.json"
        cli.main(["baseline", "--store", str(cohort.commit().root),
                  "--model", "tray", "--out", str(out)])
        printed = capsys.readouterr().out
        assert "derived from 25 unit(s)" in printed
        assert "captured between" in printed

    def test_the_window_is_recorded(self, fleet, tmp_path):
        for index in range(20):
            fleet.add(f"h-{index:04d}", ["Fan_CPU_1"], model="tray",
                      captured_at=f"2026-08-{(index % 9) + 1:02d}T00:00:00Z")
        out = tmp_path / "b.json"
        cli.main(["baseline", "--store", str(fleet.commit().root),
                  "--model", "tray", "--out", str(out)])
        window = json.loads(out.read_text())["derived"]["captured_between"]
        assert window == ["2026-08-01T00:00:00Z", "2026-08-09T00:00:00Z"]


class TestAnUnreadableUnitDoesNotVanish:
    def test_a_failed_walk_makes_the_derivation_incomplete(self, fleet,
                                                           tmp_path, capsys):
        for index in range(20):
            fleet.add(f"h-{index:04d}", ["Fan_CPU_1"], model="tray")
        fleet.add_failed("h-9999", model="tray")
        code = cli.main(["baseline", "--store", str(fleet.commit().root),
                         "--model", "tray", "--out", str(tmp_path / "b.json")])
        assert code == INCOMPLETE
        assert "excluded h-9999" in capsys.readouterr().out
