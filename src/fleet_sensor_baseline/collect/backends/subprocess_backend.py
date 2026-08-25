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

**TLS is declared, never defaulted.** A target's `pin_sha256` wins, then the
run's `--cafile`, then `insecure`. They are mutually exclusive by construction
here and refused as a pair by the format validator, so an operator who declared
a pin can never silently get an unverified connection.

**A skipped walk is read from a declared line, not from prose.** `capture`
prints one `OUTCOME ` line and the referee publishes it as contract. Until
0.1.3 there was no such line and this matched a sentence instead -- which
worked, and rested on nothing.
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

#: The referee's declared outcome line, published as CONTRACT from 0.1.3.
#:
#: **This used to match a printed sentence**, because until 0.1.3 that was the
#: only signal a skip gave: it exits `0` like a walk, and writes no file like a
#: failure. Prose can be reworded without that reading as a breaking change, so
#: the match was a guess with no promise behind it. Reported as issue #6 and
#: closed there; the pin floor moved to `>=0.1.3` to consume it.
OUTCOME_LINE = re.compile(r"^OUTCOME (\w+)$", re.MULTILINE)

#: The values this build knows how to act on. A value outside it is `2`, not a
#: guess -- a vocabulary that grew a member is exactly the case where guessing
#: silently picks the wrong branch.
OUTCOMES = {"walked", "unchanged"}

DEFAULT_COMMAND = ("bmc-sensor-audit",)

#: The release that gave the referee a `--version` flag. Before it, `--version`
#: exited 2 with an argparse usage error, so a referee that cannot answer is not
#: an unknown version -- it is conclusively OLDER than this one. That inference
#: is what lets an absent flag be treated as a floor violation rather than as a
#: shrug.
VERSION_FLAG_SINCE = (0, 2, 0)

VERSION_LINE = re.compile(r"^bmc-sensor-audit\s+(\d+)\.(\d+)\.(\d+)")


class RefereeTooOld(Exception):
    """The tool on PATH is below the floor this package declares.

    Worth its own type because it is INCOMPLETE (2), never a finding (1): a run
    that used the wrong referee has not audited a fleet badly, it has not
    audited it at all.
    """


def declared_floor():
    """The floor this package declares, read from its OWN metadata.

    Not a constant repeated here. The requirement lives once, in
    `pyproject.toml`, and ships into the installed distribution's metadata; a
    second copy in this module would be the same number written twice and would
    drift the first time the floor moved -- which it has done three times.

    Returns None from a source tree with no installed metadata, which is a
    can-not-tell rather than a pass.
    """
    from importlib import metadata
    try:
        requirements = metadata.requires("fleet-sensor-baseline") or []
    except metadata.PackageNotFoundError:
        return None
    for requirement in requirements:
        if not requirement.startswith("bmc-sensor-audit"):
            continue
        found = re.search(r">=\s*(\d+)\.(\d+)\.(\d+)", requirement)
        if found:
            return tuple(int(g) for g in found.groups())
    return None


class SubprocessBackend:
    """Runs `bmc-sensor-audit capture` once per target."""

    def __init__(self, command=DEFAULT_COMMAND, *, runner=None,
                 cafile: str | None = None) -> None:
        self.command = tuple(command)
        #: One trust store for the whole run. Per-target CA files are not
        #: offered because nobody has needed one: a fleet either shares an
        #: internal CA or uses self-signed certificates, and the second case is
        #: what `pin_sha256` is for.
        self.cafile = cafile
        #: Injected so a test can assert the ARGV this backend builds without
        #: needing the tool installed. The default is the real thing.
        self.runner = runner or _run

    def referee_version(self):
        """What the tool on PATH says it is, or None if it cannot say.

        The version that matters is not the one pip resolved. This package never
        imports the referee -- it runs it as a subprocess found on PATH -- so a
        system-wide install, a pipx shim, or another venv earlier on PATH answers
        instead, and the declared floor never sees it. Measured: metadata can
        report 0.1.5 while PATH answers 0.1.1.
        """
        try:
            done = self.runner([*self.command, "--version"])
        except FileNotFoundError:
            return None
        found = VERSION_LINE.search((done.stdout or "") + (done.stderr or ""))
        return tuple(int(g) for g in found.groups()) if found else None

    def preflight(self):
        """Check the referee ONCE per run, before any machine is walked.

        Once, not per target: a fleet run walks thousands of BMCs and the
        referee cannot change underneath it. Returns the version it observed so
        the caller can record it beside the verdicts -- a reader asking which
        referee produced a record should not have to guess.
        """
        floor = declared_floor()
        observed = self.referee_version()
        if observed is None:
            if floor is not None and floor >= VERSION_FLAG_SINCE:
                raise RefereeTooOld(
                    f"{self.command[0]} on PATH cannot report a version, so it "
                    f"predates {'.'.join(map(str, VERSION_FLAG_SINCE))}; this "
                    f"build needs >= {'.'.join(map(str, floor))}")
            return None
        if floor is not None and observed < floor:
            raise RefereeTooOld(
                f"{self.command[0]} on PATH is "
                f"{'.'.join(map(str, observed))}; this build needs >= "
                f"{'.'.join(map(str, floor))}. The floor pip enforced applies "
                f"to the environment it installed, not to what PATH resolves")
        return observed

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
            if target.pin_sha256:
                argv += ["--pin-sha256", target.pin_sha256]
            elif self.cafile is not None:
                argv += ["--cafile", self.cafile]
            elif target.insecure:
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
                found = OUTCOME_LINE.search(completed.stdout or "")
                outcome = found.group(1) if found else None
                if outcome is not None and outcome not in OUTCOMES:
                    # The contract grew a value this build has never seen.
                    # Guessing which branch it means is how a consumer silently
                    # does the wrong thing for a whole fleet.
                    return Capture(2, detail=(
                        f"the tool reported OUTCOME {outcome!r}, which this "
                        f"build does not know how to act on"))
                if outcome == "unchanged":
                    return Capture(CLEAN, unchanged=True,
                                   detail="the BMC reports its sensor set "
                                          "unchanged; not re-walked")
                if not out.is_file():
                    return Capture(2, detail=(
                        f"the tool exited 0, reported OUTCOME {outcome or '(none)'} "
                        f"and wrote no file at {out}"))
                capture.raw = out.read_bytes()
            return capture


def subprocess_backend(command=DEFAULT_COMMAND, *, runner=None,
                       cafile: str | None = None) -> SubprocessBackend:
    return SubprocessBackend(command, runner=runner, cafile=cafile)


def _run(argv):
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def _first_line(text: str | None) -> str:
    if not text:
        return ""
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""
