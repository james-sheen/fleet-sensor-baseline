# Formats

Six this build reads, each versioned in its own key and each with a validator
that ships, because **the person who receives the file is the one who needs to
check it.** A control plane ingesting thousands of records has to be able to
refuse a malformed one using the format's own words, not a shape it inferred
from the files that happened to arrive first.

| format | is |
|---|---|
| `fleet-sensor-baseline/fleet-record/1` | one capture of one unit |
| `fleet-sensor-baseline/fleet-baseline/1` | **superseded; refused, not upgraded** |
| `fleet-sensor-baseline/fleet-baseline/2` | a derived declaration, carrying `divergent` |
| `fleet-sensor-baseline/summary/1` | a verdict, in the family's one vocabulary |
| `fleet-sensor-baseline/targets/1` | the collector's rack list |
| `fleet-sensor-baseline/targets/2` | the same, and may declare `pin_sha256` |

and one it **writes for somebody else to read**:
`bmc-sensor-audit/fleet-baseline/1`, produced by `baseline --for-referee`. That
one is not validated here; the tool that consumes it is the tool that checks it.

`tests/test_formats.py::TestThisDocumentNamesEveryFormat` derives this list from
the code. It exists because the list was hand-written and went stale: for two
releases this page opened *Four*, presented `fleet-baseline/1` as current, and
did not mention `targets/2` or the `divergent` band at all — while the README,
whose count IS pinned by a test, said six. Two records of one fact, and the
unguarded one drifted.

Every validator returns *problems* rather than raising, so a caller reports all
of them at once instead of one per run. Every one checks **malformation only**:
a validator that rejects valid input is one people learn to route around, and
they take the malformed cases with them.

```
fleet-sensor-baseline validate PATH...
```

dispatches on the format key the file declares — never on a shape guessed from
the fields present, because a `walk/1` has both `format` and `sensors` and would
sail through a guess.

---

## `fleet-sensor-baseline/fleet-record/1`

One capture of one unit.

```json
{
  "format": "fleet-sensor-baseline/fleet-record/1",
  "unit_key": "opaque-string-chosen-by-operator",
  "topology": {"host": "h-0042", "satellite": "hmc-0"},
  "captured_at": "2026-08-22T04:10:00Z",
  "model": "GB200-NVL-tray",
  "firmware": {"version": "GB200-fw-1.4.2", "release": "1.4.2",
               "source": "redfish:/UpdateService"},
  "trigger": "maintenance-event",
  "exit_code": 0,
  "payload_digest": "sha256:...",
  "collector": {"id": "rack-17"}
}
```

`unit_key` is **opaque to this tool** — the operator's naming, required to be a
non-empty string and never parsed for meaning.

`topology` exists because on NVIDIA-class platforms one physical unit answers on
more than one BMC. **A unit is the tuple**: two records differing only in
`satellite` are two surfaces of one machine, not two machines.

`walk_ref` is **derived, not supplied**. It is `cas/sha256/<hex>` -- a pure
function of `payload_digest` -- so requiring it made a producer know the store's
internal directory layout to file a record, and wrote one fact twice with nothing
comparing the copies: a record naming a real digest beside a ref pointing at an
object that did not exist was accepted and stored clean. It may still be supplied
for records already written, and it must then agree.

**Identity lives here and only here.** The walk payload behind the digest
carries none — *the parse is the redaction*, upstream, by design.

### The two fields the specification did not enumerate

`model` and `firmware.release` are additions, and both exist so the **scope of a
baseline can be matched on declared fields rather than inferred ones**:

- `model` is compared as an opaque string. It is never derived from a sensor
  name or a URI.
- `firmware.release` is a dotted numeric version, and it is the **only** thing a
  `--firmware-range` compares against. A vendor string like `GB200-fw-1.4.2` is
  never pattern-matched for a version, because that guess would be a hardcoded
  assumption about one vendor's formatting living inside a tool that claims not
  to have any.

A record with no `firmware.release` is **excluded from the denominator and
named** when a range is in use. Not counted as absent, and not silently dropped.

### `exit_code` and the record that reports a failure

`0` clean, `1` findings, `2` could not be walked. A record with `exit_code` 2
legitimately carries no digest and no `walk_ref` — that is the whole point of it.
A record with `exit_code` 0 and no payload is **refused**: a clean capture that
cannot produce its walk is a claim, not a record.

### Duplicates and corrections

