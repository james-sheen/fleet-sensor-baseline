"""The three formats this repository emits, and a validator for each.

Every validator ships for the same reason `bmc-sensor-audit`'s does: **the
person who RECEIVES the file is the one who needs to check it.** A control plane
ingesting thousands of records has to be able to refuse a malformed one using
the format's own words, not a shape it inferred from the files that happened to
arrive first.

Each returns problems rather than raising, so a caller reports all of them at
once instead of one per run.

**Malformation only.** A baseline with no sensors is a legal derivation over a
cohort whose units report none; a record with `exit_code` 2 and no digest is the
whole point of Sec. 6. A validator that rejects valid input is one people learn
to route around, and they take the malformed cases with them.

**Imports nothing outside the standard library, and in particular not
`bmc_sensor_audit`.** These files are JSON and checking one is arithmetic.
"""

from __future__ import annotations

import re
from typing import Any

RECORD_FORMAT = "fleet-sensor-baseline/fleet-record/1"
BASELINE_FORMAT = "fleet-sensor-baseline/fleet-baseline/1"
SUMMARY_FORMAT = "fleet-sensor-baseline/summary/1"
#: The collector's input. Not one of the three formats the specification
#: enumerates -- it is an implementation necessity of `collect --targets FILE`,
#: and it ships with a validator for the same reason the other three do.
TARGETS_FORMAT = "fleet-sensor-baseline/targets/1"
#: **Version 2 exists for one reason: a declaration an older reader would
#: IGNORE.** `pin_sha256` says *require exactly this certificate*. A reader that
#: does not know the key would drop it and connect unpinned -- a security
#: expectation stated by an operator and met with silence, which is the failure
#: mode this whole family is pointed at.
#:
#: An older build refuses an unknown format outright, so bumping is what turns a
#: silent downgrade into a refusal. `targets/1` stays valid, and stays the right
#: choice for a rack list that pins nothing.
TARGETS_V2_FORMAT = "fleet-sensor-baseline/targets/2"

#: Both are readable by this build. The tuple is ordered oldest-first so a
#: message can name them in the order somebody would have written them.
TARGETS_FORMATS = (TARGETS_FORMAT, TARGETS_V2_FORMAT)

#: The pin, in the spelling `openssl x509 -fingerprint -sha256` prints, with or
#: without colons and in either case.
PIN_SHA256 = re.compile(r"^(?:[0-9A-Fa-f]{2}:){31}[0-9A-Fa-f]{2}$|^[0-9A-Fa-f]{64}$")

#: Every format key this build writes and reads, for `validate` to dispatch on.
FORMATS = (RECORD_FORMAT, BASELINE_FORMAT, SUMMARY_FORMAT, TARGETS_FORMAT,
           TARGETS_V2_FORMAT)

#: **Part of the format, not of the renderer.** Every consumer that judges
#: against a `fleet-baseline/1` prints this sentence. A fleet-derived baseline
#: is blind to an absence the whole cohort shares -- which is the founding
#: problem of this family, fleet-sized -- and a reader who is not told that will
#: read a clean outlier report as a clean fleet.
DOWNGRADE_NOTICE = (
    "This baseline was derived from the fleet, not declared by a manufacturer. "
    "It cannot see an absence the whole cohort shares.")

#: What a record says about where its capture came from. `fleet-derived` is the
#: downgraded one; it exists so a summary can say which kind of truth it used.
PROVENANCE_DERIVED = "fleet-derived"

#: The digest form the referee's `capture --print-digest` prints, and the only
#: one this build stores. Checked by shape here and against the bytes on ingest.
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")

#: Triggers a record may declare. Open on purpose -- an operator's cadence is
#: theirs -- but the three the spec names are what the collector emits, and an
#: unknown one is noted rather than refused.
KNOWN_TRIGGERS = ("maintenance-event", "scheduled", "manual")


def _kind(value: Any) -> str:
    return type(value).__name__


