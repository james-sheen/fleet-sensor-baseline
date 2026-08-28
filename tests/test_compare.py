"""`compare` -- selection and handover, and the one refusal that is not hygiene.

The referee already judges a pair of walks and this store already keeps whole
`walk/1` payloads. What was missing was deciding WHICH two answer the question.
These tests are mostly about that decision, because the judging is not this
package's to test -- `tests/test_seam.py` is where the handover meets the real
program.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from conftest import record, walk
from fleet_sensor_baseline import cli
from fleet_sensor_baseline.compare import (CompareError, compare_unit, judge,
                                           pair_for)
from fleet_sensor_baseline.exits import CLEAN, FINDINGS, INCOMPLETE
from fleet_sensor_baseline.store import Store

EARLY, LATE = "2026-08-01T00:00:00Z", "2026-08-20T00:00:00Z"


def _walk_with(upper, *, captured_at):
    payload = walk(["INLET_TEMP", "FAN_1"], captured_at=captured_at)
    payload["fields_observed"] = True
    payload["sensors"][0]["thresholds"] = {"upper/critical": upper}
    return payload


@pytest.fixture
def store(tmp_path):
    """Two captures of one unit, with a threshold edited between them."""
    st = Store(tmp_path / "store")
    st.initialise()
    rows = []
    for when, upper in ((EARLY, 95.0), (LATE, 105.0)):
        raw = json.dumps(_walk_with(upper, captured_at=when),
                         sort_keys=True).encode("utf-8")
        rows.append(record("tray-01", captured_at=when,
                           digest=st.put_payload(raw)))
    st.append(rows)
    return st


def _fake(code, *, stdout="", stderr=""):
    def runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, code, stdout, stderr)
    return runner


class TestTheMembershipRefusalIsNotHygiene:
    """**The rule this command would be wrong without.**

    A skip record copies the PREVIOUS capture's `payload_digest` forward -- it
    reused those bytes because the BMC said the sensor set was unchanged. So
    comparing a walk against a skip resolves both sides to the same CAS object.
    The referee is then handed two identical files and correctly answers `0`.

    Clean, and a lie: the question was whether a threshold moved, and a
    collection ETag cannot see a threshold move on a sensor that stayed
    present. The record says `proves: membership` for exactly this reason.
    """

    @staticmethod
    def _with_skip(store):
        """A third record, filed from a skip, reusing the LATE payload."""
        late = [r for r in store.latest() if r["captured_at"] == LATE][0]
        skip = record("tray-01", captured_at="2026-08-25T00:00:00Z",
                      digest=late["payload_digest"])
        skip["unchanged"] = {"basis": "collection-etag", "proves": "membership",
                             "reused_from": LATE}
        store.append([skip])
        return skip

    def test_a_skip_record_is_refused_as_an_input(self, store):
        self._with_skip(store)
        rows = compare_unit(store, "tray-01", before=EARLY,
                            after="2026-08-25T00:00:00Z",
                            runner=_fake(0))
        assert [r.exit_code for r in rows] == [INCOMPLETE]
        assert "proves membership" in rows[0].detail
        assert "threshold" in rows[0].detail

    def test_and_the_comparison_it_prevents_would_have_read_clean(self, store):
        """**Non-vacuity, and the whole argument.** Without the refusal the two
        ends resolve to one object, so the referee compares a file with itself
        and says `0`. This asserts the false clean is really there to prevent
        -- a refusal guarding nothing would pass the test above unchanged."""
        skip = self._with_skip(store)
        late = [r for r in store.latest() if r["captured_at"] == LATE][0]
        assert skip["payload_digest"] == late["payload_digest"], (
            "the skip record no longer reuses the earlier payload, so the "
            "false clean this refusal exists for has changed shape")
        assert store.payload(skip) == store.payload(late)

    def test_an_unchanged_block_this_build_cannot_read_is_also_refused(self, store):
        """A vocabulary that grew a member is where guessing picks the wrong
        branch. `proves: something-else` is not assumed to be weaker or
        stronger than membership; it is not compared."""
        late = [r for r in store.latest() if r["captured_at"] == LATE][0]
        odd = record("tray-01", captured_at="2026-08-26T00:00:00Z",
                     digest=late["payload_digest"])
        odd["unchanged"] = {"basis": "something", "proves": "readings"}
        store.append([odd])
        rows = compare_unit(store, "tray-01", before=EARLY,
                            after="2026-08-26T00:00:00Z", runner=_fake(0))
        assert rows[0].exit_code == INCOMPLETE
        assert "'readings'" in rows[0].detail


class TestWhichTwoRecordsAnswerTheQuestion:
    def test_the_newest_at_or_before_each_time(self, store):
        early, late = pair_for(store.latest(), before=EARLY, after=LATE)
        assert (early["captured_at"], late["captured_at"]) == (EARLY, LATE)

    def test_a_time_before_every_capture_is_refused(self, store):
        with pytest.raises(CompareError, match="no capture at or before"):
            pair_for(store.latest(), before="2025-01-01T00:00:00Z", after=LATE)

    def test_both_ends_resolving_to_one_capture_is_refused(self, store):
        """Clean by construction says nothing, so it is not said."""
        with pytest.raises(CompareError, match="compared with itself"):
            pair_for(store.latest(), before=LATE, after=LATE)

    def test_a_unit_the_store_does_not_hold_is_refused(self, store):
        with pytest.raises(CompareError, match="no record for tray-99"):
            compare_unit(store, "tray-99", before=EARLY, after=LATE)

    def test_pairing_is_surface_to_surface(self, tmp_path):
        """**A unit is the tuple.** One machine answering on a host BMC and an
        HMC is two surfaces; pairing by `unit_key` alone would compare one
        against the other and report every sensor as having vanished."""
        st = Store(tmp_path / "s")
        st.initialise()
        rows = []
        for satellite in ("host", "hmc"):
            for when in (EARLY, LATE):
                raw = json.dumps(_walk_with(95.0, captured_at=when),
                                 sort_keys=True).encode("utf-8")
                rows.append(record("tray-01", captured_at=when,
                                   digest=st.put_payload(raw),
                                   topology={"satellite": satellite}))
        st.append(rows)
        seen = compare_unit(st, "tray-01", before=EARLY, after=LATE,
                            runner=_fake(0))
        assert [r.surface for r in seen] == [
            ("tray-01", "satellite=hmc"), ("tray-01", "satellite=host")]


class TestWhatTheRefereeSaysIsWhatIsReported:
    def test_a_refusal_travels_in_the_referees_own_words(self, store):
        runner = _fake(2, stderr="--before was captured after --after, which is "
                                 "the wrong way round.")
        rows = compare_unit(store, "tray-01", before=EARLY, after=LATE,
                            runner=runner)
        assert rows[0].exit_code == INCOMPLETE
        assert "the wrong way round" in rows[0].detail

    def test_a_verdict_carries_no_detail_at_all(self, store):
        """The report is printed under its surface; a detail repeating the
        verdict word renders as `findings -- findings`."""
        rows = compare_unit(store, "tray-01", before=EARLY, after=LATE,
                            runner=_fake(1, stdout="Firmware regression"))
        assert rows[0].exit_code == FINDINGS
        assert "detail" not in rows[0].to_dict()

    def test_a_code_outside_the_vocabulary_is_two_and_keeps_the_original(self, store):
        rows = compare_unit(store, "tray-01", before=EARLY, after=LATE,
                            runner=_fake(127))
        assert rows[0].exit_code == INCOMPLETE
        assert rows[0].raw_exit_code == 127
        assert "127" in rows[0].detail

    def test_an_absent_referee_is_incomplete_and_says_which_command(self, store):
        def missing(argv, **kwargs):
            raise FileNotFoundError(argv[0])
        rows = compare_unit(store, "tray-01", before=EARLY, after=LATE,
                            runner=missing)
        assert rows[0].exit_code == INCOMPLETE
        assert "not on PATH" in rows[0].detail

    def test_the_flags_are_passed_through_and_nothing_is_inferred(self, store):
        seen = {}

        def spy(argv, **kwargs):
            seen["argv"] = argv
            return subprocess.CompletedProcess(argv, 0, "", "")
        compare_unit(store, "tray-01", before=EARLY, after=LATE, runner=spy,
                     strict_fields=True, prefixes=["OLD=NEW"])
        assert "--strict-fields" in seen["argv"]
        assert seen["argv"][seen["argv"].index("--aggregation-prefix") + 1] == "OLD=NEW"

    def test_no_prefix_is_sent_when_none_was_declared(self, store):
        seen = {}

        def spy(argv, **kwargs):
            seen["argv"] = argv
            return subprocess.CompletedProcess(argv, 0, "", "")
        compare_unit(store, "tray-01", before=EARLY, after=LATE, runner=spy)
        assert "--aggregation-prefix" not in seen["argv"]
        assert "--strict-fields" not in seen["argv"]


class TestTheCommandLine:
    def test_the_worst_surface_decides_the_exit_code(self, store, monkeypatch):
        monkeypatch.setattr("fleet_sensor_baseline.compare._run", _fake(1))
        assert cli.main(["compare", "--store", str(store.root),
                         "--unit", "tray-01", "--before", EARLY,
                         "--after", LATE]) == FINDINGS

    def test_a_clean_pair_exits_clean(self, store, monkeypatch):
        monkeypatch.setattr("fleet_sensor_baseline.compare._run", _fake(0))
        assert cli.main(["compare", "--store", str(store.root),
                         "--unit", "tray-01", "--before", EARLY,
                         "--after", LATE]) == CLEAN

    def test_an_unknown_unit_refuses_rather_than_reporting_nothing(
            self, store, capsys):
        assert cli.main(["compare", "--store", str(store.root),
                         "--unit", "tray-99", "--before", EARLY,
                         "--after", LATE]) == INCOMPLETE
        assert "no record for tray-99" in capsys.readouterr().err

    def test_the_json_is_a_summary_the_validator_accepts(self, store, tmp_path,
                                                         monkeypatch):
        """`--json` writes the same `summary/1` every other command emits, so
        `validate` can check it without a format of its own."""
        monkeypatch.setattr("fleet_sensor_baseline.compare._run", _fake(0))
        out = tmp_path / "summary.json"
        cli.main(["compare", "--store", str(store.root), "--unit", "tray-01",
                  "--before", EARLY, "--after", LATE, "--json", str(out)])
        assert cli.main(["validate", str(out)]) == CLEAN
