"""Synthetic fleets, built without the referee installed.

**The walk payloads here are hand-built, and that is a liability this suite
names rather than hides.** A fixture written from a reading of the format can
drift away from what the producer actually writes, and nothing inside this
repository would notice. `tests/test_seam.py` is the answer: it generates a walk
with the referee's own reader and asserts this builder still agrees with it. The
rest of the suite stays dependency-free, which is what lets it run on a bring-up
bench where nothing was provisioned.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fleet_sensor_baseline.formats import RECORD_FORMAT
from fleet_sensor_baseline.store import Store, digest_bytes, ref_for
from fleet_sensor_baseline.walk import WALK_FORMAT


def walk(names, *, chassis=("1",), captured_at="2026-08-20T00:00:00Z",
         prefix="", path_prefix="/redfish/v1/Chassis/1/Sensors"):
    """A minimal `bmc-sensor-audit/walk/1` carrying the named sensors."""
    return {
        "format": WALK_FORMAT,
        "chassis": list(chassis),
        "shapes_seen": ["sensors"],
        "errors": [],
        "captured_at": captured_at,
        "fields_observed": False,
        "latencies": [],
        "sensors": [
            {"name": f"{prefix}{name}", "path": f"{path_prefix}/{name}",
             "reading": 21.5, "units": "Cel", "state": "Enabled",
             "health": "OK", "shape": "sensors",
             "resource": f"{path_prefix}/{name}", "thresholds": {}}
            for name in names
        ],
    }


def record(unit_key, *, captured_at, digest=None, topology=None, model=None,
           firmware=None, release=None, exit_code=0, detail=None,
           trigger="scheduled", collector_id="test-rack"):
    """One `fleet-record/1`, with only the keys a real emitter would write."""
    out = {
        "format": RECORD_FORMAT,
        "unit_key": unit_key,
        "captured_at": captured_at,
        "trigger": trigger,
        "collector": {"id": collector_id},
        "exit_code": exit_code,
    }
    if topology:
        out["topology"] = dict(topology)
    if model is not None:
        out["model"] = model
    if firmware is not None or release is not None:
        info = {}
        if firmware is not None:
            info["version"] = firmware
        if release is not None:
            info["release"] = release
        out["firmware"] = info
    if detail is not None:
        out["detail"] = detail
    if digest is not None:
        out["payload_digest"] = digest
        out["walk_ref"] = ref_for(digest)
    return out


class Fleet:
    """A store on disk, plus the shorthand for putting a unit into it."""

    def __init__(self, root: Path) -> None:
        self.store = Store(root)
        self.store.initialise()
        self.records: list[dict] = []

    def add(self, unit_key, names, *, captured_at="2026-08-20T00:00:00Z",
            **kwargs):
        payload = walk(names, captured_at=captured_at)
        raw = json.dumps(payload, sort_keys=True).encode("utf-8")
        digest = self.store.put_payload(raw)
        entry = record(unit_key, captured_at=captured_at, digest=digest,
                       **kwargs)
        self.records.append(entry)
        return entry

    def add_failed(self, unit_key, *, captured_at="2026-08-20T00:00:00Z",
                   detail="the BMC did not answer", **kwargs):
        entry = record(unit_key, captured_at=captured_at, exit_code=2,
                       detail=detail, **kwargs)
        self.records.append(entry)
        return entry

    def commit(self):
        self.store.append(self.records)
        self.records = []
        return self.store


@pytest.fixture
def fleet(tmp_path):
    return Fleet(tmp_path / "store")


@pytest.fixture
def cohort(fleet):
    """Twenty-five identical units -- above the default floor of twenty.

    The size is chosen so a baseline derived from it is legal without raising
    the floor: a test that had to pass `--floor 3` to work would be exercising
    the escape hatch instead of the rule.
    """
    names = ["Fan_CPU_1", "Fan_CPU_2", "Inlet_Temp", "PSU1_Input_Power"]
    for index in range(25):
        fleet.add(f"h-{index:04d}", names, model="tray", release="1.4.2",
                  firmware="GB200-fw-1.4.2")
    return fleet


def write_json(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path


def digest_of(payload) -> str:
    return digest_bytes(json.dumps(payload, sort_keys=True).encode("utf-8"))
