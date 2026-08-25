# fleet-sensor-baseline

**Which units differ from their cohort, and what changed on this unit across time.**

`bmc-sensor-audit` judges one machine against one declaration. This layer holds the
list of machines and the history of captures, and answers the two questions the
referee cannot.

**Released — 0.1.4**, tagged `v0.1.4`, Apache-2.0, on PyPI as
`fleet-sensor-baseline`.

---

## The problem

A tray reported forty-five sensors before a firmware update and forty-two after it.
Every remaining sensor is healthy. Every threshold is in range. A single-machine
audit finds nothing, because the machine agrees with itself — only the previous
capture disagrees, and nothing was holding it.

Scale that sideways: five trays in a rack of two thousand quietly stopped exposing
a fan sensor after a board revision. Each one, audited alone, looks fine.

Those are the two axes here. **Vertical** is one unit across time. **Horizontal**
is one unit against its cohort.

## Install

```
pip install fleet-sensor-baseline
```

The core has **no dependencies**. Ingest, baseline, outliers, drift, verdict and
validate are JSON and arithmetic, so the vertical and horizontal axes run on a jump
host with nothing provisioned. The collector needs the referee on PATH:

```
pip install "fleet-sensor-baseline[collect]"
```

## Use

```
# file the captures somebody gathered
fleet-sensor-baseline ingest --store fleet-store --payloads walks/ records/*.json

# what changed on one unit, across time and firmware
fleet-sensor-baseline drift --store fleet-store --unit h-0042

# derive a cohort baseline, then find the units that differ from it
fleet-sensor-baseline baseline --store fleet-store --model GB200-NVL-tray \
    --firmware-range ">=1.4,<1.5" --out baseline.json
fleet-sensor-baseline outliers --store fleet-store --baseline baseline.json

# the fleet run: every expected unit must have reported
fleet-sensor-baseline verdict --store fleet-store --expect-units racks.txt

# check any artifact against the format it declares
fleet-sensor-baseline validate baseline.json

# walk a rack of BMCs yourself, serially, and file what comes back
fleet-sensor-baseline collect --targets rack-17.json --store fleet-store \
    --collector-id rack-17
```

### Asking before walking

```
fleet-sensor-baseline collect --targets rack-17.json --store fleet-store \
    --collector-id rack-17 --etag-cache
```

The referee keeps one collection-ETag cache per BMC **surface** in the store and
asks each machine whether its sensor set changed before walking it — a handful of
requests instead of one per sensor.

**It proves membership, and a record filed from a skip says so.** The record
reuses the previous capture's payload and carries
`{"basis": "collection-etag", "proves": "membership"}`. That is exactly what
`drift`, `outliers` and `verdict` need, since all three read the name set; it is
not enough for a threshold audit, and the record is explicit rather than looking
like a fresh walk. A BMC that does not implement ETags is walked every time.

Needs `bmc-sensor-audit>=0.1.2`.

`collect` is the only subcommand that needs the referee on PATH. It walks one
BMC at a time with exponential backoff, because AST2600-class BMCs measure a
Redfish walk in seconds and a central plane that fans out to ten thousand of
them is a denial of service with a scheduler. **A walk that fails is a record,
emitted** — a unit that could not be walked reports *as* could-not-walk and
keeps its place in the denominator.

Credentials are named, never stored: a target carries `password_env`, the name
of an environment variable, and a `password` key in a targets file is refused
rather than ignored.

**A certificate pin is the opposite, and belongs in the file.** A SHA-256
fingerprint of a public certificate is public — anyone who can connect can
compute it — so it is not a credential but an *expectation*, and one that
changes should show up in a review diff. Declare it per target in a
`targets/2` file:

```json
{"unit_key": "h-0042", "base_url": "https://192.0.2.1",
 "pin_sha256": "AB:CD:...:EF"}
```

**A pin needs `targets/2` and version 2 exists for exactly that.** A reader
predating the key would ignore it and connect unpinned — a declaration met with
silence — and it refuses an unknown format outright, so the bump turns a silent
downgrade into a refusal.

