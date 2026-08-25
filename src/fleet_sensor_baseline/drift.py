"""The vertical axis: what changed on one unit across time and firmware.

The 45-to-42 case. A tray reported forty-five sensors before a firmware update
and forty-two after it, every one of them healthy, and nothing in a
single-machine audit can see the difference -- the machine agrees with itself.
Only the previous capture disagrees.

**Pairing is surface to surface.** One physical unit answers on more than one
BMC on NVIDIA-class platforms, and pairing by `unit_key` alone would compare a
host BMC's capture against an HMC's and report the whole sensor set as having
been replaced.

**A firmware boundary is annotated, never assumed to be the cause.** The tool
says *these three went away, and the firmware changed here*. Which of those
facts explains the other is a question about a machine, and this is a report.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .exits import CLEAN, FINDINGS
from .walk import apply_prefix, common_prefix_shift


@dataclass
class Step:
    """One adjacent pair of captures on one surface."""
    surface: tuple[str, ...]
    before_at: str
    after_at: str
    gone: list[str] = field(default_factory=list)
    arrived: list[str] = field(default_factory=list)
    firmware_before: str | None = None
    firmware_after: str | None = None
    #: `(old, new)` when a single undeclared prefix rename explains the whole
    #: difference. Reported and still counted -- see below.
    prefix_shift: tuple[str, str] | None = None
    #: True when a declared `--aggregation-prefix` paired these names.
    paired_through_declared_prefix: bool = False

    @property
    def firmware_changed(self) -> bool:
        return (self.firmware_before is not None
                and self.firmware_after is not None
                and self.firmware_before != self.firmware_after)

    @property
    def exit_code(self) -> int:
        return FINDINGS if (self.gone or self.arrived) else CLEAN

    def to_dict(self) -> dict:
        row: dict = {
            "surface": list(self.surface),
            "before": self.before_at,
            "after": self.after_at,
            "exit_code": self.exit_code,
        }
        if self.gone:
            row["gone"] = self.gone
        if self.arrived:
            row["arrived"] = self.arrived
        if self.firmware_changed:
            row["firmware"] = {"before": self.firmware_before,
                               "after": self.firmware_after}
        if self.prefix_shift is not None:
            row["undeclared_prefix_shift"] = {"old": self.prefix_shift[0],
                                              "new": self.prefix_shift[1]}
        if self.paired_through_declared_prefix:
            row["paired_through_declared_prefix"] = True
        return row


def steps(ordered: list[tuple[str, set[str], str | None]],
          surface: tuple[str, ...],
          prefix_map: dict[str, str] | None = None) -> list[Step]:
    """Adjacent pairs of `(captured_at, names, firmware_version)`.

    Adjacent rather than first-to-last: a sensor that vanished at one firmware
    and came back at the next is a different fact from a sensor that was never
    missing, and an endpoints-only comparison cannot tell them apart.
    """
    prefix_map = prefix_map or {}
    out: list[Step] = []
    for (before_at, before_names, before_fw), (after_at, after_names, after_fw) in zip(
            ordered, ordered[1:]):
        mapped = {apply_prefix(name, prefix_map) for name in before_names}
        paired = bool(prefix_map) and mapped != before_names
        gone = sorted(mapped - after_names)
        arrived = sorted(after_names - mapped)
        step = Step(surface=surface, before_at=before_at, after_at=after_at,
                    gone=gone, arrived=arrived,
                    firmware_before=before_fw, firmware_after=after_fw,
                    paired_through_declared_prefix=paired and not (gone or arrived))
        if gone and arrived:
            # **Reported, never applied.** A rename this tool inferred is a
            # rename nobody declared, and pairing through it silently is how a
            # real mass-disappearance gets absorbed into a note. The operator
            # declares it with --aggregation-prefix, or the difference stands.
            step.prefix_shift = common_prefix_shift(set(mapped), set(after_names))
        out.append(step)
    return out
