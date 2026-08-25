"""The seam with `bmc-sensor-audit`, asserted against the real tool.

**Everything else in this suite reads a walk this repository wrote from its own
understanding of the format.** That is fast, dependency-free, and can never
falsify a change in the thing it is imitating: a fixture generated from a
reading of a specification agrees with the specification by construction. This
file is where the fixture meets the producer.

Every check here **skips in prose and exits clean** when the referee is not
installed. *Could not check* is a different answer from *found nothing*, and a
suite that reported the two identically would let a missing dependency read as a
green seam.

The upstream surfaces relied on and the release they arrived in:

    validate-walk               0.1.1   (absent in 0.1.0)
    capture --print-digest      0.1.1   (absent in 0.1.0)
    walk/1 format string        0.1.0

which is why `pyproject.toml` pins `>=0.1.1` and not the `>=0.1.0` the
specification asked for.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest

from conftest import walk as fixture_walk
from fleet_sensor_baseline.store import digest_bytes
from fleet_sensor_baseline.walk import (WALK_FORMAT, WalkError, parse_prefix_map,
                                        sensor_names, sensor_paths)

#: Every test in this file needs the referee. The marker makes the count
#: derivable by collection rather than by running a lane and reading its skips:
#: `pytest -m seam --collect-only` answers the same on any machine.
pytestmark = pytest.mark.seam

TOOL = shutil.which("bmc-sensor-audit")
NEEDS_TOOL = pytest.mark.skipif(
    TOOL is None,
    reason="bmc-sensor-audit is not on PATH; the upstream seam could not be "
           "checked here. This is not a passing seam, it is an unchecked one")


def _import_referee():
    try:
        import bmc_sensor_audit  # noqa: F401
    except ImportError:
        pytest.skip("bmc-sensor-audit is not importable; the fixture could not "
                    "be compared against the real producer")


@pytest.fixture(scope="module")
def real_walk():
    """A walk produced by the referee's own reader against its own mock BMC.

    This is the independent oracle. A fixture written here could never falsify
    the format it imitates; a walk the publisher produced can.
    """
    _import_referee()
    from bmc_sensor_audit.inventory.redfish import RedfishClient, walk_chassis
    from bmc_sensor_audit.testing.mock_redfish import MockBMC, serve

    machine = MockBMC()
    for name in ("Fan_CPU_1", "Fan_CPU_2", "Inlet_Temp"):
        machine.add(name, upper_critical=95.0, upper_warning=85.0)
    with serve(machine) as base_url:
        return walk_chassis(RedfishClient(base_url)).to_dict()


class TestTheFormatStringIsUnchanged:
    """The canary's first contract: the published stability statement holds."""

    def test_the_referee_still_writes_the_string_this_build_reads(self):
        _import_referee()
        from bmc_sensor_audit.inventory.redfish import WALK_FORMAT as THEIRS
        assert THEIRS == WALK_FORMAT, (
            f"the referee now writes {THEIRS!r} and this build reads "
            f"{WALK_FORMAT!r}. Every walk in every store just became "
            f"unreadable, and nothing else here would have said so")

    def test_a_real_walk_declares_it(self, real_walk):
        assert real_walk["format"] == WALK_FORMAT


class TestTheFixtureAgreesWithTheProducer:
    """What `conftest.walk` claims about the format, checked against the real one."""

    def test_the_top_level_keys_are_a_superset_of_the_fixture(self, real_walk):
        mine = set(fixture_walk(["Fan_CPU_1"]))
        theirs = set(real_walk)
        assert mine <= theirs, (
            f"the fixture invents top-level keys the producer does not write: "
            f"{sorted(mine - theirs)}")

    def test_the_sensor_keys_agree(self, real_walk):
        mine = set(fixture_walk(["Fan_CPU_1"])["sensors"][0])
        theirs = set(real_walk["sensors"][0])
        assert mine <= theirs, (
            f"the fixture invents sensor keys the producer does not write: "
            f"{sorted(mine - theirs)}. Every presence test in this suite is "
            f"reading a shape that does not exist")

    def test_this_layer_can_read_a_real_walk(self, real_walk):
        assert sensor_names(real_walk) == {"Fan_CPU_1", "Fan_CPU_2",
                                           "Inlet_Temp"}
        assert set(sensor_paths(real_walk)) == sensor_names(real_walk)

    def test_the_real_walk_carries_no_identity(self, real_walk):
        """*The parse is the redaction.* If a `unit_key` ever appeared upstream,
        this layer's whole reason for holding identity would have moved."""
        text = json.dumps(real_walk)
        assert "unit_key" not in text
        assert "serial" not in text.lower()


