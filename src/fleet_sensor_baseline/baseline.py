"""Deriving a `fleet-baseline/1` -- an additional, labeled, downgraded declaration.

**Wherever a manufacturer declaration exists, it wins.** A fleet-derived
baseline is blind to an absence the whole cohort shares, which is the founding
problem of this family at fleet scale: two thousand trays that all lost the same
sensor in the same firmware agree with each other perfectly. Consensus reports
them clean. Only a declaration written by somebody who knew what the board has
can see it, and `bmc-sensor-audit coverage` is where that comparison belongs.

So this module derives, labels what it derived, and refuses to derive from a
cohort too small to mean anything.

**The scope is matched on DECLARED fields and never inferred.** `model` is the
operator's own name for a class of machine, compared as an opaque string;
`firmware.release` is a dotted version the collector declares. A range is never
matched by pattern-guessing at a vendor string like `GB200-fw-1.4.2`, because
the guess would be a hardcoded assumption about one vendor's formatting living
inside a tool that claims not to have any. A record with no `firmware.release`
is EXCLUDED FROM THE DENOMINATOR AND NAMED when a range is in use -- never
counted as absent, and never silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .formats import BASELINE_FORMAT, DOWNGRADE_NOTICE, PROVENANCE_DERIVED
from .store import surface_of

#: A cohort smaller than this is an anecdote wearing a format key. Lowering it
#: takes an explicit flag, so a nine-unit baseline is always somebody's decision.
DEFAULT_FLOOR = 20

DEFAULT_THRESHOLD = 0.99

#: At or below this ratio a sensor is FOREIGN to the cohort, and a unit that has
#: one is genuinely unusual. Between the two thresholds the cohort disagrees with
#: itself, and that is a fact about the COHORT rather than about any unit in it.
#:
#: The band has to exist. Without it every sensor was either expected or foreign,
#: so a sensor present on 22 of 24 units -- below 0.99, far above nothing -- was
#: classed foreign, and the 22 units that HAD it were reported as the outliers
#: while the 2 that had lost it came back clean. A proportion is a coarse
#: instrument at rack scale: 0.99 of 24 is 23.76, so ONE deviant unit crossed it,
#: and `--floor` admits cohorts from 20.
DEFAULT_ABSENT_THRESHOLD = 0.01


class BaselineError(Exception):
    """A derivation this module refuses, with the reason in the message."""


@dataclass
class Selection:
    """What the scope selected, and what it could not judge."""
    records: list[dict] = field(default_factory=list)
    #: `(unit_key, reason)` for records a range could not be applied to. Kept
    #: because a unit excluded for being unjudgeable is not a unit that failed.
    excluded: list[tuple[str, str]] = field(default_factory=list)


def parse_range(text: str) -> list[tuple[str, tuple[int, ...]]]:
    """`>=1.4,<1.5` into comparable clauses.

    Deliberately small: `>=`, `>`, `<=`, `<`, `==`, over dotted integers.
    Categorical presence needs explainable, auditable arithmetic, and a
    comparator nobody can read by eye has no place deciding which machines are
    in a denominator.
    """
    clauses: list[tuple[str, tuple[int, ...]]] = []
    for raw in text.split(","):
        piece = raw.strip()
        if not piece:
            continue
        for operator in (">=", "<=", "==", ">", "<"):
            if piece.startswith(operator):
                value = piece[len(operator):].strip()
                clauses.append((operator, _version(value, piece)))
                break
        else:
            raise BaselineError(
                f"{piece!r} does not start with one of >=, <=, ==, >, <; a "
                f"firmware range is a list of comparisons")
    if not clauses:
        raise BaselineError(f"{text!r} contains no comparisons")
    return clauses


def _version(value: str, where: str) -> tuple[int, ...]:
    parts = value.split(".")
    if not value or not all(part.isdigit() for part in parts):
        raise BaselineError(
            f"{where!r} compares against {value!r}, which is not a dotted "
            f"numeric version")
    return tuple(int(part) for part in parts)


def _pad(left: tuple[int, ...], right: tuple[int, ...]) -> tuple[tuple, tuple]:
    """`1.4` and `1.4.2` compare as `1.4.0` and `1.4.2`.

    Without this, `>=1.4` refuses `1.4.2` on a tuple comparison of unequal
    length in some orderings and accepts it in others -- a range whose answer
    depends on how many components somebody typed.
    """
    width = max(len(left), len(right))
    return (left + (0,) * (width - len(left)), right + (0,) * (width - len(right)))


def in_range(release: str, clauses: Sequence[tuple[str, tuple[int, ...]]]) -> bool:
    version = _version(release, release)
    for operator, bound in clauses:
        left, right = _pad(version, bound)
        if operator == ">=" and not left >= right:
            return False
        if operator == ">" and not left > right:
            return False
        if operator == "<=" and not left <= right:
            return False
        if operator == "<" and not left < right:
            return False
        if operator == "==" and left != right:
            return False
    return True


def select(records: Iterable[dict], *, model: str | None = None,
           firmware_range: str | None = None,
           firmware: str | None = None) -> Selection:
    """Records within a scope, plus the ones a range could not judge."""
    clauses = parse_range(firmware_range) if firmware_range else None
    out = Selection()
    for record in records:
        if model is not None and record.get("model") != model:
            continue
        info = record.get("firmware") or {}
        if firmware is not None and info.get("version") != firmware:
            continue
        if clauses is not None:
            release = info.get("release")
            if not isinstance(release, str) or not release:
                out.excluded.append((
                    record["unit_key"],
                    "no firmware.release to compare against the range; a "
                    "version string is not a version"))
                continue
            try:
                if not in_range(release, clauses):
                    continue
            except BaselineError as exc:
                out.excluded.append((record["unit_key"], str(exc)))
                continue
        out.records.append(record)
    return out


def latest_per_unit(records: Iterable[dict]) -> dict[str, list[dict]]:
    """The newest capture per surface, grouped by unit.

    **A unit contributes once to the denominator, however often it was
    captured.** Otherwise a rack that reports hourly outvotes a rack that
    reports weekly, and the baseline describes the collection schedule rather
    than the fleet.
    """
    newest: dict[tuple, dict] = {}
    for record in records:
        key = surface_of(record)
        current = newest.get(key)
        if current is None or record.get("captured_at", "") >= current.get(
                "captured_at", ""):
            newest[key] = record
    out: dict[str, list[dict]] = {}
    for record in newest.values():
        out.setdefault(record["unit_key"], []).append(record)
    return out


def derive(present_by_unit: dict[str, set[str]],
           paths_by_unit: dict[str, dict[str, str]],
           *, scope: dict, threshold: float = DEFAULT_THRESHOLD,
           absent_threshold: float = DEFAULT_ABSENT_THRESHOLD,
           floor: int = DEFAULT_FLOOR,
           window: tuple[str, str] | None = None) -> dict:
    """Build the `fleet-baseline/1`. Raises `BaselineError` below the floor."""
    total = len(present_by_unit)
    if total == 0:
        raise BaselineError(
            "the scope selected no units at all. A baseline over an empty "
            "cohort is not a weak baseline, it is no measurement")
    if total < floor:
        raise BaselineError(
            f"the scope selected {total} unit(s) and the floor is {floor}. A "
            f"baseline of {total} is an anecdote wearing a format key: at that "
            f"size one unlucky machine moves every ratio past the threshold. "
            f"Raise the cohort, or lower the floor explicitly with --floor")
    if not 0.0 < threshold <= 1.0:
        raise BaselineError(
            f"--present-threshold is {threshold}, outside (0, 1]")
    if not 0.0 <= absent_threshold < threshold:
        raise BaselineError(
            f"--absent-threshold is {absent_threshold}, which must be at least "
            f"0 and below --present-threshold ({threshold}). With the two "
            f"equal there is no band for a cohort that disagrees with itself, "
            f"and the disagreement is charged to individual units instead")

    counts: dict[str, int] = {}
    for names in present_by_unit.values():
        for name in names:
            counts[name] = counts.get(name, 0) + 1

    sensors = []
    divergent = []
    for name in sorted(counts):
        ratio = counts[name] / total
        if ratio < threshold:
            if ratio > absent_threshold:
                # Recorded, not dropped. `/1` dropped it, and a consumer then
                # had no way to tell "the cohort disagrees about this" from
                # "nobody has this" -- so it charged the difference to whichever
                # group was larger, which is the wrong one.
                divergent.append({
                    "name": name,
                    "present_ratio": round(ratio, 6),
                    "present_on": counts[name],
                    "of": total,
                })
            continue
        entry: dict = {"name": name, "present_ratio": round(ratio, 6)}
        # **Only when the contributing units agree.** A baseline that asserts
        # one URI while the cohort reports several is asserting something no
        # unit said. Matching is by name regardless; this key is advisory, and
        # advisory metadata that is wrong is worse than absent.
        seen = {paths[name] for paths in paths_by_unit.values() if name in paths}
        if len(seen) == 1:
            entry["uri_suffix"] = seen.pop()
        sensors.append(entry)

    derived: dict = {"units": total, "present_threshold": threshold,
                     "absent_threshold": absent_threshold}
    if window is not None:
        derived["captured_between"] = list(window)
    return {
        "format": BASELINE_FORMAT,
        "scope": scope,
        "derived": derived,
        "sensors": sensors,
        "divergent": divergent,
        "provenance": PROVENANCE_DERIVED,
        "notice": DOWNGRADE_NOTICE,
    }


def expected_names(baseline: dict) -> set[str]:
    return {sensor["name"] for sensor in baseline.get("sensors", [])}


def divergent_names(baseline: dict) -> set[str]:
    """Sensors the cohort disagrees about.

    A judgment must not charge these to a unit in either direction: having one
    is not an anomaly and lacking one is not an absence. The disagreement is
    the finding, and it belongs to the cohort.
    """
    return {entry["name"] for entry in baseline.get("divergent", [])}


def derivation_line(baseline: dict) -> str:
    """The one line every consumer prints above a judgment made with this.

    Denominator, window and threshold, because a ratio without its predicate is
    a number somebody will quote.
    """
    derived = baseline.get("derived", {})
    window = derived.get("captured_between")
    when = f", captured between {window[0]} and {window[1]}" if window else ""
    return (f"derived from {derived.get('units')} unit(s) at a presence "
            f"threshold of {derived.get('present_threshold')}{when}")
