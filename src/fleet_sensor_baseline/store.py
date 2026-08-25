"""Append-only JSONL index plus a content-addressed store for walk payloads.

**No time-series database in 0.x, and the reason is a measurement rather than a
preference.** Configuration drift is per-boot and per-firmware-event, not
per-second. A TSDB is the right shape for the wrong problem at this cadence, and
the JSONL index *is* the time series here. Revisit when cross-month aggregation
queries exist and are slow -- measured on real volumes, not assumed from the
word *fleet*.

**The content store does NOT collapse a homogeneous fleet, and this docstring
used to say it did.** Measured 2026-08-25: two walks of one unchanged machine
produce different bytes, because `walk/1` carries a `latencies` array of
per-fetch timings and a `captured_at`. Neither is ever the same twice, so no two
captures ever share a digest -- across time or across identical trays.

The claim was plausible, written from the shape of a content-addressed store
rather than from a measurement of what goes into one, and nothing checked it.
`tests/test_store.py` now pins the real behaviour.

Payloads still live behind digests, for the reasons that survive: a digest is a
handle the referee also prints, a record can name a capture without embedding
it, and a corrupted object is detectable. **Deduplication is not among them.**
What actually avoids re-storing an unchanged walk is not walking it --
`collect --etag-cache`, which asks the BMC first.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from .formats import RECORD_FORMAT, validate_record

RECORDS = "records.jsonl"
CAS = "cas"
ETAGS = "etags"


class StoreError(Exception):
    """Something the store refuses. Always reported, never raised past the CLI."""


def digest_bytes(raw: bytes) -> str:
    """`sha256:` and the hex digest of the BYTES.

    Byte-for-byte identical to what `bmc-sensor-audit capture --print-digest`
    prints, and reproducible by `sha256sum` in any language. Deliberately not a
    canonical-JSON digest: that would survive re-indentation, and it would also
    require every consumer to reproduce one language's float formatting exactly
    before it could agree with this one.
    """
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def ref_for(digest: str) -> str:
    """The `cas/sha256/<hex>` reference a record carries for a digest."""
    algorithm, _, hexdigest = digest.partition(":")
    return f"{CAS}/{algorithm}/{hexdigest}"


def surface_of(record: dict) -> tuple[str, ...]:
    """The BMC surface a record describes: unit key plus its topology.

    **A unit is the tuple, not the `unit_key` alone.** On NVIDIA-class platforms
    one physical unit answers on more than one BMC -- a host BMC and an HMC
    behind bmcweb aggregation -- so two records differing only in `satellite`
    are two surfaces of one machine, not two machines. Pairing across time has
    to be surface-to-surface, or the vertical axis reports every sensor on the
    host BMC as having vanished the moment an HMC record lands beside it.
    """
    topology = record.get("topology") or {}
    return (record["unit_key"],) + tuple(
        f"{k}={topology[k]}" for k in sorted(topology))


def key_of(record: dict) -> tuple[str, ...]:
    """The identity a duplicate is a duplicate of: surface plus capture time."""
    return surface_of(record) + (record.get("captured_at", ""),)


@dataclass(frozen=True)
class Stored:
    """One line of the index, with the position that decides latest-wins."""
    line_number: int
    record: dict


class Store:
    """A directory holding `records.jsonl` and `cas/sha256/<digest>`."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)

    @property
    def index_path(self) -> Path:
        return self.root / RECORDS

    def cas_path(self, digest: str) -> Path:
        algorithm, _, hexdigest = digest.partition(":")
        return self.root / CAS / algorithm / hexdigest

    def etag_path(self, record_or_surface) -> Path:
        """Where the referee's ETag cache for ONE BMC surface lives.

        **One cache per surface, not per unit.** `capture --etag-cache` holds the
        collection ETags of a single Redfish tree. A machine that answers on a
        host BMC and an HMC has two trees and two sets of collections; pointing
        both at one file would make each walk invalidate the other's cache and
        the feature would quietly do nothing.

        The name is a readable slug plus a short digest of the exact surface.
        The slug is for whoever has to look in this directory at three in the
        morning; the digest is what makes it unambiguous, because `unit_key` is
        opaque operator naming and may contain anything at all -- including the
        separator the slug uses.
        """
        surface = (record_or_surface if isinstance(record_or_surface, tuple)
                   else surface_of(record_or_surface))
        joined = "\u0000".join(surface)
        digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]
        slug = re.sub(r"[^A-Za-z0-9._=-]+", "-", "--".join(surface)).strip("-")
        return self.root / ETAGS / f"{slug[:80]}-{digest}.json"

    def initialise(self) -> None:
        (self.root / CAS / "sha256").mkdir(parents=True, exist_ok=True)
        (self.root / ETAGS).mkdir(parents=True, exist_ok=True)
        self.index_path.touch()

    # -- reading ---------------------------------------------------------

    def read_all(self) -> list[Stored]:
        """Every line, in file order, with malformed lines refused by position.

        A line number rather than a bare list because **latest-wins is decided
        by position in the file**, and a reader that sorted by `captured_at`
        would put a correction before the thing it corrects whenever the
        correction restates the original capture time -- which is exactly what a
        correction does.
        """
        if not self.index_path.is_file():
            return []
        out: list[Stored] = []
        with self.index_path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise StoreError(
                        f"{self.index_path}:{number} is not JSON: {exc}") from exc
                problems = validate_record(payload)
                if problems:
                    raise StoreError(
                        f"{self.index_path}:{number} is not a valid "
                        f"{RECORD_FORMAT}: " + "; ".join(problems))
                out.append(Stored(number, payload))
        return out

    def latest(self) -> list[dict]:
        """One record per `(surface, captured_at)`, the last line winning.

        The append-only rule and this function are one design: corrections are
        new lines, never edits, so the file keeps the history of what was
        believed and the reader still answers with one record per question.
        """
        seen: dict[tuple[str, ...], dict] = {}
        for stored in self.read_all():
            seen[key_of(stored.record)] = stored.record
        return list(seen.values())

    def payload(self, record: dict) -> bytes:
        digest = record.get("payload_digest")
        if not digest:
            raise StoreError(
                f"{record['unit_key']} at {record.get('captured_at')} carries no "
                f"payload digest; a record that could not be walked has no walk")
        path = self.cas_path(digest)
        if not path.is_file():
            raise StoreError(
                f"{digest} is referenced by {record['unit_key']} at "
                f"{record.get('captured_at')} and is not in the store")
        return path.read_bytes()

    # -- writing ---------------------------------------------------------

    def put_payload(self, raw: bytes) -> str:
        """Store bytes under their digest and return it. Idempotent by design."""
        digest = digest_bytes(raw)
        path = self.cas_path(digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file():
            path.write_bytes(raw)
        return digest

    def append(self, records: Iterable[dict]) -> int:
        """Append records to the index. Returns how many lines were written."""
        records = list(records)
        if not records:
            return 0
        self.initialise()
        with self.index_path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
        return len(records)

    def existing_keys(self) -> set[tuple[str, ...]]:
        return {key_of(stored.record) for stored in self.read_all()}


def units(records: Iterable[dict]) -> dict[str, list[dict]]:
    """Records grouped by `unit_key` -- the machine, across all its surfaces."""
    out: dict[str, list[dict]] = {}
    for record in records:
        out.setdefault(record["unit_key"], []).append(record)
    return out


def surfaces(records: Iterable[dict]) -> dict[tuple[str, ...], list[dict]]:
    """Records grouped by surface, each list ordered by capture time."""
    out: dict[tuple[str, ...], list[dict]] = {}
    for record in records:
        out.setdefault(surface_of(record), []).append(record)
    for group in out.values():
        group.sort(key=lambda r: r.get("captured_at", ""))
    return out


def iter_json(paths: Iterable[str | os.PathLike[str]]) -> Iterator[tuple[Path, Any]]:
    """Read JSON files, naming the one that failed rather than the batch."""
    for raw_path in paths:
        path = Path(raw_path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StoreError(f"{path}: {exc.strerror or exc}") from exc
        try:
            yield path, json.loads(text)
        except json.JSONDecodeError as exc:
            raise StoreError(f"{path} is not JSON: {exc}") from exc
