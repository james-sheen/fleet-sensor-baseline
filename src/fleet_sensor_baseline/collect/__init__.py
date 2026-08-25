"""The rack collector: BMC endpoints in, `fleet-record/1` out.

**Serialized and low-concurrency, on purpose.** AST2600-class BMCs measure a
Redfish walk in seconds, and a central plane that fans out to ten thousand of
them directly is a denial of service with a scheduler. One collector per rack or
per failure domain, walking its targets one at a time with exponential backoff.

**A walk that fails is a record, emitted.** A unit that could not be walked
reports *as* could-not-walk, at exit 2, and keeps its place in the denominator.
The alternative -- a unit that simply does not appear -- is indistinguishable
from a unit nobody was asked to walk, and that is the shape of every silent
fleet-wide failure this repository exists to make impossible.

**The BMC is never asked to run an agent.** Read-only Redfish, from outside.
"""

from __future__ import annotations

from .collector import (Capture, CollectError, Collector, Target,
                        read_targets)

__all__ = ["Capture", "CollectError", "Collector", "Target", "read_targets"]