`(unit_key, topology, captured_at)` is the identity. Two of them is a harness
that cannot say which run it describes, so `ingest` refuses one — unless
`--correct` is passed, which is the operator writing down *this line supersedes
that one*. The store is append-only either way; the reader takes the latest line
per identity, and position in the file decides, not the timestamp.

---

## `fleet-sensor-baseline/fleet-baseline/2`

A derived declaration, labeled as such.

```json
{
  "format": "fleet-sensor-baseline/fleet-baseline/2",
  "scope": {"model": "GB200-NVL-tray", "firmware_range": ">=1.4,<1.5"},
  "derived": {"units": 1987, "present_threshold": 0.99,
              "absent_threshold": 0.01,
              "captured_between": ["2026-08-01T00:00:00Z",
                                   "2026-08-21T00:00:00Z"]},
  "sensors": [{"name": "Fan_CPU_1", "uri_suffix": "/Sensors/Fan_CPU_1",
               "present_ratio": 0.9975}],
  "divergent": [{"name": "Fan_CPU_2", "present_ratio": 0.916667,
                 "present_on": 22, "of": 24}],
  "provenance": "fleet-derived",
  "notice": "This baseline was derived from the fleet, not declared by a manufacturer. It cannot see an absence the whole cohort shares."
}
```

**The `notice` sentence is part of the format.** Every consumer that judges
against a baseline prints it verbatim, and the validator refuses a baseline that
lost it or reworded it. That is not pedantry about prose: a baseline without the
sentence validates as a manufacturer declaration everywhere downstream, and the
loss is invisible because every other field still reads correctly.

`provenance` carries the same fact in a machine-readable field, and every summary
this layer emits records which kind of truth decided it.

### `divergent` — the third state, and why the format had to move

A sensor is **expected** at or above `present_threshold`, **foreign** at or below
`absent_threshold`, and between the two the cohort **disagrees with itself**. That
middle band is reported once, against the cohort, naming the minority, and charged
to no unit.

Two states were not enough, and the failure was not a miss — it was an inversion.
With 22 of 24 trays carrying a sensor the ratio is 0.9167, below the 0.99 default,
so the sensor left the baseline entirely and every tray that **had** it was
reported as carrying something unexpected while the two that had lost it came back
clean. A proportion is a coarse instrument at rack scale: 0.99 of 24 is 23.76, so
one deviant unit crosses it, and `--floor` admits cohorts from 20.

**`divergent` is why this is `/2` and not a new key on `/1`.** An unknown format
is refused; an unknown key is dropped. A reader that predated the key would ignore
it and charge every divergent sensor to the units that have it — the same
inversion, arrived at silently, which is the one way it could get worse.

`uri_suffix` is written **only when every contributing unit agrees**. Matching is
by name regardless; a baseline asserting one URI while the cohort reports several
would be asserting something no unit said, and advisory metadata that is wrong is
worse than absent.

**A baseline refuses to derive from fewer than 20 units** (`--floor N` to change
it, explicitly). A cohort of nine is an anecdote wearing a format key: at that size
one unlucky machine moves every ratio past any threshold worth setting.

## `fleet-sensor-baseline/fleet-baseline/1`

**Superseded. Refused, and deliberately not upgraded.**

A `/1` recorded only the sensors that cleared the presence threshold and silently
forgot every sensor the cohort disagreed about. `validate` refuses one and says
why; nothing reads it. There is no migration path and there cannot be one: the
information needed to judge was discarded at derivation, so a `/1` cannot be told
apart from a cohort that genuinely never had those sensors. **Derive again** — the
records it came from are still in the store, which is what an append-only store is
for.

## `bmc-sensor-audit/fleet-baseline/1` — written, not read

`baseline --for-referee PATH` writes the referee's declaration-source format, so a
cohort baseline can be used as the third source in `bmc-sensor-audit coverage`.
It is specified in that repository's `docs/declaration-sources.md`, not here, and
this build has no validator for it — the consumer is the checker.

Two properties of the conversion belong on this page because they are decisions
about what a format may lose:

- **It is a candidate.** `"reviewed": null`, so the referee refuses it until a
  person adds their name and a date. The conversion is not the review.
- **The divergent band does not cross.** The referee's format has two states, not
  three, because it judges one machine against a list of what that machine should
  have. Divergent sensors are carried under `divergent_not_declared`, a key that
  reader ignores by its own `/1` rule, and named on stderr at export. Declaring
  them would rebuild the inversion one tool over; dropping them without a word
  would rebuild `/1`.

