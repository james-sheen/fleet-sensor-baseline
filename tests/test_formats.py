"""Round-trips and refusals for every format this repository writes.

Three malformations per format, because the three fail differently: a **wrong
version** is a reader being handed a file from a future it cannot parse, a
**missing key** is a producer that half-wrote, and a **wrong type** is the one
that gets through a key-presence check and blows up in the consumer.

**Every validator is proved able to refuse.** A validator that has never said no
is not evidence, and one that returns `[]` because its own dispatch fell through
looks exactly like a clean file.
"""

from __future__ import annotations

import json

import pytest

from conftest import digest_of, walk, write_json
from fleet_sensor_baseline import cli
from fleet_sensor_baseline.exits import CLEAN, INCOMPLETE
from fleet_sensor_baseline.formats import (BASELINE_FORMAT, DOWNGRADE_NOTICE,
                                           PROVENANCE_DERIVED, RECORD_FORMAT,
                                           SUMMARY_FORMAT, TARGETS_FORMAT,
                                           validate_any, validate_baseline,
                                           validate_record, validate_summary,
                                           validate_targets)

GOOD_RECORD = {
    "format": RECORD_FORMAT,
    "unit_key": "h-0042",
    "topology": {"host": "h-0042", "satellite": "hmc-0"},
    "captured_at": "2026-08-22T04:10:00Z",
    "firmware": {"version": "GB200-fw-1.4.2", "source": "redfish:/UpdateService"},
    "trigger": "maintenance-event",
    "payload_digest": "sha256:" + "a" * 64,
    "walk_ref": "cas/sha256/" + "a" * 64,
    "collector": {"id": "rack-17"},
}

GOOD_BASELINE = {
    "format": BASELINE_FORMAT,
    "scope": {"model": "GB200-NVL-tray", "firmware_range": ">=1.4,<1.5"},
    "derived": {"units": 1987, "present_threshold": 0.99,
                "captured_between": ["2026-08-01T00:00:00Z",
                                     "2026-08-21T00:00:00Z"]},
    "sensors": [{"name": "Fan_CPU_1", "uri_suffix": "/Sensors/Fan_CPU_1",
                 "present_ratio": 0.9975}],
    "provenance": PROVENANCE_DERIVED,
    "notice": DOWNGRADE_NOTICE,
}

GOOD_SUMMARY = {
    "format": SUMMARY_FORMAT, "exit_code": 0, "verdict": "clean",
    "decided_by": [], "rows": [], "missing": [], "skipped": [],
}

# **TEST-NET-1, and deliberately.** RFC 5737 reserves 192.0.2.0/24 for
# documentation, so this address cannot name a real internal network and the
# publication guard has nothing to find. An RFC1918 address here would be
# suppressed with a `hygiene: synthetic` marker instead -- which silences the
# check rather than removing the hazard. Do not "fix" this back to a 10.x.
GOOD_TARGETS = {
    "format": TARGETS_FORMAT,
    "targets": [{"unit_key": "h-0042", "base_url": "https://192.0.2.1",
                 "topology": {"satellite": "hmc-0"}}],
}

CASES = [
    ("record", GOOD_RECORD, validate_record),
    ("baseline", GOOD_BASELINE, validate_baseline),
    ("summary", GOOD_SUMMARY, validate_summary),
    ("targets", GOOD_TARGETS, validate_targets),
]


@pytest.mark.parametrize("name,good,check", CASES)
class TestTheGoodOnePasses:
    def test_it_validates(self, name, good, check):
        assert check(good) == [], f"the reference {name} does not validate"

    def test_it_survives_a_json_round_trip(self, name, good, check):
        assert check(json.loads(json.dumps(good))) == []

    def test_dispatch_finds_it_by_its_own_format_key(self, name, good, check):
        kind, problems = validate_any(good)
        assert kind == good["format"]
        assert problems == []


@pytest.mark.parametrize("name,good,check", CASES)
class TestTheThreeMalformations:
    def test_a_wrong_version_is_refused(self, name, good, check):
        bad = dict(good, format=good["format"][:-1] + "9")
        problems = check(bad)
        assert problems, f"a {name} declaring version 9 validated"
        assert "this build reads" in problems[0]

    def test_an_absent_format_key_is_refused(self, name, good, check):
        bad = {k: v for k, v in good.items() if k != "format"}
        assert check(bad), f"a {name} with no format key validated"
        assert validate_any(bad)[0] is None

    def test_a_non_object_is_refused(self, name, good, check):
        assert check(["not", "an", "object"])
        assert check("a string")


