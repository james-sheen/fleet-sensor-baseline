"""The horizontal axis: which units differ from their cohort.

**Set difference and frequency thresholds, nothing heavier.** Categorical
presence needs explainable, auditable arithmetic. A model that cannot show its
denominator has no place in a family whose product *is* the denominator: the
answer to *why is this unit an outlier* has to be a sentence an operator can
check by counting, at three in the morning, against a machine in front of them.

**Both directions are reported.** A sensor the cohort has and this unit does not
is the case everybody expects. A sensor this unit has and the cohort does not is
the case that finds the tray somebody re-cabled, the pre-production board that
escaped into the fleet, and the unit running firmware nobody meant to ship.
Reporting only absences would make the second class invisible by construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .exits import CLEAN, FINDINGS, INCOMPLETE


@dataclass
class UnitOutlier:
    unit_key: str
    absent: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    #: Why this unit could not be judged, when it could not be. A unit that
    #: could not be read is `2`, and it keeps its place in the report -- it does
    #: not vanish from the denominator for having failed.
    unreadable: str | None = None

    @property
    def exit_code(self) -> int:
        if self.unreadable is not None:
            return INCOMPLETE
        return FINDINGS if (self.absent or self.extra) else CLEAN

    def to_dict(self) -> dict:
        row: dict = {"unit_key": self.unit_key, "exit_code": self.exit_code}
        if self.unreadable is not None:
            row["unreadable"] = self.unreadable
            return row
        if self.absent:
            row["absent"] = self.absent
        if self.extra:
            row["extra"] = self.extra
        return row


def compare(expected: set[str], present_by_unit: dict[str, set[str]],
            unreadable: dict[str, str] | None = None) -> list[UnitOutlier]:
    """Every unit against the baseline, in a stable order.

    Every unit appears, including the clean ones. A report that listed only the
    outliers would be indistinguishable from a report over a cohort nobody
    walked, and *silence cannot impersonate a pass* is the first anti-goal of
    this repository.
    """
    unreadable = unreadable or {}
    rows: list[UnitOutlier] = []
    for unit in sorted(set(present_by_unit) | set(unreadable)):
        if unit in unreadable:
            rows.append(UnitOutlier(unit, unreadable=unreadable[unit]))
            continue
        present = present_by_unit[unit]
        rows.append(UnitOutlier(unit,
                                absent=sorted(expected - present),
                                extra=sorted(present - expected)))
    return rows