@NEEDS_TOOL
class TestTheCommandSurface:
    """E1 and E2, exercised as a fleet would: through the command."""

    def _capture(self, tmp_path):
        _import_referee()
        from bmc_sensor_audit.testing.mock_redfish import MockBMC, serve

        machine = MockBMC()
        machine.add("Fan_CPU_1")
        out = tmp_path / "walk.json"
        with serve(machine) as base_url:
            result = subprocess.run(
                [TOOL, "capture", "--target", base_url, "--out", str(out),
                 "--print-digest"], capture_output=True, text=True)
        return result, out

    def test_capture_writes_a_walk_and_prints_a_handle(self, tmp_path):
        result, out = self._capture(tmp_path)
        assert result.returncode == 0, result.stderr
        assert out.is_file()
        assert "sha256:" in result.stdout

    def test_the_printed_handle_is_the_digest_of_the_file(self, tmp_path):
        """**The consumer is an oracle for the producer.** The collector binds
        identity to this handle; if it did not describe the bytes on disk, every
        record in the store would point at something else."""
        result, out = self._capture(tmp_path)
        printed = [w for w in result.stdout.split() if w.startswith("sha256:")]
        assert printed, result.stdout
        assert printed[0] == digest_bytes(out.read_bytes())

    def test_validate_walk_accepts_what_capture_writes(self, tmp_path):
        """The seam canary's contract, stated as an assertion.

        A validator that rejected its own producer's output would be one people
        learn to route around, and they would take the malformed cases with
        them.
        """
        _, out = self._capture(tmp_path)
        result = subprocess.run([TOOL, "validate-walk", str(out)],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_validate_walk_refuses_a_malformed_walk(self, tmp_path):
        """Non-vacuity. A validator that has never refused anything is not
        evidence that the file it accepted was well formed."""
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"format": WALK_FORMAT,
                                   "sensors": [{"reading": 1.0}]}))
        result = subprocess.run([TOOL, "validate-walk", str(bad)],
                                capture_output=True, text=True)
        assert result.returncode != 0

    def test_this_layer_reads_a_captured_walk_end_to_end(self, tmp_path):
        _, out = self._capture(tmp_path)
        payload = json.loads(out.read_text())
        assert sensor_names(payload) == {"Fan_CPU_1"}


@NEEDS_TOOL
class TestTheCollectorAgainstAMockRack:
    """0.3's exit criterion: a verdict from nothing but BMC endpoints."""

    def test_a_rack_walk_produces_records_and_a_fleet_verdict(self, tmp_path,
                                                              capsys):
        _import_referee()
        from bmc_sensor_audit.testing.mock_redfish import MockBMC

        from fleet_sensor_baseline import cli
        from fleet_sensor_baseline.collect.backends.mock import MockRackBackend
        from fleet_sensor_baseline.collect.collector import Collector, Target
        from fleet_sensor_baseline.exits import CLEAN, INCOMPLETE
        from fleet_sensor_baseline.store import Store

        machines = {}
        for index in range(3):
            machine = MockBMC()
            for name in ("Fan_CPU_1", "Fan_CPU_2", "Inlet_Temp"):
                machine.add(name)
            machines[f"h-{index:04d}"] = machine

        store = Store(tmp_path / "store")
        store.initialise()
        backend = MockRackBackend(
            machines, failures={"h-0003": (INCOMPLETE, "the BMC did not answer")})
        collector = Collector(backend, store, collector_id="rack-17",
                              attempts=1, sleep=lambda _: None,
                              clock=lambda: "2026-08-20T00:00:00Z")
        targets = [Target(unit_key=f"h-{i:04d}", base_url=f"https://h-{i}")
                   for i in range(4)]
        records = collector.run(targets)
        store.append(records)

        assert len(records) == 4
        assert [r["exit_code"] for r in records] == [CLEAN, CLEAN, CLEAN,
                                                     INCOMPLETE]

        expected = tmp_path / "expected.txt"
        expected.write_text("\n".join(t.unit_key for t in targets) + "\n")
        code = cli.main(["verdict", "--store", str(store.root),
                         "--expect-units", str(expected)])
        out = capsys.readouterr().out
        assert code == INCOMPLETE, out
        assert "the BMC did not answer" in out
        assert "units that never reported" not in out, (
            "h-0003 reported; what it reported is that it could not be walked")


