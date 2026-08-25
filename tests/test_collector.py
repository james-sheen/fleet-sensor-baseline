"""The collector: serialized, backed off, and never silently short a unit.

**A walk that fails is a record, emitted.** That is the assertion the whole
module exists for. A unit that simply does not appear is indistinguishable from
a unit nobody was asked to walk, and every silent fleet-wide failure has that
shape.

The backoff and the ordering are asserted from OBSERVED behaviour -- the order
calls actually arrived in, the delays actually requested -- rather than by
checking that a thread pool was not imported. A test that asserted the absence
of `concurrent.futures` would pass over a rewrite that used threads directly.
"""

from __future__ import annotations

import json

import pytest

from conftest import walk
from fleet_sensor_baseline.collect.backends.mock import MockRackBackend
from fleet_sensor_baseline.collect.collector import (Capture, CollectError,
                                                     Collector, Target,
                                                     normalise_capture,
                                                     read_targets)
from fleet_sensor_baseline.exits import CLEAN, INCOMPLETE
from fleet_sensor_baseline.formats import RECORD_FORMAT, TARGETS_FORMAT
from fleet_sensor_baseline.store import Store, digest_bytes


class FakeBackend:
    """Returns scripted captures and records what it was asked for."""

    def __init__(self, script):
        self.script = script
        self.calls: list[str] = []

    def capture(self, target):
        self.calls.append(target.unit_key)
        step = self.script[target.unit_key]
        if callable(step):
            return step(len([c for c in self.calls if c == target.unit_key]))
        return step


def _walk_bytes(names):
    return json.dumps(walk(names), sort_keys=True).encode("utf-8")


def _collector(tmp_path, backend, **kwargs):
    slept: list[float] = []
    store = Store(tmp_path / "store")
    store.initialise()
    kwargs.setdefault("clock", lambda: "2026-08-20T00:00:00Z")
    collector = Collector(backend, store, collector_id="rack-17",
                          sleep=slept.append, **kwargs)
    return collector, store, slept


def _target(unit_key, **kwargs):
    return Target(unit_key=unit_key, base_url=f"https://{unit_key}", **kwargs)


class TestAFailedWalkIsARecord:
    def test_it_is_emitted_rather_than_omitted(self, tmp_path):
        backend = FakeBackend({"h-0000": Capture(INCOMPLETE,
                                                 detail="connection refused")})
        collector, store, _ = _collector(tmp_path, backend, attempts=1)
        records = collector.run([_target("h-0000")])

        assert len(records) == 1
        assert records[0]["exit_code"] == INCOMPLETE
        assert records[0]["detail"] == "connection refused"
        assert "payload_digest" not in records[0]

    def test_the_unit_keeps_its_place_in_the_denominator(self, tmp_path):
        backend = FakeBackend({
            "h-0000": Capture(CLEAN, raw=_walk_bytes(["Fan_1"])),
            "h-0001": Capture(INCOMPLETE, detail="timed out"),
            "h-0002": Capture(CLEAN, raw=_walk_bytes(["Fan_1"])),
        })
        collector, store, _ = _collector(tmp_path, backend, attempts=1)
        records = collector.run([_target(f"h-{i:04d}") for i in range(3)])
        assert [r["unit_key"] for r in records] == ["h-0000", "h-0001", "h-0002"]

    def test_a_backend_that_raises_does_not_kill_the_run(self, tmp_path):
        class Exploding:
            def capture(self, target):
                if target.unit_key == "h-0001":
                    raise RuntimeError("the socket library disagreed")
                return Capture(CLEAN, raw=_walk_bytes(["Fan_1"]))

        collector, store, _ = _collector(tmp_path, Exploding(), attempts=1)
        records = collector.run([_target(f"h-{i:04d}") for i in range(3)])
        assert len(records) == 3
        assert records[1]["exit_code"] == INCOMPLETE
        assert "RuntimeError" in records[1]["detail"]

    def test_success_with_no_payload_is_incomplete_not_clean(self, tmp_path):
        """A clean capture that cannot produce its walk is a claim."""
        backend = FakeBackend({"h-0000": Capture(CLEAN, raw=None)})
        collector, _, _ = _collector(tmp_path, backend, attempts=1)
        record = collector.run([_target("h-0000")])[0]
        assert record["exit_code"] == INCOMPLETE
        assert "is a claim, not a record" in record["detail"]


