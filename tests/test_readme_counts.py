"""Every number the README states, pinned to something that derives it.

**A count in prose has no owner.** `bmc-sensor-audit` published *84* while its
suite was 91, and reached 104 before anyone read it -- false on a public surface
the whole time, because nothing compared the two. The alternative was deleting
the numbers; they are kept because they are what tells a reader the project is
measured at all, and a claim worth making is worth pinning.

**Wired before the first release rather than after the first embarrassment.**
That is the only difference between this file and the one that had to be written
upstream, and it is the reason it exists on day one here.

Each number below is derived from the thing it describes -- the parser, the
format table, the marker -- never from a second copy of the number.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from fleet_sensor_baseline import cli
from fleet_sensor_baseline.formats import FORMATS

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"


def _tracked_tests() -> list[str]:
    """The test files this repository HAS, asked of git rather than the disk.

    Falls back to the directory when git cannot answer, because an empty list
    would collect nothing and report a confident zero -- and a zero is the most
    dangerous wrong count there is.

    Git fails in two ways and only one is a return code: a checkout with no
    `.git` exits non-zero, and an image with no git BINARY raises. Catching only
    the first turns this into an error on any environment that runs the suite
    without git installed.

    **The population is what git TRACKS, not what the directory holds.** The
    README describes the repository; a count taken from a working tree carrying
    an uncommitted test file is true of that disk and false of this project.
    """
    try:
        listed = subprocess.run(["git", "ls-files", "--", "tests/test_*.py"],
                                cwd=str(ROOT), capture_output=True, text=True)
    except OSError:
        return [str(ROOT / "tests")]
    paths = [line for line in listed.stdout.split() if line]
    if listed.returncode != 0 or not paths:
        return [str(ROOT / "tests")]
    return [str(ROOT / path) for path in paths]


def _collect(*extra: str) -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *_tracked_tests(), *extra,
         "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=str(ROOT), capture_output=True, text=True,
        env={"PYTHONPATH": f"{ROOT / 'src'}:{ROOT / 'tests'}", "PATH": "/usr/bin:/bin"})
    found = re.search(r"(\d+)(?:/\d+)? tests? collected", result.stdout)
    assert found, f"could not read a collection count:\n{result.stdout[-500:]}"
    return int(found.group(1))


def _claimed(label: str) -> int:
    """A number from the README's Tests table, by the row it sits in.

    The row label is matched as a PREFIX and the rest of the cell is allowed to
    say anything, because it does -- one of these rows names the package it
    depends on. An exact-cell match read as a missing row the first time this
    ran, which is the failure mode where a tripwire reports on its own regex
    instead of on the number it was pointed at.
    """
    found = re.search(rf"\|\s*{label}[^|]*\|\s*(\d+)\s*\|", README.read_text())
    assert found, f"the README Tests table no longer states {label!r}"
    return int(found.group(1))


class TestTheTestCounts:
    def test_the_total_matches_what_pytest_collects(self):
        assert _claimed("tests collected") == _collect(), (
            "the README's test count and pytest disagree -- update the README "
            "in the same change that added or removed tests")

    def test_the_seam_count_matches_the_marker(self):
        """Derived by COLLECTION, not by running a lane and reading its skips.

        A skip count depends on what happens to be installed on the machine that
        measured it, so the number would be true there and false in CI. The
        marker answers the same everywhere.
        """
        assert _claimed("of those, requiring") == _collect("-m", "seam")

    def test_the_seam_lane_is_not_empty(self):
        """Non-vacuity. If the marker were dropped from every test, both numbers
        above would agree with a README saying zero, and the seam would be
        untested while the counts stayed green."""
        assert _collect("-m", "seam") > 0


class TestTheSurfaceCounts:
    def test_the_readme_documents_every_subcommand(self):
        """Read off the parser, so a subcommand added without a mention here is
        a red rather than an undocumented feature."""
        parser = cli.build_parser()
        actions = [a for a in parser._subparsers._group_actions
                   if hasattr(a, "choices")]
        names = sorted(actions[0].choices)
        readme = README.read_text()
        missing = [n for n in names
                   if f"fleet-sensor-baseline {n}" not in readme
                   and f"`{n}`" not in readme]
        assert not missing, (
            f"the CLI has {missing} and the README never names them")

    def test_the_readme_lists_every_format(self):
        readme = README.read_text()
        missing = [f for f in FORMATS if f not in readme]
        assert not missing, f"the README does not list {missing}"

    def test_the_format_table_has_a_row_per_format(self):
        """The count and the table, compared. A format added to the code and to
        the prose but not to the table leaves a reader one short."""
        rows = re.findall(r"^\|\s*`(fleet-sensor-baseline/[^`]+)`\s*\|",
                          README.read_text(), re.MULTILINE)
        assert sorted(rows) == sorted(FORMATS)


class TestThePinIsQuotedNotParaphrased:
    def test_the_readme_states_the_same_floor_as_the_packaging(self):
        """Two published records of one fact. The packaging is the one that
        binds, so the README is compared against it rather than the reverse."""
        pin = re.search(r'collect = \["(bmc-sensor-audit[^"]+)"\]',
                        (ROOT / "pyproject.toml").read_text())
        assert pin, "the collect extra no longer pins the referee"
        assert pin.group(1) in README.read_text(), (
            f"the README does not quote the pin {pin.group(1)!r} that "
            f"pyproject.toml declares")

    def test_the_floor_is_at_least_the_release_that_has_what_we_call(self):
        """`validate-walk` and `capture --print-digest` arrived in 0.1.1.

        Measured against every published release in the range before this was
        written -- 0.1.0 has neither. A pin is a claim about every release it
        admits, and this one is the claim.
        """
        pin = re.search(r'"bmc-sensor-audit>=(\d+)\.(\d+)\.(\d+)',
                        (ROOT / "pyproject.toml").read_text())
        assert pin, "the pin floor is no longer a three-part version"
        assert tuple(int(p) for p in pin.groups()) >= (0, 1, 1)
