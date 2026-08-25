"""The fleet run: every expected unit must have reported, or the run is `2`.

**A unit that did not report has not passed.** This is the whole reason the
expectation list is an input rather than something derived from the store: a
fleet audit that enumerates the machines it happens to have records for cannot
distinguish *two thousand clean trays* from *five clean trays and a collector
that died at rack two*. Both render as silence, and silence cannot impersonate a
pass.

**`--optional-unit` is a decision on the record.** A unit declared optional in
advance moves to the skipped row and stops deciding the exit code. That is
somebody writing down *this one is allowed to be absent*, which is a different
thing from nobody noticing it was.

**Freshness is declared, never assumed.** `--since` is what makes a record count
as reporting; there is no default staleness threshold, because a number nobody
published would be this tool inventing a policy and then enforcing it. A floor
is a specification, not a guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .exits import CLEAN, FINDINGS, INCOMPLETE, VERDICTS, worst


class VerdictError(Exception):
    """An expectation list this module refuses."""


def read_expectations(text: str) -> list[str]:
    """One unit key per line; `#` comments and blank lines ignored.

    Deliberately not JSON: this file is maintained by whoever owns the rack
    list, and the failure mode of a JSON list is one missing comma turning the
    whole fleet into *nothing was expected* -- which passes.
    """
    units = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            units.append(line)
    duplicates = sorted({u for u in units if units.count(u) > 1})
    if duplicates:
        raise VerdictError(
            f"the expectation list names {', '.join(duplicates)} more than "
            f"once; a denominator that counts a unit twice is not a count")
    if not units:
        raise VerdictError(
            "the expectation list is empty. A fleet run over no expected units "
            "would pass by having nothing to check")
    return units


@dataclass
class Row:
    unit_key: str
    exit_code: int
    detail: str = ""
    raw_exit_code: int | None = None

    def to_dict(self) -> dict:
        row = {"unit_key": self.unit_key, "exit_code": self.exit_code,
               "verdict": VERDICTS[self.exit_code], "detail": self.detail}
        if self.raw_exit_code is not None:
            row["raw_exit_code"] = self.raw_exit_code
        return row


@dataclass
class Fleet:
    rows: list[Row] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return worst(row.exit_code for row in self.rows)


def assess(expected: Iterable[str], reported: dict[str, dict],
           optional: Iterable[str] = ()) -> Fleet:
    """Judge the run. `reported` is `{unit_key: latest record}` in scope."""
    expected = list(expected)
    optional = set(optional)

    unknown = sorted(set(reported) - set(expected))
    if unknown:
        # The pipeline's rule, inherited: a result for something the run does
        # not declare means the expectation list and the store disagree about
        # what fleet this is, and picking one silently is a guess.
        raise VerdictError(
            "record(s) for unit(s) this run does not expect: "
            + ", ".join(unknown)
            + "; add them to the expectation list, or scope the store")

    fleet = Fleet()
    for unit in expected:
        record = reported.get(unit)
        if record is not None:
            code = record.get("exit_code", CLEAN)
            detail = record.get("detail", "")
            if code == CLEAN:
                detail = detail or f"reported at {record.get('captured_at')}"
            fleet.rows.append(Row(unit, code, detail))
        elif unit in optional:
            fleet.skipped.append(unit)
            fleet.rows.append(Row(
                unit, CLEAN,
                "declared optional for this run and did not report"))
        else:
            fleet.missing.append(unit)
            fleet.rows.append(Row(
                unit, INCOMPLETE,
                "this unit reported nothing at all. A unit that did not report "
                "has not passed"))
    return fleet


__all__ = ["Fleet", "Row", "VerdictError", "assess", "read_expectations",
           "CLEAN", "FINDINGS", "INCOMPLETE"]