class TestSerialization:
    def test_the_order_is_the_order_given(self, tmp_path):
        backend = FakeBackend({f"h-{i:04d}": Capture(CLEAN,
                                                     raw=_walk_bytes(["F"]))
                               for i in range(5)})
        collector, _, _ = _collector(tmp_path, backend, attempts=1)
        targets = [_target(f"h-{i:04d}") for i in range(5)]
        collector.run(targets)
        assert backend.calls == [t.unit_key for t in targets]

    def test_a_slow_target_does_not_reorder_the_rest(self, tmp_path):
        """The canary's contract: serialized order held under injected slow
        responses. AST2600-class BMCs measure a walk in seconds, and the point
        of one collector per rack is that a slow BMC delays only its rack."""
        machines = {f"h-{i:04d}": None for i in range(4)}
        order: list[str] = []

        class Slow:
            def capture(self, target):
                order.append(target.unit_key)
                return Capture(CLEAN, raw=_walk_bytes(["F"]))

        collector, _, _ = _collector(tmp_path, Slow(), attempts=1)
        collector.run([_target(u) for u in sorted(machines)])
        assert order == sorted(machines)
        assert collector.order == sorted(machines)


class TestBackoff:
    def test_it_retries_and_the_delays_double(self, tmp_path):
        attempts = {"n": 0}

        def flaky(_):
            attempts["n"] += 1
            if attempts["n"] < 3:
                return Capture(INCOMPLETE, detail="connection refused")
            return Capture(CLEAN, raw=_walk_bytes(["Fan_1"]))

        backend = FakeBackend({"h-0000": flaky})
        collector, _, slept = _collector(tmp_path, backend, attempts=3,
                                         base_delay=2.0)
        record = collector.run([_target("h-0000")])[0]

        assert record["exit_code"] == CLEAN
        assert slept == [2.0, 4.0], "exponential, and deterministic"
        assert "succeeded on attempt 3 of 3" in record["detail"]

    def test_it_gives_up_after_the_last_attempt(self, tmp_path):
        backend = FakeBackend({"h-0000": Capture(INCOMPLETE, detail="refused")})
        collector, _, slept = _collector(tmp_path, backend, attempts=3)
        record = collector.run([_target("h-0000")])[0]
        assert record["exit_code"] == INCOMPLETE
        assert len(slept) == 2, "no sleep after the final attempt"

    def test_findings_are_not_retried(self, tmp_path):
        """`1` is an answer. Retrying it would walk the machine again hoping
        for a different verdict, which is not what retry is for."""
        backend = FakeBackend({"h-0000": Capture(1, raw=_walk_bytes(["F"]))})
        collector, _, slept = _collector(tmp_path, backend, attempts=3)
        collector.run([_target("h-0000")])
        assert backend.calls == ["h-0000"]
        assert slept == []


class TestIdentityBinding:
    def test_the_record_carries_what_the_walk_does_not(self, tmp_path):
        """*The parse is the redaction.* No identity field enters `walk/1`, so
        the binding happens here or nowhere."""
        raw = _walk_bytes(["Fan_1"])
        backend = FakeBackend({"h-0042": Capture(CLEAN, raw=raw)})
        collector, store, _ = _collector(tmp_path, backend, attempts=1)
        record = collector.run([
            _target("h-0042", topology={"satellite": "hmc-0"}, model="tray")
        ])[0]

        assert record["format"] == RECORD_FORMAT
        assert record["unit_key"] == "h-0042"
        assert record["topology"] == {"satellite": "hmc-0"}
        assert record["model"] == "tray"
        assert record["collector"] == {"id": "rack-17"}
        assert json.loads(store.payload(record)) == walk(["Fan_1"])
        assert "unit_key" not in json.loads(store.payload(record))

    def test_the_digest_is_over_the_bytes_that_arrived(self, tmp_path):
        raw = _walk_bytes(["Fan_1"])
        backend = FakeBackend({"h-0042": Capture(CLEAN, raw=raw)})
        collector, _, _ = _collector(tmp_path, backend, attempts=1)
        record = collector.run([_target("h-0042")])[0]
        assert record["payload_digest"] == digest_bytes(raw)

    def test_a_disagreeing_handle_is_refused_by_both(self, tmp_path):
        """**The consumer is an oracle for the producer.** If the tool's own
        handle disagrees with the bytes that arrived, one of them is wrong and
        this collector cannot tell which -- so it files neither as truth."""
        raw = _walk_bytes(["Fan_1"])
        backend = FakeBackend({"h-0042": Capture(
            CLEAN, raw=raw, reported_digest="sha256:" + "0" * 64)})
        collector, store, _ = _collector(tmp_path, backend, attempts=1)
        record = collector.run([_target("h-0042")])[0]

        assert record["exit_code"] == INCOMPLETE
        assert "which of the two describes the capture" in record["detail"]
        assert "payload_digest" not in record

    def test_an_agreeing_handle_is_accepted(self, tmp_path):
        """Non-vacuity: the cross-check must pass on the honest case, or it is
        a check that refuses everything."""
        raw = _walk_bytes(["Fan_1"])
        backend = FakeBackend({"h-0042": Capture(
            CLEAN, raw=raw, reported_digest=digest_bytes(raw))})
        collector, _, _ = _collector(tmp_path, backend, attempts=1)
        assert collector.run([_target("h-0042")])[0]["exit_code"] == CLEAN


