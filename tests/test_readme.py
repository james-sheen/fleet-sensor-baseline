"""The README is the one surface that can promise what no index can deliver.

This package declares `0.0.0` and is on no index. The sentinel therefore means
what it says here -- *unreleased* -- and the checks below hold the README to it:
no `pip install fleet-sensor-baseline` before the name exists, and no tag string
handed to a reader before the tag does.

`qa-orchestrator` learned this the awkward way round: it copied a version
sentinel from a package that used `0.0.0` to mean unreleased, while declaring a
real `0.1.0` of its own. The sentinel read *released*, the check passed, and the
README went on describing an installation that could not happen. **A mechanism
copied without its premise is a check that runs correctly and asks the wrong
question**, so the premise is stated here rather than assumed.

## The converged block

`test_the_readme_names_the_tag_this_version_will_carry` and
`test_a_tag_and_the_tree_do_not_disagree` are **byte-identical across every
public repository in this family**, and that is enforced from outside -- none of
these packages can see the others, so the check that they are still one
implementation lives where all of them are visible. Edit them in one place and
the others go red; edit them here alone and the umbrella does.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from fleet_sensor_baseline import __version__

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
DIST = "fleet-sensor-baseline"

UNRELEASED = "Not yet released"

# The wording `bmc-sensor-audit` announces a release with. Reused verbatim rather
# than invented, so this family has one vocabulary for one fact and a future
# release here needs no new spelling.
RELEASED = re.compile(r"\*\*Released[^*]*?(\d+\.\d+\.\d+)\*\*")

# `pip install fleet-sensor-baseline`, quoted or not, with or without an extra --
# the form that only works once an index carries the name. The lookahead is what
# makes it usable while unreleased: `pip install "fleet-sensor-baseline @
# git+https://..."` is a direct reference, not an index lookup.
#
# Spelled strictly on purpose: a guard with false positives is a guard the next
# person loosens, and a loosened guard stops catching the real thing.
INDEX_INSTALL = re.compile(
    r"pip install\s+(?:-[^\s]+\s+)*['\"]?"
    + re.escape(DIST)
    + r"(?:\[[A-Za-z0-9,_\-]+\])?['\"]?(?!\s*@)")


# **The ANCHOR for every version record in this repository.** `pyproject.toml`
# reads the literal for packaging, so it is the one record that cannot disagree
# with the artifact -- and the only one that answers in an sdist and in a shallow
# checkout with no tags. Everything else is compared against it, never against
# another derivation of it.
NO_RELEASE = "0.0.0"

#: The tag the README's Status line names, so the two can be compared without
#: asking git anything.
TAGGED = re.compile(r"tagged `([^`]+)`")

#: The tag namespace this project releases in: `v` and a dotted version.
_TOOL_TAG = re.compile(r"^v(\d+(?:\.\d+)*)$")


def _released_versions(tags):
    """Every tag naming a version of THIS package, as comparable tuples."""
    return [tuple(int(part) for part in match.group(1).split("."))
            for match in (_TOOL_TAG.match(tag) for tag in tags) if match]


def _named_tag():
    """The tag string the README's Status line names, or None."""
    found = TAGGED.search(README.read_text())
    return found.group(1) if found else None


def _version():
    from fleet_sensor_baseline import __version__
    return __version__


def _tags():
    """Repository tags, or None when git cannot answer -- borrowed from
    `bmc-sensor-audit`, caveat included. A checkout with no `.git` exits
    non-zero and an image with no git binary raises; answering `[]` for either
    would turn *cannot tell* into *there are no tags*. A shallow clone fetched
    without tags answers successfully and is still not an answer.
    """
    try:
        listed = subprocess.run(["git", "tag"], cwd=str(ROOT),
                                capture_output=True, text=True)
    except OSError:
        return None
    if listed.returncode != 0:
        return None
    return [line for line in listed.stdout.split() if line]


