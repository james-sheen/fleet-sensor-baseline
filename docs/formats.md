# Formats

Four, each versioned in its own key and each with a validator that ships, because
**the person who receives the file is the one who needs to check it.** A control
plane ingesting thousands of records has to be able to refuse a malformed one
using the format's own words, not a shape it inferred from the files that
happened to arrive first.

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

## `fleet-sensor-baseline/fleet-baseline/1`

A derived declaration, labeled as such.

```json
{
  "format": "fleet-sensor-baseline/fleet-baseline/1",
  "scope": {"model": "GB200-NVL-tray", "firmware_range": ">=1.4,<1.5"},
  "derived": {"units": 1987, "present_threshold": 0.99,
              "captured_between": ["2026-08-01T00:00:00Z",
                                   "2026-08-21T00:00:00Z"]},
  "sensors": [{"name": "Fan_CPU_1", "uri_suffix": "/Sensors/Fan_CPU_1",
               "present_ratio": 0.9975}],
  "provenance": "fleet-derived",
  "notice": "This baseline was derived from the fleet, not declared by a manufacturer. It cannot see an absence the whole cohort shares."
}
```

**The `notice` sentence is part of the format.** Every consumer that judges
against a `fleet-baseline/1` prints it verbatim, and the validator refuses a
baseline that lost it or reworded it. That is not pedantry about prose: a
baseline without the sentence validates as a manufacturer declaration everywhere
downstream, and the loss is invisible because every other field still reads
correctly.

`provenance` carries the same fact in a machine-readable field, and every
summary this layer emits records which kind of truth decided it.

**A baseline refuses to derive from fewer than 20 units** (`--floor N` to change
it, explicitly). A cohort of nine is an anecdote wearing a format key: at that
size one unlucky machine moves every ratio past any threshold worth setting.

`uri_suffix` is written **only when every contributing unit agrees**. Matching is
by name regardless; a baseline asserting one URI while the cohort reports several
would be asserting something no unit said, and advisory metadata that is wrong is
worse than absent.

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
