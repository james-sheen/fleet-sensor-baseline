"""The threshold audit across time, by handing two stored walks to the referee.

**The pieces already existed and nothing joined them.** The store keeps whole
`walk/1` payloads under CAS; the referee already judges a pair of them, with
`--strict-fields` and `--aggregation-prefix`. What was missing was neither
storage nor judgment -- it was selection and handover. This module is that, and
nothing else: it decides WHICH two walks answer the question and gives them to
the program whose job it is to compare them.

The ETag design said the gap out loud. A skip record self-describes as
`{"basis": "collection-etag", "proves": "membership"}` and the documentation
says in words that membership *is not enough for a threshold audit*. That
sentence names the capability this module supplies.

## Why a membership record is refused rather than compared

**This is the load-bearing rule here, and it is not hygiene.** A skip record
carries the PREVIOUS capture's `payload_digest` forward -- it reuses the earlier
bytes because the BMC said the sensor set had not changed. So comparing a walk
against a skip resolves both sides to the SAME CAS object, hands the referee two
identical files, and gets `0`. Clean, fast, and a lie: the question asked was
whether a threshold moved, and a collection ETag cannot see a threshold move on
a sensor that stayed present.

That is a false clean of exactly the shape this family keeps arriving at through
new doors, so the record's own `proves` field is read and the comparison is
refused. The field exists for this.

## The boundary

The referee is run, never imported, for the reason `collect` gives: a change
that broke the tool's published output while leaving its internals intact would
still pass every test here, and the published output is the only thing a fleet
ever sees. Its exit codes are mapped through the family vocabulary and its
refusals are passed through in its own words -- the ordering refusal is better
worded than anything this module would write, and re-wording it would put a
second author between the operator and the reason.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from .exits import INCOMPLETE, VERDICTS, normalise
from .store import Store, StoreError, surface_of

#: What a record says it established when the walk was skipped. A record whose
#: `unchanged.proves` is this cannot answer a threshold question -- see the
#: module docstring. Read rather than assumed: an `unchanged` block that says
#: something else is a claim this build has not been taught, and is refused too.
PROVES_MEMBERSHIP = "membership"

DEFAULT_COMMAND = ("bmc-sensor-audit",)


class CompareError(Exception):
    """A comparison this module refuses, with the reason in the message."""


@dataclass
class Comparison:
    """One surface, judged across two captures."""
    surface: tuple[str, ...]
    exit_code: int
    detail: str
    before: str = ""
    after: str = ""
    raw_exit_code: int | None = None
    report: str = ""

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "surface": list(self.surface),
            "exit_code": self.exit_code,
            "verdict": VERDICTS[self.exit_code],
        }
        if self.detail:
            # Omitted when empty so the row renders as the verdict alone. A
            # detail repeating the verdict word reads as `findings -- findings`.
            row["detail"] = self.detail
        if self.before or self.after:
            row["captured"] = {"before": self.before, "after": self.after}
        if self.raw_exit_code is not None:
            row["raw_exit_code"] = self.raw_exit_code
        return row


def _at_or_before(records: Sequence[dict], when: str) -> dict | None:
    """The newest record at or before `when`. Inclusive, like `--since/--at`.

    String comparison on ISO-8601, the same rule the rest of this package uses
    for a window; a differently-shaped timestamp sorts where its text puts it
    rather than being silently reordered.
    """
    candidates = [r for r in records if r.get("captured_at", "") <= when]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r.get("captured_at", ""))


def _refuse_if_membership_only(record: dict, side: str) -> None:
    unchanged = record.get("unchanged")
    if not isinstance(unchanged, dict):
        return
    proves = unchanged.get("proves")
    captured = record.get("captured_at", "?")
    if proves == PROVES_MEMBERSHIP:
        raise CompareError(
            f"the {side} record at {captured} was filed from a skip: it proves "
            f"{PROVES_MEMBERSHIP} and reuses the earlier capture's payload, so "
            f"comparing it would judge one file against itself and report no "
            f"drift. A collection ETag cannot see a threshold edited on a "
            f"sensor that stayed present. Force a full walk for this surface, "
            f"or pick a time when one was taken")
    raise CompareError(
        f"the {side} record at {captured} says it proves {proves!r}, which this "
        f"build has not been taught to reason about; it is not compared")


def pair_for(records: Sequence[dict], *, before: str, after: str
             ) -> tuple[dict, dict]:
    """The two records that answer the question for ONE surface.

    Refuses rather than guesses when either end is missing or when both ends
    resolve to one record -- a comparison of a capture with itself is clean by
    construction and says nothing.
    """
    early = _at_or_before(records, before)
    late = _at_or_before(records, after)
    if early is None:
        raise CompareError(f"no capture at or before {before}")
    if late is None:
        raise CompareError(f"no capture at or before {after}")
    if early.get("captured_at") == late.get("captured_at"):
        raise CompareError(
            f"both ends resolve to the capture at {early.get('captured_at')}; "
            f"one capture compared with itself is clean by construction")
    _refuse_if_membership_only(early, "--before")
    _refuse_if_membership_only(late, "--after")
    return early, late


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), capture_output=True, text=True,
                          check=False)


def judge(store: Store, early: dict, late: dict, *,
          command: Sequence[str] = DEFAULT_COMMAND,
          strict_fields: bool = False,
          prefixes: Sequence[str] = (),
          runner: Callable[..., Any] | None = None) -> Comparison:
    """Hand one surface's two payloads to the referee and read its answer."""
    run = runner or _run
    surface = surface_of(late)
    payloads = (store.payload(early), store.payload(late))
    with tempfile.TemporaryDirectory() as scratch:
        paths = []
        for name, raw in zip(("before.json", "after.json"), payloads):
            path = Path(scratch) / name
            path.write_bytes(raw)
            paths.append(str(path))
        argv = [*command, "regression", "--before", paths[0], "--after", paths[1]]
        if strict_fields:
            argv.append("--strict-fields")
        for mapping in prefixes:
            argv += ["--aggregation-prefix", mapping]
        try:
            done = run(argv)
        except FileNotFoundError:
            # Same answer the collector gives, for the same reason: the referee
            # is an optional extra here, so its absence is a fact about the
            # environment and not about the machine.
            return Comparison(
                surface=surface, exit_code=INCOMPLETE,
                before=early.get("captured_at", ""),
                after=late.get("captured_at", ""),
                detail=(f"{command[0]} is not on PATH; this comparison needs "
                        f"the referee. Install it with the collect extra"))

    code, note = normalise(done.returncode)
    # **A refusal travels in the referee's own words; a verdict does not need
    # any.** Its refusals name the thing an operator has to fix -- captures the
    # wrong way round, a capture with no field record under `--strict-fields` --
    # and it says them better than a second author would. On a verdict, stderr
    # is empty and the full report is printed above under its surface, so the
    # detail says the verdict rather than passing the report's HEADING off as a
    # summary of it, which is what a first-line-of-stdout rule would do.
    refusal = (done.stderr or "").strip()
    detail = refusal.splitlines()[0] if refusal else ""
    if note:
        detail = f"{detail}; {note}" if detail else note
    return Comparison(
        surface=surface, exit_code=code, detail=detail,
        before=early.get("captured_at", ""), after=late.get("captured_at", ""),
        raw_exit_code=None if note is None else done.returncode,
        report=(done.stdout or "").strip())


