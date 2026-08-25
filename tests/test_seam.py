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
from fleet_sensor_baseline.walk import WALK_FORMAT, sensor_names, sensor_paths

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