class TestRecordRefusals:
    def test_a_record_without_a_unit_key_is_refused(self):
        bad = {k: v for k, v in GOOD_RECORD.items() if k != "unit_key"}
        assert "unit_key" in validate_record(bad)[0]

    def test_a_record_without_a_capture_time_is_refused(self):
        bad = {k: v for k, v in GOOD_RECORD.items() if k != "captured_at"}
        assert any("captured_at" in p for p in validate_record(bad))

    def test_a_clean_record_must_carry_its_payload(self):
        """The half that would otherwise validate: exit 0 and no digest."""
        bad = {k: v for k, v in GOOD_RECORD.items()
               if k not in ("payload_digest", "walk_ref")}
        problems = validate_record(bad)
        assert any("payload_digest" in p for p in problems)

    def test_an_exit_two_record_needs_no_payload(self):
        """**And this is why the check above is conditional.** A unit that could
        not be walked reports AS could-not-walk; refusing it here would refuse
        the one record Sec. 6 exists to produce."""
        failed = {"format": RECORD_FORMAT, "unit_key": "h-0042",
                  "captured_at": "2026-08-22T04:10:00Z", "exit_code": 2,
                  "detail": "the BMC did not answer"}
        assert validate_record(failed) == []

    def test_an_exit_code_outside_the_vocabulary_is_refused(self):
        assert validate_record(dict(GOOD_RECORD, exit_code=127))

    def test_a_topology_value_that_is_not_a_name_is_refused(self):
        assert validate_record(dict(GOOD_RECORD, topology={"satellite": 3}))


class TestBaselineRefusals:
    def test_a_reworded_notice_is_refused(self):
        """**The one that would otherwise be invisible.** Every other field
        still reads correctly, and the artifact now validates as a manufacturer
        declaration everywhere downstream."""
        bad = dict(GOOD_BASELINE,
                   notice="Derived from the fleet. May be incomplete.")
        assert any("notice" in p for p in validate_baseline(bad))

    def test_a_missing_notice_is_refused(self):
        bad = {k: v for k, v in GOOD_BASELINE.items() if k != "notice"}
        assert any("notice" in p for p in validate_baseline(bad))

    def test_a_provenance_that_claims_a_declaration_is_refused(self):
        bad = dict(GOOD_BASELINE, provenance="entity-manager")
        assert any("provenance" in p for p in validate_baseline(bad))

    def test_a_baseline_with_no_denominator_is_refused(self):
        bad = dict(GOOD_BASELINE, derived={"present_threshold": 0.99})
        assert any("units" in p for p in validate_baseline(bad))

    def test_a_ratio_outside_zero_to_one_is_refused(self):
        bad = dict(GOOD_BASELINE, sensors=[
            {"name": "Fan_CPU_1", "present_ratio": 1.4}])
        assert any("outside [0, 1]" in p for p in validate_baseline(bad))

    def test_a_baseline_with_no_sensors_is_legal(self):
        """A cohort that reports none is a measurement, not a malformation."""
        assert validate_baseline(dict(GOOD_BASELINE, sensors=[])) == []


class TestTargetsRefusals:
    def test_a_password_in_the_file_is_refused_not_ignored(self):
        bad = dict(GOOD_TARGETS, targets=[
            dict(GOOD_TARGETS["targets"][0], password="hunter2")])
        problems = validate_targets(bad)
        assert any("password" in p for p in problems)
        assert any("password_env" in p for p in problems)

    def test_naming_an_environment_variable_is_accepted(self):
        good = dict(GOOD_TARGETS, targets=[
            dict(GOOD_TARGETS["targets"][0], password_env="BMC_PASS_RACK17")])
        assert validate_targets(good) == []

    def test_an_empty_target_list_is_refused(self):
        assert validate_targets(dict(GOOD_TARGETS, targets=[]))

    def test_two_targets_for_one_surface_are_refused(self):
        one = GOOD_TARGETS["targets"][0]
        assert validate_targets(dict(GOOD_TARGETS, targets=[one, dict(one)]))

    def test_the_same_unit_on_two_satellites_is_fine(self):
        """A unit is the tuple. Two surfaces of one machine are not a duplicate."""
        one = GOOD_TARGETS["targets"][0]
        two = dict(one, topology={"satellite": "hmc-1"})
        assert validate_targets(dict(GOOD_TARGETS, targets=[one, two])) == []


class TestTheValidateSubcommand:
    def test_it_names_the_format_it_found(self, tmp_path, capsys):
        path = write_json(tmp_path / "b.json", GOOD_BASELINE)
        assert cli.main(["validate", str(path)]) == CLEAN
        assert BASELINE_FORMAT in capsys.readouterr().out

    def test_a_malformed_artifact_exits_two(self, tmp_path, capsys):
        bad = {k: v for k, v in GOOD_BASELINE.items() if k != "notice"}
        path = write_json(tmp_path / "b.json", bad)
        assert cli.main(["validate", str(path)]) == INCOMPLETE

    def test_a_file_that_is_not_json_exits_two_and_says_which(self, tmp_path,
                                                              capsys):
        path = tmp_path / "b.json"
        path.write_text("{not json")
        assert cli.main(["validate", str(path)]) == INCOMPLETE
        assert str(path) in capsys.readouterr().out

    def test_a_walk_is_not_one_of_ours_and_says_so(self, tmp_path, capsys):
        """Dispatch on the declared key, never on the fields present. A walk
        has `format` and `sensors` and would pass a shape-guess."""
        path = write_json(tmp_path / "w.json", walk(["Fan_CPU_1"]))
        assert cli.main(["validate", str(path)]) == INCOMPLETE
        assert "this build reads" in capsys.readouterr().out
