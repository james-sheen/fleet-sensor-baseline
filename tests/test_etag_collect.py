"""The collector asking whether a walk is needed, before doing one.

`bmc-sensor-audit` 0.1.2 gained `capture --etag-cache` because this repository
reported it missing. This is the consuming half.

**What it proves is MEMBERSHIP, and the record says so.** A Redfish collection's
representation is its member list, so its ETag moves when a sensor appears or
disappears. A threshold edited on a sensor that stayed present moves that
sensor's resource and not its collection. That is exactly sufficient for the
questions this layer asks -- `drift`, `outliers` and `verdict` all read the name
SET -- and it would be wrong for a configuration audit, so a record filed from a
skip carries an `unchanged` block naming the basis rather than looking like a
fresh walk.

**One cache per SURFACE.** A machine answering on a host BMC and an HMC has two
Redfish trees; one shared cache would have each walk invalidate the other's and
the feature would quietly do nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from conftest import walk
from fleet_sensor_baseline.collect.collector import Capture, Collector, Target
from fleet_sensor_baseline.exits import CLEAN, INCOMPLETE
from fleet_sensor_baseline.formats import validate_record
from fleet_sensor_baseline.store import Store, digest_bytes


def _bytes(names=("Fan_1", "Fan_2")):
    return json.dumps(walk(list(names)), sort_keys=True).encode("utf-8")


class Scripted:
    """A backend that answers *unchanged* when handed a cache, and walks when not."""

    def __init__(self, unchanged=False, raw=None):
        self.unchanged = unchanged
        self.raw = raw if raw is not None else _bytes()
        self.caches: list[str | None] = []

    def capture(self, target, etag_cache=None):
        self.caches.append(etag_cache)
        if self.unchanged and etag_cache is not None:
            return Capture(CLEAN, unchanged=True, detail="set unchanged")
        return Capture(CLEAN, raw=self.raw)


def _collector(tmp_path, backend, **kwargs):
    store = Store(tmp_path / "store")
    store.initialise()
    kwargs.setdefault("clock", lambda: "2026-08-21T00:00:00Z")
    return Collector(backend, store, collector_id="rack-17", attempts=1,
                     sleep=lambda _: None, **kwargs), store


def _target(unit="h-0042", **kw):
    return Target(unit_key=unit, base_url=f"https://{unit}", **kw)


class TestTheCachePathIsPerSurface:
    def test_two_satellites_of_one_unit_get_two_caches(self, tmp_path):
        """The failure this exists to prevent: one file, each walk invalidating
        the other's ETags, and the feature silently never firing."""
        backend = Scripted()
        collector, store = _collector(tmp_path, backend, etag_cache=True)
        collector.run([_target(topology={"satellite": "hmc-0"}),
                       _target(topology={"satellite": "hmc-1"})])
        assert len(set(backend.caches)) == 2, backend.caches

    def test_the_same_surface_gets_the_same_cache_across_runs(self, tmp_path):
        backend = Scripted()
        collector, store = _collector(tmp_path, backend, etag_cache=True)
        collector.run([_target()])
        again, _ = _collector(tmp_path, backend, etag_cache=True)
        again.store = store
        again.run([_target()])
        assert backend.caches[0] == backend.caches[1]

    def test_an_opaque_unit_key_still_yields_one_file(self, tmp_path):
        """`unit_key` is the operator's naming and may contain a path separator.
        A name that escaped the directory would be a collector writing outside
        its own store."""
        backend = Scripted()
        collector, store = _collector(tmp_path, backend, etag_cache=True)
        collector.run([_target("rack 1/slot 7/../..")])
        path = Path(backend.caches[0])
        assert path.parent == store.root / "etags", path

    def test_without_the_flag_no_cache_is_passed(self, tmp_path):
        """Non-vacuity, and the compatibility promise: opt-in means the default
        path is byte-for-byte the behaviour that shipped in 0.1.0."""
        backend = Scripted(unchanged=True)
        collector, _ = _collector(tmp_path, backend)
        record = collector.run([_target()])[0]
        assert backend.caches == [None]
        assert "unchanged" not in record