@NEEDS_TOOL
def test_the_pin_floor_still_resolves():
    """The pin is a claim about every release in its range.

    Checked against what is INSTALLED rather than against the metadata, because
    the metadata is the claim and the installation is the fact.
    """
    from importlib import metadata
    installed = metadata.version("bmc-sensor-audit")
    parts = tuple(int(p) for p in installed.split(".")[:3] if p.isdigit())
    assert parts >= (0, 1, 1), (
        f"bmc-sensor-audit {installed} is installed and this build calls "
        f"validate-walk and capture --print-digest, neither of which exists "
        f"before 0.1.1")
    assert parts < (0, 2), f"bmc-sensor-audit {installed} is outside the pin"

@NEEDS_TOOL
class TestTheDialectsAgree:
    """The two `OLD=NEW` parsers, compared against each other.

    **This is the test that should have existed from the first commit.** There
    was one asserting that an added prefix could NOT be declared -- and it
    asserted it of THIS repository's parser, which is a mirror of upstream's and
    changes only when somebody changes it here. Its docstring claimed it would
    "fail the day it is lifted upstream". It could not: it never looked upstream.

    When `bmc-sensor-audit` 0.1.2 lifted the refusal, this repository's copy went
    on refusing and the whole suite stayed green, while the two dialects had
    silently parted -- exactly the divergence the mirroring exists to prevent.
    **A claim about another program has to be measured against that program.**
    """

    @staticmethod
    def _referee(entry):
        from bmc_sensor_audit.inventory.regression import parse_prefix_map
        try:
            return dict(parse_prefix_map([entry]))
        except ValueError:
            return None

    @staticmethod
    def _ours(entry):
        try:
            return parse_prefix_map([entry])
        except WalkError:
            return None

    @pytest.mark.parametrize("entry", [
        "HMC0_=GPU0_",   # a plain rename
        "HMC0_=",        # the prefix was dropped
        "=HMC0_",        # the prefix was ADDED -- lifted upstream in 0.1.2
        "=",             # declares nothing
        "nonsense",      # no separator
        "",              # empty
        "Fan_=HMC0_Fan_",
    ])
    def test_both_parsers_agree_on_every_spelling(self, entry):
        _import_referee()
        theirs, ours = self._referee(entry), self._ours(entry)
        assert (theirs is None) == (ours is None), (
            f"{entry!r}: the referee "
            f"{'refuses' if theirs is None else 'accepts'} it and this layer "
            f"{'refuses' if ours is None else 'accepts'} it. One declaration has "
            f"to mean the same thing in both tools, or an operator writes the "
            f"rename twice and the two copies drift")
        if theirs is not None:
            assert theirs == ours, f"{entry!r}: {theirs} vs {ours}"

    def test_the_comparison_can_fail(self):
        """Non-vacuity. If both parsers were replaced by ones that accepted
        everything, every case above would agree and prove nothing."""
        assert self._ours("=") is None, "this layer no longer refuses anything"
        assert self._ours("=HMC0_") is not None, "this layer refuses everything"

