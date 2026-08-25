"""The architectural constraint, pinned by reading the source.

This layer aggregates the referee's records. If it could reach into the
referee's internals to find out what the referee concluded, it would be marking
the exam with the answer key: a change that broke the tool's real output while
leaving its internals intact would still pass here, and the real output is the
only thing a fleet ever sees.

So the package talks to the tool as a subprocess and reads its published files.
This asserts that by reading the source, because the alternative -- trusting a
convention -- is how the convention gets broken by someone who did not know it
existed.

`collect/backends/mock.py` may import the tool, and the distinction is the
design: `MockBMC` is a fake *machine*. It stands in for the thing being walked,
not for the thing doing the walking.

The `qa-orchestrator` precedent, verbatim, and for the same reason.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "fleet_sensor_baseline"

#: The one module permitted to import the referee.
CARVE_OUT = SRC / "collect" / "backends" / "mock.py"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def _referee_imports(path: Path) -> set[str]:
    return {m for m in _imported_modules(path)
            if m.split(".")[0] == "bmc_sensor_audit"}


def _modules():
    return sorted(SRC.rglob("*.py"))


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_every_module_parses(path):
    ast.parse(path.read_text(encoding="utf-8"))


def test_nothing_but_the_mock_backend_imports_the_referee():
    for path in _modules():
        if path == CARVE_OUT:
            continue
        offending = _referee_imports(path)
        assert not offending, (
            f"{path.relative_to(SRC)} imports {sorted(offending)}. This layer "
            f"must read the tool through exit codes, stdout and the files it "
            f"writes only -- importing it means a change that breaks the "
            f"published output while leaving internals intact would still pass")


def test_the_mock_backend_still_imports_it():
    """Non-vacuity in the other direction.

    If nothing imported the tool anywhere, the test above would pass by finding
    nothing and the boundary it describes would be untested. The mock backend is
    the one place the import is correct, so its presence is what makes the
    absence elsewhere meaningful.
    """
    assert CARVE_OUT.is_file(), "the carve-out module is gone; re-point this"
    assert _referee_imports(CARVE_OUT), (
        "the mock backend no longer imports the referee's MockBMC, so the "
        "boundary test above is now asserting something nothing could violate")


def test_the_subprocess_backend_runs_it_rather_than_importing_it():
    """The production path, specifically. It is the one a fleet uses."""
    path = SRC / "collect" / "backends" / "subprocess_backend.py"
    assert not _referee_imports(path)
    assert "subprocess" in _imported_modules(path), (
        "the subprocess backend imports neither the tool nor subprocess; it is "
        "no longer reaching the referee at all")


def test_the_core_modules_do_not_import_the_collector_either():
    """A quieter boundary, and the reason the core has no dependencies.

    `ingest`, `baseline`, `outliers`, `drift`, `verdict` and `validate` are JSON
    and arithmetic. If any of them reached into `collect/`, installing this tool
    to read records somebody else gathered would start requiring the referee on
    PATH -- and the vertical axis is supposed to run on a jump host.
    """
    for name in ("store.py", "formats.py", "baseline.py", "outliers.py",
                 "drift.py", "verdict.py", "walk.py", "report.py", "exits.py"):
        imported = _imported_modules(SRC / name)
        offending = {m for m in imported if "collect" in m.split(".")}
        assert not offending, f"{name} imports {sorted(offending)}"
