"""`| head` is not a fault in the fleet.

**The exit code is the claim, and the claim is about a rack.** A reader that
stops reading has said something about itself, not about the machines, so the
code this tool returns must not move when somebody pipes a report into `head`.

Until this was guarded it moved two different ways, and 0.2.2 shipped with both.
A report long enough to fill the pipe buffer raised `BrokenPipeError` out of
`print`, escaped `main`, and left Python to exit `1` -- and `1` in this
vocabulary means FINDINGS, so a truncated report was indistinguishable from a
complete one to anything reading the code. A short report failed later instead,
at the interpreter's shutdown flush, printing `Exception ignored` and exiting
`120`, which `exits.normalise` reads as INCOMPLETE. Either way an aggregator
files a rack on the strength of where the operator's terminal stopped.

`--help` was in the second class, which is the part worth stating plainly: the
likeliest thing anybody pipes was the likeliest thing to fail.

The sibling package guards this and has since its own two occurrences. This is
the third in the family. The rule was written down after the second; writing it
down is not a mechanism, and a test is, which is why this file exists rather
than a line in a document.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from conftest import record  # noqa: E402

#: Enough one-line results to carry the rendered output comfortably past a 64KB
#: pipe buffer, so the failure lands in `print` rather than at the shutdown
#: flush. Both paths are exercised below; non-vacuity is asserted, not assumed.
WIDE = 1200


def _write_records(where: Path, count: int, *, valid: bool) -> list[str]:
    paths = []
    for index in range(count):
        path = where / f"r{index:05d}.json"
        if valid:
            payload = record(f"rack-01/unit{index:05d}",
                             captured_at="2026-08-20T00:00:00Z",
                             digest="sha256:" + f"{index:064x}")
        else:
            payload = {"format": "fleet-sensor-baseline/fleet-record/1"}
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths.append(str(path))
    return paths


@pytest.fixture(scope="module")
def wide_clean(tmp_path_factory):
    """A long report whose verdict is CLEAN, and the reason it must be.

    **An unhandled `BrokenPipeError` makes Python exit `1`.** A fixture whose
    honest verdict is also `1` cannot tell a preserved verdict from a crash --
    the two agree by coincidence and the assertion passes against the unguarded
    code. `validate` answers only `0` or `2`, so every fixture here is already
    clear of the crash code; this one is pinned anyway, because the day
    `validate` grows a FINDINGS path is the day these tests go quiet without
    going red.
    """
    where = tmp_path_factory.mktemp("wide_clean")
    return ["validate", *_write_records(where, WIDE, valid=True)]


@pytest.fixture(scope="module")
def wide_incomplete(tmp_path_factory):
    """The same length, verdict INCOMPLETE.

    Kept because `2` is the verdict an operator most often pipes into `head` --
    the run that found something wrong is the run worth skimming.
    """
    where = tmp_path_factory.mktemp("wide_incomplete")
    return ["validate", *_write_records(where, WIDE, valid=False)]


@pytest.fixture(scope="module")
def narrow(tmp_path_factory):
    """A run whose report is short, and whose verdict is CLEAN.

    Too little output to fail during `print`, so it reaches the shutdown flush
    instead -- the path that produced `Exception ignored` and `120`.
    """
    where = tmp_path_factory.mktemp("narrow")
    return ["validate", *_write_records(where, 2, valid=True)]


def _environment() -> dict:
    return {"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"}


def _unpiped(argv) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "fleet_sensor_baseline.cli", *argv],
                          capture_output=True, text=True, env=_environment())


def _through_head(argv, lines: int) -> tuple[int, str]:
    """`(writer exit code, writer stderr)` -- the writer's, never the pipe's.

    A shell pipeline reports the LAST command's status, which here is `head`,
    which is always `0`. Measuring that would make every assertion below
    vacuously true; this project has three recorded instances of exactly that
    mistake, so the writer is held open and asked directly.
    """
    writer = subprocess.Popen(
        [sys.executable, "-m", "fleet_sensor_baseline.cli", *argv],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=_environment())
    reader = subprocess.Popen(["head", "-n", str(lines)], stdin=writer.stdout,
                              stdout=subprocess.DEVNULL)
    writer.stdout.close()
    reader.wait()
    stderr = writer.stderr.read()
    writer.stderr.close()
    writer.wait()
    return writer.returncode, stderr


class TestTheReportIsLongEnoughToReachTheDefect:
    def test_the_wide_fixture_would_fill_a_pipe_buffer(self, wide_clean):
        """Non-vacuity for everything below. A report that fits inside the 64KB
        buffer never makes the writer notice the reader has gone, so a suite
        built on a short report passes against the unguarded code."""
        rendered = _unpiped(wide_clean)
        assert len(rendered.stdout) > 65536, (
            f"the report is {len(rendered.stdout)} bytes and cannot close a "
            f"pipe before the writer finishes")

    def test_the_incomplete_fixture_is_long_too(self, wide_incomplete):
        assert len(_unpiped(wide_incomplete).stdout) > 65536

    def test_no_fixture_shares_the_crash_code(self, wide_clean, wide_incomplete,
                                              narrow):
        """`1` is what an unhandled exception exits with. If any fixture ever
        returns it, the assertions below agree with a crash by coincidence."""
        for argv in (wide_clean, wide_incomplete, narrow):
            assert _unpiped(argv).returncode != 1


class TestAClosedPipeDoesNotMoveTheVerdict:
    def test_a_long_clean_report_keeps_its_exit_code(self, wide_clean):
        """**The assertion the whole guard exists for.** Not *no traceback* --
        a run that printed nothing and exited 120 would satisfy that."""
        expected = _unpiped(wide_clean).returncode
        assert expected == 0
        code, _ = _through_head(wide_clean, 10)
        assert code == expected, (
            f"piping the report changed the verdict from {expected} to {code}")

    def test_a_long_incomplete_report_keeps_its_exit_code(self, wide_incomplete):
        expected = _unpiped(wide_incomplete).returncode
        assert expected == 2
        code, _ = _through_head(wide_incomplete, 10)
        assert code == expected

    def test_a_clean_run_stays_clean(self, narrow):
        """The direction a refusal-shaped fix would break. Mapping a closed pipe
        to INCOMPLETE would file a healthy rack as unwalked."""
        assert _unpiped(narrow).returncode == 0
        code, _ = _through_head(narrow, 1)
        assert code == 0

    def test_a_reader_that_leaves_immediately_is_also_clean(self, narrow):
        """The shutdown-flush path, which is where `--help` failed."""
        code, stderr = _through_head(narrow, 0)
        assert code == 0, f"exit {code} for a clean run whose reader left"
        assert "Exception ignored" not in stderr


class TestNothingIsPrintedAboutIt:
    @pytest.mark.parametrize("lines", [0, 1, 10])
    def test_no_traceback_reaches_the_operator(self, wide_clean, lines):
        _, stderr = _through_head(wide_clean, lines)
        assert stderr == "", stderr

    @pytest.mark.parametrize("argv", ["--help", "validate --help",
                                      "baseline --help", "collect --help"])
    def test_help_survives_its_likeliest_reader(self, argv):
        """`--help | head` is the commonest pipe there is, and it was broken on
        every subcommand. Parametrised over four because argparse exits from
        inside `parse_args`, before anything this program wrote could run."""
        code, stderr = _through_head(argv.split(), 0)
        assert stderr == "", stderr
        assert code == 0
