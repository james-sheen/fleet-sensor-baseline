"""The command surface. Every subcommand answers in the same exit vocabulary.

    0  clean   1  findings   2  could-not-complete

**`2` is printed, never omitted.** *"N units could not be walked"* is the
sentence this whole layer exists to keep sayable: a fleet report that renders
incompleteness as silence is a fleet report that renders a dead collector as a
clean rack.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

from . import __version__
from .baseline import (BaselineError, DEFAULT_ABSENT_THRESHOLD,
                       DEFAULT_FLOOR, DEFAULT_THRESHOLD, derive,
                       divergent_names, expected_names,
                       latest_per_unit, select)
from .drift import steps
from .compare import CompareError, compare_unit
from .exits import CLEAN, FINDINGS, INCOMPLETE, worst
from .for_referee import (RefereeExportError, declaration_from_baseline,
                          export_preamble)
from .formats import (BASELINE_FORMAT, RECORD_FORMAT, short, validate_any,
                      validate_record)
from .outliers import compare, divergences
from .report import baseline_preamble, render, summary
from .store import (Store, StoreError, digest_bytes, iter_json, key_of,
                    ref_for, surface_of)
from .verdict import VerdictError, assess, read_expectations
from .walk import WalkError, parse_prefix_map, sensor_names, sensor_paths


def _load(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _emit(payload: dict, title: str, args: argparse.Namespace,
          preamble: Iterable[str] = ()) -> int:
    for line in preamble:
        print(line)
    print(render(payload, title=title))
    if getattr(args, "json", None):
        Path(args.json).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        print(f"  wrote {args.json}")
    return payload["exit_code"]


def _in_scope(records: list[dict], since: str | None, at: str | None) -> list[dict]:
    """Records inside a declared time window. Both bounds are inclusive.

    String comparison on ISO-8601 timestamps, which orders correctly for the
    `...Z` form this layer writes. A record with a differently-shaped timestamp
    is not silently reordered -- it sorts where its text puts it, and the format
    validator is what keeps the field a string in the first place.
    """
    out = records
    if since is not None:
        out = [r for r in out if r.get("captured_at", "") >= since]
    if at is not None:
        out = [r for r in out if r.get("captured_at", "") <= at]
    return out


# -- ingest ---------------------------------------------------------------

def _cmd_ingest(args: argparse.Namespace) -> int:
    """Validate, verify digests, refuse duplicates, store.

    **A duplicate is refused and a correction is declared.** The store is
    append-only and the reader takes the latest line per surface-and-time, so
    the file format supports corrections by construction. What it must not
    support is an ACCIDENTAL second answer to one question: that is a harness
    that cannot say which run it describes. `--correct` is the operator writing
    down *this line supersedes that one*, which is a decision on the record;
    silence is not.
    """
    store = Store(args.store)
    store.initialise()
    try:
        existing = store.existing_keys()
    except StoreError as exc:
        print(f"the index could not be read: {exc}", file=sys.stderr)
        return INCOMPLETE

    accepted: list[dict] = []
    problems: list[str] = []
    seen_here: set[tuple[str, ...]] = set()

    try:
        loaded = list(iter_json(args.records))
    except StoreError as exc:
        print(f"{exc}", file=sys.stderr)
        return INCOMPLETE

    for path, payload in loaded:
        found = validate_record(payload)
        if found:
            problems.append(f"{path}: " + "; ".join(found))
            continue
        key = key_of(payload)
        if key in seen_here:
            problems.append(f"{path}: repeats a record already in this batch "
                            f"({' '.join(key)})")
            continue
        if key in existing and not args.correct:
            problems.append(
                f"{path}: the store already holds {' '.join(key)}. Two answers "
                f"to one question is a harness that cannot say which run it "
                f"describes -- pass --correct to supersede it deliberately")
            continue
        seen_here.add(key)

        digest = payload.get("payload_digest")
        if digest is not None:
            walk_path = getattr(args, "payload_for", {}).get(str(path))
            if args.payloads:
                walk_path = walk_path or _find_payload(args.payloads, digest)
            if walk_path is not None:
                raw = Path(walk_path).read_bytes()
                computed = digest_bytes(raw)
                if computed != digest:
                    # The record refusing itself. Named, and exit 2 -- a
                    # mismatch is not a finding about a machine, it is this
                    # layer being unable to say what it stored.
                    problems.append(
                        f"{path}: declares {digest} and the payload at "
                        f"{walk_path} digests to {computed}")
                    continue
                store.put_payload(raw)
            elif args.require_payload:
                problems.append(
                    f"{path}: declares {digest} and no payload with that "
                    f"digest was supplied or is already stored")
                continue
            elif not store.cas_path(digest).is_file():
                problems.append(
                    f"{path}: references {digest}, which is not in the store. "
                    f"Supply it with --payloads, or pass --allow-dangling if "
                    f"the payload lives somewhere this run cannot see")
                if not args.allow_dangling:
                    continue
                problems.pop()
        accepted.append(payload)

    written = store.append(accepted)
    print(f"ingest: stored {written} record(s) into {store.index_path}")
    for problem in problems:
        print(f"  refused {problem}")
    if problems:
        return INCOMPLETE
    return CLEAN


def _find_payload(directories: list[str], digest: str) -> str | None:
    """Locate a walk by digest, checking the bytes rather than the filename.

    Filenames are a convenience and digests are the contract. A file named after
    a digest it does not have is exactly the case this whole check exists for.
    """
    _, _, hexdigest = digest.partition(":")
    for directory in directories:
        base = Path(directory)
        named = base / f"{hexdigest}.json"
        if named.is_file() and digest_bytes(named.read_bytes()) == digest:
            return str(named)
        for candidate in sorted(base.rglob("*.json")):
            if digest_bytes(candidate.read_bytes()) == digest:
                return str(candidate)
    return None


# -- baseline -------------------------------------------------------------

def _presence(store: Store, records: list[dict]) -> tuple[
        dict[str, set[str]], dict[str, dict[str, str]], dict[str, str]]:
    """`(names by unit, paths by unit, unreadable by unit)`.

    The union across a unit's surfaces, because a unit is the tuple: a sensor
    that answers on the HMC is present on the machine even when the host BMC
    does not report it.
    """
    present: dict[str, set[str]] = {}
    paths: dict[str, dict[str, str]] = {}
    unreadable: dict[str, str] = {}
    for unit, group in latest_per_unit(records).items():
        names: set[str] = set()
        found: dict[str, str] = {}
        for record in group:
            if record.get("exit_code", CLEAN) != CLEAN:
                unreadable[unit] = record.get(
                    "detail", "the record reports it could not be walked")
                break
            try:
                payload = json.loads(store.payload(record))
                names |= sensor_names(payload)
                found.update(sensor_paths(payload))
            except (StoreError, WalkError, json.JSONDecodeError) as exc:
                unreadable[unit] = str(exc)
                break
        else:
            present[unit] = names
            paths[unit] = found
    return present, paths, unreadable


def _cmd_baseline(args: argparse.Namespace) -> int:
    store = Store(args.store)
    try:
        records = _in_scope(store.latest(), args.since, args.at)
        selection = select(records, model=args.model,
                           firmware_range=args.firmware_range,
                           firmware=args.firmware)
    except (StoreError, BaselineError) as exc:
        print(f"baseline: {exc}", file=sys.stderr)
        return INCOMPLETE

    for unit, reason in selection.excluded:
        print(f"  excluded {unit}: {reason}")

    present, paths, unreadable = _presence(store, selection.records)
    for unit, reason in unreadable.items():
        print(f"  excluded {unit}: {reason}")

    scope: dict[str, Any] = {}
    if args.model is not None:
        scope["model"] = args.model
    if args.firmware_range is not None:
        scope["firmware_range"] = args.firmware_range
    if args.firmware is not None:
        scope["firmware"] = args.firmware

    window = None
    times = sorted(r.get("captured_at", "") for r in selection.records)
    if times:
        window = (times[0], times[-1])

    try:
        artifact = derive(present, paths, scope=scope,
                          absent_threshold=args.absent_threshold,
                          threshold=args.present_threshold, floor=args.floor,
                          window=window)
    except BaselineError as exc:
        print(f"baseline: {exc}", file=sys.stderr)
        return INCOMPLETE

    Path(args.out).write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    divergent = len(artifact.get("divergent", []))
    tail = (f", and {divergent} the cohort disagrees about" if divergent else "")
    print(f"baseline: {len(artifact['sensors'])} sensor(s) over "
          f"{artifact['derived']['units']} unit(s){tail} -> {args.out}")
    for line in baseline_preamble(artifact):
        print(f"  {line}")

    if args.for_referee is None and args.for_referee_platform is not None:
        # A flag that quietly does nothing is the failure this family exists to
        # refuse -- the referee's own `--pin-sha256` was built, dropped and
        # ignored on an `http://` target for four releases. Somebody naming a
        # platform has asked for the export.
        print("baseline: --for-referee-platform names the platform for "
              "--for-referee, which was not given, so nothing would be "
              "written. Add --for-referee PATH, or drop the flag",
              file=sys.stderr)
        return INCOMPLETE

    if args.for_referee is not None:
        try:
            declaration = declaration_from_baseline(
                artifact, platform=args.for_referee_platform)
        except RefereeExportError as exc:
            # The baseline itself was written and is good. Only the export
            # failed, and saying so is the difference between a derivation an
            # operator can keep and one they think they have to run again.
            print(f"baseline: --for-referee: {exc}", file=sys.stderr)
            return INCOMPLETE
        Path(args.for_referee).write_text(
            json.dumps(declaration, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        print(f"baseline: {len(declaration['sensors'])} sensor(s) declared to "
              f"the referee -> {args.for_referee}")
        for line in export_preamble(declaration):
            print(f"  {line}")

    return CLEAN if not unreadable else INCOMPLETE


# -- outliers -------------------------------------------------------------

def _cmd_outliers(args: argparse.Namespace) -> int:
    store = Store(args.store)
    try:
        artifact = _load(args.baseline)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"outliers: {args.baseline}: {exc}", file=sys.stderr)
        return INCOMPLETE
    kind, problems = validate_any(artifact)
    if kind != BASELINE_FORMAT or problems:
        print(f"outliers: {args.baseline} is not a usable baseline: "
              + "; ".join(problems or [f"format is {kind!r}"]), file=sys.stderr)
        return INCOMPLETE

    try:
        records = _in_scope(store.latest(), args.since, args.at)
    except StoreError as exc:
        print(f"outliers: {exc}", file=sys.stderr)
        return INCOMPLETE

    present, _, unreadable = _presence(store, records)
    diverged = divergences(artifact, present)
    rows = [row.to_dict()
            for row in compare(expected_names(artifact), present, unreadable,
                               divergent=divergent_names(artifact))]
    payload = summary(
        rows, judged_against=artifact.get("provenance"),
        cohort_code=FINDINGS if diverged else CLEAN,
        cohort_decided_by=[f"cohort:{d.name}" for d in diverged])
    if diverged:
        payload["divergent"] = [d.to_dict() for d in diverged]
    preamble = baseline_preamble(artifact)
    for d in diverged:
        label, units = d.minority
        preamble.append(
            f"divergent: {d.name} present on {d.present_on} of {d.of} unit(s). "
            f"The cohort disagrees with itself, so this is reported here and "
            f"charged to no unit; the {len(units)} that {label}: "
            f"{', '.join(units)}")
    return _emit(payload, "outliers", args, preamble=preamble)


# -- drift ----------------------------------------------------------------

def _cmd_drift(args: argparse.Namespace) -> int:
    store = Store(args.store)
    try:
        prefix_map = parse_prefix_map(args.aggregation_prefix or [])
        records = _in_scope(store.latest(), args.since, args.at)
    except (StoreError, WalkError) as exc:
        print(f"drift: {exc}", file=sys.stderr)
        return INCOMPLETE

    mine = [r for r in records if r["unit_key"] == args.unit]
    if not mine:
        print(f"drift: no records for unit {args.unit!r} in this window. "
              f"*Nothing to compare* is not *nothing changed*", file=sys.stderr)
        return INCOMPLETE

    by_surface: dict[tuple[str, ...], list[dict]] = {}
    for record in mine:
        by_surface.setdefault(surface_of(record), []).append(record)

    rows: list[dict] = []
    notes: list[str] = []
    codes: list[int] = []
    for surface, group in sorted(by_surface.items()):
        group.sort(key=lambda r: r.get("captured_at", ""))
        ordered: list[tuple[str, set[str], str | None]] = []
        for record in group:
            if record.get("exit_code", CLEAN) != CLEAN:
                notes.append(
                    f"{'/'.join(surface)} at {record.get('captured_at')}: "
                    f"could not be walked -- "
                    + record.get("detail", "no detail recorded"))
                codes.append(INCOMPLETE)
                continue
            try:
                payload = json.loads(store.payload(record))
                names = sensor_names(payload)
            except (StoreError, WalkError, json.JSONDecodeError) as exc:
                notes.append(f"{'/'.join(surface)} at "
                             f"{record.get('captured_at')}: {exc}")
                codes.append(INCOMPLETE)
                continue
            ordered.append((record.get("captured_at", ""), names,
                            (record.get("firmware") or {}).get("version")))
        if len(ordered) < 2:
            notes.append(
                f"{'/'.join(surface)}: {len(ordered)} readable capture(s); the "
                f"vertical axis needs two to say anything")
            continue
        rows.extend(step.to_dict() for step in steps(ordered, surface, prefix_map))

    payload = summary(rows, notes=notes)
    payload["exit_code"] = worst([payload["exit_code"], *codes])
    payload["verdict"] = {CLEAN: "clean", FINDINGS: "findings",
                          INCOMPLETE: "incomplete"}[payload["exit_code"]]
    return _emit(payload, f"drift {args.unit}", args)


# -- verdict --------------------------------------------------------------

def _cmd_verdict(args: argparse.Namespace) -> int:
    store = Store(args.store)
    try:
        expected = read_expectations(
            Path(args.expect_units).read_text(encoding="utf-8"))
        records = _in_scope(store.latest(), args.since, args.at)
    except (OSError, StoreError, VerdictError) as exc:
        print(f"verdict: {exc}", file=sys.stderr)
        return INCOMPLETE

    newest: dict[str, dict] = {}
    for record in records:
        current = newest.get(record["unit_key"])
        if current is None or record.get("captured_at", "") >= current.get(
                "captured_at", ""):
            newest[record["unit_key"]] = record

    try:
        fleet = assess(expected, newest, args.optional_unit or [])
    except VerdictError as exc:
        print(f"verdict: {exc}", file=sys.stderr)
        return INCOMPLETE

    payload = summary([row.to_dict() for row in fleet.rows],
                      missing=fleet.missing, skipped=fleet.skipped)
    return _emit(payload, "fleet", args)


# -- compare --------------------------------------------------------------

def _cmd_compare(args: argparse.Namespace) -> int:
    store = Store(args.store)
    try:
        rows = compare_unit(store, args.unit, before=args.before,
                            after=args.after,
                            command=tuple(args.command.split()),
                            strict_fields=args.strict_fields,
                            prefixes=args.aggregation_prefix or [])
    except (OSError, StoreError, CompareError) as exc:
        print(f"compare: {exc}", file=sys.stderr)
        return INCOMPLETE

    payload = summary(
        [row.to_dict() for row in rows],
        judged_against=(f"the capture at or before {args.before}, against the "
                        f"one at or before {args.after}"))
    # The referee's own report is the answer; this layer chose the inputs. It
    # is printed under the surface it belongs to rather than merged, because a
    # unit answering on two BMCs gets two reports and a merged one would name
    # no surface for either.
    preamble = []
    for row in rows:
        if row.report:
            preamble.append("/".join(row.surface) + ":")
            preamble += [f"  {line}" for line in row.report.splitlines()]
    return _emit(payload, "comparison", args, preamble)


# -- validate -------------------------------------------------------------

def _cmd_validate(args: argparse.Namespace) -> int:
    worst_code = CLEAN
    for path in args.paths:
        try:
            payload = _load(path)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"{path}: {exc}")
            worst_code = INCOMPLETE
            continue
        kind, problems = validate_any(payload)
        if problems:
            print(f"{path}: not a valid {kind or 'artifact'}")
            for problem in problems:
                print(f"  {problem}")
            worst_code = INCOMPLETE
        else:
            print(f"{path}: valid {kind}")
    return worst_code


# -- collect --------------------------------------------------------------

def _cmd_collect(args: argparse.Namespace) -> int:
    from .collect.collector import CollectError, Collector, load_targets_file

    store = Store(args.store)
    store.initialise()
    try:
        targets = load_targets_file(args.targets)
    except (OSError, CollectError) as exc:
        print(f"collect: {exc}", file=sys.stderr)
        return INCOMPLETE

    if args.backend == "mock":
        print("collect: the mock backend walks fake machines and is for "
              "exercising this collector, never for auditing a fleet",
              file=sys.stderr)
        return INCOMPLETE

    from .collect.backends.subprocess_backend import (ABSENT, RefereeTooOld,
                                                      subprocess_backend)
    backend = subprocess_backend(args.command.split(), cafile=args.cafile)

    # Before any machine is walked, once. The floor in this package's metadata
    # governs the environment pip installed into; PATH decides what actually
    # answers, and the two disagree whenever a system-wide install or another
    # venv sits earlier. INCOMPLETE, never a finding: a fleet audited by the
    # wrong referee has not been audited.
    try:
        referee = backend.preflight()
    except RefereeTooOld as exc:
        print(f"collect: {exc}", file=sys.stderr)
        return INCOMPLETE
    if referee is ABSENT:
        # Say nothing here. Each target reports `is not on PATH` in its own
        # record, and announcing that the tool cannot report a version would be
        # a sentence about a program that is not installed.
        pass
    elif referee is None:
        print("collect: the referee on PATH cannot report a version, so which "
              "one produced these records is not recorded", file=sys.stderr)
    else:
        print(f"collect: referee {'.'.join(map(str, referee))} on PATH")

    contradictory = [t.unit_key for t in targets if t.insecure] if args.cafile else []
    if contradictory:
        # Declared verification for the run and no verification for a target.
        # Silently letting either win is a guess about which was meant.
        print(f"collect: --cafile verifies every BMC and "
              f"{', '.join(contradictory)} declare 'insecure'. Remove one",
              file=sys.stderr)
        return INCOMPLETE

    collector = Collector(backend, store, collector_id=args.collector_id,
                          attempts=args.attempts, base_delay=args.base_delay,
                          trigger=args.trigger, etag_cache=args.etag_cache)
    try:
        records = collector.run(targets)
    except CollectError as exc:
        print(f"collect: {exc}", file=sys.stderr)
        return INCOMPLETE

    store.append(records)
    if args.out:
        Path(args.out).write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in records),
            encoding="utf-8")

    skipped = sum(1 for r in records if r.get("unchanged"))
    if args.etag_cache:
        print(f"collect: {skipped} of {len(records)} surface(s) reported their "
              f"sensor set unchanged and were not walked")
    rows = [{"unit_key": r["unit_key"], "exit_code": r.get("exit_code", CLEAN),
             "detail": r.get("detail", "")} for r in records]
    payload = summary(rows)
    return _emit(payload, "collect", args)


# -- parser ---------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fleet-sensor-baseline",
        description="Sensor presence and configuration drift across a fleet "
                    "and across time. Exit 0 clean, 1 findings, 2 incomplete.")
    parser.add_argument("--version", action="version",
                        version=f"fleet-sensor-baseline {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def store_arg(sub):
        sub.add_argument("--store", default="fleet-store",
                         help="the directory holding records.jsonl and cas/")

    def window(sub):
        sub.add_argument("--since", help="ignore captures before this timestamp")
        sub.add_argument("--at", help="ignore captures after this timestamp")

    def json_out(sub):
        sub.add_argument("--json", help="also write the summary to this path")

    ingest = subparsers.add_parser(
        "ingest", help="validate and store fleet-records")
    store_arg(ingest)
    ingest.add_argument("records", nargs="+", help="fleet-record/1 JSON files")
    ingest.add_argument("--payloads", action="append", default=[],
                        help="directory holding walk payloads to store")
    ingest.add_argument("--correct", action="store_true",
                        help="this record deliberately supersedes one already "
                             "stored for the same surface and time")
    ingest.add_argument("--require-payload", action="store_true",
                        help="refuse a record whose payload is not supplied")
    ingest.add_argument("--allow-dangling", action="store_true",
                        help="accept a record whose payload lives elsewhere")
    ingest.set_defaults(func=_cmd_ingest, payload_for={})

    baseline = subparsers.add_parser(
        "baseline", help=f"derive a {short(BASELINE_FORMAT)} from stored records")
    store_arg(baseline)
    window(baseline)
    baseline.add_argument("--model", help="declared model, matched exactly")
    baseline.add_argument("--firmware-range",
                          help="a range over firmware.release, e.g. >=1.4,<1.5")
    baseline.add_argument("--firmware",
                          help="a firmware.version string, matched exactly")
    baseline.add_argument("--present-threshold", type=float,
                          default=DEFAULT_THRESHOLD,
                          help=f"at or above this ratio a sensor is expected of "
                               f"every unit (default {DEFAULT_THRESHOLD})")
    baseline.add_argument("--absent-threshold", type=float,
                          default=DEFAULT_ABSENT_THRESHOLD,
                          help=f"at or below this ratio a sensor is foreign to "
                               f"the cohort (default {DEFAULT_ABSENT_THRESHOLD}). "
                               f"Between the two the cohort disagrees with "
                               f"itself and the sensor is charged to no unit")
    baseline.add_argument("--floor", type=int, default=DEFAULT_FLOOR,
                          help=f"refuse a cohort smaller than this "
                               f"(default {DEFAULT_FLOOR})")
    baseline.add_argument("--out", required=True)
    baseline.add_argument(
        "--for-referee", metavar="PATH",
        help="also write a bmc-sensor-audit declaration-source CANDIDATE, for "
             "coverage --declaration. It carries no reviewed marker, so the "
             "referee refuses it until somebody puts their name to it, and it "
             "does not declare the sensors the cohort disagreed about")
    baseline.add_argument(
        "--for-referee-platform", metavar="NAME",
        help="the platform to name in that file, when the cohort was not "
             "scoped with --model")
    baseline.set_defaults(func=_cmd_baseline)

    outliers = subparsers.add_parser(
        "outliers", help="units that differ from their cohort")
    store_arg(outliers)
    window(outliers)
    json_out(outliers)
    outliers.add_argument("--baseline", required=True)
    outliers.set_defaults(func=_cmd_outliers)

    drift = subparsers.add_parser(
        "drift", help="one unit across time and firmware")
    store_arg(drift)
    window(drift)
    json_out(drift)
    drift.add_argument("--unit", required=True)
    drift.add_argument("--aggregation-prefix", action="append", default=[],
                       metavar="OLD=NEW",
                       help="declare a known prefix rename so the pair is not "
                            "reported as a disappearance and an arrival")
    drift.set_defaults(func=_cmd_drift)

    verdict = subparsers.add_parser(
        "verdict", help="the fleet run: every expected unit must have reported")
    store_arg(verdict)
    window(verdict)
    json_out(verdict)
    verdict.add_argument("--expect-units", required=True,
                         help="one unit key per line")
    verdict.add_argument("--optional-unit", action="append", default=[],
                         help="this unit is allowed not to report; a decision "
                              "on the record")
    verdict.set_defaults(func=_cmd_verdict)

    compare = subparsers.add_parser(
        "compare", help="judge one unit's stored walks across two times, by "
                        "handing them to the referee")
    store_arg(compare)
    json_out(compare)
    compare.add_argument("--unit", required=True, help="the unit key")
    compare.add_argument("--before", required=True, metavar="TIME",
                         help="use the newest capture at or before this time")
    compare.add_argument("--after", required=True, metavar="TIME",
                         help="and compare it with the newest at or before this")
    compare.add_argument("--strict-fields", action="store_true",
                         help="pass through to the referee: exit 2 if either "
                              "capture carries no record of object properties")
    compare.add_argument("--aggregation-prefix", action="append", metavar="OLD=NEW",
                         help="pass through to the referee. Repeatable; nothing "
                              "is inferred")
    compare.add_argument("--command", default="bmc-sensor-audit",
                         help="how to invoke the referee")
    compare.set_defaults(func=_cmd_compare)

    validate = subparsers.add_parser(
        "validate", help="check an artifact against the format it declares")
    validate.add_argument("paths", nargs="+")
    validate.set_defaults(func=_cmd_validate)

    collect = subparsers.add_parser(
        "collect", help="walk a rack of BMCs and file the records")
    store_arg(collect)
    json_out(collect)
    collect.add_argument("--targets", required=True)
    collect.add_argument("--out", help="also write the records as JSONL here")
    collect.add_argument("--collector-id", default="unnamed")
    collect.add_argument("--command", default="bmc-sensor-audit",
                         help="how to invoke the referee")
    collect.add_argument("--backend", default="subprocess",
                         choices=("subprocess", "mock"))
    collect.add_argument("--attempts", type=int, default=3)
    collect.add_argument("--base-delay", type=float, default=1.0)
    collect.add_argument("--trigger", default="scheduled")
    collect.add_argument("--cafile", metavar="PATH",
                         help="verify every BMC against this certificate or CA "
                              "bundle. A path is machine-specific, so it is a "
                              "flag rather than a field in a rack list; a "
                              "per-machine self-signed certificate is what "
                              "pin_sha256 in targets/2 is for")
    collect.add_argument("--etag-cache", action="store_true",
                         help="ask each BMC whether its sensor SET changed "
                              "before walking it, keeping one cache per surface "
                              "in the store. Membership only: a record filed "
                              "from a skip reuses the previous capture and says "
                              "so. Needs bmc-sensor-audit>=0.1.2")
    collect.set_defaults(func=_cmd_collect)

    return parser


class _StdoutThatOutlivesItsReader:
    """`sys.stdout`, for a program whose exit code is a claim about a fleet.

    **A reader that stops reading has said something about itself, not about
    the rack.** `fleet-sensor-baseline baseline ... | head` is an ordinary
    thing to do, and before this every subcommand died of it -- including
    `--help`, which is the likeliest thing anyone pipes. The failure arrived
    two ways, and neither is in the vocabulary `exits.py` defines: a report
    long enough to fill the pipe buffer raised out of `print` and the
    interpreter exited `1`, which this tool means as FINDINGS; a shorter one
    survived to the shutdown flush, printed `Exception ignored` to stderr and
    exited `120`, which is not a verdict at all.

    Both replace a statement about the fleet with a statement about the
    terminal. Absorbing rather than refusing is the point: `INCOMPLETE` says
    the fleet could not be assessed, and a baseline whose reader walked away
    was assessed perfectly well.

    The sibling package has carried this since its own two occurrences. This
    is the third in the family, and it shipped in 0.2.2.
    """

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self.reader_left = False

    def _abandon(self) -> None:
        """Point the descriptor at nowhere, then stop trying.

        The interpreter flushes `stdout` again on its way out, on bytes this
        stream may still hold. Without the redirect that second flush raises
        where no `except` can reach it -- which is the `Exception ignored`
        line, and the `120`.
        """
        self.reader_left = True
        try:
            fileno = self._stream.fileno()
        except (AttributeError, ValueError, OSError):
            return  # captured by a harness rather than piped; nothing to point
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), fileno)
        except OSError:
            pass

    def write(self, text: str) -> int:
        if self.reader_left:
            return len(text)
        try:
            return self._stream.write(text)
        except BrokenPipeError:
            self._abandon()
            return len(text)

    def flush(self) -> None:
        if self.reader_left:
            return
        try:
            self._stream.flush()
        except BrokenPipeError:
            self._abandon()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


def main(argv: list[str] | None = None) -> int:
    # Installed before the parser runs, because `--help` prints through it too
    # and argparse exits from inside `parse_args`.
    stdout = _StdoutThatOutlivesItsReader(sys.stdout)
    sys.stdout = stdout
    try:
        args = build_parser().parse_args(argv)
        try:
            return args.func(args)
        except StoreError as exc:
            print(f"{args.command}: {exc}", file=sys.stderr)
            return INCOMPLETE
    except BrokenPipeError:
        # A pipe that broke somewhere the wrapper does not cover is a failure
        # to deliver, and this tool says so with the code that means it.
        print("the output could not be written: the pipe closed",
              file=sys.stderr)
        return INCOMPLETE
    finally:
        # **Flush through the wrapper, before handing the stream back.** A
        # report short enough to sit in the buffer is not written until the
        # interpreter flushes on its way out -- by which point this wrapper is
        # gone and the failure lands where no `except` can reach it.
        try:
            stdout.flush()
        finally:
            sys.stdout = stdout._stream


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