A CA bundle is a path on one operator's disk and usually fleet-wide, so it is
`collect --cafile PATH` rather than a field in a list that goes into version
control.

## Exit codes

| code | means |
|---|---|
| `0` | clean |
| `1` | findings, and they are named |
| `2` | the check could not be completed, and it says which part |

Precedence is `max`, and **`2` beats `1` deliberately**. A run that found three
outliers and could not reach a fourth unit has not found three outliers; it has
found three and does not know about the fourth. Anything outside `{0,1,2}` from a
subprocess is read as `2` with the raw code kept beside it — *"exited 127"* is the
useful half of that sentence.

**"N units could not be walked" is printed, never omitted.** A fleet report that
renders incompleteness as silence renders a dead collector as a clean rack.

## What this is not

1. **Never a dashboard.** The verdict is an exit code. Visualisation may exist and
   is never the source of judgment. Silence cannot impersonate a pass.
2. **Never metric values.** This audits presence and configuration, not readings.
3. **Never majority-truth over declarations.** A fleet-derived baseline is an
   *additional, labeled, downgraded* declaration source. Wherever a manufacturer
   declaration exists, it wins.
4. **Never identity inside the referee's artifacts.** The walk payload carries no
   `unit_key`. Identity binds here.

### Point 3 is the one that matters

A baseline derived from a fleet **cannot see an absence the whole cohort shares.**
Two thousand trays that all lost the same sensor in the same firmware agree with
each other perfectly, and consensus reports them clean.

So every `fleet-baseline/2` carries this sentence as **part of the format**, and
every consumer prints it verbatim:

> This baseline was derived from the fleet, not declared by a manufacturer. It
> cannot see an absence the whole cohort shares.

`tests/test_scenarios.py::TestS2CommonModeBlindness` demonstrates the blindness on
purpose, and pairs it with `bmc-sensor-audit coverage` finding the same absence
against a manufacturer declaration. The pair of assertions *is* the precedence
rule, made executable.

## Formats

Six, each versioned and each with a shipped validator, because **the person who
receives the file is the one who needs to check it**:

| format | is |
|---|---|
| `fleet-sensor-baseline/fleet-record/1` | one capture of one unit |
| `fleet-sensor-baseline/fleet-baseline/1` | superseded; refused, because it dropped the sensors the cohort disagreed about |
| `fleet-sensor-baseline/fleet-baseline/2` | a derived declaration, labeled as such, carrying `divergent` |
| `fleet-sensor-baseline/summary/1` | a verdict, in the family's one vocabulary |
| `fleet-sensor-baseline/targets/1` | the collector's rack list |
| `fleet-sensor-baseline/targets/2` | the same, and may declare `pin_sha256` |

`fleet-sensor-baseline validate PATH` checks any of them, dispatching on the format
key the file declares rather than on a shape guessed from the fields present.

See [docs/formats.md](docs/formats.md).

## Storage

### When the cohort disagrees with itself

A sensor is **expected** at or above `--present-threshold` (0.99), **foreign** at
or below `--absent-threshold` (0.01), and between the two the cohort simply
disagrees. That middle band is reported once, against the cohort, naming the
minority -- and charged to no unit.

The band is not a refinement, it is a correction. Without it a sensor on 22 of 24
trays was below 0.99, so it left the baseline entirely, and every unit that
**had** it was reported as carrying something unexpected:

    tray-01 .. tray-24 (22 of them)   findings -- unexpected: Fan_CPU_2
    tray-06, tray-07                  clean

The 22 healthy trays were the outliers and the two that had lost the sensor
passed. A proportion is a coarse instrument at rack scale -- 0.99 of 24 is
23.76, so one deviant unit crosses it, and `--floor` admits cohorts from 20. The
README's own opening case inverted between 20 and about 100 units.

It now reads:

    divergent: Fan_CPU_2 present on 22 of 24 unit(s). The cohort disagrees with
    itself, so this is reported here and charged to no unit; the 2 that do not
    report it: tray-06, tray-07

`tests/test_scenarios.py::TestS6TheCohortThatDisagreesWithItself` pins it.