def _base_problems(payload: Any, expected: str) -> list[str] | None:
    """The two checks every validator starts with, or None if they passed."""
    if not isinstance(payload, dict):
        return [f"the artifact is {_kind(payload)}, not an object"]
    declared = payload.get("format")
    if declared != expected:
        return [f"format is {declared!r}, this build reads {expected!r}"]
    return None


def validate_record(payload: Any) -> list[str]:
    """Check one `fleet-record/1`.

    **`unit_key` is opaque.** It is the operator's naming and is never parsed
    for meaning -- only required to be a non-empty string, because it is the
    identity every other row is grouped by and a record without one cannot be
    filed at all.
    """
    problems = _base_problems(payload, RECORD_FORMAT)
    if problems is not None:
        return problems

    unit_key = payload.get("unit_key")
    if not isinstance(unit_key, str) or not unit_key:
        problems = ["'unit_key' is missing or is not a non-empty string; it is "
                    "the identity every other row is grouped by"]
        return problems

    problems = []
    topology = payload.get("topology")
    if topology is not None and not isinstance(topology, dict):
        problems.append("'topology' is present and is not an object")
    elif isinstance(topology, dict):
        for key, value in topology.items():
            if not isinstance(value, str):
                problems.append(f"topology[{key!r}] is {_kind(value)}, not a "
                                f"string; a surface name is a name")

    captured_at = payload.get("captured_at")
    if not isinstance(captured_at, str) or not captured_at:
        # The vertical axis is ordered by this. A record without it cannot take
        # part in `drift` at all, and silently sorting it to the front or the
        # back would be this tool inventing a history it was not given.
        problems.append("'captured_at' is missing or is not a non-empty string; "
                        "the vertical axis is ordered by it")

    exit_code = payload.get("exit_code", 0)
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        problems.append(f"'exit_code' is {exit_code!r}, not an integer")
    elif exit_code not in (0, 1, 2):
        problems.append(f"'exit_code' is {exit_code}, which is not one of 0/1/2")
    elif exit_code == 0:
        # **A clean record must carry its payload.** The exit-2 record of Sec. 6
        # legitimately has neither digest nor ref -- a unit that could not be
        # walked reports AS could-not-walk. A clean one with neither is a record
        # claiming a successful capture it cannot produce.
        digest = payload.get("payload_digest")
        if not isinstance(digest, str) or not DIGEST.match(digest):
            problems.append(
                "a record with exit_code 0 carries no valid 'payload_digest' "
                "(sha256: and 64 hex); a clean capture that cannot produce its "
                "payload is a claim, not a record")
        if not isinstance(payload.get("walk_ref"), str):
            problems.append("a record with exit_code 0 carries no 'walk_ref'")

    for key in ("firmware", "collector", "unchanged"):
        if key in payload and not isinstance(payload[key], dict):
            problems.append(f"{key!r} is present and is not an object")
    unchanged = payload.get("unchanged")
    if isinstance(unchanged, dict) and not unchanged.get("proves"):
        # A record that reused an earlier payload has to say what was actually
        # established. Without it a reader cannot tell a re-walk from a skip,
        # and the two answer different questions.
        problems.append("'unchanged' is present and does not say what it "
                        "'proves'; a reused payload without a basis is a "
                        "capture claiming more than was checked")
    if "trigger" in payload and not isinstance(payload["trigger"], str):
        problems.append("'trigger' is present and is not a string")
    return problems


