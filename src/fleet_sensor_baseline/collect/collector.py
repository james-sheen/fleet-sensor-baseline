"""Walking a rack's worth of targets, one at a time, and binding identity.

**Identity binds here and nowhere upstream.** The referee's walk payload carries
no `unit_key` -- *the parse is the redaction*, and no identity field enters
`walk/1`. This is the layer whose job is to name things, so `{unit_key,
topology, digest, walk_ref}` is assembled on this side of the line and travels
in this layer's own envelopes.

## What this collector does not do, and why saying so matters

Two capabilities in the specification are **not reachable through the referee's
published surface**, and both are recorded here rather than faked:

- **Conditional requests (ETag).** `bmc-sensor-audit capture` exposes no
  `If-None-Match`, so a collector cannot ask a BMC *has this changed*. What is
  implemented instead is digest deduplication: an unchanged walk stores no new
  object and its record points at the one already there. That saves storage and
  saves nothing on the wire, and the difference is real -- the BMC is still
  walked. Closing it needs an upstream surface, not a workaround here.
- **Pinned-certificate reads toward BMCs.** The referee's only TLS control is
  `--insecure`. A collector that wants certificate pinning cannot express it,
  so this one does not claim to.

A third is a hazard rather than a gap: `capture` takes `--password` **in
argv**, where `ps` can read it on a shared host. This collector never puts a
password in a targets file -- it takes the NAME of an environment variable and
reads the value at the moment of the call -- but the value still crosses argv
on the way to the subprocess, and no amount of care on this side changes that.
All three are filed for upstream in `docs/upstream-asks.md`.
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
from ..store import Store, digest_bytes, ref_for


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


class Backend(Protocol):
    """How a collector reaches a BMC. Subprocess in production, mock in tests."""

    def capture(self, target: Target) -> Capture:  # pragma: no cover - protocol
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


class Collector:
    """Walks targets serially, with backoff, and files what it finds."""

    def __init__(self, backend: Backend, store: Store, *, collector_id: str,
                 attempts: int = 3, base_delay: float = 1.0,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], str] | None = None,
                 trigger: str = "scheduled") -> None:
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
        #: Walk order, recorded so a test can assert serialization rather than
        #: assert that a thread pool was not imported.
        self.order: list[str] = []

    def run(self, targets: Sequence[Target]) -> list[dict]:
        return [self.walk(target) for target in targets]

    def walk(self, target: Target) -> dict:
        """One target, with backoff, always producing exactly one record."""
        self.order.append(target.unit_key)
        capture = self._attempt(target)
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

    def _attempt(self, target: Target) -> Capture:
        last = Capture(INCOMPLETE, detail="no attempt was made")
        for attempt in range(1, self.attempts + 1):
            try:
                last = self.backend.capture(target)
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