@NEEDS_TOOL
class TestTheEtagSkipIsDetectedAgainstTheRealTool:
    """The collector asking the real `capture` whether a walk is needed.

    **This is the test the feature actually rests on.** The referee announces a
    skip in PROSE -- it exits 0 like a walk and writes no file like a failure --
    so the backend tells them apart by matching a printed line. A line is a
    weaker contract than an exit code, and the only way to know it still holds
    is to run the real command and look.
    """

    def _rack(self, tmp_path, machine, unchanged_ok=True):
        from fleet_sensor_baseline.collect.backends.subprocess_backend import \
            subprocess_backend
        from fleet_sensor_baseline.collect.collector import Collector, Target
        from fleet_sensor_baseline.store import Store
        from bmc_sensor_audit.testing.mock_redfish import serve

        store = Store(tmp_path / "store")
        store.initialise()
        with serve(machine) as base_url:
            def run(when):
                collector = Collector(subprocess_backend(), store,
                                      collector_id="rack-17", attempts=1,
                                      sleep=lambda _: None, etag_cache=True,
                                      clock=lambda: when)
                records = collector.run([Target(unit_key="h-0042",
                                                base_url=base_url)])
                store.append(records)
                return records[0]
            first = run("2026-08-21T00:00:00Z")
            second = run("2026-08-22T00:00:00Z")
        return store, first, second

    def _machine(self, etags=True):
        _import_referee()
        from bmc_sensor_audit.testing.mock_redfish import MockBMC
        machine = MockBMC(etags=etags)
        for name in ("Fan_CPU_1", "Fan_CPU_2", "Inlet_Temp"):
            machine.add(name)
        return machine

    def test_the_first_walk_records_a_payload(self, tmp_path):
        store, first, _ = self._rack(tmp_path, self._machine())
        assert first["exit_code"] == 0
        assert "unchanged" not in first
        assert json.loads(store.payload(first))["sensors"]

    def test_the_second_run_is_skipped_and_reuses_it(self, tmp_path):
        """The whole loop, against the published tool: cache written, BMC asked,
        walk skipped, record filed pointing at the earlier capture."""
        store, first, second = self._rack(tmp_path, self._machine())
        assert second["exit_code"] == 0, second
        assert second["unchanged"]["proves"] == "membership"
        assert second["payload_digest"] == first["payload_digest"]
        assert second["unchanged"]["reused_from"] == first["captured_at"]

    def test_the_cache_file_is_the_referees_own_format(self, tmp_path):
        """Written by the referee, read by the referee. This layer only chooses
        WHERE it goes, and must not start parsing it."""
        from bmc_sensor_audit.inventory.redfish import ETAG_CACHE_FORMAT
        store, _, _ = self._rack(tmp_path, self._machine())
        caches = sorted((store.root / "etags").glob("*.json"))
        assert len(caches) == 1, caches
        assert json.loads(caches[0].read_text())["format"] == ETAG_CACHE_FORMAT

    def test_a_changed_set_is_walked_rather_than_skipped(self, tmp_path):
        """Non-vacuity, and the failure that would matter most: a sensor that
        vanished must not be skipped past."""
        from fleet_sensor_baseline.collect.backends.subprocess_backend import \
            subprocess_backend
        from fleet_sensor_baseline.collect.collector import Collector, Target
        from fleet_sensor_baseline.store import Store
        from bmc_sensor_audit.testing.mock_redfish import serve

        machine = self._machine()
        store = Store(tmp_path / "store")
        store.initialise()
        with serve(machine) as base_url:
            collector = Collector(subprocess_backend(), store,
                                  collector_id="r", attempts=1,
                                  sleep=lambda _: None, etag_cache=True,
                                  clock=lambda: "2026-08-21T00:00:00Z")
            store.append(collector.run([Target(unit_key="h", base_url=base_url)]))
        machine.remove("Fan_CPU_2")
        with serve(machine) as base_url:
            collector = Collector(subprocess_backend(), store,
                                  collector_id="r", attempts=1,
                                  sleep=lambda _: None, etag_cache=True,
                                  clock=lambda: "2026-08-22T00:00:00Z")
            second = collector.run([Target(unit_key="h", base_url=base_url)])[0]
        assert "unchanged" not in second, "a removed sensor was skipped past"
        assert len(json.loads(store.payload(second))["sensors"]) == 2

    def test_a_bmc_without_etags_is_walked_every_time(self, tmp_path):
        """*Cannot tell* must never be read as *unchanged*. A BMC that ignores
        the header would otherwise never be walked again."""
        _, first, second = self._rack(tmp_path, self._machine(etags=False))
        assert "unchanged" not in second

        # **And the digests DIFFER, which is the finding this assertion was
        # written wrongly to expect.** A `walk/1` carries per-fetch latencies, so
        # two walks of one unchanged machine never share a digest. The content
        # store therefore never collapses a homogeneous fleet -- a claim this
        # repository made in three places until it was measured.
        assert second["payload_digest"] != first["payload_digest"]