def validate_baseline(payload: Any) -> list[str]:
    """Check one `fleet-baseline/1`.

    **The notice and the provenance are checked as format, not as prose.** A
    baseline that lost its downgrade notice validates as a manufacturer
    declaration everywhere downstream, and the loss is invisible: every other
    field still reads correctly.
    """
    problems = _base_problems(payload, BASELINE_FORMAT)
    if problems is not None:
        return problems

    problems = []
    if payload.get("provenance") != PROVENANCE_DERIVED:
        problems.append(
            f"'provenance' is {payload.get('provenance')!r}; a "
            f"{BASELINE_FORMAT} is always {PROVENANCE_DERIVED!r}. This is the "
            f"field that stops a derived baseline being read as a declaration")
    if payload.get("notice") != DOWNGRADE_NOTICE:
        problems.append(
            "'notice' is missing or reworded. It is part of the format: a "
            "consumer prints it verbatim, and a baseline without it cannot say "
            "what kind of truth it is")

    derived = payload.get("derived")
    if not isinstance(derived, dict):
        problems.append("'derived' is missing or is not an object; a baseline "
                        "that cannot show its denominator is an assertion")
    else:
        units = derived.get("units")
        if not isinstance(units, int) or isinstance(units, bool) or units < 0:
            problems.append("derived['units'] is missing or is not a "
                            "non-negative integer; it is the denominator")
        threshold = derived.get("present_threshold")
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
            problems.append("derived['present_threshold'] is missing or is not "
                            "a number")
        elif not 0.0 < threshold <= 1.0:
            problems.append(f"derived['present_threshold'] is {threshold}, "
                            f"outside (0, 1]")
        window = derived.get("captured_between")
        if window is not None and (not isinstance(window, list) or len(window) != 2
                                   or not all(isinstance(t, str) for t in window)):
            problems.append("derived['captured_between'] is present and is not "
                            "a pair of timestamp strings")

    scope = payload.get("scope")
    if not isinstance(scope, dict):
        problems.append("'scope' is missing or is not an object; a baseline "
                        "that does not say what it covers cannot be applied")

    sensors = payload.get("sensors")
    if not isinstance(sensors, list):
        return problems + ["'sensors' is missing or is not a list"]
    for index, item in enumerate(sensors):
        where = f"sensors[{index}]"
        if not isinstance(item, dict):
            problems.append(f"{where} is {_kind(item)}, not an object")
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            problems.append(f"{where} carries no 'name'")
            continue
        ratio = item.get("present_ratio")
        if not isinstance(ratio, (int, float)) or isinstance(ratio, bool):
            problems.append(f"{where} ({name}) has a non-numeric "
                            f"'present_ratio'")
        elif not 0.0 <= ratio <= 1.0:
            problems.append(f"{where} ({name}) has a 'present_ratio' of "
                            f"{ratio}, outside [0, 1]")
    return problems


def validate_summary(payload: Any) -> list[str]:
    """Check one `summary/1` -- the shape every subcommand renders its verdict in."""
    problems = _base_problems(payload, SUMMARY_FORMAT)
    if problems is not None:
        return problems

    problems = []
    code = payload.get("exit_code")
    if not isinstance(code, int) or isinstance(code, bool) or code not in (0, 1, 2):
        problems.append(f"'exit_code' is {code!r}, which is not one of 0/1/2")
    if not isinstance(payload.get("verdict"), str):
        problems.append("'verdict' is missing or is not a string")
    for key in ("decided_by", "rows", "missing"):
        if not isinstance(payload.get(key), list):
            problems.append(f"{key!r} is missing or is not a list")
    return problems