class TestASkipReusesThePreviousCapture:
    def _seed(self, tmp_path):
        """One real walk on disk, so there is something to reuse."""
        backend = Scripted()
        collector, store = _collector(tmp_path, backend, etag_cache=True)
        store.append(collector.run([_target()]))
        return store

    def test_the_record_points_at_the_earlier_payload(self, tmp_path):
        store = self._seed(tmp_path)
        first = store.latest()[0]

        backend = Scripted(unchanged=True)
        collector = Collector(backend, store, collector_id="rack-17", attempts=1,
                              sleep=lambda _: None, etag_cache=True,
                              clock=lambda: "2026-08-22T00:00:00Z")
        record = collector.run([_target()])[0]

        assert record["exit_code"] == CLEAN
        assert record["payload_digest"] == first["payload_digest"]
        assert record["walk_ref"] == first["walk_ref"]
        assert validate_record(record) == []

    def test_it_says_what_was_proven_and_from_when(self, tmp_path):
        """**The sentence that keeps a skip from reading as a walk.** Collection
        ETags answer membership. A reader has to be able to tell which question
        this record answers, and `basis` is how."""
        store = self._seed(tmp_path)
        backend = Scripted(unchanged=True)
        collector = Collector(backend, store, collector_id="rack-17", attempts=1,
                              sleep=lambda _: None, etag_cache=True,
                              clock=lambda: "2026-08-22T00:00:00Z")
        record = collector.run([_target()])[0]
        assert record["unchanged"] == {
            "basis": "collection-etag", "proves": "membership",
            "reused_from": "2026-08-21T00:00:00Z"}

    def test_the_reused_payload_is_readable_from_the_store(self, tmp_path):
        """The point of reusing a digest rather than inventing one: the walk
        behind it has to still be there, or every downstream read fails."""
        store = self._seed(tmp_path)
        backend = Scripted(unchanged=True)
        collector = Collector(backend, store, collector_id="rack-17", attempts=1,
                              sleep=lambda _: None, etag_cache=True,
                              clock=lambda: "2026-08-22T00:00:00Z")
        record = collector.run([_target()])[0]
        assert json.loads(store.payload(record))["sensors"]

    def test_a_skip_with_nothing_to_reuse_is_incomplete(self, tmp_path):
        """**A cache without a capture behind it.** Somebody deleted records, or
        pointed two stores at one cache. Inventing a clean record would assert a
        payload that does not exist."""
        backend = Scripted(unchanged=True)
        collector, store = _collector(tmp_path, backend, etag_cache=True)
        record = collector.run([_target()])[0]
        assert record["exit_code"] == INCOMPLETE
        assert "no earlier capture to reuse" in record["detail"]
        assert "payload_digest" not in record
        assert validate_record(record) == []

    def test_a_skip_on_one_surface_does_not_reuse_another_surface(self, tmp_path):
        """Pairing is surface to surface everywhere else in this layer; a reuse
        that crossed surfaces would file the host BMC's walk as the HMC's."""
        backend = Scripted()
        collector, store = _collector(tmp_path, backend, etag_cache=True)
        store.append(collector.run([_target(topology={"satellite": "hmc-0"})]))

        quiet = Scripted(unchanged=True)
        second = Collector(quiet, store, collector_id="rack-17", attempts=1,
                           sleep=lambda _: None, etag_cache=True,
                           clock=lambda: "2026-08-22T00:00:00Z")
        record = second.run([_target(topology={"satellite": "hmc-1"})])[0]
        assert record["exit_code"] == INCOMPLETE, record


class TestTheFormatHoldsTheClaimHonest:
    def test_an_unchanged_block_without_a_basis_is_refused(self):
        """A reused payload with no statement of what was checked is a capture
        claiming more than happened."""
        record = {
            "format": "fleet-sensor-baseline/fleet-record/1",
            "unit_key": "h-0042", "captured_at": "2026-08-22T00:00:00Z",
            "exit_code": 0, "payload_digest": "sha256:" + "a" * 64,
            "walk_ref": "cas/sha256/" + "a" * 64,
            "unchanged": {"reused_from": "2026-08-21T00:00:00Z"}}
        problems = validate_record(record)
        assert any("proves" in p for p in problems), problems

    def test_a_complete_unchanged_block_validates(self):
        record = {
            "format": "fleet-sensor-baseline/fleet-record/1",
            "unit_key": "h-0042", "captured_at": "2026-08-22T00:00:00Z",
            "exit_code": 0, "payload_digest": "sha256:" + "a" * 64,
            "walk_ref": "cas/sha256/" + "a" * 64,
            "unchanged": {"basis": "collection-etag", "proves": "membership"}}
        assert validate_record(record) == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
