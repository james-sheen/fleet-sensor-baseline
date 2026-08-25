"""The floor is enforced against the tool that ANSWERS, not the one pip resolved.

This package never imports `bmc_sensor_audit`. It runs it as a subprocess found
on **PATH**, and that is the whole reason this file exists: `pip` enforces
`bmc-sensor-audit>=X` over the environment it installed into, and then PATH
decides what actually runs. The two disagree whenever a system-wide install, a
pipx shim, or another activated venv sits earlier on PATH -- measured, with
`importlib.metadata` reporting 0.1.5 while PATH answered 0.1.1.

The guard that was supposed to cover this asserted `>= (0, 1, 1)` while the
package pinned 0.1.5 -- a tolerance left behind by two floor moves -- and it read
`importlib.metadata`, which is the claim, while its own docstring said it was
checking the installation because the installation is the fact.
"""

from __future__ import annotations

import subprocess

import pytest

from fleet_sensor_baseline.collect.backends import subprocess_backend as backend
from fleet_sensor_baseline.collect.backends.subprocess_backend import (
    VERSION_FLAG_SINCE, RefereeTooOld, SubprocessBackend, declared_floor)


def runner_saying(stdout="", stderr="", returncode=0):
    def run(argv):
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)
    return run


def backend_with(floor, monkeypatch, **kwargs):
    monkeypatch.setattr(backend, "declared_floor", lambda: floor)
    return SubprocessBackend(runner=runner_saying(**kwargs))


class TestTheVersionComesFromTheSubprocess:
    def test_it_reads_what_the_tool_on_path_prints(self):
        tool = SubprocessBackend(runner=runner_saying("bmc-sensor-audit 0.2.0\n"))
        assert tool.referee_version() == (0, 2, 0)

    def test_a_tool_that_cannot_answer_reports_none(self):
        """`--version` used to exit 2 with an argparse usage dump."""
        tool = SubprocessBackend(
            runner=runner_saying(stderr="usage: bmc-sensor-audit [-h]\n",
                                 returncode=2))
        assert tool.referee_version() is None

    def test_a_missing_tool_reports_none_rather_than_raising(self):
        def absent(argv):
            raise FileNotFoundError(2, "No such file or directory")
        assert SubprocessBackend(runner=absent).referee_version() is None


class TestTheFloorIsDerivedNotRestated:
    def test_it_is_read_from_the_packages_own_metadata(self):
        """The requirement lives once, in `pyproject.toml`. A copy in the module
        would be the same number written twice, and this floor has moved three
        times."""
        floor = declared_floor()
        assert floor is None or (isinstance(floor, tuple) and len(floor) == 3)

    def test_no_version_literal_is_restated_in_the_module(self):
        """Guards the property above. `VERSION_FLAG_SINCE` is a different fact --
        which release grew the flag -- and is allowed."""
        import inspect
        source = inspect.getsource(backend)
        body = source.split("def declared_floor", 1)[1]
        assert '"0.1.' not in body and '"0.2.' not in body


class TestTooOldIsRefused:
    def test_a_referee_below_the_floor_is_refused(self, monkeypatch):
        tool = backend_with((0, 2, 0), monkeypatch, stdout="bmc-sensor-audit 0.1.5\n")
        with pytest.raises(RefereeTooOld) as caught:
            tool.preflight()
        assert "0.1.5" in str(caught.value) and "0.2.0" in str(caught.value)

    def test_the_refusal_says_why_pip_did_not_catch_it(self, monkeypatch):
        """The operator's first reaction is *but I pinned it*. They did; the pin
        governs the environment, not PATH."""
        tool = backend_with((0, 2, 0), monkeypatch, stdout="bmc-sensor-audit 0.1.5\n")
        with pytest.raises(RefereeTooOld) as caught:
            tool.preflight()
        assert "PATH" in str(caught.value)

    def test_a_referee_at_the_floor_passes(self, monkeypatch):
        tool = backend_with((0, 2, 0), monkeypatch, stdout="bmc-sensor-audit 0.2.0\n")
        assert tool.preflight() == (0, 2, 0)

    def test_a_newer_referee_passes(self, monkeypatch):
        tool = backend_with((0, 2, 0), monkeypatch, stdout="bmc-sensor-audit 0.3.1\n")
        assert tool.preflight() == (0, 3, 1)


class TestSilenceIsItselfAVersionSignal:
    """A referee that cannot say what it is predates the flag, which is a FACT
    about its version rather than an absence of one."""

    def test_no_version_flag_is_refused_when_the_floor_needs_one(self, monkeypatch):
        tool = backend_with(VERSION_FLAG_SINCE, monkeypatch,
                            stderr="usage: bmc-sensor-audit [-h]\n", returncode=2)
        with pytest.raises(RefereeTooOld) as caught:
            tool.preflight()
        assert "cannot report a version" in str(caught.value)

    def test_no_version_flag_is_ACCEPTED_below_that_floor(self, monkeypatch):
        """Non-vacuity, and the half that keeps this honest: while the declared
        floor is a release that never had the flag, being unable to answer is
        not evidence of anything and must not be treated as a violation."""
        older = (VERSION_FLAG_SINCE[0], VERSION_FLAG_SINCE[1],
                 VERSION_FLAG_SINCE[2] - 1)
        tool = backend_with(older, monkeypatch,
                            stderr="usage: bmc-sensor-audit [-h]\n", returncode=2)
        assert tool.preflight() is None

    def test_an_unknown_floor_never_refuses(self, monkeypatch):
        """A source tree with no installed metadata cannot tell, and a check that
        cannot tell does not get to refuse."""
        tool = backend_with(None, monkeypatch,
                            stderr="usage: bmc-sensor-audit [-h]\n", returncode=2)
        assert tool.preflight() is None


class TestTheRefereeIsAskedOncePerRun:
    def test_preflight_runs_exactly_one_subprocess(self, monkeypatch):
        """A fleet run walks thousands of BMCs; the referee cannot change
        underneath it, so asking per target would be thousands of processes for
        one unchanging answer."""
        calls = []

        def counting(argv):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "bmc-sensor-audit 0.2.0\n", "")

        monkeypatch.setattr(backend, "declared_floor", lambda: (0, 2, 0))
        SubprocessBackend(runner=counting).preflight()
        assert len(calls) == 1
        assert calls[0][-1] == "--version"
