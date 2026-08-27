"""Exporting a derived baseline as a declaration source `bmc-sensor-audit` reads.

**Both repositories described this seam and neither implemented it.** This
layer's third anti-goal says a fleet-derived baseline is *an additional,
labeled, downgraded declaration source*; the referee's `declaration_source`
module lists `fleet-baseline/1` as its explicit last resort. Between the two
sentences there was no converter, no documented recipe, and two different
format namespaces -- so feeding a baseline this layer emits to the referee got
a refusal naming formats nothing produced:

    baseline.json declares format 'fleet-sensor-baseline/fleet-baseline/2'.
    This build consumes 'bmc-sensor-audit/pdr/1' and
    'bmc-sensor-audit/fleet-baseline/1'

Both refusals were correct and loud. The capability both documents described
simply did not exist. This module is that capability, and it is deliberately
one direction only: this layer WRITES the referee's format and never reads it.

## What the conversion is allowed to lose, and where it says so

**The divergent band does not cross.** A `fleet-baseline/2` reports three
states -- expected, foreign, and *the cohort disagrees with itself*. The
referee's format has two, because it judges ONE machine against a declaration
and a declaration is a list of what that machine should have. So the sensors
the cohort disagreed about are not declared.

That is the right ruling and it is also exactly how the inversion happened one
tool over, so it is not allowed to be silent. Declaring them would expect a
sensor of every unit when 8 percent of the fleet does not have it, and each of
those units would be charged with a finding for a disagreement that is a fact
about the cohort. Dropping them without a word would reproduce `/1`, whose
whole defect was that the information needed to judge was discarded at
derivation and the file did not admit it.

So they are written into the emitted file under `divergent_not_declared`, which
the referee ignores by its own `/1` rule -- *unknown keys are ignored; a
producer may carry whatever else it needs* -- and named on stderr at the moment
of export. The reviewer reads the file. The reviewer is the one who needs them.

## Why the output is a candidate and cannot be anything else

It carries `reviewed: null`, so the referee refuses to consume it until a
person adds their name and a date. **The conversion is not the review.** A
baseline derived from a fleet of unprovisioned boards is an empty declaration
that reads healthy against every other unprovisioned board, and no check inside
the file can tell that from a good one -- the referee's founding hazard, at
fleet scale, where it is worse: consensus makes the empty answer look
corroborated.

Emitting a reviewed file would forge the one signature the gate exists to
require. Nothing here can supply it, so nothing here tries; the export composes
with the referee's existing gate rather than adding machinery beside it, which
is the same shape `pdr/1` already has.
"""

from __future__ import annotations

from typing import Any

from .baseline import divergent_names
from .formats import (BASELINE_FORMAT, DOWNGRADE_NOTICE, PROVENANCE_DERIVED,
                      REFEREE_BASELINE_FORMAT)

#: Re-exported, not redefined. `validate` has to refuse this format by name and
#: lives in `formats`, this module writes it, and a constant with two homes is
#: a constant with two versions -- which is the defect three surfaces of this
#: package had already shipped. Imported for readers who reach for it here.
__all__ = ["REFEREE_BASELINE_FORMAT", "DIVERGENT_KEY", "RefereeExportError",
           "declaration_from_baseline", "export_preamble"]

#: Written into the emitted file, ignored by the referee, and the reason the
#: conversion is honest rather than merely lossy. See the module docstring.
DIVERGENT_KEY = "divergent_not_declared"


class RefereeExportError(Exception):
    """An export this module refuses, with the reason in the message."""