def validate_targets(payload: Any) -> list[str]:
    """Check a `targets/1` -- the collector's rack list.

    **`password` is refused as a key, not ignored.** Ignoring it would let a
    file that looks like it configures authentication sit in a repository
    holding a real credential while quietly doing nothing, which is the worst of
    both outcomes. `password_env` names an environment variable instead, so the
    file carries the name and the host carries the value.
    """
    if not isinstance(payload, dict):
        return [f"the targets file is {_kind(payload)}, not an object"]
    declared = payload.get("format")
    if declared not in TARGETS_FORMATS:
        return [f"format is {declared!r}, this build reads "
                + " or ".join(repr(f) for f in TARGETS_FORMATS)]
    pins_allowed = declared == TARGETS_V2_FORMAT

    targets = payload.get("targets")
    if not isinstance(targets, list):
        return ["'targets' is missing or is not a list"]
    if not targets:
        return ["'targets' is empty; a collection run over no targets would "
                "report a clean rack it never touched"]

    problems = []
    seen: set[tuple] = set()
    for index, item in enumerate(targets):
        where = f"targets[{index}]"
        if not isinstance(item, dict):
            problems.append(f"{where} is {_kind(item)}, not an object")
            continue
        for key in ("unit_key", "base_url"):
            if not isinstance(item.get(key), str) or not item[key]:
                problems.append(f"{where} carries no {key!r}")
        pin = item.get("pin_sha256")
        if pin is not None:
            if not pins_allowed:
                # **The whole reason version 2 exists.** Accepting it here would
                # mean an older build reads the same file, silently ignores the
                # pin and connects unverified -- the operator's declaration met
                # with silence.
                problems.append(
                    f"{where} declares 'pin_sha256' and this file is "
                    f"{TARGETS_FORMAT!r}. A reader that predates the key would "
                    f"ignore it and connect unpinned; declare "
                    f"{TARGETS_V2_FORMAT!r} so such a reader refuses the file "
                    f"instead")
            elif not str(item.get("base_url", "")).lower().startswith("https://"):
                # **Defence in depth, and it belongs here too.** The referee
                # refuses this from 0.1.4, but a rack list is reviewed long
                # before it is run: catching it in the file is catching it where
                # somebody is looking. Before 0.1.4 the flag was built and
                # silently dropped, and the walk succeeded unverified.
                problems.append(
                    f"{where} declares 'pin_sha256' and a base_url that is not "
                    f"https. Nothing would verify the connection")
            elif not isinstance(pin, str) or not PIN_SHA256.match(pin):
                problems.append(
                    f"{where} has a 'pin_sha256' that is not a SHA-256 "
                    f"fingerprint (64 hex, colons optional)")
            if item.get("insecure"):
                # Two answers to one question. A pin IS the verification, and
                # `insecure` turns verification off; whichever won would be a
                # guess about which the operator meant.
                problems.append(
                    f"{where} declares both 'pin_sha256' and 'insecure'. A pin "
                    f"is the verification and 'insecure' removes it")
        if "password" in item:
            problems.append(
                f"{where} carries a 'password'. Use 'password_env' and name an "
                f"environment variable: a credential in a targets file is a "
                f"credential in version control")
        topology = item.get("topology")
        if topology is not None and not isinstance(topology, dict):
            problems.append(f"{where} has a 'topology' that is not an object")
            continue
        if isinstance(item.get("unit_key"), str):
            key = (item["unit_key"],) + tuple(sorted((topology or {}).items()))
            if key in seen:
                problems.append(
                    f"{where} repeats the surface {item['unit_key']!r}; two "
                    f"targets for one surface is a rack list that cannot say "
                    f"which walk it describes")
            seen.add(key)
    return problems


#: Format key -> validator, so `validate PATH` dispatches on what the file says
#: it is rather than on what the caller guessed from the filename.
VALIDATORS = {
    RECORD_FORMAT: validate_record,
    BASELINE_FORMAT: validate_baseline,
    SUMMARY_FORMAT: validate_summary,
    TARGETS_FORMAT: validate_targets,
}


def validate_any(payload: Any) -> tuple[str | None, list[str]]:
    """`(format_key, problems)` for any artifact this repository writes.

    An unknown or absent format key is a problem in itself and is reported as
    one: guessing the shape from the fields present is how a validator ends up
    checking a `fleet-record/1` against the baseline rules and passing it.
    """
    if not isinstance(payload, dict):
        return None, [f"the artifact is {_kind(payload)}, not an object"]
    declared = payload.get("format")
    if declared not in VALIDATORS:
        return None, [
            f"format is {declared!r}; this build reads "
            + ", ".join(repr(f) for f in FORMATS)]
    return declared, VALIDATORS[declared](payload)
