"""The store: append-only, latest-wins, digest-verified, duplicate-refusing.

The rules here are small and each one has a specific way of going wrong:

- **latest-wins is decided by POSITION**, not by `captured_at`. A correction
  restates the capture time it corrects, so ordering by timestamp would put the
  correction before the thing it corrects and the store would answer with the
  superseded record forever.
- **a digest mismatch is `2`, not `1`.** It is not a finding about a machine; it
  is this layer being unable to say what it stored.
- **a duplicate is refused and a correction is declared.** The difference is
  whether somebody wrote down that they meant it.
"""

from __future__ import annotations

import json

import pytest

from conftest import digest_of, record, walk, write_json
from fleet_sensor_baseline import cli
from fleet_sensor_baseline.exits import CLEAN, INCOMPLETE
from fleet_sensor_baseline.store import (Store, StoreError, digest_bytes,
                                         key_of, ref_for, surface_of)


class TestContentAddressing:
    def test_a_payload_round_trips_by_digest(self, tmp_path):
        store = Store(tmp_path)
        store.initialise()
        raw = json.dumps(walk(["Fan_CPU_1"]), sort_keys=True).encode()
        digest = store.put_payload(raw)
        assert store.cas_path(digest).read_bytes() == raw

    def test_two_identical_walks_store_one_object(self, tmp_path):
        """The property that makes a homogeneous fleet affordable."""
        store = Store(tmp_path)
        store.initialise()
        raw = json.dumps(walk(["Fan_CPU_1"]), sort_keys=True).encode()
        first = store.put_payload(raw)
        second = store.put_payload(raw)
        assert first == second
        assert len(list((tmp_path / "cas" / "sha256").iterdir())) == 1

    def test_the_digest_is_over_the_bytes_and_sha256sum_agrees(self, tmp_path):
        """Reproducible without this tool installed, which is the whole point
        of hashing the file rather than a re-serialisation of it."""
        import hashlib
        raw = b'{"format": "x"}'
        assert digest_bytes(raw) == "sha256:" + hashlib.sha256(raw).hexdigest()

    def test_the_ref_and_the_digest_name_the_same_object(self):
        digest = "sha256:" + "b" * 64
        assert ref_for(digest) == "cas/sha256/" + "b" * 64


class TestTheSurfaceIsTheIdentity:
    def test_two_satellites_are_two_surfaces_of_one_unit(self):
        host = record("h-0042", captured_at="t", topology={"satellite": "hmc-0"})
        hmc = record("h-0042", captured_at="t", topology={"satellite": "hmc-1"})
        assert surface_of(host) != surface_of(hmc)
        assert host["unit_key"] == hmc["unit_key"]

    def test_topology_key_order_does_not_change_the_surface(self):
        """Otherwise a collector that serialised its dict differently would
        file a second surface for one BMC and the unit would look duplicated."""
        one = record("h", captured_at="t", topology={"a": "1", "b": "2"})
        two = record("h", captured_at="t", topology={"b": "2", "a": "1"})
        assert surface_of(one) == surface_of(two)


class TestLatestWins:
    def _two_lines(self, tmp_path, first_detail, second_detail):
        store = Store(tmp_path)
        store.initialise()
        base = dict(captured_at="2026-08-20T00:00:00Z", exit_code=2)
        store.append([record("h-0042", detail=first_detail, **base),
                      record("h-0042", detail=second_detail, **base)])
        return store

    def test_the_later_line_wins(self, tmp_path):
        store = self._two_lines(tmp_path, "first answer", "corrected answer")
        latest = store.latest()
        assert len(latest) == 1
        assert latest[0]["detail"] == "corrected answer"

    def test_both_lines_are_still_on_disk(self, tmp_path):
        """Append-only means the history of what was BELIEVED survives, even
        though the reader answers with one record per question."""
        store = self._two_lines(tmp_path, "first answer", "corrected answer")
        assert len(store.read_all()) == 2
        assert "first answer" in store.index_path.read_text()

    def test_position_decides_not_the_timestamp(self, tmp_path):
        """A correction restates the time it corrects. Sorting by `captured_at`
        would be a stable sort over equal keys -- and would break the moment a
        reader used a different sort."""
        store = self._two_lines(tmp_path, "first", "second")
        keys = [key_of(s.record) for s in store.read_all()]
        assert keys[0] == keys[1], "the two lines answer the same question"
        assert store.latest()[0]["detail"] == "second"

    def test_a_malformed_line_is_refused_by_position(self, tmp_path):
        store = Store(tmp_path)
        store.initialise()
        store.index_path.write_text(
            json.dumps(record("h", captured_at="t", exit_code=2)) + "\n"
            + "{not json\n")
        with pytest.raises(StoreError) as caught:
            store.read_all()
        assert ":2" in str(caught.value), "the failing line is named"


