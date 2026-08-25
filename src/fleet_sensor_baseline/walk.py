"""Reading `bmc-sensor-audit/walk/1` through its published surface.

**This module never imports `bmc_sensor_audit`, and that is the architectural
constraint of the whole repository.** If this layer could reach into the
referee's internals, a change that broke the referee's published output while
leaving its internals intact would still pass here -- and the referee's published
output is the only thing a real fleet ever sees. `tests/test_boundary.py`
asserts the absence by reading the source, because the alternative is trusting a
convention, and that is how conventions get broken by someone who did not know
they existed.

What is consumed here is the format string, the key names, and nothing else.
The format string is checked daily by a canary rather than assumed: it is a
published stability statement, and a statement is a claim.
"""

from __future__ import annotations

from typing import Any, Iterable

#: The referee's walk format, quoted rather than imported. A canary asserts the
#: published tool still writes exactly this.
WALK_FORMAT = "bmc-sensor-audit/walk/1"


class WalkError(Exception):
    """A walk this layer cannot read. Reported, never raised past the CLI."""


def is_walk(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("format") == WALK_FORMAT


def sensor_names(payload: Any) -> set[str]:
    """The set of sensor names a walk reports.

    **Presence, never readings.** This layer audits which sensors exist and how
    they are configured; what they measure is out of scope by construction, and
    keeping the reading out of the returned type is what makes that structural
    rather than a rule somebody has to remember.
    """
    return {sensor["name"] for sensor in _sensors(payload)}


def sensor_paths(payload: Any) -> dict[str, str]:
    """`{name: path}` for sensors that carry a path.

    The URI is what survives an aggregation-prefix rename: on a bmcweb
    aggregating BMC the satellite prefix moves into the name while the resource
    path keeps its shape, so the pair of records is pairable by path when it is
    not pairable by name.
    """
    return {sensor["name"]: sensor["path"]
            for sensor in _sensors(payload)
            if isinstance(sensor.get("path"), str)}


def captured_at(payload: Any) -> str | None:
    value = payload.get("captured_at") if isinstance(payload, dict) else None
    return value if isinstance(value, str) else None


def _sensors(payload: Any) -> Iterable[dict]:
    if not is_walk(payload):
        declared = payload.get("format") if isinstance(payload, dict) else None
        raise WalkError(
            f"format is {declared!r}, this build reads {WALK_FORMAT!r}")
    sensors = payload.get("sensors")
    if not isinstance(sensors, list):
        raise WalkError("'sensors' is missing or is not a list")
    for index, sensor in enumerate(sensors):
        if not isinstance(sensor, dict) or not isinstance(sensor.get("name"), str):
            raise WalkError(f"sensors[{index}] carries no name")
        yield sensor


def parse_prefix_map(pairs: Iterable[str]) -> dict[str, str]:
    """`OLD=NEW` strings into a mapping, refusing the ambiguous ones.

    **Deliberately the referee's dialect, refusals included**, because an
    operator's declaration has to mean the same thing in both tools. A rename
    written twice is a rename that will drift.

    An empty NEW means the prefix was dropped. An empty OLD means it was ADDED,
    which is the direction aggregation actually goes when a satellite BMC
    appears behind an aggregator. `=` alone declares neither and is refused.

    **The empty OLD was refused here until 2026-08-25, mirroring a refusal
    upstream that this repository asked to have lifted** (`bmc-sensor-audit` #5,
    released in 0.1.2). Mirroring it was right; noticing that it had been lifted
    was not automatic, and `tests/test_seam.py` now compares the two dialects
    directly rather than asserting anything about upstream from this side.
    """
    mapping: dict[str, str] = {}
    for pair in pairs:
        old, sep, new = pair.partition("=")
        if not sep:
            raise WalkError(
                f"--aggregation-prefix {pair!r} is not OLD=NEW. The old prefix "
                f"is the one in the earlier capture; an empty new prefix is "
                f"allowed and means the prefix was dropped, and an empty OLD is "
                f"allowed and means a prefix was added to every name")
        if not old and not new:
            raise WalkError(
                f"--aggregation-prefix {pair!r} has neither an old nor a new "
                f"prefix, so it declares no rename at all")
        if old in mapping and mapping[old] != new:
            raise WalkError(
                f"{old!r} is declared as both {mapping[old]!r} and {new!r}; two "
                f"answers to one rename is a declaration that cannot be applied")
        mapping[old] = new
    return mapping


def apply_prefix(name: str, mapping: dict[str, str]) -> str:
    """Rewrite a declared prefix, longest match first.

    Longest-first because `HMC_` and `HMC_0_` are both plausible declarations on
    one machine, and shortest-first would rewrite the second with the first and
    then report the difference it just created.
    """
    for old in sorted(mapping, key=len, reverse=True):
        if name.startswith(old):
            return mapping[old] + name[len(old):]
    return name


def common_prefix_shift(before: set[str], after: set[str]) -> tuple[str, str] | None:
    """An undeclared prefix rename, if one explains the whole difference.

    Returns `(old, new)` when every name that vanished and every name that
    appeared are the same set under a single prefix substitution. **Reported,
    never applied** -- the referee's rule, inherited: a rename this layer
    inferred is a rename nobody declared, and silently pairing through it is how
    a real mass-disappearance gets absorbed into a note. `drift` prints it and
    still counts the difference until an operator declares it.
    """
    gone, arrived = sorted(before - after), sorted(after - before)
    if not gone or len(gone) != len(arrived):
        return None
    old, new = _shared_head(gone), _shared_head(arrived)
    # Trim the part the two heads agree on, so the reported rename is the
    # substitution an operator would DECLARE. Without this, `Fan_1` becoming
    # `HMC0_Fan_1` is reported as `Fan_` -> `HMC0_Fan_`: exact, and not the
    # sentence anybody would type into `--aggregation-prefix`.
    while old and new and old[-1] == new[-1]:
        old, new = old[:-1], new[:-1]
    if old == new:
        return None
    if sorted(new + name[len(old):] for name in gone) != arrived:
        return None
    return old, new


def _shared_head(names: list[str]) -> str:
    head = names[0]
    for name in names[1:]:
        while not name.startswith(head):
            head = head[:-1]
            if not head:
                return ""
    return head
