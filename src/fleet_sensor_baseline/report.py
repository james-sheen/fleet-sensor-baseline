"""Rendering, and the `summary/1` every subcommand answers in.

One vocabulary for one set of numbers: `exit_code`, `verdict`, `decided_by`,
per-row detail, and `missing` for the subjects that never reported. A reader who
has learned to read one of this family's summaries can read all of them.

**The rendered text is never the source of judgment.** The exit code is. This
module exists so a log is legible, not so a person can decide by squinting at
it -- which is the whole reason anti-goal 1 says *never a dashboard*.
"""

from __future__ import annotations

from typing import Any, Iterable

from .baseline import derivation_line
from .exits import CLEAN, VERDICTS, worst
from .formats import DOWNGRADE_NOTICE, SUMMARY_FORMAT, PROVENANCE_DERIVED


def summary(rows: Iterable[dict], *, missing: Iterable[str] = (),
            skipped: Iterable[str] = (), judged_against: str | None = None,
            notes: Iterable[str] = (),
            cohort_code: int = CLEAN,
            cohort_decided_by: Iterable[str] = ()) -> dict:
    """Assemble a `summary/1`.

    `judged_against` names WHICH KIND OF TRUTH decided this, and it is not
    decoration: an outlier report made against a fleet-derived baseline and one
    made against a manufacturer declaration can carry identical rows and mean
    entirely different things. Every artifact this layer emits says which it was.
    """
    rows = [dict(row) for row in rows]
    # `cohort_code` folds in a finding that belongs to the COHORT rather than to
    # any unit -- a sensor the cohort disagrees about. Folded in here rather
    # than patched onto the result afterwards, because `verdict` is derived from
    # the code and setting one without the other printed `clean (exit 1)`.
    cohort_decided_by = list(cohort_decided_by)
    code = worst([*(row.get("exit_code", CLEAN) for row in rows), cohort_code])
    decided_by = [_name(row) for row in rows
                  if row.get("exit_code") == code and code != CLEAN]
    if cohort_code == code and code != CLEAN:
        decided_by.extend(cohort_decided_by)
    out: dict[str, Any] = {
        "format": SUMMARY_FORMAT,
        "exit_code": code,
        "verdict": VERDICTS[code],
        "decided_by": decided_by,
        "rows": rows,
        "missing": list(missing),
        "skipped": list(skipped),
    }
    if judged_against is not None:
        out["judged_against"] = judged_against
    notes = list(notes)
    if notes:
        out["notes"] = notes
    return out


def _name(row: dict) -> str:
    for key in ("unit_key", "surface", "gate"):
        if key in row:
            value = row[key]
            return "/".join(value) if isinstance(value, list) else str(value)
    return "?"


def render(payload: dict, *, title: str) -> str:
    """The summary as an operator reads it in a log."""
    lines = [f"{title}: {payload['verdict']} (exit {payload['exit_code']})"]
    if payload.get("judged_against"):
        lines.append(f"  judged against: {payload['judged_against']}")
    for row in payload["rows"]:
        lines.append("  " + _row_line(row))
    if payload.get("missing"):
        # The spec's sentence, verbatim, because it is what an operator greps
        # for across four tools in this family.
        lines.append("  units that never reported: "
                     + ", ".join(payload["missing"]))
    if payload.get("skipped"):
        lines.append("  declared optional and did not report: "
                     + ", ".join(payload["skipped"]))
    if payload.get("decided_by"):
        lines.append("  decided by: " + ", ".join(payload["decided_by"]))
    for note in payload.get("notes", []):
        lines.append("  " + note)
    return "\n".join(lines)


def _row_line(row: dict) -> str:
    name = _name(row)
    verdict = row.get("verdict") or VERDICTS.get(row.get("exit_code", CLEAN), "?")
    parts = [f"{name:<28} {verdict}"]
    if row.get("unreadable"):
        parts.append(f"-- {row['unreadable']}")
    if row.get("absent"):
        parts.append("-- absent: " + ", ".join(row["absent"]))
    if row.get("extra"):
        parts.append("-- unexpected: " + ", ".join(row["extra"]))
    if row.get("gone"):
        parts.append("-- gone: " + ", ".join(row["gone"]))
    if row.get("arrived"):
        parts.append("-- arrived: " + ", ".join(row["arrived"]))
    if row.get("firmware"):
        parts.append(f"-- firmware {row['firmware']['before']} -> "
                     f"{row['firmware']['after']}")
    if row.get("undeclared_prefix_shift"):
        shift = row["undeclared_prefix_shift"]
        parts.append(f"-- undeclared prefix {shift['old']!r} -> {shift['new']!r};"
                     f" declare it with --aggregation-prefix to pair these")
    if row.get("paired_through_declared_prefix"):
        parts.append("-- paired through a declared prefix")
    if row.get("detail"):
        parts.append(f"-- {row['detail']}")
    return " ".join(parts)


def baseline_preamble(baseline: dict) -> list[str]:
    """The lines that must precede any judgment made against a baseline.

    **The notice is emitted verbatim and a test asserts it.** A consumer that
    printed a reworded version would still look responsible while having quietly
    changed what the report claims: the sentence is part of the format, not
    part of this renderer's prose.
    """
    lines = [f"baseline: {derivation_line(baseline)}"]
    if baseline.get("provenance") == PROVENANCE_DERIVED:
        lines.append(f"notice: {DOWNGRADE_NOTICE}")
    return lines