@NEEDS_TOOL
class TestTheOutcomeVocabularyAgrees:
    """The values this layer acts on, against the values the referee declares.

    **Written this way because of what happened with the prefix dialect.** That
    one was asserted from this side only -- a mirror of the referee's rule that
    could not notice the referee changing -- and when upstream lifted a refusal
    the two parted silently under a green suite. A claim about another program
    has to be measured against that program.

    So this reads `bmc_sensor_audit.cli.OUTCOMES` rather than restating it.
    """

    @staticmethod
    def _theirs():
        _import_referee()
        from bmc_sensor_audit import cli
        return cli

    def test_this_layer_knows_every_value_the_referee_declares(self):
        from fleet_sensor_baseline.collect.backends.subprocess_backend import \
            OUTCOMES
        theirs = set(self._theirs().OUTCOMES)
        assert theirs <= OUTCOMES, (
            f"the referee declares {sorted(theirs - OUTCOMES)} and this layer "
            f"does not act on it. An unknown outcome becomes exit 2 rather than "
            f"a guess, so nothing is silently wrong -- but a whole fleet stops "
            f"being collectable until this set is updated")

    def test_it_claims_no_value_the_referee_does_not(self):
        """The other direction. A value we act on and they never emit is dead
        code that reads as coverage."""
        from fleet_sensor_baseline.collect.backends.subprocess_backend import \
            OUTCOMES
        assert OUTCOMES <= set(self._theirs().OUTCOMES)

    def test_the_prefix_this_layer_matches_is_the_one_they_print(self):
        from fleet_sensor_baseline.collect.backends.subprocess_backend import \
            OUTCOME_LINE
        prefix = self._theirs().OUTCOME
        assert OUTCOME_LINE.match(f"{prefix}walked"), (
            f"the referee prints {prefix!r} and this layer's pattern does not "
            f"match it")

    def test_an_unknown_outcome_is_refused_rather_than_guessed(self, tmp_path):
        """Non-vacuity, and the branch that matters: a vocabulary that grows a
        member must not silently take the walked path."""
        from fleet_sensor_baseline.collect.backends.subprocess_backend import \
            subprocess_backend
        from fleet_sensor_baseline.collect.collector import Target

        class Completed:
            returncode = 0
            stdout = "OUTCOME reconsidered\n"
            stderr = ""

        backend = subprocess_backend(runner=lambda argv: Completed())
        capture = backend.capture(Target(unit_key="h", base_url="https://h"))
        assert capture.exit_code == 2
        assert "does not know how to act on" in capture.detail

@NEEDS_TOOL
class TestTheTlsFlagsExistOnTheReferee:
    """The last of the five asks, checked against the tool rather than assumed.

    This layer sends `--pin-sha256` and `--cafile`. Both were absent until
    `bmc-sensor-audit` 0.1.3 and were reported from here. **Asserting they exist
    from this side would be the prefix-dialect mistake again** -- so this asks
    the installed command.
    """

    def _capture_flags(self):
        result = subprocess.run([TOOL, "capture", "--help"],
                                capture_output=True, text=True)
        return result.stdout

    def test_the_referee_accepts_the_flags_this_layer_sends(self):
        helptext = self._capture_flags()
        for flag in ("--pin-sha256", "--cafile"):
            assert flag in helptext, (
                f"this layer sends {flag} and the installed referee does not "
                f"take it; the pin floor is lower than the surface being used")

    def test_a_pin_on_a_non_https_target_is_refused(self, tmp_path):
        """**The assertion that matters, and the one that found a defect.**

        This was written expecting a WRONG pin to fail the walk. It passed:
        urllib picks a handler by scheme, so the pinned HTTPS handler was never
        consulted for the mock's `http://` URL. The pin was built, dropped, and
        the walk succeeded unverified -- an operator who typed a fingerprint
        would have believed the connection was checked.

        Reported upstream and fixed in 0.1.4, which is why this repository's
        floor moved. **A security flag that is ignored is worse than one that
        does not exist.**

        The mock speaks HTTP, so this now asserts the refusal rather than a
        handshake -- and a real handshake still cannot be tested here, because
        that needs a certificate and its private key as a fixture.
        """
        _import_referee()
        from bmc_sensor_audit.testing.mock_redfish import serve

        with serve(self._machine()) as base_url:
            assert base_url.startswith("http://"), "the mock stopped being plain"
            result = subprocess.run(
                [TOOL, "capture", "--target", base_url,
                 "--out", str(tmp_path / "w.json"),
                 "--pin-sha256", "AB" * 32],
                capture_output=True, text=True)
        assert result.returncode != 0, (
            "a pin on a non-https target was accepted, so it was ignored")
        assert "not https" in (result.stdout + result.stderr)

    def _machine(self):
        _import_referee()
        from bmc_sensor_audit.testing.mock_redfish import MockBMC
        machine = MockBMC()
        machine.add("Fan_CPU_1")
        return machine

    def test_the_collector_sends_the_pin_it_was_given(self, tmp_path):
        """End to end through this layer's own backend, so the argv assembled
        here is the argv the referee sees."""
        from fleet_sensor_baseline.collect.backends.subprocess_backend import \
            subprocess_backend
        from fleet_sensor_baseline.collect.collector import Target
        from bmc_sensor_audit.testing.mock_redfish import serve

        with serve(self._machine()) as base_url:
            backend = subprocess_backend()
            capture = backend.capture(
                Target(unit_key="h", base_url=base_url, pin_sha256="AB" * 32))
        assert capture.exit_code == 2, (
            "a walk under a pin that could not be honoured came back clean")

