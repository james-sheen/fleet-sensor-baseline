"""The publication vocabulary, and the surface it used to miss.

This repository had **no hygiene tooling at all** until an internal repository
nickname reached its commit message — and the commit messages of three sibling
repositories, from one message written once and reused. A commit message is the
one published surface that cannot be corrected after a push, and nothing here was
looking at any surface.

`tools/hygiene_check.py` is the same file in all four repositories, and the same
vocabulary now runs over BOTH surfaces: staged files at `pre-commit`, and the
message at `commit-msg`. The rule that matches a nickname already existed
elsewhere; what was missing was any guard here, and the surface it needed to
cover.

**A hook is not the durable gate** — `core.hooksPath` is per-clone and easy to
leave unset — so these tests assert the tool's behaviour rather than the hook's
activation. What a fresh clone gets is the file; what a contributor has to do is
one `git config`, and CONTRIBUTING says so.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "hygiene_check.py"
HOOK = ROOT / ".githooks" / "commit-msg"

#: The literal that reached four public commit messages. Held here so the
#: non-vacuity check below is against the real thing, not a paraphrase.
THE_LEAK = ("- The two checks are now byte-identical across all four. Repo #1 "  # hygiene: synthetic
            "tracks every\n  tree, so the check that they stay that way lives there.\n")


def hygiene():
    spec = importlib.util.spec_from_file_location("hygiene_check", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestTheToolIsPresentAndShipsTheRule:
    def test_the_tool_exists(self):
        assert TOOL.is_file(), (
            "this repository has no hygiene tooling; a public repo with no "
            "publication guard is one where the only check is somebody's attention")

    def test_the_nickname_rule_ships(self):
        assert "repository_nickname" in {r.name for r in hygiene().RULES}

    def test_it_catches_the_literal_that_shipped(self):
        module = hygiene()
        hits = module.scan_text(THE_LEAK, "(message)", module.RULES)
        assert [h[2].name for h in hits] == ["repository_nickname"]

    def test_it_does_not_fire_on_lookalikes(self):
        """`repo` sits inside `repository` and `reported`, and appears as a URL
        fragment. A word-level match would flag all three."""
        module = hygiene()
        for text in ("the repository holds it", "reported a defect",
                     "https://github.com/x/repo#readme", "repo number 3"):
            assert module.scan_text(text, "(m)", module.RULES) == [], text


class TestBothSurfacesAreCovered:
    def test_the_message_surface_exists(self, tmp_path):
        path = tmp_path / "msg.txt"
        path.write_text(THE_LEAK)
        result = subprocess.run([sys.executable, str(TOOL), "--message", str(path)],
                                capture_output=True, text=True, cwd=str(ROOT))
        assert result.returncode == 1
        assert "repository_nickname" in result.stderr

    def test_a_clean_message_passes(self, tmp_path):
        """Non-vacuity: a checker that refused everything would satisfy the test
        above and stop anybody committing."""
        path = tmp_path / "msg.txt"
        path.write_text("Anchor the release-tag rule on the version literal\n")
        result = subprocess.run([sys.executable, str(TOOL), "--message", str(path)],
                                capture_output=True, text=True, cwd=str(ROOT))
        assert result.returncode == 0, result.stderr

    def test_an_unreadable_message_exits_2_not_1(self, tmp_path):
        """Could-not-check is neither found-nothing nor found-something. The same
        three-valued contract the audit tool uses."""
        result = subprocess.run(
            [sys.executable, str(TOOL), "--message", str(tmp_path / "absent.txt")],
            capture_output=True, text=True, cwd=str(ROOT))
        assert result.returncode == 2

    def test_this_tree_is_clean_under_the_whole_vocabulary(self):
        """The file surface, over what this repository actually contains."""
        result = subprocess.run([sys.executable, str(TOOL), "--all"],
                                capture_output=True, text=True, cwd=str(ROOT))
        assert result.returncode == 0, result.stdout + result.stderr


class TestTheHooksRunTheVocabulary:
    def test_the_commit_msg_hook_runs_it_over_the_message(self):
        commands = [l for l in HOOK.read_text().splitlines()
                    if l.strip() and not l.lstrip().startswith("#")]
        assert any("hygiene_check.py" in l and "--message" in l for l in commands), (
            "the commit-msg hook does not run the publication vocabulary over the "
            "message, which is the surface that cannot be corrected after a push")

    def test_the_pre_commit_hook_runs_it_over_the_files(self):
        pre = ROOT / ".githooks" / "pre-commit"
        assert pre.is_file()
        commands = [l for l in pre.read_text().splitlines()
                    if l.strip() and not l.lstrip().startswith("#")]
        assert any("hygiene_check.py" in l for l in commands)

    def test_the_hook_refuses_the_message_that_shipped(self, tmp_path):
        path = tmp_path / "msg.txt"
        path.write_text(THE_LEAK)
        result = subprocess.run(["sh", str(HOOK), str(path)],
                                capture_output=True, text=True, cwd=str(ROOT))
        assert result.returncode != 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
