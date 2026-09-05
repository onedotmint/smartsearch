import json
import subprocess
import sys
from pathlib import Path

from fixtures.v1_cli_inventory import CANONICAL_TOP_LEVEL_COMMANDS, inventory_from_parser

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_SKILL = ROOT / "skills/smart-search-cli/SKILL.md"
PACKAGED_SKILL = ROOT / "src/smart_search/assets/skills/smart-search-cli/SKILL.md"


def test_public_and_packaged_skill_are_v1_and_identical():
    assert PUBLIC_SKILL.read_bytes() == PACKAGED_SKILL.read_bytes()
    text = PUBLIC_SKILL.read_text(encoding="utf-8")
    for marker in ("web_search", "web_read", "web_research"):
        assert marker in text
    for retired in ("mcp__smart-search__", "research plan", "research run", "capabilities"):
        assert retired not in text


def test_public_docs_use_the_real_v1_commands():
    docs = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "docs").rglob("*.md"))
    assert all(f"smart-search {command}" in docs for command in ("setup", "search", "read", "research"))
    assert "no runtime aliases" in (ROOT / "docs/migration.md").read_text(encoding="utf-8")
    assert "schema selector" in (ROOT / "docs/migration.md").read_text(encoding="utf-8")


def test_readmes_have_valid_v1_entrypoints_and_links():
    for name in ("README.md", "README.zh-CN.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "docs/migration.md" in text
        assert "docs/getting-started.md" in text
        assert "smart-search read" in text
        assert "smart-search research" in text


def test_root_help_is_exactly_the_four_v1_commands():
    inventory = inventory_from_parser()
    assert inventory["canonical_top_level"] == CANONICAL_TOP_LEVEL_COMMANDS
    assert inventory["aliases"] == {}
    assert inventory["nested"] == {}
    output = subprocess.check_output(
        [sys.executable, "-m", "smart_search.cli", "--help"],
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src")},
        text=True,
    )
    commands = output.split("positional arguments:", 1)[1]
    assert "{search,setup,read,research}" in commands
    assert "fetch" not in commands
    assert "capabilities" not in commands


def test_package_metadata_and_release_workflow_are_stable():
    root = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    pi = json.loads((ROOT / "integrations/pi/package.json").read_text(encoding="utf-8"))
    assert root["version"] == pi["version"] == "1.0.0"
    workflow = (ROOT / ".github/workflows/publish-npm.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" in workflow
    assert "--tag latest" in workflow
    assert workflow.count("NODE_AUTH_TOKEN: ${{ secrets.NPM_TOKEN }}") == 2


def test_provider_guide_keeps_v1_setup_keys_and_document_links():
    text = (ROOT / "docs/providers.md").read_text(encoding="utf-8")
    assert "`setup`" in text
    for key in ("TAVILY_API_KEY", "EXA_API_KEY", "BRAVE_API_KEY", "JINA_API_KEY"):
        assert key in text
    assert "[getting started](getting-started.md)" in text
    assert "migration.md" in text
    assert "[the\nmigration guide](migration.md)" in text
    assert "`setup` prompts only for the supported discovery keys" in text
    assert "Reader keys, model keys" in text
    assert "they are not setup prompts" in text
    for retired in ("`smart-search config", "`smart-search doctor", "`smart-search capabilities"):
        assert retired not in text


def test_readmes_keep_language_and_public_detail_links_aligned():
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    for text in (english, chinese):
        for link in ("docs/getting-started.md", "docs/commands.md", "docs/providers.md", "docs/migration.md"):
            assert link in text
        for command in ("smart-search setup", "smart-search search", "smart-search read", "smart-search research"):
            assert command in text
    assert "[简体中文](README.zh-CN.md) | English" in english
    assert "简体中文 | [English](README.md)" in chinese
    assert "README.zh-CN.md" in json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["files"]


def test_packaged_document_invariants_cover_v1_entrypoints():
    manifest = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    packaged_docs = {
        "README.md",
        "README.zh-CN.md",
        "docs/getting-started.md",
        "docs/commands.md",
        "docs/migration.md",
        "docs/providers.md",
    }
    assert packaged_docs <= set(manifest["files"])
    for name in ("README.md", "README.zh-CN.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "@onedotmint/smart-search@latest" in text
        assert "@onedotmint/pi-smart-search@latest" in text
        assert "docs/migration.md" in text
