"""Fleet-wide sensor presence and configuration drift, across machines and time.

`bmc-sensor-audit` judges one machine against one declaration. This layer holds
the list of machines and the history of captures, and answers the two questions
the referee cannot: *which units differ from their cohort*, and *what changed on
this unit across time and firmware*.
"""

from __future__ import annotations

# One home for the version, read by `pyproject.toml` through hatchling. Two
# literals that happen to agree is the arrangement that drifts.
#
# **0.0.0 means unreleased, and it is load-bearing.** The community-file checks
# read this to decide whether the README may name a tag at all: an unreleased
# tree that announces `v0.1.0` hands a reader a tag to check out that does not
# exist. Bump this at step 1 of a release, not at step 9.
__version__ = "0.2.2"

__all__ = ["__version__"]
