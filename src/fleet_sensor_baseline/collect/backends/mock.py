"""A rack of fake machines, for exercising the collector end to end.

**This is the one module in the package that may import `bmc_sensor_audit`, and
the distinction is the design.** `MockBMC` is a fake *machine*. It stands in for
the thing being walked, not for the thing doing the walking. Importing a fake
machine says nothing about whether this layer can read the referee's real
output; importing the referee would let every test here pass over a tool whose
published surface had changed underneath it.

`tests/test_boundary.py` asserts the import is present here as well as absent
everywhere else -- because if nothing imported the referee anywhere, the absence
tests would pass by finding nothing and the boundary would be untested.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from ...exits import CLEAN, INCOMPLETE
from ..collector import Capture, Target

#: Injected failures, `{unit_key: (exit_code, detail)}`, so a test can make one
#: BMC in a rack unreachable and assert the run still files a record for it.
Failures = dict


class MockRackBackend:
    """Serves `walk/1` from `MockBMC` instances, one per surface.

    The walk is produced by the referee's own reader against the referee's own
    mock server, so what this backend returns is the real published shape rather
    than a hand-written approximation of it. A fixture invented here could never
    falsify a change in the format it is imitating.
    """

    def __init__(self, machines: dict[str, Any], *,
                 failures: Failures | None = None,
                 slow: dict[str, float] | None = None,
                 sleep: Callable[[float], None] | None = None,
                 unchanged: set | None = None) -> None:
        self.machines = machines
        self.failures = failures or {}
        self.slow = slow or {}
        self.sleep = sleep
        #: Surfaces whose BMC answers *set unchanged* when asked with a cache.
        self.unchanged = unchanged or set()
        self.caches: list[str | None] = []
        #: Every capture attempted, in order. The evidence a test uses to assert
        #: the run was serialized -- an assertion about observed order, not
        #: about which concurrency library was imported.
        self.calls: list[str] = []

    def capture(self, target: Target, etag_cache: str | None = None) -> Capture:
        key = _surface_key(target)
        self.calls.append(key)
        #: Every cache path this backend was handed, so a test can assert the
        #: paths are PER SURFACE rather than shared -- which is the whole reason
        #: the store derives one per surface.
        self.caches.append(etag_cache)
        if etag_cache is not None and key in self.unchanged:
            return Capture(CLEAN, unchanged=True,
                           detail="the BMC reports its sensor set unchanged; "
                                  "not re-walked")

        delay = self.slow.get(key)
        if delay and self.sleep:
            self.sleep(delay)

        if key in self.failures:
            code, detail = self.failures[key]
            return Capture(code, detail=detail)

        machine = self.machines.get(key)
        if machine is None:
            return Capture(INCOMPLETE,
                           detail=f"no mock machine is configured for {key}")

        raw = json.dumps(walk_of(machine), sort_keys=True).encode("utf-8")
        from ...store import digest_bytes
        return Capture(CLEAN, raw=raw, reported_digest=digest_bytes(raw))


def walk_of(machine: Any) -> dict:
    """Walk a `MockBMC` with the referee's real reader and return `walk/1`.

    Imported inside the function rather than at module scope so that merely
    importing this package does not require the referee to be installed: the
    collector's production path is a subprocess and must keep working on a host
    where nothing was pip-installed at all.
    """
    from bmc_sensor_audit.inventory.redfish import RedfishClient, walk_chassis
    from bmc_sensor_audit.testing.mock_redfish import serve

    with serve(machine) as base_url:
        client = RedfishClient(base_url)
        walk = walk_chassis(client)
    return walk.to_dict()


def _surface_key(target: Target) -> str:
    if not target.topology:
        return target.unit_key
    tail = ",".join(f"{k}={target.topology[k]}" for k in sorted(target.topology))
    return f"{target.unit_key}[{tail}]"
