"""Reaching the referee the way a fleet does: as a command.

**This module does not import `bmc_sensor_audit`.** It runs it. If it could
import the tool, a change that broke the tool's published output while leaving
its internals intact would still pass every test here -- and the published
output is the only thing a real deployment ever sees.

What is read back: the exit code, the digest line printed by `--print-digest`,
and the bytes of the file the tool wrote. Three surfaces, all published, all
checked against each other.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from ...exits import CLEAN
from ..collector import Capture, CollectError, Target, normalise_capture

#: What `capture --print-digest` prints. Matched loosely on the label and
#: strictly on the value: the label is prose and may be re-spaced, the digest is
#: the contract.
DIGEST_LINE = re.compile(r"\bdigest\s+(sha256:[0-9a-f]{64})\b")

DEFAULT_COMMAND = ("bmc-sensor-audit",)


class SubprocessBackend:
    """Runs `bmc-sensor-audit capture` once per target."""

    def __init__(self, command=DEFAULT_COMMAND, *, runner=None) -> None:
        self.command = tuple(command)
        #: Injected so a test can assert the ARGV this backend builds without
        #: needing the tool installed. The default is the real thing.
        self.runner = runner or _run

    def capture(self, target: Target) -> Capture:
        with tempfile.TemporaryDirectory(prefix="fsb-capture-") as tmp:
            out = Path(tmp) / "walk.json"
            argv = list(self.command) + [
                "capture", "--target", target.base_url,
                "--out", str(out), "--print-digest"]
            if target.username:
                argv += ["--username", target.username]
            password = target.password()
            if password is not None:
                # **This crosses argv, and that is a real exposure on a shared
                # host.** The referee publishes no other way to pass one. Filed
                # upstream rather than papered over; see the module docstring in
                # `collect/collector.py`.
                argv += ["--password", password]
            if target.insecure:
                argv += ["--insecure"]
            if target.timeout is not None:
                argv += ["--timeout", str(target.timeout)]

            try:
                completed = self.runner(argv)
            except FileNotFoundError as exc:
                raise CollectError(
                    f"{self.command[0]} is not on PATH: {exc.strerror or exc}"
                ) from exc

            capture = normalise_capture(completed.returncode,
                                        detail=_first_line(completed.stderr))
            found = DIGEST_LINE.search(completed.stdout or "")
            if found:
                capture.reported_digest = found.group(1)
            if capture.exit_code == CLEAN:
                if not out.is_file():
                    return Capture(
                        2, detail=f"the tool exited 0 and wrote no file at {out}")
                capture.raw = out.read_bytes()
            return capture


def subprocess_backend(command=DEFAULT_COMMAND, *, runner=None) -> SubprocessBackend:
    return SubprocessBackend(command, runner=runner)


def _run(argv):
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def _first_line(text: str | None) -> str:
    if not text:
        return ""
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""
