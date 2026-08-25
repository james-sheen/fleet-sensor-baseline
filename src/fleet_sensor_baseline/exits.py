"""The exit contract, inherited verbatim from the rest of the family.

    0  clean       nothing to report
    1  findings    something was found, and it is named
    2  incomplete  the check could not be completed, and it says so

**Precedence is `max`, and `2` beats `1` deliberately.** A run that found three
outliers and could not reach a fourth unit has not found three outliers; it has
found three and does not know about the fourth. Reporting `1` there would let a
CI job print *findings: 3* over a fleet nobody finished walking.

**Anything outside `{0, 1, 2}` is read as `2`, with the raw code kept beside
it.** A subprocess that dies on `command not found` exits `127`. Taking `max`
over raw codes would return `127` -- still non-zero, so a job would still fail,
but the summary would claim a verdict vocabulary it does not have. Normalising
without keeping the original throws away the only half of the sentence anybody
can act on: *"exited 127"* is what tells an operator the tool was not installed.
"""

from __future__ import annotations

CLEAN = 0
FINDINGS = 1
INCOMPLETE = 2

#: The whole vocabulary. A code outside this set is not a verdict.
VERDICTS: dict[int, str] = {
    CLEAN: "clean",
    FINDINGS: "findings",
    INCOMPLETE: "incomplete",
}


def normalise(raw: int) -> tuple[int, str | None]:
    """`(exit_code, note)` -- the code to use, and what to say if it changed.

    The note is `None` when nothing was normalised, so a caller can append it
    unconditionally without producing an empty clause on the clean path.
    """
    if not isinstance(raw, int) or isinstance(raw, bool):
        return INCOMPLETE, (f"exit code was {raw!r}, which is not an integer; "
                            f"read as incomplete")
    if raw in VERDICTS:
        return raw, None
    return INCOMPLETE, (f"exited {raw}, which is not one of 0/1/2; "
                        f"read as incomplete")


def worst(codes) -> int:
    """`max` over already-normalised codes. Empty is clean, not incomplete.

    An empty fleet is a question with no subjects, and the caller that knows it
    expected subjects is the one that turns that into `2` -- `verdict` does,
    by name, in its *units that never reported* row. Deciding it here would make
    every legitimately empty aggregation incomplete.
    """
    return max(codes, default=CLEAN)
