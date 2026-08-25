# Security

## Reporting

Open an issue at
https://github.com/james-sheen/fleet-sensor-baseline/issues. If the finding
should not be public first, say so in the issue title and leave the detail out —
a way to make contact privately will be arranged in the thread.

## What this tool touches

Read-only, and only two things:

- **Files you point it at** — walk payloads, records, baselines, expectation
  lists. It writes only inside the `--store` directory and the `--out` paths you
  name.
- **BMCs, through `bmc-sensor-audit`** — and only when you run `collect`. This
  package contains no HTTP client of its own; the walk is the referee's, run as
  a subprocess. The BMC is never asked to run an agent.

## Credentials

A targets file carries **`password_env`, the name of an environment variable**,
never a password. A literal `password` key is **refused, not ignored** — ignoring
it would let a file that looks like it configures authentication sit in version
control holding a real credential while doing nothing.

**One exposure is known and not fixable here.** `bmc-sensor-audit capture` accepts
`--password` on the command line, so the value crosses argv where `ps` can read
it on a shared host. This tool reads the value late and passes it once, which
narrows the window and does not close it. Closing it needs a `--password-env`,
`--password-file` or stdin surface on the referee; the ask is written up with its
measurement in [docs/upstream-asks.md](docs/upstream-asks.md).

Until then: run collectors on hosts where the process table is not shared, or use
BMC accounts scoped to read-only Redfish and rotate them.

## What captures contain

Walk payloads carry **parsed sensor data only** — names, readings, units, states,
thresholds. That is a property of the referee, not of this tool: *the parse is
the redaction*, and a raw Redfish chassis walk would carry serial numbers, part
numbers, asset tags and MAC addresses that the parsed form does not.

**A sensor NAME can still embed a hostname on some platforms.** Read a capture
before publishing one.

**Identity lives in this layer's records, not in the payloads.** `unit_key`,
`topology`, `model` and collector identity are in `fleet-record/1` files. Those
name your machines. Treat the record index as more sensitive than the content
store, and note that the content store deduplicates — an identical walk from two
thousand trays is one object, which is a storage property and not an anonymity
one.

## TLS

`insecure: true` on a target disables certificate verification, because BMCs ship
self-signed certificates as a rule. It is **opt-in per target** and has to be
typed. Certificate pinning is not available; see the upstream asks.