class TestIngestRefusals:
    def _record_file(self, tmp_path, name, payload, digest):
        return write_json(tmp_path / name,
                          record("h-0042", captured_at="2026-08-20T00:00:00Z",
                                 digest=digest))

    def test_a_digest_mismatch_is_refused_and_named(self, tmp_path, capsys):
        payload = walk(["Fan_CPU_1"])
        wrong = "sha256:" + "c" * 64
        entry = self._record_file(tmp_path, "r.json", payload, wrong)
        payloads = tmp_path / "payloads"
        write_json(payloads / "walk.json", payload)

        code = cli.main(["ingest", "--store", str(tmp_path / "store"),
                         str(entry), "--payloads", str(payloads),
                         "--require-payload"])
        assert code == INCOMPLETE
        assert "no payload with that digest" in capsys.readouterr().out

    def test_a_matching_digest_is_stored(self, tmp_path, capsys):
        payload = walk(["Fan_CPU_1"])
        digest = digest_of(payload)
        entry = self._record_file(tmp_path, "r.json", payload, digest)
        payloads = tmp_path / "payloads"
        payloads.mkdir()
        (payloads / "walk.json").write_bytes(
            json.dumps(payload, sort_keys=True).encode())

        code = cli.main(["ingest", "--store", str(tmp_path / "store"),
                         str(entry), "--payloads", str(payloads)])
        assert code == CLEAN, capsys.readouterr().out
        assert Store(tmp_path / "store").cas_path(digest).is_file()

    def test_a_second_answer_to_one_question_is_refused(self, tmp_path, capsys):
        payload = walk(["Fan_CPU_1"])
        digest = digest_of(payload)
        entry = self._record_file(tmp_path, "r.json", payload, digest)
        store = str(tmp_path / "store")
        assert cli.main(["ingest", "--store", store, str(entry),
                         "--allow-dangling"]) == CLEAN
        capsys.readouterr()
        assert cli.main(["ingest", "--store", store, str(entry),
                         "--allow-dangling"]) == INCOMPLETE
        assert "already holds" in capsys.readouterr().out

    def test_a_declared_correction_is_accepted(self, tmp_path, capsys):
        """Non-vacuity for the refusal above: the escape hatch must work, or
        the append-only store has no way to record a correction at all."""
        payload = walk(["Fan_CPU_1"])
        entry = self._record_file(tmp_path, "r.json", payload,
                                  digest_of(payload))
        store = str(tmp_path / "store")
        cli.main(["ingest", "--store", store, str(entry), "--allow-dangling"])
        capsys.readouterr()
        assert cli.main(["ingest", "--store", store, str(entry), "--correct",
                         "--allow-dangling"]) == CLEAN

    def test_a_duplicate_inside_one_batch_is_refused(self, tmp_path, capsys):
        payload = walk(["Fan_CPU_1"])
        digest = digest_of(payload)
        one = self._record_file(tmp_path, "a.json", payload, digest)
        two = self._record_file(tmp_path, "b.json", payload, digest)
        code = cli.main(["ingest", "--store", str(tmp_path / "store"),
                         str(one), str(two), "--allow-dangling"])
        assert code == INCOMPLETE
        assert "already in this batch" in capsys.readouterr().out

    def test_a_malformed_record_is_named_not_the_batch(self, tmp_path, capsys):
        good = self._record_file(tmp_path, "a.json", walk(["F"]),
                                 "sha256:" + "d" * 64)
        bad = write_json(tmp_path / "b.json", {"format": "nope"})
        code = cli.main(["ingest", "--store", str(tmp_path / "store"),
                         str(good), str(bad), "--allow-dangling"])
        assert code == INCOMPLETE
        out = capsys.readouterr().out
        assert "b.json" in out and "stored 1 record" in out