class TestTheReadmeDoesNotPromiseAnIndex:

    def test_the_readme_states_a_release_state_at_all(self):
        """Non-vacuity, and it is the whole reason this file is not one test.

        Every rule below is conditional on one of these two markers being
        present. Without this, deleting the marker is a way to pass -- the
        prohibition would find nothing and report success, which is the failure
        shape this suite refuses everywhere else.
        """
        readme = README.read_text()
        assert UNRELEASED in readme or RELEASED.search(readme), (
            f"the README says neither {UNRELEASED!r} nor `**Released -- X.Y.Z**`. "
            f"A reader cannot tell whether the install line will work, and "
            f"nothing below can either")

    def test_an_unreleased_package_does_not_offer_an_index_install(self):
        readme = README.read_text()
        if RELEASED.search(readme):
            return
        found = INDEX_INSTALL.search(readme)
        assert not found, (
            f"the README says {UNRELEASED!r} and still shows "
            f"{found.group(0)!r}. That command fails with *No matching "
            f"distribution*, which reads to a newcomer as a broken tool rather "
            f"than an unpublished one")

    def test_a_released_package_announces_the_version_it_reports(self):
        released = RELEASED.search(README.read_text())
        if not released:
            return
        assert released.group(1) == __version__, (
            f"the README announces {released.group(1)} and the package reports "
            f"{__version__}; both are published records of one fact")

    def test_the_readme_names_the_tag_this_version_will_carry(self):
        """The tag string and the version literal, compared without asking git.

        **The anchor is the version LITERAL.** It is what the package reports
        about itself, what `pyproject.toml` reads for packaging, and the only one
        of these records that answers in an sdist and in a shallow checkout with
        no tags. Every other record is derived from it, so every other record is
        compared against it rather than against another derivation.

        The Status line carries a tag string that nothing used to check, so a
        `v0.1.1` left behind by a bump to 0.1.2 sent a reader to a tag describing
        different code -- and both strings look right in isolation.

        Tree-local, so it holds at every instant of a release. The check below
        cannot say that of itself.
        """
        version = _version()
        named = _named_tag()
        if version == NO_RELEASE:
            assert named is None, (
                f"the README names the tag {named!r} while the package reports "
                f"{NO_RELEASE}; an unreleased tree must not hand a reader a tag "
                f"to check out")
            return
        assert named, (
            f"the package reports {version} and the README names no tag. The "
            f"Status line should read: tagged `v{version}`")
        assert named == f"v{version}", (
            f"the README names the tag {named!r} and the package reports "
            f"{version}; they must be `v{version}`. A leading v dropped from one, "
            f"or a tag string left behind by a bump, is how these two part company")

    def test_a_tag_and_the_tree_do_not_disagree(self):
        """A tag is the one part of the claim the tree cannot write about itself.

        **What was wrong with this before.** It tolerated only a repository with
        NO TAGS AT ALL -- true when written, false forever after the first
        release. The tag is made OF the commit that bumps the version, so from
        then on it went red between the bump and the tag, every release, at the
        moment somebody is most likely to reach for `--no-verify`.

        Worse than red, it RACED. CI fetches whatever tags the remote holds at
        checkout and a release pushes master before the tag, so a release
        commit's own CI run passed or failed on which push won.

        **The window is carved to the rule rather than widened.** Only this
        version may be untagged, and only while no LATER version is tagged: a
        release in flight is always the newest one. A reverted bump that left its
        tag, or a tag made from the wrong commit, both leave a later tag behind
        and still fail here.

        Whether the tag was ever PUSHED is a fact about the remote, and no
        assertion from a working tree can reach it. Saying so is the honest
        version; asserting it would be a check that is right by luck.
        """
        tags = _tags()
        if not tags:
            pytest.skip("no tags visible here; *cannot tell* is not *no tags*")
        version = _version()
        assert version != NO_RELEASE, (
            f"the repository has tags {tags} and the package still reports "
            f"{NO_RELEASE}")
        if f"v{version}" in tags:
            return
        current = tuple(int(part) for part in version.split("."))
        ahead = sorted(t for t in _released_versions(tags) if t > current)
        assert not ahead, (
            f"v{version} has no tag, and "
            f"{['v' + '.'.join(map(str, t)) for t in ahead]} name later versions. "
            f"A release in flight is the only reason this version should be "
            f"untagged, and a release in flight is always the newest one -- so "
            f"either a bump was reverted with its tag left behind, or a tag was "
            f"made from the wrong commit")
        pytest.skip(
            f"v{version} is not tagged in this tree. The tag is made OF the "
            f"commit that sets the version literal, so this is the one legitimate "
            f"window and `git tag -a v{version}` closes it. Whether the tag was "
            f"ever pushed is a fact about the remote rather than this tree.")

class TestTheReadmeCanBeActedOn:
    """A reader with only this file has to be able to obtain and run the tool."""

    def test_it_shows_how_to_obtain_the_command(self):
        readme = README.read_text()
        assert ("pip install" in readme or "git clone" in readme), (
            "the README shows the command being run and no way to obtain it. "
            "Silence is not a false claim and a reader still cannot act on it")

    def test_it_names_the_exit_contract(self):
        """The one thing a caller MUST know before wiring this into anything."""
        readme = README.read_text()
        for token in ("0", "1", "2"):
            assert token in readme
        assert "exit" in readme.lower()