def compare_unit(store: Store, unit_key: str, *, before: str, after: str,
                 command: Sequence[str] = DEFAULT_COMMAND,
                 strict_fields: bool = False,
                 prefixes: Sequence[str] = (),
                 runner: Callable[..., Any] | None = None) -> list[Comparison]:
    """Every surface of one unit, judged across the two times.

    **Surface to surface, never unit to unit.** One physical machine can answer
    on more than one BMC, so pairing by `unit_key` alone would compare a host
    BMC's walk against an HMC's and report every sensor on one as having
    vanished. `surface_of` is the same rule the rest of the package pairs by.
    """
    by_surface: dict[tuple[str, ...], list[dict]] = {}
    for record in store.latest():
        if record.get("unit_key") != unit_key:
            continue
        by_surface.setdefault(surface_of(record), []).append(record)

    if not by_surface:
        raise CompareError(
            f"the store holds no record for {unit_key}; nothing to compare")

    out: list[Comparison] = []
    for surface in sorted(by_surface):
        try:
            early, late = pair_for(by_surface[surface], before=before,
                                   after=after)
        except CompareError as refusal:
            out.append(Comparison(surface=surface, exit_code=INCOMPLETE,
                                  detail=str(refusal)))
            continue
        try:
            out.append(judge(store, early, late, command=command,
                             strict_fields=strict_fields, prefixes=prefixes,
                             runner=runner))
        except StoreError as missing:
            out.append(Comparison(surface=surface, exit_code=INCOMPLETE,
                                  detail=str(missing),
                                  before=early.get("captured_at", ""),
                                  after=late.get("captured_at", "")))
    return out
