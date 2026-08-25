# What this layer cannot do through the referee's published surface

Three capabilities the specification asks for are **not reachable** by any
consumer of `bmc-sensor-audit 0.1.1`. They are recorded here rather than faked,
because a collector that claimed conditional requests while walking every BMC on
every pass would be lying in exactly the direction that costs a fleet the most.

Each entry names what was measured, what this repository does instead, and what
would close it upstream.

---

## 1. Conditional requests (ETag / `If-None-Match`)

**Measured.** `bmc-sensor-audit capture --help` on 0.1.1 offers `--target`,
`--out`, `--print-digest`, `--username`, `--password`, `--insecure` and
`--timeout`. There is no way to pass a prior ETag, and no way to read one back.

**What the specification wanted.** *ETag / conditional requests where the BMC
honors them* — so a collector on a weekly sweep over ten thousand BMCs does not
re-walk machines that have not changed.

**What this repository does instead.** Digest deduplication. A walk whose bytes
are already in the content store writes no new object and its record points at
the existing one. That saves storage — which on a homogeneous fleet is most of
the cost — and **saves nothing on the wire**. The BMC is still walked.

The difference is not cosmetic. AST2600-class BMCs measure a Redfish walk in
seconds, and the walk itself is the load this collector is serialized and backed
off to avoid.

**What would close it.** A `--if-none-match ETAG` on `capture`, and the response
ETag on stdout beside the digest. Exit `0` with no file written, or a distinct
code, for `304 Not Modified` — the three-valued contract already has room for
*nothing to report* to be distinguishable from *nothing changed*.

---

## 2. Certificate pinning toward BMCs

**Measured.** The only TLS control on `capture` is `--insecure`, which disables
verification entirely.

**What the specification wanted.** *pinned-cert read-only toward BMCs* — the
correct posture for a device that ships a self-signed certificate that never
changes and sits on a management network.

**What this repository does instead.** Nothing. `insecure` is passed through
when a target declares it, and a collector that wants pinning cannot express it.
This layer does not implement its own Redfish client to work around the gap:
duplicating the referee's walker is how two implementations of one thing start
disagreeing about what a sensor is.

**What would close it.** `--cafile PATH` or `--pin-sha256 FINGERPRINT` on
`capture`. The standard library supports both; the surface is what is missing.

---

## 3. A password that does not cross argv

**Measured.** `capture --password PASSWORD`. On a shared host, `ps` shows it to
every user for the lifetime of the walk — and a rack collector walks continuously.

**What this repository does instead.** A targets file carries `password_env`,
the **name** of an environment variable, and a literal `password` key is
*refused rather than ignored* — ignoring it would let a file that looks like it
configures authentication sit in version control holding a real credential while
quietly doing nothing. The value is read at the moment of the call.

That protects the file. It does not protect the process table: the value still
crosses argv on its way to the subprocess, and **no amount of care on this side
changes that.**

**What would close it.** `--password-env NAME`, `--password-file PATH`, or
reading a password from stdin. Any one of the three.

---

## 4. A prefix being ADDED cannot be declared in one entry

**Measured.** `parse_prefix_map` in `inventory/regression.py` refuses an empty
`OLD`: *"an empty new prefix is allowed and means the prefix was dropped"*. So
`HMC0_=` is expressible and `=HMC0_` is not.

**Why it matters.** Aggregation appearing *where there was none* is the common
direction: a satellite BMC comes online behind a bmcweb aggregator and every
sensor name gains a prefix. That is scenario S5 in this repository's
specification, and it is the one that cannot be said in a single declaration.

**What this repository does instead.** Carries the referee's dialect verbatim,
refusals included, and declares the rename **stem by stem** —
`Fan_CPU_=HMC0_Fan_CPU_`, `Inlet_=HMC0_Inlet_`. Being *more* permissive here
would be worse than sharing the limit: an operator's declaration would work in
one tool of this family and be rejected by the next, and one rename written
twice is one rename that will drift.

`tests/test_scenarios.py::TestS5ThePrefixChange::test_adding_a_prefix_cannot_be_declared_in_one_entry`
pins the limitation, so it fails the day it is lifted — which is when the
stem-by-stem workaround should be removed rather than kept out of habit.

**What would close it.** Accept an empty `OLD` and treat it as *prefix every
name*. The refusal's stated reason — that an empty old prefix would rewrite
every sensor on the machine — is a description of the intended behaviour rather
than an argument against it.

---

## Status

**None of these are filed upstream yet.** They are written down here first, with
the measurement attached, because a report that says *this does not work* and
cannot say what was run is a report the maintainer has to re-derive before
acting on it.
