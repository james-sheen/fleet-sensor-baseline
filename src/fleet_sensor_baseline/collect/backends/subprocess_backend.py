"""Reaching the referee the way a fleet does: as a command.

**This module does not import `bmc_sensor_audit`.** It runs it. If it could
import the tool, a change that broke the tool's published output while leaving
its internals intact would still pass every test here -- and the published
output is the only thing a real deployment ever sees.

What is read back: the exit code, the digest line printed by `--print-digest`,
and the bytes of the file the tool wrote. Three surfaces, all published, all
checked against each other.

**Credentials never cross argv.** A target names an environment variable and
this backend passes the NAME through `--password-env`; the referee reads the
value in its own process. It still checks the variable is set before spawning,
so a missing credential is a refusal here rather than a misleading 401 from the
BMC.
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

#: How the referee announces that it did not walk. **Matched as prose, because
#: prose is the only signal it gives**: the skip exits 0, like a walk. Anchored
#: on the two words that carry the meaning rather than the whole sentence, which
#: also names a timestamp and a count.
UNCHANGED_LINE = re.compile(r"\bsensor set unchanged\b")

DEFAULT_COMMAND = ("bmc-sensor-audit",)


class SubprocessBackend:
    """Runs `bmc-sensor-audit capture` once per target."""

    def __init__(self, command=DEFAULT_COMMAND, *, runner=None) -> None:
        self.command = tuple(command)
        #: Injected so a test can assert the ARGV this backend builds without
        #: needing the tool installed. The default is the real thing.
        self.runner = runner or _run

    def capture(self, target: Target, etag_cache: str | None = None) -> Capture:
        with tempfile.TemporaryDirectory(prefix="fsb-capture-") as tmp:
            out = Path(tmp) / "walk.json"
            argv = list(self.command) + [
                "capture", "--target", target.base_url,
                "--out", str(out), "--print-digest"]
            if etag_cache is not None:
                argv += ["--etag-cache", etag_cache]
            if target.username:
                argv += ["--username", target.username]
            if target.password_env is not None:
                # **The value never enters argv.** The referee reads the
                # variable itself, in its own process. Until 0.1.2 the only way
                # to pass a password was `--password VALUE`, where `ps` shows it
                # to every user on the host for the length of the walk -- and a
                # rack collector walks continuously. Reported from here as
                # issue #4 and released there; this is the whole reason the
                # floor moved to 0.1.2.
                target.password()   # refuses early if the variable is unset
                argv += ["--password-env", target.password_env]
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
            if capture.exit_code == CLEAN and not out.is_file():
                # **A skip and a silent failure look identical from here**: exit
                # 0 and no file, because the out path is a fresh temporary
                # directory every call. The referee distinguishes them only in
                # prose, so this reads the prose -- and says so rather than
                # pretending the signal is structural. Filed upstream; see the
                # module docstring in `collect/collector.py`.
                if UNCHANGED_LINE.search(completed.stdout or ""):
                    return Capture(CLEAN, unchanged=True,
                                   detail="the BMC reports its sensor set "
                                          "unchanged; not re-walked")
                return Capture(
                    2, detail=f"the tool exited 0 and wrote no file at {out}")
            if capture.exit_code == CLEAN:
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
