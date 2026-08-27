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
from fleet_sensor_baseline.for_referee import REFEREE_BASELINE_FORMAT
from fleet_sensor_baseline import cli
from fleet_sensor_baseline.exits import CLEAN, INCOMPLETE
from fleet_sensor_baseline import formats
from fleet_sensor_baseline.formats import (BASELINE_FORMAT, DOWNGRADE_NOTICE,
                                           FORMATS, TARGETS_V2_FORMAT,
                                           PROVENANCE_DERIVED, RECORD_FORMAT,
                                           SUMMARY_FORMAT, TARGETS_FORMAT,
                                           VALIDATORS, validate_any,
                                           validate_baseline,
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

GOOD_TARGETS_V2 = {
    "format": TARGETS_V2_FORMAT,
    "targets": [{"unit_key": "h-0042", "base_url": "https://192.0.2.1",
                 "pin_sha256": "AB" * 32,
                 "topology": {"satellite": "hmc-0"}}],
}

CASES = [
    ("record", GOOD_RECORD, validate_record),
    ("baseline", GOOD_BASELINE, validate_baseline),
    ("summary", GOOD_SUMMARY, validate_summary),
    ("targets", GOOD_TARGETS, validate_targets),
    ("targets/2", GOOD_TARGETS_V2, validate_targets),
]

#: Format keys with no entry in `CASES`, each with the reason WRITTEN DOWN.
#:
#: An exemption that is merely absent is indistinguishable from a member
#: somebody forgot, which is the whole defect below. Naming it costs one line
#: and makes the omission a decision.
NO_REFERENCE_ARTIFACT = {
    formats.BASELINE_V1_FORMAT:
        "superseded and refused by design, so there is no good one to check",
}


class TestEveryDeclaredFormatIsDispatchable:
    """The format set, written four times, and two of the copies had drifted.

    `FORMATS` says what this build reads. `VALIDATORS` says what `validate`
    can dispatch. `targets/2` was in the first and not the second, so
    `validate` refused every `targets/2` file -- with a message that listed
    `targets/2` among the formats it had just said it read. `validate_targets`
    handled both versions from the day `/2` landed: the function was complete
    and only the wiring was missing.

    **The format it locked out is the one that exists to carry `pin_sha256`.**
    So the file an operator most needs checked before a run -- the one
    declaring which certificate the collector must see -- was the one file
    `validate` would not check, and the `pin_sha256`-on-`http://` refusal
    inside `validate_targets` was unreachable from the command line entirely.

    Nothing went red because every `targets/2` test called the validator
    **directly**. `TestTheGoodOnePasses` does go through the dispatcher, but it
    runs over `CASES`, a third hand-written copy of the same set, missing the
    same member. A check whose population is transcribed is blind to precisely
    what the transcription forgot. These derive the population instead.
    """

    def test_every_format_this_build_reads_has_a_validator(self):
        missing = [f for f in FORMATS if f not in VALIDATORS]
        assert not missing, (
            f"{missing} are in FORMATS and not in VALIDATORS, so `validate` "
            f"refuses them while naming them in the list of formats it reads")

    def test_no_validator_is_registered_for_a_format_that_is_not_declared(self):
        """The other direction. A validator reachable for a key `FORMATS` does
        not list is a format this build reads and does not admit to reading."""
        extra = [f for f in VALIDATORS if f not in FORMATS]
        assert not extra, f"{extra} dispatch and are not declared in FORMATS"

    @pytest.mark.parametrize("declared", FORMATS)
    def test_the_dispatcher_recognises_it_by_name(self, declared):
        """Recognition, not acceptance. An empty artifact is refused by every
        validator here; what is asserted is that the dispatcher NAMED the
        format rather than falling through to `this build reads ...`."""
        kind, problems = validate_any({"format": declared})
        assert kind == declared, (
            f"validate_any fell through on {declared!r}, which FORMATS "
            f"declares this build reads")
        assert problems, (
            f"an artifact carrying nothing but a format key validated as "
            f"{declared!r}")

    def test_an_undeclared_format_still_falls_through(self):
        """Non-vacuity. If the dispatcher recognised everything the three
        assertions above would hold for a table that had been deleted."""
        kind, problems = validate_any({"format": "fleet-sensor-baseline/nope/1"})
        assert kind is None and problems

    @pytest.mark.parametrize("declared", FORMATS)
    def test_it_has_a_reference_artifact_or_a_written_reason(self, declared):
        """`CASES` is the fourth copy. This is what keeps it from drifting too:
        a new format arrives with a good artifact, or with a sentence saying
        why there cannot be one."""
        covered = {good["format"] for _, good, _ in CASES}
        assert declared in covered or declared in NO_REFERENCE_ARTIFACT, (
            f"{declared!r} is in FORMATS with no entry in CASES and no reason "
            f"in NO_REFERENCE_ARTIFACT, so no malformation battery runs "
            f"against it")


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


class TestTargetsVersionTwo:
    """Version 2 exists for exactly one reason: a silent downgrade.

    `pin_sha256` says *require exactly this certificate*. A reader that predates
    the key would drop it and connect unpinned — an operator's security
    expectation met with silence. An older build refuses an unknown FORMAT
    outright, so bumping is what turns that downgrade into a refusal.

    `targets/1` stays valid and stays right for a rack list that pins nothing.
    """

    PIN = "AB" * 32
    BASE = {"unit_key": "h-0042", "base_url": "https://192.0.2.1"}

    def _file(self, version, **extra):
        return {"format": version, "targets": [dict(self.BASE, **extra)]}

    def test_version_one_without_a_pin_is_still_valid(self):
        """Non-vacuity. If v1 had been retired, every rule below would be about
        a format nobody can write."""
        assert validate_targets(self._file(TARGETS_FORMAT)) == []

    def test_version_two_without_a_pin_is_valid_too(self):
        assert validate_targets(self._file(TARGETS_V2_FORMAT)) == []

    def test_a_pin_in_version_one_is_REFUSED(self):
        """The whole point. Accepting it would let an older build read the same
        file and connect unverified."""
        problems = validate_targets(self._file(TARGETS_FORMAT, pin_sha256=self.PIN))
        assert problems
        assert "would ignore it and connect unpinned" in problems[0]
        assert TARGETS_V2_FORMAT in problems[0], "the refusal must say what to write"

    def test_a_pin_in_version_two_is_accepted(self):
        assert validate_targets(self._file(TARGETS_V2_FORMAT,
                                           pin_sha256=self.PIN)) == []

    @pytest.mark.parametrize("spelling", [
        "AB" * 32,
        ("ab" * 32),
        ":".join("AB" for _ in range(32)),
    ])
    def test_the_spellings_openssl_prints_are_accepted(self, spelling):
        """`openssl x509 -fingerprint -sha256` prints colons and uppercase. An
        operator copies that string; re-typing it is how a pin goes subtly
        wrong."""
        assert validate_targets(self._file(TARGETS_V2_FORMAT,
                                           pin_sha256=spelling)) == []

    @pytest.mark.parametrize("bad", ["nope", "AB" * 31, "ZZ" * 32, ""])
    def test_a_pin_that_is_not_a_fingerprint_is_refused(self, bad):
        assert validate_targets(self._file(TARGETS_V2_FORMAT, pin_sha256=bad))

    def test_a_pin_and_insecure_together_are_refused(self):
        """Two answers to one question. A pin IS the verification and
        `insecure` removes it; whichever won would be a guess."""
        problems = validate_targets(
            self._file(TARGETS_V2_FORMAT, pin_sha256=self.PIN, insecure=True))
        assert any("insecure" in p for p in problems)

    def test_an_older_build_refuses_the_whole_file(self):
        """The property the bump buys, asserted rather than assumed: a reader
        that only knows v1 must refuse v2 outright rather than read it and drop
        the key it does not recognise."""
        problems = _base_only_v1(self._file(TARGETS_V2_FORMAT,
                                            pin_sha256=self.PIN))
        assert problems and "this build reads" in problems[0]


def _base_only_v1(payload):
    """What `validate_targets` did before version 2 existed."""
    if payload.get("format") != TARGETS_FORMAT:
        return [f"format is {payload.get('format')!r}, this build reads "
                f"{TARGETS_FORMAT!r}"]
    return []



class TestWalkRefIsDerivedNotSupplied:
    """It was required, redundant, and unchecked -- all three at once.

    `walk_ref` is `cas/sha256/<hex>`, a pure function of `payload_digest`. A
    producer therefore had to know the store's internal directory layout to file
    a record, which is the consumer friction that surfaced this: a record built
    from the documented example was refused for lacking it.

    And nothing compared the two. A record naming a real digest beside a
    `walk_ref` pointing at an object that did not exist was accepted and stored,
    exit 0 -- one fact written twice, in a shipped format, with no check that
    the copies agreed.
    """

    def _record(self, **over):
        digest = "sha256:" + "a" * 64
        record = {
            "format": RECORD_FORMAT, "unit_key": "h-1",
            "topology": {"host": "h-1"},
            "captured_at": "2026-08-25T09:00:00Z", "exit_code": 0,
            "payload_digest": digest,
        }
        record.update(over)
        return record

    def test_a_record_without_a_walk_ref_is_accepted(self):
        """The friction, removed. The digest is enough to find the object."""
        assert validate_record(self._record()) == []

    def test_a_matching_walk_ref_is_accepted(self):
        """Records already written keep working."""
        digest = "sha256:" + "a" * 64
        assert validate_record(
            self._record(walk_ref=formats.ref_for(digest))) == []

    def test_a_walk_ref_that_disagrees_is_refused(self):
        problems = validate_record(
            self._record(walk_ref="cas/sha256/" + "f" * 64))
        assert problems, "a ref naming a different object was accepted"
        assert "disagreeing with the digest" in " ".join(problems)

    def test_a_walk_ref_that_is_not_a_string_is_refused(self):
        assert validate_record(self._record(walk_ref=17)) != []

    def test_the_two_ref_for_helpers_agree(self):
        """`formats` cannot import `store` -- `store` imports `formats` -- so
        the function exists twice. This is the check that makes a second copy
        safe, and its absence is exactly how the two copies of `walk_ref` came
        to disagree."""
        from fleet_sensor_baseline import store as store_module
        for digest in ("sha256:" + "a" * 64, "sha256:" + "0123abcd" * 8):
            assert formats.ref_for(digest) == store_module.ref_for(digest)


class TestNoSurfaceNamesAStaleFormatVersion:
    """Help text and prose, compared against the constants they describe.

    `baseline` emitted `/2` from the release that added it, and the top-level
    `--help` went on offering to *derive a fleet-baseline/1* -- the first line
    a new reader meets, naming a format this build refuses. Two module
    docstrings and a validator docstring said the same. None of it was reached
    by a test, because a version inside a sentence is prose to everything that
    reads prose and a constant to nobody.

    The remedy is not a better sentence, it is one fewer copy: `short()`
    derives the display name, and these assert that nothing has gone back to
    spelling it by hand.
    """

    @staticmethod
    def _baseline_help():
        from fleet_sensor_baseline import cli
        group = cli.build_parser()._subparsers._group_actions[0]
        return next(action.help for action in group._get_subactions()
                    if action.dest == "baseline")

    def test_the_baseline_help_names_the_format_it_emits(self):
        assert formats.short(BASELINE_FORMAT) in self._baseline_help()

    def test_it_does_not_name_the_superseded_one(self):
        assert formats.short(formats.BASELINE_V1_FORMAT) not in \
            self._baseline_help(), (
            "the help offers to derive a format this build refuses to read")

    def test_short_is_derived_and_not_a_second_spelling(self):
        """Non-vacuity, and the reason the helper exists at all."""
        assert formats.short(BASELINE_FORMAT) == "fleet-baseline/2"
        assert formats.short(RECORD_FORMAT) == "fleet-record/1"
        assert all(formats.short(f) in f for f in FORMATS)

    @pytest.mark.parametrize("module", ["baseline", "formats", "cli",
                                        "for_referee"])
    def test_no_module_docstring_names_the_superseded_baseline_as_current(
            self, module):
        """A grep, but a narrow one: the superseded key may be NAMED -- three
        modules legitimately explain why it is refused -- so this looks only
        for the phrase that presents it as the thing being produced."""
        import importlib
        source = importlib.import_module(
            f"fleet_sensor_baseline.{module}").__doc__ or ""
        stale = formats.short(formats.BASELINE_V1_FORMAT)
        for verb in ("Deriving a", "derive a", "Build the", "Check one"):
            assert f"{verb} `{stale}`" not in source, (
                f"{module} still describes producing {stale}")


class TestThisDocumentNamesEveryFormat:
    """`docs/formats.md`, checked against the constants it documents.

    The README's format count is pinned by `test_readme_counts.py` and stayed
    right. This page had no tripwire and went stale in the same release: it
    opened *Four*, presented the superseded `fleet-baseline/1` as current, and
    never mentioned `targets/2` or the `divergent` band -- while the README,
    three links away, said six. **The unguarded copy is the one that drifts,
    and which copy is guarded is not a property of how important it is.**

    A document cannot be checked for being right. It can be checked for naming
    everything the code exports, which is the failure this one actually had.
    """

    @staticmethod
    def _doc():
        from pathlib import Path
        path = Path(__file__).resolve().parent.parent / "docs" / "formats.md"
        if not path.exists():          # pragma: no cover - sdist without docs
            pytest.skip(f"{path} is not present in this tree, so the document "
                        f"could not be checked. That is not a passing check")
        return path.read_text()

    @pytest.mark.parametrize("declared", FORMATS)
    def test_every_format_this_build_reads_has_a_section(self, declared):
        assert f"## `{declared}`" in self._doc(), (
            f"{declared} is in FORMATS and has no section in docs/formats.md")

    def test_the_format_this_build_writes_for_the_referee_has_one(self):
        """It is not in `FORMATS` -- nothing here reads it -- and a reader who
        meets `--for-referee` still arrives at this page looking for it."""
        assert f"## `{REFEREE_BASELINE_FORMAT}`" in self._doc()

    def test_the_stated_count_is_the_measured_one(self):
        """The opening word. It said *Four* through two releases that made it
        six, which is the whole reason this class exists."""
        words = {4: "Four", 5: "Five", 6: "Six", 7: "Seven", 8: "Eight"}
        expected = words[len(FORMATS)]
        assert self._doc().lstrip().startswith(f"# Formats\n\n{expected} "), (
            f"docs/formats.md does not open by naming {expected.lower()} "
            f"formats, and FORMATS has {len(FORMATS)}")

    def test_the_superseded_one_is_marked_superseded(self):
        """Named is not enough. A section that merely exists reads as current,
        which is exactly how `/1` went on looking like the thing to derive."""
        doc = self._doc()
        section = doc[doc.index(f"## `{formats.BASELINE_V1_FORMAT}`"):]
        section = section[:section.index("\n## ")]
        assert "Superseded" in section and "Refused" in section

    def test_the_divergent_key_is_documented(self):
        """The key the format was bumped FOR. A page describing `/2` without it
        describes `/1` under a new name."""
        assert "divergent" in self._doc()
