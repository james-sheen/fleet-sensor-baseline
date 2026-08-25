"""How a collector reaches a BMC.

Two backends, and the difference between them is the architectural boundary of
this repository rather than a convenience:

- `subprocess_backend` runs the published `bmc-sensor-audit` command and reads
  its exit code, its stdout and the file it wrote. Nothing else. That is the
  surface a real fleet has.
- `mock` stands up the referee's own `MockBMC` -- **a fake MACHINE, not a fake
  referee.** It is the one module in this package permitted to import
  `bmc_sensor_audit`, and `tests/test_boundary.py` asserts both halves: that
  nothing else imports it, and that this one still does. Without the second
  assertion the first would pass over a package that had simply stopped
  exercising the seam.
"""

from __future__ import annotations

__all__ = ["subprocess_backend"]