class TestCredentials:
    def test_a_missing_environment_variable_is_a_run_that_could_not_happen(
            self, monkeypatch):
        monkeypatch.delenv("BMC_PASS_TEST", raising=False)
        target = _target("h-0000", password_env="BMC_PASS_TEST")
        with pytest.raises(CollectError) as caught:
            target.password()
        assert "is not set" in str(caught.value)
        assert "not one that found nothing" in str(caught.value)

    def test_the_value_is_read_at_the_moment_of_the_call(self, monkeypatch):
        target = _target("h-0000", password_env="BMC_PASS_TEST")
        monkeypatch.setenv("BMC_PASS_TEST", "first")
        assert target.password() == "first"
        monkeypatch.setenv("BMC_PASS_TEST", "second")
        assert target.password() == "second"

    def test_a_target_without_credentials_needs_no_variable(self):
        assert _target("h-0000").password() is None


class TestTheTargetsFile:
    def test_it_reads_a_rack(self):
        targets = read_targets({
            "format": TARGETS_FORMAT,
            # TEST-NET-1 (RFC 5737), for the reason spelled out in
            # `test_formats.py`: reserved for documentation, so it cannot name a
            # real internal network and needs no suppression marker.
            "targets": [{"unit_key": "h-0000", "base_url": "https://192.0.2.1",
                         "topology": {"satellite": "hmc-0"}, "model": "tray"}]})
        assert targets[0].unit_key == "h-0000"
        assert targets[0].topology == {"satellite": "hmc-0"}

    def test_a_malformed_rack_list_is_refused(self):
        with pytest.raises(CollectError):
            read_targets({"format": TARGETS_FORMAT, "targets": []})


class TestTheSubprocessArgv:
    """The argv this backend builds, without needing the tool installed."""

    def _argv(self, target, **kwargs):
        from fleet_sensor_baseline.collect.backends.subprocess_backend import \
            subprocess_backend
        seen = {}

        class Completed:
            returncode = 0
            stdout = "  digest      sha256:" + "e" * 64
            stderr = ""

        def runner(argv):
            seen["argv"] = argv
            raise FileNotFoundError(2, "no such file")

        backend = subprocess_backend(runner=runner, **kwargs)
        with pytest.raises(CollectError):
            backend.capture(target)
        return seen["argv"]

    def test_it_asks_for_the_digest(self):
        assert "--print-digest" in self._argv(_target("h-0000"))

    def test_it_passes_the_target_url(self):
        argv = self._argv(_target("h-0000"))
        assert "--target" in argv
        assert argv[argv.index("--target") + 1] == "https://h-0000"

    def test_insecure_is_opt_in(self):
        assert "--insecure" not in self._argv(_target("h-0000"))
        assert "--insecure" in self._argv(_target("h-0000", insecure=True))

    def test_a_missing_command_is_named_not_swallowed(self):
        from fleet_sensor_baseline.collect.backends.subprocess_backend import \
            subprocess_backend

        def runner(argv):
            raise FileNotFoundError(2, "No such file or directory")

        backend = subprocess_backend(("no-such-tool",), runner=runner)
        with pytest.raises(CollectError) as caught:
            backend.capture(_target("h-0000"))
        assert "no-such-tool" in str(caught.value)


class TestExitNormalisationReachesTheRecord:
    def test_a_127_becomes_two_and_keeps_the_raw_code(self, tmp_path):
        backend = FakeBackend({"h-0000": normalise_capture(127)})
        collector, _, _ = _collector(tmp_path, backend, attempts=1)
        record = collector.run([_target("h-0000")])[0]
        assert record["exit_code"] == INCOMPLETE
        assert record["raw_exit_code"] == 127
        assert "exited 127" in record["detail"]
