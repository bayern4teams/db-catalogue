"""Plan section 2.4: no name from the user's databases in `packages/*/src` or in these fixtures.

The fix must work on any catalogue, so no source, table or column name from the live test may be
hard-coded in application code, and the fixtures here use invented generic names. The baseline
recorded in Phase 0 is zero hits; this stays a control that must keep passing for every item.

Hard-coded numbers cannot be found by a grep of names. The acceptance file lists the diff command
the reviewer runs for those (see `.handoff/PHASE0.acceptance.md`).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Built from fragments so this file does not contain the literals it forbids elsewhere.
NAMES = [
    "zoom" + "info", "diff" + "bot", "enrichment" + "_engine", "company" + "revenue",
    "company" + "ticker", "company" + "employees", "company" + "experience",
    "industry" + "classification",
]
PATTERN = re.compile("|".join(NAMES), re.IGNORECASE)


def _hits(paths):
    found = []
    for path in paths:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if PATTERN.search(line):
                found.append(f"{path.relative_to(ROOT)}:{number}")
    return found


def test_no_corpus_name_in_application_source():
    sources = sorted(ROOT.glob("packages/*/src/**/*.py"))
    assert sources, "the glob must find the packages"
    assert _hits(sources) == []


def test_the_fixtures_use_invented_names_only():
    here = Path(__file__).resolve()
    mine = [p for p in sorted(here.parent.glob("*.py")) if p != here]
    assert len(mine) >= 7
    assert _hits(mine) == []
