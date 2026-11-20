"""``docs/tools.md`` against the server and the per-module docs (PRD sections 5.7 and 8.6)."""

from __future__ import annotations

import re

from tests.catalogue import CATALOGUE
from tests.docs_tools import DOCS, OUTPUT, area, assemble, modules
from wasabi_mcp.config import Settings
from wasabi_mcp.server import build_server

ROW = re.compile(r"^\| `(?P<name>\w+)` \| (?P<kind>[RWD]) \|", re.MULTILINE)


def _rows(text: str) -> list[tuple[str, str]]:
    return [(match["name"], match["kind"]) for match in ROW.finditer(text)]


async def test_docs_tools_names_equal_list_tools():
    server = build_server(Settings(access_key_id="AKIAFAKE", secret_access_key="fake"))
    registered = sorted(tool.name for tool in await server.list_tools())
    documented = [name for name, _ in _rows(OUTPUT.read_text())]
    assert len(documented) == len(set(documented)), "a tool is documented twice"
    assert sorted(documented) == registered


def test_docs_tools_rows_follow_the_catalogue():
    assert _rows(OUTPUT.read_text()) == [(name, kind) for name, kind, _ in CATALOGUE]


def test_docs_tools_is_assembled_from_the_module_docs():
    assert OUTPUT.read_text() == assemble(), "run: uv run python -m tests.docs_tools"


def test_every_module_doc_belongs_to_a_catalogue_module():
    files = sorted(path.stem for path in (DOCS / "tools").glob("*.md"))
    assert files == sorted(modules())


def test_readme_lists_every_tool_under_its_area():
    readme = (DOCS.parent / "README.md").read_text()
    tools = readme.split("## Tools", 1)[1].split("\n## ", 1)[0]
    sections = re.split(r"^\*\*(.+?)\*\* ", tools, flags=re.MULTILINE)[1:]
    listed = {
        name: heading
        for heading, body in zip(sections[::2], sections[1::2], strict=True)
        for name in re.findall(r"`(\w+)`", body)
    }
    expected = {name: area(module) for name, _, module in CATALOGUE}
    assert {name: listed.get(name) for name in expected} == expected


def test_readme_documents_every_prd_environment_variable():
    prd = (DOCS / "prd.md").read_text()
    section = prd.split("## 7. Configuration", 1)[1].split("\n## ", 1)[0]
    variables = set(re.findall(r"`(WASABI_\w+)`", section))
    readme = (DOCS.parent / "README.md").read_text()
    configuration = readme.split("## Configuration", 1)[1].split("\n## ", 1)[0]
    assert len(variables) == 11
    assert {v for v in variables if f"`{v}`" not in configuration} == set()