---

## `fleet-sensor-baseline/summary/1`

A verdict, in the vocabulary the rest of the family uses: `exit_code`, `verdict`,
`decided_by`, per-row detail, `missing` for the subjects that never reported, and
`skipped` for the ones declared optional in advance.

`judged_against` names which kind of truth decided it. An outlier report made
against a fleet-derived baseline and one made against a manufacturer declaration
can carry identical rows and mean entirely different things.

---

## `fleet-sensor-baseline/targets/1`

The collector's rack list.

```json
{
  "format": "fleet-sensor-baseline/targets/1",
  "targets": [
    {"unit_key": "h-0042", "base_url": "https://192.0.2.1",
     "topology": {"satellite": "hmc-0"}, "model": "GB200-NVL-tray",
     "username": "readonly", "password_env": "BMC_PASS_RACK17",
     "insecure": true, "timeout": 30}
  ]
}
```

Not one of the three formats the specification enumerates — it is an
implementation necessity of `collect --targets FILE`, and it ships with a
validator for the same reason the others do.

**`password` is refused as a key, not ignored.** Ignoring it would let a file
that looks like it configures authentication sit in a repository holding a real
credential while quietly doing nothing, which is the worst of both outcomes.
`password_env` names an environment variable, so the file carries the name and
the host carries the value.

Two targets for one surface are refused: a rack list that names a BMC twice
cannot say which walk it describes. The same `unit_key` on two different
satellites is fine — that is one machine with two surfaces.

---

## `fleet-sensor-baseline/targets/2`

The same rack list, and the only one that may declare a certificate pin.

```json
{
  "format": "fleet-sensor-baseline/targets/2",
  "targets": [
    {"unit_key": "h-0042", "base_url": "https://192.0.2.1",
     "pin_sha256": "AB:CD:...:EF", "password_env": "BMC_PASS_RACK17"}
  ]
}
```

**A pin is an expectation, not a credential.** A SHA-256 fingerprint of a public
certificate is public — anyone who can connect can compute it — so it belongs in
the file, where a change to it shows up in a review diff. A CA bundle is a path on
one operator's disk and is `collect --cafile PATH` instead.

**Version 2 exists for one reason: a declaration an older reader would IGNORE.**
`pin_sha256` says *require exactly this certificate*. A reader that does not know
the key would drop it and connect unpinned — a security expectation stated by an
operator and met with silence. An older build refuses an unknown format outright,
so bumping is what turns a silent downgrade into a refusal. `targets/1` stays
valid and stays the right choice for a rack list that pins nothing.

The validator refuses, in the file, before any connection is made:

- `pin_sha256` in a `targets/1`, naming `targets/2` as what to write instead;
- `pin_sha256` beside a `base_url` that is not `https` — nothing would verify the
  connection. The referee refuses this too, from 0.1.4, but a rack list is
  reviewed long before it is run, and catching it in the file is catching it where
  somebody is looking;
- a fingerprint that is not 64 hex digits, colons optional;
- `pin_sha256` beside `insecure` — two answers to one question, and whichever won
  would be a guess about which the operator meant.

**This section is here because `validate` could not check any of it.** `targets/2`
shipped in the format list and not in the dispatch table, so every `targets/2` file
was refused — with a message that named `targets/2` among the formats it had just
said it read. The validator had handled both versions from the day `/2` landed;
only the wiring was missing, and every test called the validator directly rather
than through `validate`. The format that exists to carry a security expectation was
the one the checker would not check.

---

## Content handles

`payload_digest` is `sha256:` and the hex digest of the **file bytes**, identical
to what `bmc-sensor-audit capture --print-digest` prints and reproducible by
`sha256sum` in any language, by a recipient who has not installed either tool.

Deliberately not a canonical-JSON digest: that would survive re-indentation, and
would require every consumer to reproduce one language's float formatting exactly
before it could agree. The cost is stated rather than hidden — rewriting the file
changes the handle even where the walk is unchanged. That is correct for a handle
on a *received artifact* and wrong for one on a walk's *meaning*, and this is the
first.

`walk_ref` is the same digest in path form, `cas/sha256/<hex>`.

On ingest the digest is verified against the bytes. A mismatch is **exit 2** and
named — it is not a finding about a machine, it is this layer being unable to say
what it stored.
