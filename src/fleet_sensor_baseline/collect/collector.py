"""Walking a rack's worth of targets, one at a time, and binding identity.

**Identity binds here and nowhere upstream.** The referee's walk payload carries
no `unit_key` -- *the parse is the redaction*, and no identity field enters
`walk/1`. This is the layer whose job is to name things, so `{unit_key,
topology, digest, walk_ref}` is assembled on this side of the line and travels
in this layer's own envelopes.

## What this collector does not do, and why saying so matters

One capability in the specification is still **not reachable through the
referee's published surface**, and one that was has since been closed:

- ~~**Conditional requests (ETag).**~~ **Closed in `bmc-sensor-audit` 0.1.2 and
  used here.** With `--etag-cache`, the referee asks the BMC whether its sensor
  SET changed and skips the walk when it has not: a handful of requests instead
  of one per sensor. See `Collector(etag_cache=True)`.

  **It proves membership, and this collector must not claim more.** A record
  filed from a skip reuses the previous capture's payload, which is exactly
  right for the name-set questions this layer asks and would be wrong for a
  threshold audit. The record says so, in an `unchanged` block naming the basis.

  **The skip is announced in PROSE, not in an exit code** -- it exits 0, like a
  walk, and writes no file, like a failure. The backend reads the printed line
  to tell them apart. That is a real seam and it is filed upstream rather than
  hidden: a machine-readable signal would be a better contract.
- **Pinned-certificate reads toward BMCs.** The referee's only TLS control is
  `--insecure`. A collector that wants certificate pinning cannot express it,
  so this one does not claim to.

~~A third is a hazard rather than a gap~~ -- also closed in 0.1.2: the
credential is passed as `--password-env NAME` and the value never enters argv.
All of them are recorded in `docs/upstream-asks.md`, fixed and open alike.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol, Sequence

from ..exits import CLEAN, INCOMPLETE, normalise
from ..formats import RECORD_FORMAT, validate_targets
from ..store import Store, digest_bytes, ref_for, surface_of


class CollectError(Exception):
    """A collection run this module refuses, with the reason in the message."""


@dataclass(frozen=True)
class Target:
    """One BMC surface to walk, as the operator declared it."""
    unit_key: str
    base_url: str
    topology: dict[str, str] = field(default_factory=dict)
    model: str | None = None
    username: str | None = None
    #: The NAME of an environment variable, never a password. A credential in a
    #: targets file is a credential in version control the first time somebody
    #: is helpful with it.
    password_env: str | None = None
    insecure: bool = False
    timeout: float | None = None

    def password(self) -> str | None:
        if self.password_env is None:
            return None
        value = os.environ.get(self.password_env)
        if value is None:
            raise CollectError(
                f"{self.unit_key}: the targets file names the environment "
                f"variable {self.password_env!r} and it is not set. A missing "
                f"credential is a run that could not happen, not one that "
                f"found nothing")
        return value


@dataclass
class Capture:
    """What a backend returns for one target."""
    exit_code: int
    raw: bytes | None = None
    detail: str = ""
    #: The digest the TOOL reported, when it reported one. Kept separately from
    #: the digest computed here so the two can be compared -- see `Collector`.
    reported_digest: str | None = None
    raw_exit_code: int | None = None
    #: The BMC said its sensor SET is unchanged, so no walk happened and there
    #: is no payload. Distinct from a clean capture with no payload, which is a
    #: tool that claimed success and produced nothing.
    unchanged: bool = False


class Backend(Protocol):
    """How a collector reaches a BMC. Subprocess in production, mock in tests.

    `etag_cache` is where the referee should keep this surface's collection
    ETags. It is a parameter rather than a field on `Target` because it is a
    fact about THIS STORE, not about the rack: the same targets file pointed at
    two stores has two caches, and a rack list committed to version control
    should not name a path on one operator's disk.

    **It is optional, and a backend that omits it still works.** The collector
    passes it only when ETag caching is on, so an implementation written before
    the parameter existed is unaffected until somebody enables the feature.
    """

    def capture(self, target: Target,
                etag_cache: str | None = None) -> Capture:  # pragma: no cover
        ...


def read_targets(payload: Any) -> list[Target]:
    problems = validate_targets(payload)
    if problems:
        raise CollectError("; ".join(problems))
    out = []
    for item in payload["targets"]:
        out.append(Target(
            unit_key=item["unit_key"],
            base_url=item["base_url"],
            topology=dict(item.get("topology") or {}),
            model=item.get("model"),
            username=item.get("username"),
            password_env=item.get("password_env"),
            insecure=bool(item.get("insecure", False)),
            timeout=item.get("timeout"),
        ))
    return out


#: `captured_at` has one-second resolution, so two `collect` runs inside one
#: second file records with the SAME `(surface, captured_at)` key. The store
#: tolerates it -- append-only keeps both lines and latest-wins answers with the
#: newer -- but note that `ingest` REFUSES that shape from files while `collect`
#: creates it directly. **`--etag-cache` makes it likelier rather than causing
#: it**: a skipped surface returns in milliseconds where a walk took seconds.
#: Left as it is deliberately: adding sub-second precision would mix two
#: timestamp spellings in one store, and `2026-01-01T00:00:00.5Z` sorts BEFORE
#: `2026-01-01T00:00:00Z` as text, which would silently reorder history.


class Collector:
    """Walks targets serially, with backoff, and files what it finds."""

    def __init__(self, backend: Backend, store: Store, *, collector_id: str,
                 attempts: int = 3, base_delay: float = 1.0,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], str] | None = None,
                 trigger: str = "scheduled", etag_cache: bool = False) -> None:
        if attempts < 1:
            raise CollectError("attempts must be at least 1")
        self.backend = backend
        self.store = store
        self.collector_id = collector_id
        self.attempts = attempts
        self.base_delay = base_delay
        self.sleep = sleep
        self.trigger = trigger
        self._clock = clock or _utc_now
        self.etag_cache = etag_cache
        #: Newest record carrying a payload, per surface. Read ONCE: nothing
        #: appends to the store during a run, and re-reading per target would be
        #: quadratic on a rack.
        self._previous: dict[tuple[str, ...], dict] | None = None
        #: Walk order, recorded so a test can assert serialization rather than
        #: assert that a thread pool was not imported.
        self.order: list[str] = []

    def run(self, targets: Sequence[Target]) -> list[dict]:
        return [self.walk(target) for target in targets]

    def _last_payload(self, surface: tuple[str, ...]) -> dict | None:
        if self._previous is None:
            newest: dict[tuple[str, ...], dict] = {}
            try:
                records = self.store.read_all()
            except Exception:  # noqa: BLE001 - an unreadable index is not fatal
                records = []
            for stored in records:
                record = stored.record
                if not record.get("payload_digest"):
                    continue
                key = surface_of(record)
                current = newest.get(key)
                if current is None or record.get("captured_at", "") >= current.get(
                        "captured_at", ""):
                    newest[key] = record
            self._previous = newest
        return self._previous.get(surface)

    def walk(self, target: Target) -> dict:
        """One target, with backoff, always producing exactly one record."""
        self.order.append(target.unit_key)
        surface = surface_of({"unit_key": target.unit_key,
                              "topology": dict(target.topology)})
        cache = None
        if self.etag_cache:
            path = self.store.etag_path(surface)
            path.parent.mkdir(parents=True, exist_ok=True)
            cache = str(path)
        capture = self._attempt(target, cache)
        captured_at = self._clock()

        record: dict[str, Any] = {
            "format": RECORD_FORMAT,
            "unit_key": target.unit_key,
            "captured_at": captured_at,
            "trigger": self.trigger,
            "collector": {"id": self.collector_id},
            "exit_code": capture.exit_code,
        }
        if target.topology:
            record["topology"] = dict(target.topology)
        if target.model is not None:
            record["model"] = target.model
        if capture.detail:
            record["detail"] = capture.detail
        if capture.raw_exit_code is not None:
            record["raw_exit_code"] = capture.raw_exit_code

        if capture.unchanged:
            previous = self._last_payload(surface)
            if previous is None:
                # The referee had a cache and this store has no capture to point
                # at. Somebody deleted records, or pointed two stores at one
                # cache. Either way there is nothing to reuse and inventing a
                # clean record would assert a payload that does not exist.
                record["exit_code"] = INCOMPLETE
                record["detail"] = (
                    "the BMC reports its sensor set unchanged and this store "
                    "holds no earlier capture to reuse; delete the etag cache "
                    "for this surface to force a full walk")
                return record
            record["payload_digest"] = previous["payload_digest"]
            record["walk_ref"] = previous["walk_ref"]
            # **What was proven, and by what.** The collection ETags say the
            # membership is the same. They say nothing about a threshold edited
            # on a sensor that stayed present, and a reader of this record has
            # to be able to tell which question it answers.
            record["unchanged"] = {
                "basis": "collection-etag",
                "proves": "membership",
                "reused_from": previous.get("captured_at"),
            }
            return record

        if capture.exit_code == CLEAN and capture.raw is not None:
            computed = digest_bytes(capture.raw)
            if (capture.reported_digest is not None
                    and capture.reported_digest != computed):
                # **The consumer is an oracle for the producer.** If the tool's
                # own handle disagrees with the bytes that arrived, one of them
                # is wrong and this collector cannot tell which -- so it files
                # neither as truth.
                record["exit_code"] = INCOMPLETE
                record["detail"] = (
                    f"the tool reported {capture.reported_digest} and the bytes "
                    f"that arrived digest to {computed}; a record cannot say "
                    f"which of the two describes the capture")
                return record
            self.store.put_payload(capture.raw)
            record["payload_digest"] = computed
            record["walk_ref"] = ref_for(computed)
        elif capture.exit_code == CLEAN:
            record["exit_code"] = INCOMPLETE
            record["detail"] = ("the backend reported success and returned no "
                                "payload; a clean capture that cannot produce "
                                "its walk is a claim, not a record")
        return record

    def _attempt(self, target: Target,
                 etag_cache: str | None = None) -> Capture:
        last = Capture(INCOMPLETE, detail="no attempt was made")
        for attempt in range(1, self.attempts + 1):
            try:
                # **Passed only when there is one.** The parameter was added
                # for `--etag-cache`, which is opt-in; a backend written against
                # the earlier protocol keeps working untouched while the feature
                # is off, and fails loudly the moment somebody turns it on --
                # which is the right time to find out, rather than breaking
                # every existing backend for a feature they did not ask for.
                last = (self.backend.capture(target) if etag_cache is None
                        else self.backend.capture(target, etag_cache))
            except CollectError as exc:
                last = Capture(INCOMPLETE, detail=str(exc))
            except Exception as exc:  # noqa: BLE001 - a backend must not kill a run
                last = Capture(
                    INCOMPLETE,
                    detail=f"{type(exc).__name__}: {exc}")
            if last.exit_code != INCOMPLETE or attempt == self.attempts:
                if attempt > 1 and last.exit_code != INCOMPLETE:
                    note = f"succeeded on attempt {attempt} of {self.attempts}"
                    last.detail = f"{last.detail}; {note}" if last.detail else note
                return last
            # Exponential, deterministic, and injected so a test can assert the
            # SHAPE of the backoff instead of waiting for it.
            self.sleep(self.base_delay * (2 ** (attempt - 1)))
        return last


def normalise_capture(raw_exit: int, **kwargs) -> Capture:
    """Build a `Capture` from a raw subprocess code, keeping the original."""
    code, note = normalise(raw_exit)
    detail = kwargs.pop("detail", "")
    if note:
        detail = f"{detail}; {note}" if detail else note
    return Capture(code, detail=detail,
                   raw_exit_code=raw_exit if note else None, **kwargs)


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_targets_file(path: str | os.PathLike[str]) -> list[Target]:
    text = Path(path).read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CollectError(f"{path} is not JSON: {exc}") from exc
    return read_targets(payload)
