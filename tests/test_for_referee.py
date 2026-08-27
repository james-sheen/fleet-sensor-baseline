"""The export that closes the seam, and the one thing it must not export.

These run without the referee installed and check the SHAPE of the conversion.
`tests/test_seam.py::TestTheExportedDeclarationIsWhatTheRefereeReads` is where
the same file meets the real reader, because a format key copied out of another
program's source is a claim about that program and this file cannot settle it.
"""

from __future__ import annotations

import json

import pytest

from conftest import write_json
from fleet_sensor_baseline import cli
from fleet_sensor_baseline.baseline import derive
from fleet_sensor_baseline.exits import CLEAN, INCOMPLETE
from fleet_sensor_baseline.for_referee import (DIVERGENT_KEY,
                                               REFEREE_BASELINE_FORMAT,
                                               RefereeExportError,
                                               declaration_from_baseline,
                                               export_preamble)
from fleet_sensor_baseline.formats import (BASELINE_V1_FORMAT, DOWNGRADE_NOTICE,
                                           PROVENANCE_DERIVED)

SCOPE = {"model": "GB200-NVL-tray", "firmware_range": ">=1.4,<1.5"}


def _cohort(units=24, missing_on=("tray-06", "tray-07"), sensor="Fan_CPU_2"):
    """22 of 24 units carrying `sensor` -- the case the README opens with."""
    names = {"Fan_CPU_1", sensor, "Inlet_Temp"}
    present, paths = {}, {}
    for index in range(1, units + 1):
        unit = f"tray-{index:02d}"
        have = set(names)
        if unit in missing_on:
            have.discard(sensor)
        present[unit] = have
        paths[unit] = {name: f"/redfish/v1/Chassis/1/Sensors/{name}"
                       for name in have}
    return derive(present, paths, scope=dict(SCOPE),
                  window=("2026-08-01T00:00:00Z", "2026-08-21T00:00:00Z"))


class TestTheDivergentBandDoesNotCross:
    """**The whole reason this conversion needs a rule at all.**

    A `fleet-baseline/2` has three states and the referee's format has two.
    Whatever the third becomes on the other side is a decision, and only one
    of the three available answers is safe.

    Declaring the divergent sensors would expect one of every unit while 8
    percent of the cohort does not have it, so each of those units collects a
    finding for a disagreement that is a fact about the cohort -- which is the
    0.1.x inversion, arrived at a second time through a different door.
    Dropping them silently would rebuild `/1`, whose defect was that the
    information needed to judge was discarded at derivation and the file did
    not say so.
    """

    def test_a_divergent_sensor_is_not_declared(self):
        baseline = _cohort()
        assert [s["name"] for s in baseline["divergent"]] == ["Fan_CPU_2"]
        declaration = declaration_from_baseline(baseline)
        assert "Fan_CPU_2" not in {s["name"] for s in declaration["sensors"]}

    def test_it_is_carried_in_the_file_rather_than_dropped(self):
        """Under a key the referee ignores by its own `/1` rule. The reviewer
        is the reader who needs it, and the reviewer reads the file."""
        declaration = declaration_from_baseline(_cohort())
        dropped = declaration[DIVERGENT_KEY]
        assert [entry["name"] for entry in dropped] == ["Fan_CPU_2"]
        assert dropped[0]["present_on"] == 22 and dropped[0]["of"] == 24

    def test_the_export_names_it_out_loud(self):
        """A count says something was lost. A name says whether it was the one
        the reader was looking for."""
        lines = export_preamble(declaration_from_baseline(_cohort()))
        assert any("Fan_CPU_2 (22 of 24)" in line for line in lines)

    def test_an_expected_sensor_still_crosses(self):
        """Non-vacuity. If the conversion dropped everything, every assertion
        above would hold and the export would be worthless."""
        declaration = declaration_from_baseline(_cohort())
        assert {"Fan_CPU_1", "Inlet_Temp"} <= {s["name"]
                                               for s in declaration["sensors"]}


class TestItIsACandidateAndNothingElse:
    def test_the_reviewed_marker_is_null(self):
        assert declaration_from_baseline(_cohort())["reviewed"] is None

    def test_the_preamble_says_what_is_missing_and_how_to_add_it(self):
        lines = " ".join(export_preamble(declaration_from_baseline(_cohort())))
        assert "asserts nothing yet" in lines
        assert '"reviewed"' in lines and '"by"' in lines and '"on"' in lines

    def test_no_flag_can_make_it_reviewed(self):
        """**The conversion is not the review.** There is deliberately no
        `--reviewed-by`: a marker this tool could write is a marker nobody put
        their name to, and the gate exists precisely to require that name."""
        parser = cli.build_parser()
        flags = {action.dest for action in parser._subparsers._group_actions[0]
                 .choices["baseline"]._actions}
        assert "reviewed_by" not in flags and "reviewed" not in flags


