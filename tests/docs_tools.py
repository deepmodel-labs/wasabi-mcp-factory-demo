"""Assembles ``docs/tools.md`` from the per-module ``docs/tools/<module>.md`` files.

``tests/test_docs.py`` checks the committed file matches. After changing a module's doc,
regenerate it with ``uv run python -m tests.docs_tools``.
"""

from __future__ import annotations

from pathlib import Path

from tests.catalogue import CATALOGUE

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
OUTPUT = DOCS / "tools.md"
AREAS = ("Storage", "Identity", "Usage", "Account governance", "Server")

HEADER = """\
# Tool catalogue

All {total} tools, grouped by area, in the order of PRD section 6. Generated from
`docs/tools/*.md` by `uv run python -m tests.docs_tools`; edit those files, not this one.
Each module's file also describes its shared behaviour (regions, paging, errors).

Kinds: R reads. W writes and refuses while `WASABI_MCP_READ_ONLY` is true (the default).
D is destructive and also needs `confirm=true` on each call.
"""


def area(module: str) -> str:
    if module.startswith("iam_"):
        return "Identity"
    if module == "stats":
        return "Usage"
    if module.startswith("wacm_"):
        return "Account governance"
    if module == "meta":
        return "Server"
    return "Storage"


def modules() -> list[str]:
    """Catalogue modules in order of their first tool."""
    return list(dict.fromkeys(module for _, _, module in CATALOGUE))


def _title_and_table(module: str) -> tuple[str, list[str]]:
    lines = (DOCS / "tools" / f"{module}.md").read_text().splitlines()
    title = next(line.removeprefix("# ") for line in lines if line.startswith("# "))
    table = [line for line in lines if line.startswith("|")]
    return title, table


def assemble() -> str:
    parts = [HEADER.format(total=len(CATALOGUE))]
    for name in AREAS:
        parts.append(f"## {name}\n")
        for module in (m for m in modules() if area(m) == name):
            title, table = _title_and_table(module)
            parts.append(
                f"### {title}\n\nDetails: [`docs/tools/{module}.md`](tools/{module}.md).\n"
            )
            parts.append("\n".join(table) + "\n")
    return "\n".join(parts)


if __name__ == "__main__":
    OUTPUT.write_text(assemble())