def declaration_from_baseline(baseline: dict, *,
                              platform: str | None = None) -> dict:
    """Convert one `fleet-baseline/2` into a referee declaration candidate.

    `platform` overrides `scope.model`. One of the two must be present: the
    referee requires a platform on every declaration source and there is
    nothing here to infer one from. A cohort scoped across models has no single
    answer, and guessing one would be this tool asserting a machine class
    nobody declared.
    """
    declared = baseline.get("format")
    if declared != BASELINE_FORMAT:
        # A `/1` is refused here for the same reason `validate` refuses it: the
        # sensors the cohort disagreed about are precisely what must not be
        # exported, and a `/1` dropped them at derivation, so there is no way
        # to tell one it never had from one it discarded.
        raise RefereeExportError(
            f"the baseline declares format {declared!r} and this exports "
            f"{BASELINE_FORMAT!r}. Derive again")

    scope = baseline.get("scope") or {}
    model = platform if platform is not None else scope.get("model")
    if not isinstance(model, str) or not model:
        raise RefereeExportError(
            "the baseline names no model, so this declaration would name no "
            "platform, and the referee requires one on every declaration "
            "source. Derive the baseline with --model, or name it here with "
            "--for-referee-platform")

    sensors = []
    for entry in baseline.get("sensors") or []:
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise RefereeExportError(
                "the baseline carries a sensor with no name")
        # Thresholds are not exported and there are none to export: this layer
        # audits PRESENCE across a cohort. A bound invented here would be a
        # number no unit reported, arriving in the referee's vocabulary where
        # it would read exactly like a manufacturer's.
        out: dict[str, Any] = {"name": name}
        if isinstance(entry.get("uri_suffix"), str):
            out["uri_suffix"] = entry["uri_suffix"]
        if isinstance(entry.get("present_ratio"), (int, float)):
            out["present_ratio"] = entry["present_ratio"]
        sensors.append(out)

    if not sensors:
        # The referee refuses this too, and says so well. Refusing here names
        # the cohort that produced it, which is the thing an operator can act
        # on -- and writing the file first would mean the failure surfaces one
        # tool later, against a file whose derivation is no longer on screen.
        raise RefereeExportError(
            "the cohort agreed on no sensor at all, so this declaration would "
            "be empty. An empty declaration reads clean against every machine, "
            "which is the one answer neither tool will emit")

    derived = baseline.get("derived") or {}
    units = derived.get("units")
    window = derived.get("captured_between")
    span = ""
    if isinstance(window, list) and len(window) == 2:
        span = f", captured between {window[0]} and {window[1]}"
    # `firmware` and `captured_at` are deliberately absent. The referee requires
    # them of a `pdr/1` and not of this format, for a reason it writes down: a
    # fleet baseline spans firmware levels by construction and says so through
    # `derived_from` instead. Supplying one anyway would pick a level out of a
    # range and present it as the level this was taken at.
    provenance = (f"{BASELINE_FORMAT} over {units} unit(s){span}"
                  if units is not None else BASELINE_FORMAT)
    if scope.get("firmware_range"):
        provenance += f", firmware {scope['firmware_range']}"
    elif scope.get("firmware"):
        provenance += f", firmware {scope['firmware']}"

    divergent = sorted(divergent_names(baseline))
    return {
        "format": REFEREE_BASELINE_FORMAT,
        "platform": model,
        "derived_from": provenance,
        # The gate. Not a flag that can be deleted -- a marker that can only be
        # added by somebody writing their own name into it.
        "reviewed": None,
        "sensors": sensors,
        "provenance": PROVENANCE_DERIVED,
        "notice": DOWNGRADE_NOTICE,
        DIVERGENT_KEY: [
            {"name": entry["name"], "present_on": entry.get("present_on"),
             "of": entry.get("of")}
            for entry in baseline.get("divergent") or []
            if entry.get("name") in set(divergent)
        ],
    }


def export_preamble(declaration: dict) -> list[str]:
    """What the export says about itself, for stderr and for a report.

    The dropped sensors are named here rather than counted. A count tells a
    reader that something was lost; a name tells them whether it was the one
    they were looking for.
    """
    lines = [
        f"{declaration['format']} candidate for platform "
        f"{declaration['platform']}, derived from {declaration['derived_from']}",
        DOWNGRADE_NOTICE,
    ]
    dropped = declaration.get(DIVERGENT_KEY) or []
    if dropped:
        named = ", ".join(
            f"{entry['name']} ({entry.get('present_on')} of {entry.get('of')})"
            for entry in dropped)
        lines.append(
            f"not declared, because the cohort disagreed about them: {named}. "
            f"Declaring one would expect it of every unit and charge a finding "
            f"to each unit that does not have it")
    lines.append(
        "This file asserts nothing yet. Add a reviewed marker -- "
        '"reviewed": {"by": "<name>", "on": "<date>"} -- or the referee will '
        "refuse it")
    return lines