## Storage

An append-only JSONL index and a content-addressed store. Corrections are new
lines, never edits; the reader takes the latest per surface and capture time.
**The store does not deduplicate captures, and an earlier version of this line
said it did.** A `walk/1` carries per-fetch latencies and a capture time, so two
walks of one unchanged machine never share a digest. What avoids re-storing an
unchanged walk is not walking it — see `collect --etag-cache` below.

**No time-series database in 0.x**, and that is a measurement rather than a
preference: configuration drift is per-boot and per-firmware-event, not
per-second. Revisit when cross-month queries exist and are slow.

## A unit is a tuple

On NVIDIA-class platforms one physical unit answers on more than one BMC — a host
BMC and an HMC behind bmcweb aggregation. Two records differing only in `satellite`
are two surfaces of one machine, not two machines. Pairing across time is surface
to surface; presence across the cohort is the union across a unit's surfaces.

## The boundary

This package **never imports `bmc_sensor_audit`**. It reads exit codes, stdout and
the files the tool writes. One module is exempt — `collect/backends/mock.py`, which
imports the referee's `MockBMC`, and the distinction is the design: that is a fake
*machine*, standing in for the thing being walked, not the thing doing the walking.

`tests/test_boundary.py` asserts both halves by reading the source: that nothing
else imports it, and that the mock backend still does. Without the second, the
first would pass by finding nothing.

## Tests

| | count |
|---|---|
| tests collected | 275 |
| of those, requiring `bmc-sensor-audit` | 34 |

**The predicate**: `pytest --collect-only` over the test files git tracks, and
the same again with `-m seam` for the second row. Collection rather than a pass
tally, because a skip count is true only on the machine that measured it —
`tests/test_readme_counts.py` derives both and fails if either drifts.

Run it dependency-free and the 34 skip. Install the referee and they run.

**No pass/skip tally is quoted here on purpose.** The first version of this
section did, and both numbers were wrong within a day — not because tests
changed, but because the repository gained a tag and one check stopped skipping.
A tally is a fact about the machine that measured it; the collected counts above
are facts about the suite, and something derives them.

Every skip **says in prose why it could not run, and exits clean**. *Could not
check* is a different answer from *found nothing*, and a suite that reported the
two identically would let a missing dependency read as a green seam.

The dependency-free lane's walk fixtures are hand-built, which is a liability this
suite names rather than hides: a fixture written from a reading of a format agrees
with that format by construction. `tests/test_seam.py` is the answer — it generates
a walk with the referee's own reader and asserts the fixture still matches.

## Upstream

Pinned at `bmc-sensor-audit>=0.1.5,<0.2`, and the floor is **derived, not
chosen**. It has moved twice in a day, each time to consume something reported
from here:

- `>=0.1.2` for `--password-env`, so a credential never crosses argv, and for
  declaring an added aggregation prefix in one entry.
- `>=0.1.5` for `--pin-sha256` and `--cafile` — and for the two defects taking
  them found. A pin on an `http://` target was silently ignored (urllib picks a
  handler by scheme, so the pinned handler was never consulted), and once that
  was refused, the refusal escaped as a traceback exiting `1` — *findings* — so
  a collector read a misconfigured flag as a machine with problems.
- `>=0.1.3` for the `OUTCOME` line. The collector used to tell a skipped walk
  from a real one by matching a printed **sentence**, because that was the only
  signal there was — it worked and rested on nothing. 0.1.3 publishes a declared
  line as contract, and `tests/test_seam.py` now checks this layer's accepted
  values against the referee's own declared set rather than restating them.

**Four gaps reported from here were fixed there**, and 0.1.2 is where they
landed: conditional requests, certificate pinning, a password off argv, and a
prefix that was *added* being declarable at all. Raising the floor is what turns
them from a note into a capability. See
[docs/upstream-asks.md](docs/upstream-asks.md).

**None of the four was a bug.** Each was a surface that was never there — which
a test suite cannot find, because it asks whether what exists is correct. Only a
second program with a real job discovers that what it needed was missing.

## Licence

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
