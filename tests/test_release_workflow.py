from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path

from fixtures.v1_json_baselines import assert_single_json_document, assert_v1_envelope

ROOT = Path(__file__).resolve().parent.parent


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _npm_pack_files(directory: Path) -> set[str]:
    npm = "npm.cmd" if os.name == "nt" else "npm"
    result = subprocess.run(
        [npm, "pack", "--dry-run", "--json"],
        cwd=directory,
        check=True,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    report = json.loads(result.stdout)
    metadata = next(iter(report.values())) if isinstance(report, dict) else report[0]
    return {entry["path"] for entry in metadata["files"]}


def test_npm_pack_uses_windows_executable(monkeypatch):
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout='[{"files": []}]', stderr="")

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(subprocess, "run", fake_run)
    _npm_pack_files(ROOT)
    assert commands == [["npm.cmd", "pack", "--dry-run", "--json"]]


def test_v1_cli_emits_exact_single_envelope_without_provider_access():
    result = subprocess.run(
        [sys.executable, "-m", "smart_search.cli", "read", "not-a-url", "--format", "json"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    payload = assert_single_json_document(result.stdout)
    assert_v1_envelope(payload, operation="read", status="failed")
    assert result.returncode == 2
    assert set(payload) == {"version", "operation", "status", "data", "attempts", "warnings", "error"}
    assert isinstance(payload["data"], dict)
    assert isinstance(payload["attempts"], list)
    assert isinstance(payload["warnings"], list)
    assert isinstance(payload["error"], dict)
    for operation, arguments in (("setup", ["setup", "--unknown"]), ("search", ["search"]), ("read", ["read"]), ("research", ["research"])):
        invalid = subprocess.run(
            [sys.executable, "-m", "smart_search.cli", *arguments],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        )
        invalid_payload = json.loads(invalid.stdout)
        assert set(invalid_payload) == set(payload)
        assert invalid_payload["operation"] == operation
        assert invalid_payload["status"] == "failed"


def test_public_and_packaged_skill_trees_are_byte_identical():
    public_root = ROOT / "skills/smart-search-cli"
    packaged_root = ROOT / "src/smart_search/assets/skills/smart-search-cli"
    public_files = sorted(path.relative_to(public_root) for path in public_root.rglob("*") if path.is_file())
    packaged_files = sorted(path.relative_to(packaged_root) for path in packaged_root.rglob("*") if path.is_file())
    assert public_files == packaged_files
    for relative in public_files:
        assert (public_root / relative).read_bytes() == (packaged_root / relative).read_bytes()


def test_current_public_docs_do_not_advertise_retired_command_invocations():
    paths = [ROOT / "README.md", ROOT / "README.zh-CN.md", ROOT / "CONTRIBUTING.md", ROOT / "integrations/pi/README.md"] + [
        path for path in (ROOT / "docs").rglob("*.md") if path.name != "migration.md"
    ] + list((ROOT / "skills/smart-search-cli").rglob("*.md"))
    retired_invocation = re.compile(
        r"`(?:smart-search\s+(?:fetch|map|capabilities|provider|doctor|dev|skills|deep)\b|research\s+(?:plan|run)\b)"
    )
    violations = {}
    for path in paths:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if retired_invocation.search(line) and not re.search(r"\b(no|not|removed|retired|without)\b", line, re.I):
                violations[f"{path.relative_to(ROOT)}:{line_number}"] = line.strip()
    assert not violations, violations


def test_public_markdown_relative_links_resolve():
    markdown_files = [ROOT / "README.md", ROOT / "README.zh-CN.md", ROOT / "CONTRIBUTING.md"] + list((ROOT / "docs").rglob("*.md"))
    link_pattern = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
    broken = []
    for path in markdown_files:
        for target in link_pattern.findall(path.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target_path = (path.parent / target.split("#", 1)[0]).resolve()
            if not target_path.is_file():
                broken.append(f"{path.relative_to(ROOT)} -> {target}")
    assert not broken, broken


def test_readmes_have_valid_v1_entrypoints_and_links():
    for name in ("README.md", "README.zh-CN.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "docs/migration.md" in text
        assert "docs/getting-started.md" in text
        assert "smart-search search" in text
        assert "smart-search read" in text
        assert "smart-search research" in text


def test_migration_explicitly_covers_retired_namespaces_and_replacements():
    migration = (ROOT / "docs/migration.md").read_text(encoding="utf-8")
    required_rows = {
        "`map`": "no replacement",
        "`capabilities`": "setup",
        "`provider`": "no command replacement",
        "`doctor`": "no replacement",
        "`dev`": "no CLI replacement",
        "`skills`": "web_search",
        "`deep`": "research QUERY",
        "`config`/`control`": "setup",
    }
    for old_surface, replacement in required_rows.items():
        row = next(line for line in migration.splitlines() if line.startswith(f"| {old_surface}"))
        assert replacement in row
    assert "must fail" in migration
    assert "never overwrite" in migration


def test_root_help_is_exactly_the_four_v1_commands():
    output = subprocess.check_output(
        [sys.executable, "-m", "smart_search.cli", "--help"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        text=True,
    )
    commands = output.split("positional arguments:", 1)[1]
    assert "{search,setup,read,research}" in commands
    assert all(retired not in commands for retired in ("fetch", "map", "capabilities", "provider", "doctor", "dev", "skills"))


def test_both_manifests_and_locks_are_synchronized_for_v1():
    root = _read_json(ROOT / "package.json")
    expected_version = root["version"]
    root_lock = _read_json(ROOT / "package-lock.json")
    pi = _read_json(ROOT / "integrations/pi/package.json")
    pi_lock = _read_json(ROOT / "integrations/pi/package-lock.json")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert root["name"] == root_lock["name"] == "@onedotmint/smart-search"
    assert pi["name"] == pi_lock["name"] == "@onedotmint/pi-smart-search"
    assert root["version"] == root_lock["version"] == root_lock["packages"][""]["version"] == expected_version
    assert pi["version"] == pi_lock["version"] == pi_lock["packages"][""]["version"] == expected_version
    assert re.search(rf'^version = "{re.escape(expected_version)}"$', pyproject, re.MULTILINE)
    assert "Deep Research planning" not in root["description"]
    assert "Deep Research planning" not in pyproject

def test_root_package_whitelist_and_dry_pack_contents():
    manifest = _read_json(ROOT / "package.json")
    expected_files = [
        "npm/",
        "src/smart_search/**/*.py",
        "src/smart_search/assets/skills/smart-search-cli/**",
        "pyproject.toml",
        "README.md",
        "README.zh-CN.md",
        "docs/getting-started.md",
        "docs/commands.md",
        "docs/migration.md",
        "docs/providers.md",
        "docs/concepts/*.md",
        "LICENSE",
    ]
    assert manifest["files"] == expected_files
    packed = _npm_pack_files(ROOT)
    assert {"README.md", "docs/migration.md", "docs/commands.md", "pyproject.toml"} <= packed
    assert not any(path.startswith(("tests/", "skills/", ".trellis/")) for path in packed)
    assert "docs/development.md" not in packed
    assert "CONTRIBUTING.md" not in packed
    def matches_whitelist(path: str, pattern: str) -> bool:
        if pattern == "npm/" or pattern.endswith("/**"):
            return path.startswith(pattern.rstrip("/**") + "/")
        if pattern == "src/smart_search/**/*.py":
            return path.startswith("src/smart_search/") and path.endswith(".py")
        return pattern == path or fnmatch(path, pattern)

    for path in packed:
        assert any(matches_whitelist(path, pattern) for pattern in expected_files) or path == "package.json"


def test_pi_package_whitelist_and_exact_native_tool_contents():
    manifest = _read_json(ROOT / "integrations/pi/package.json")
    assert manifest["files"] == ["extensions/", "src/", "README.md"]
    assert manifest["publishConfig"] == {"access": "public"}
    assert _npm_pack_files(ROOT / "integrations/pi") == {
        "README.md",
        "extensions/index.ts",
        "package.json",
        "src/cli.ts",
        "src/result.ts",
    }
    extension = (ROOT / "integrations/pi/extensions/index.ts").read_text(encoding="utf-8")
    assert extension.count('name: "web_') == 3
    assert all(tool in extension for tool in ("web_search", "web_read", "web_research"))


def test_v1_release_notes_and_staged_workflow_boundaries():
    expected_version = _read_json(ROOT / "package.json")["version"]
    notes_path = ROOT / f".github/releases/v{expected_version}.md"
    notes = notes_path.read_text(encoding="utf-8")
    stage = (ROOT / ".github/workflows/publish-npm.yml").read_text(encoding="utf-8")
    finalize = (ROOT / ".github/workflows/finalize-npm-release.yml").read_text(encoding="utf-8")
    heading = re.search(rf"^# v{re.escape(expected_version)}(?:\s|$)", notes, re.MULTILINE)
    assert notes_path.is_file()
    assert heading
    assert notes[heading.end():].strip()
    assert "validated offline" in notes.lower()
    assert "no live" in notes.lower()

    assert "workflow_dispatch" in stage
    assert "branches:" in stage
    assert "target_sha" in stage
    assert "reachable from origin/main" in stage
    assert "fetch-depth: 0" in stage
    assert 'node-version: "24"' in stage
    assert "npm install --global npm@11.15.0" in stage
    assert "id-token: write" in stage
    assert "contents: read" in stage
    assert "contents: write" not in stage
    assert stage.count("npm stage publish --provenance --access public --tag latest") == 2
    assert "npm publish" not in stage
    for workflow in (stage, finalize):
        assert "NODE_AUTH_TOKEN" not in workflow
        assert "NPM_TOKEN" not in workflow

    registry = stage.index("Check public registry state for each exact version")
    root_stage = stage.index("stage-root:")
    pi_stage = stage.index("stage-pi:")
    summary = stage.index("approval-summary:")
    assert registry < root_stage < pi_stage < summary
    assert "root_state=\"$(registry_state '@onedotmint/smart-search' root)\"" in stage
    assert "pi_state=\"$(registry_state '@onedotmint/pi-smart-search' pi)\"" in stage
    assert "printf 'absent'" in stage
    assert "printf 'unavailable'" in stage
    assert "printf 'not_latest'" in stage
    assert "Could not verify one or more npm registry states, including latest dist-tags" in stage

    root_block = stage[root_stage:pi_stage]
    pi_block = stage[pi_stage:summary]
    assert "needs: [prepare, registry-state]" in root_block
    assert "needs: [prepare, registry-state]" in pi_block
    assert "needs.stage-root" not in root_block
    assert "needs.stage-pi" not in pi_block
    assert "needs.registry-state.outputs.root_state == 'absent'" in root_block
    assert "needs.registry-state.outputs.pi_state == 'absent'" in pi_block
    assert "Review each staged package on npmjs.com" in stage
    assert "Approve the staged packages with npm 2FA" in stage
    assert "Finalize npm release" in stage
    assert "npm staging-queue state" in stage
    assert "gh release" not in stage
    assert "git tag" not in stage
    assert "npm view" not in stage[summary:]

    assert "permissions:\n  contents: write" in finalize
    assert "id-token: write" not in finalize
    assert "workflow_dispatch" in finalize
    assert "target_sha" in finalize
    assert "^[0-9a-f]{40}$" in finalize
    assert "merge-base --is-ancestor" in finalize
    assert "+refs/heads/main:refs/remotes/origin/main" in finalize
    assert "npm view" in finalize
    assert "dist-tags.latest" in finalize
    assert "npm stage publish" not in finalize
    assert "npm publish" not in finalize


def test_staged_workflow_preserves_release_intent_and_metadata_guards():
    stage = (ROOT / ".github/workflows/publish-npm.yml").read_text(encoding="utf-8")
    finalize = (ROOT / ".github/workflows/finalize-npm-release.yml").read_text(encoding="utf-8")
    for workflow in (stage, finalize):
        for marker in (
            "git show \"$base:package.json\"",
            "Root and Pi manifests do not share the same",
            "not a stable x.y.z release version",
            "root manifest name mismatch",
            "Pi manifest name mismatch",
            "packages['']",
            "pyproject.toml version mismatch",
            "release notes missing or empty",
            "release notes heading mismatch",
            "release notes body missing or empty",
            ".github/releases/v",
        ):
            if marker == 'git show "$base:package.json"' and workflow is finalize:
                continue
            assert marker in workflow
    assert "Neither npm manifest version changed on main; no release intent." in stage
    assert "Only one npm manifest changed its version on main" in stage
    assert "PUSH_SHA: ${{ github.sha }}" in stage
    assert "PUSH_BEFORE: ${{ github.event.before }}" in stage
    assert "target_sha=$PUSH_SHA" in stage
    assert "TARGET_SHA: ${{ steps.resolve.outputs.target_sha }}" in stage
    assert "git ls-remote --tags" in stage
    assert "git ls-remote --tags" in finalize
    assert 'remote_tag="$(git ls-remote --tags --refs origin "refs/tags/$tag")"' in stage
    assert 'remote_tag="$(git ls-remote --tags --refs origin "refs/tags/$tag")"' in finalize


def test_finalize_release_is_blocked_until_both_public_latest_versions_are_verified():
    finalize = (ROOT / ".github/workflows/finalize-npm-release.yml").read_text(encoding="utf-8")
    verify = finalize.index("Verify both public exact versions and latest tags")
    release = finalize.index("Create or update stable GitHub release")
    assert verify < release
    assert "steps.verify-public.outputs.verified == 'true'" in finalize
    assert "verify_exact_version '@onedotmint/smart-search'" in finalize
    assert "verify_exact_version '@onedotmint/pi-smart-search'" in finalize
    assert "gh release edit \"$tag_name\" --title \"$tag_name\" --notes-file \"$notes_file\"" in finalize
    assert "gh release create \"$tag_name\" --target \"$TARGET_SHA\" --title \"$tag_name\" --notes-file \"$notes_file\"" in finalize

def test_npm_deterministic_probe_clears_tavily_environment():
    script = (ROOT / "npm/scripts/test.js").read_text(encoding="utf-8")
    for key in ("TAVILY_API_KEY", "TAVILY_API_URL", "TAVILY_ENABLED", "TAVILY_TIMEOUT_SECONDS"):
        assert f"  {key}:" in script
    assert 'TAVILY_API_KEY: ""' in script
    assert 'TAVILY_ENABLED: "false"' in script