class TestWhatItRefusesToExport:
    def test_a_v1_baseline_is_refused(self):
        bad = dict(_cohort(), format=BASELINE_V1_FORMAT)
        with pytest.raises(RefereeExportError) as refusal:
            declaration_from_baseline(bad)
        assert BASELINE_V1_FORMAT in str(refusal.value)

    def test_a_cohort_with_no_model_is_refused(self):
        baseline = _cohort()
        baseline["scope"] = {}
        with pytest.raises(RefereeExportError) as refusal:
            declaration_from_baseline(baseline)
        assert "platform" in str(refusal.value)

    def test_a_platform_may_be_named_explicitly_instead(self):
        baseline = _cohort()
        baseline["scope"] = {}
        declaration = declaration_from_baseline(baseline, platform="named-here")
        assert declaration["platform"] == "named-here"

    def test_an_empty_declaration_is_refused_here_rather_than_downstream(self):
        """The referee refuses it too, and says so well. Refusing at the point
        of derivation names the cohort that produced it, which is the thing an
        operator can act on."""
        baseline = _cohort()
        baseline["sensors"] = []
        with pytest.raises(RefereeExportError) as refusal:
            declaration_from_baseline(baseline)
        assert "reads clean against every machine" in str(refusal.value)


class TestWhatTheDeclarationCarries:
    def test_the_format_is_the_referees_namespace_not_ours(self):
        declaration = declaration_from_baseline(_cohort())
        assert declaration["format"] == REFEREE_BASELINE_FORMAT
        assert declaration["format"].startswith("bmc-sensor-audit/")

    def test_derived_from_records_the_cohort(self):
        """Required of this format by the referee, and the reason it does not
        require `firmware` or `captured_at`: a fleet baseline spans firmware
        levels by construction, so naming one level would be picking a point
        out of a range and presenting it as the point this was taken at."""
        declaration = declaration_from_baseline(_cohort())
        assert "24 unit(s)" in declaration["derived_from"]
        assert ">=1.4,<1.5" in declaration["derived_from"]
        assert "firmware" not in declaration
        assert "captured_at" not in declaration

    def test_the_notice_travels_with_it(self):
        declaration = declaration_from_baseline(_cohort())
        assert declaration["notice"] == DOWNGRADE_NOTICE
        assert declaration["provenance"] == PROVENANCE_DERIVED

    def test_no_threshold_is_invented(self):
        """This layer audits presence. A bound written here would be a number
        no unit reported, arriving in the referee's vocabulary where it reads
        exactly like a manufacturer's."""
        for sensor in declaration_from_baseline(_cohort())["sensors"]:
            assert "thresholds" not in sensor


class TestTheCommandLine:
    def _store(self, cohort):
        return cohort

    def test_it_writes_both_files(self, tmp_path, cohort):
        store = cohort.commit()
        out, referee = tmp_path / "b.json", tmp_path / "d.json"
        code = cli.main(["baseline", "--store", str(store.root),
                         "--model", "tray", "--out", str(out),
                         "--for-referee", str(referee)])
        assert code == CLEAN
        assert json.loads(referee.read_text())["format"] == REFEREE_BASELINE_FORMAT

    def test_without_the_flag_no_second_file_appears(self, tmp_path, cohort):
        store = cohort.commit()
        out, referee = tmp_path / "b.json", tmp_path / "d.json"
        assert cli.main(["baseline", "--store", str(store.root),
                         "--model", "tray", "--out", str(out)]) == CLEAN
        assert not referee.exists()

    def test_a_refused_export_still_leaves_the_baseline(self, tmp_path, cohort,
                                                       capsys):
        """The derivation succeeded. Only the export failed, and an operator
        who has to run the whole cohort again to find that out has been told
        the wrong thing."""
        store = cohort.commit()
        out, referee = tmp_path / "b.json", tmp_path / "d.json"
        code = cli.main(["baseline", "--store", str(store.root),
                         "--out", str(out), "--for-referee", str(referee)])
        assert code == INCOMPLETE
        assert out.exists(), "the baseline was discarded because the export failed"
        assert not referee.exists()
        assert "platform" in capsys.readouterr().err

    def test_naming_a_platform_without_asking_for_the_export_is_refused(
            self, tmp_path, cohort, capsys):
        """**A flag that quietly does nothing.** The referee's own
        `--pin-sha256` was built, dropped and silently ignored on an `http://`
        target for four releases, and the fix that closed it is the reason
        every floor in this family moved to 0.2.0. Ignoring this one would be
        the same shape at a lower stake."""
        store = cohort.commit()
        code = cli.main(["baseline", "--store", str(store.root),
                         "--model", "tray", "--out", str(tmp_path / "b.json"),
                         "--for-referee-platform", "named-but-unused"])
        assert code == INCOMPLETE
        assert "--for-referee" in capsys.readouterr().err
