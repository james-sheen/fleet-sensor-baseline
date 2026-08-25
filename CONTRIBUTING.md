# Contributing

## Turn the hooks on

A fresh clone gets the hook files and does not get them activated — `core.hooksPath`
is per-clone and git will not set it for you. One command:

```
git config core.hooksPath .githooks
```

That wires two checks:

- **`pre-commit`** runs the publication vocabulary over the staged files.
- **`commit-msg`** runs the same vocabulary over the message.

Both matter, and the second is the one that was missing across four repositories
in this family until a nickname reached their public commit messages. **A commit
message is the one published surface that cannot be corrected after a push** — a
force-push supersedes a commit, it does not remove it; the old object still
resolves by hash.

`git commit --no-verify` skips both. It is there for when you know why.

## The rules that are not negotiable

**Nothing in `src/` may import `bmc_sensor_audit`**, with exactly one exception:
`collect/backends/mock.py`, which imports `MockBMC` because that is a fake
*machine* rather than a fake referee. `tests/test_boundary.py` asserts both
halves — the absence everywhere else, and the presence there, because without
the second the first would pass by finding nothing.

**A number in prose has an owner.** Every count the README states is derived by
`tests/test_readme_counts.py` from the thing it describes. If you add a test,
that file goes red until the README agrees. That is the point.

**A guard proves it can refuse.** Every validator, every comparison, every
threshold here has a test showing it saying no to something. A check that has
never refused anything is not evidence.

**Could-not-check is not found-nothing.** Exit `2` when the answer is unknown,
`1` when something was found, and say which in prose. A skip explains itself.

## Running the tests

```
python3 -m pytest tests/ -q                 # 185 passed, 15 skipped
pip install -e ".[collect]" && pytest -q    # 199 passed, 1 skipped
```

The 14 that skip without `bmc-sensor-audit` carry the `seam` marker and exercise
the upstream boundary. The 15th skips while this repository has no tags.

Run `python3 tools/hygiene_check.py --all` before opening anything.

## Style

Docstrings say **why**, and name the specific defect a guard exists for. A
comment that restates the code is noise; a comment recording the mistake that
made the line necessary is the only durable form of that knowledge.
