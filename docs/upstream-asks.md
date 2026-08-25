# What this layer could not do, and what closed it

**Four found, fixed, released; three consumed. A fifth is open, and it was
created by the fix for the first.**

**The original four are FIXED, RELEASED and CONSUMED as of 2026-08-25.** They shipped in
`bmc-sensor-audit 0.1.2`; this repository's floor moved to `>=0.1.2` and the
workarounds below are gone from the code. The entries are kept because *what was
missing and why it mattered* is the part that does not survive in a changelog.

**Four items, of two kinds.** Three were capabilities no consumer of 0.1.1 could
reach (1-3). The fourth was a published surface that existed and could not
express the common case (4).

They are recorded here rather than faked, because a collector that claimed
conditional requests while walking every BMC on every pass would be lying in
exactly the direction that costs a fleet the most.

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

**What this repository did instead, and why it was worth less than claimed.**
Digest deduplication: a walk whose bytes are already stored writes no new object.
It saved nothing on the wire — the BMC was still walked — and **measurement later
showed it saved nothing on disk either.** A `walk/1` carries per-fetch latencies
and a capture time, so two walks of one unchanged machine never share a digest
and the store never collapses. The workaround was weaker than the sentence
describing it, which is the shape of workaround worth being suspicious of.

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

**All four filed 2026-08-25** against `bmc-sensor-audit`, each re-measured against
the published 0.1.1 artifact rather than a working tree first:

| ask | issue |
|---|---|
| conditional requests | [#2](https://github.com/james-sheen/bmc-sensor-audit/issues/2) |
| certificate pinning | [#3](https://github.com/james-sheen/bmc-sensor-audit/issues/3) |
| a password that avoids argv | [#4](https://github.com/james-sheen/bmc-sensor-audit/issues/4) |
| declaring an added prefix | [#5](https://github.com/james-sheen/bmc-sensor-audit/issues/5) |

They were written down here first, with the measurement attached, because a
report that says *this does not work* and cannot say what was run is a report the
maintainer has to re-derive before acting on it.

**All four FIXED and RELEASED in `bmc-sensor-audit 0.1.2`**, and consumed here:
the floor is `>=0.1.2`, the collector passes `--password-env`, and an added
prefix is declared in one entry.

**The test that was supposed to notice could not, and that is the lesson worth
keeping.** This file used to say the S5 scenario test would go red the day the
prefix refusal was lifted upstream. It did not. That test asserted the refusal
of **this repository's own parser** — a mirror of the referee's, which changes
only when somebody changes it here. When 0.1.2 lifted the refusal, the two
dialects silently parted and the whole suite stayed green: exactly the
divergence the mirroring exists to prevent.

**A claim about another program has to be measured against that program.**
`tests/test_seam.py::TestTheDialectsAgree` now parses the same spellings with
both parsers and compares the verdicts, so drift is caught in either direction.
That is the check that should have existed on the first commit.

Fix summary, as landed rather than as asked:

| # | landed as | used here |
|---|---|---|
| 2 | `capture --etag-cache PATH`, probing COLLECTIONS only — narrower than asked; see below | **yes** — `collect --etag-cache`, one cache per surface |
| 3 | `--cafile PATH` and `--pin-sha256 FINGERPRINT` | not yet — `targets/1` has no field for either |
| 4 | `--password-env NAME` and `--password-file PATH` | **yes** — no credential crosses argv |
| 5 | an empty `OLD` declares a prefix that was added | **yes** — one entry, not one per stem |

**Three of four taken; one still available and not taken.** `--cafile` and
`--pin-sha256` need fields in `targets/1` and a format bump, plus a decision
about whether a certificate pin belongs in a rack list that lives in version
control or in a sidecar the way the password already does. Not done is a
different sentence from *fixed*.

**Taking #2 immediately paid for itself twice**, and both are recorded rather
than absorbed:

- It exposed that the referee announces a skip **only in prose** -- exit 0, like
  a walk, with an absent file as the only structural difference. So the backend
  matches `\bsensor set unchanged\b`. That works and is the wrong kind of
  contract: prose can be reworded without it reading as a breaking change, while
  `walk/1`'s format string carries a written stability statement. Filed as
  [#6](https://github.com/james-sheen/bmc-sensor-audit/issues/6), **open**.
- A test assertion written expecting the old digest-dedup behaviour FAILED,
  which is how the *store collapses a homogeneous fleet* claim was finally
  measured and found false. See `store.py`.

## 5. Telling a skip apart from a walk (still open)

**Measured on 0.1.2.** With a populated cache and an unchanged BMC, `capture
--etag-cache` exits `0` and writes nothing; a normal walk also exits `0`. The
only affirmative signal is a printed sentence.

**What this repository does.** Matches the sentence, in
`collect/backends/subprocess_backend.py`, and says so in a comment rather than
letting it look structural.

**What would close it.** A distinct exit code for the skip, or one
machine-readable line beside the prose.

**This one is ours.** It was introduced by the fix for #1 above, written in this
family, and found within the hour by building the consumer -- which is the same
loop that produced the other four, run against our own work.

**#2 was implemented differently from the request, and the reason matters here.**
A per-resource conditional GET needs the previous BODY to use a 304, which means
a cache of raw Redfish payloads on disk — the fleet-inventory disclosure that
*the parse is the redaction* exists to prevent. Upstream probes only the
collections instead, which answers *has the sensor SET changed* and says so.
That is the question this layer asks, so the narrower fix is the useful one; a
consumer wanting configuration drift still needs the full walk.
