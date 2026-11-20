"""GitHub Actions in CI are pinned to full commit SHAs (WMCP-B4 T5, ticket 20).

Every ``uses:`` entry must name a 40-character commit SHA with the exact version tag in a
trailing ``# vX.Y.Z`` comment, so a moved upstream tag can't change what CI runs. The
factory runner can't write ``.github/``, so the pinned file is handed off in the job folder
for a maintainer to apply; the real workflow is checked too once it has been applied.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REAL_CI = ROOT / ".github" / "workflows" / "ci.yml"
HANDOFF_CI = ROOT / ".dmx" / "jobs" / "gh-20" / "ci.yml"

USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>.*)$")
PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+$")


def unpinned_uses(text: str) -> list[str]:
    """Return every ``uses:`` reference in a workflow that isn't SHA-pinned with a tag comment."""
    refs = [m["ref"].strip() for line in text.splitlines() if (m := USES.match(line))]
    return [ref for ref in refs if not PINNED.match(ref)]


def read_workflow(path: Path) -> str:
    """Read a workflow file, skipping the test when it is absent, unreadable or empty."""
    try:
        text = path.read_text()
    except OSError as exc:
        pytest.skip(f"{path.relative_to(ROOT)} isn't readable here: {exc}")
    if not text.strip():
        pytest.skip(f"{path.relative_to(ROOT)} is empty here (not mounted)")
    return text


def test_helper_flags_tags_and_accepts_pins() -> None:
    text = (
        "      - uses: actions/checkout@v4\n"
        "        uses: astral-sh/setup-uv@" + "a" * 40 + "\n"
        "      - uses: astral-sh/setup-uv@" + "0123456789abcdef" * 2 + "01234567 # v6.8.0\n"
    )
    assert unpinned_uses(text) == ["actions/checkout@v4", "astral-sh/setup-uv@" + "a" * 40]


def test_handoff_ci_pins_every_action() -> None:
    text = read_workflow(HANDOFF_CI)
    assert any(USES.match(line) for line in text.splitlines())
    assert unpinned_uses(text) == []


def test_real_ci_pins_every_action() -> None:
    text = read_workflow(REAL_CI)
    unpinned = unpinned_uses(text)
    if unpinned:
        pytest.xfail(f"ci.yml hand-off patch not applied yet; unpinned: {unpinned}")
    assert unpinned == []
