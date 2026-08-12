import ast
import json
import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESOLVER = ROOT / "npm" / "scripts" / "resolve-prerelease-version.js"
WORKFLOW = ROOT / ".github" / "workflows" / "publish-npm.yml"


def read_reference_tree(skill_dir: Path) -> str:
    return "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted((skill_dir / "references").rglob("*"))
        if p.is_file() and p.suffix == ".md"
    )


def run_resolver(base_version: str, versions: list[str]) -> str:
    result = subprocess.run(
        [
            "node",
            str(RESOLVER),
            "--package",
            "@onedotmint/smart-search",
            "--base",
            base_version,
            "--id",
            "beta",
            "--versions-json",
            json.dumps(versions),
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout


def test_resolver_counts_legacy_dev_slots_per_base_version():
    versions = [
        "0.1.9-dev.30",
        "0.1.9",
        "0.1.10-dev.32",
        "0.1.10-dev.34",
        "0.1.10",
    ]

    assert run_resolver("0.1.9", versions) == "0.1.9-beta.2"
    assert run_resolver("0.1.10", versions) == "0.1.10-beta.3"


def test_resolver_prefers_existing_beta_numbers_when_higher_than_legacy_count():
    versions = [
        "0.1.10-dev.32",
        "0.1.10-dev.34",
        "0.1.10-beta.5",
        "0.1.10",
    ]

    assert run_resolver("0.1.10", versions) == "0.1.10-beta.6"


def test_resolver_starts_at_beta_one_without_prior_versions():
    assert run_resolver("0.2.0", []) == "0.2.0-beta.1"


def test_publish_workflow_uses_beta_lane_and_prerelease_guardrails():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "github.event.inputs.target_ref" in workflow
    assert "github.event.inputs.version" in workflow
    assert "github.event.inputs.npm_tag" in workflow
    assert "resolve-prerelease-version.js" in workflow
    assert "Detect stable release bump commit" in workflow
    assert "chore\\(release\\)" in workflow
    assert "stable-bump.outputs.skip != 'true'" in workflow
    assert "-dev.${GITHUB_RUN_NUMBER}" not in workflow
    assert "&& inputs." not in workflow
    assert "|| inputs." not in workflow
    assert "tag=\"next\"" in workflow
    assert "tag=\"latest\"" in workflow
    assert "Refusing to publish prerelease version" in workflow
    assert "notes_file=\".github/releases/v${version}.md\"" in workflow
    assert "notes_footer=\"$(printf" in workflow
    assert "gh release create" in workflow
    assert "--prerelease" in workflow


def test_release_docs_explain_beta_lane_and_npm_immutability():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    readme_zh = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    development_docs = (ROOT / "docs" / "development.md").read_text(encoding="utf-8")
    public_contract = read_reference_tree(ROOT / "skills" / "smart-search-cli")
    packaged_contract = read_reference_tree(
        ROOT / "src" / "smart_search" / "assets" / "skills" / "smart-search-cli"
    )

    required_markers = [
        "Release lanes",
        "<package.json version>-beta.N",
        "dist-tag `next`",
        "0.1.10-beta.3",
        "chore(release): bump version to X.Y.Z",
        ".github/releases/vX.Y.Z.md",
        "vX.Y.Z",
        "workflow_dispatch",
        "target_ref",
        "npm versions are immutable",
        "cannot be renamed in place",
        "Release closeout checklist",
        "create_github_release=false",
        "gh release create vX.Y.Z-beta.N",
        "npm `E409`",
        "machine-readable gap check",
        "mise use -g",
        "NPM_TOKEN",
        "non-ASCII JSON",
        "ConvertFrom-Json",
    ]
    for marker in required_markers:
        assert marker in development_docs
    assert "docs/development.md" in readme
    assert "docs/development.md" in readme_zh
    contract_markers = [
        "Release Lanes",
        "<package.json version>-beta.N",
        "chore(release): bump version to X.Y.Z",
        ".github/releases/vX.Y.Z.md",
        "npm versions are immutable",
        "Release Closeout Lessons",
        "GitHub release creation fails",
        "npm `E409`",
        "diff-style gap check",
        "smart-search dev smoke --mock --format json",
        "Windows npm/mise wrapper is emitting UTF-8 JSON",
    ]
    for marker in contract_markers:
        assert marker in public_contract
        assert marker in packaged_contract


def test_current_stable_release_notes_describe_user_visible_changes():
    notes = (ROOT / ".github" / "releases" / "v0.1.0.md").read_text(encoding="utf-8")

    required_markers = [
        "Initial release",
        "@onedotmint/smart-search",
        "smart-search skills status",
        "smart-search research",
        "Context7",
        "Exa",
        "Validation",
    ]
    for marker in required_markers:
        assert marker in notes


def test_publish_workflow_trigger_checkout_and_version_resolution():
    """The workflow runs on main pushes and v* tags, accepts an explicit
    dispatch target ref, checks out that exact ref, and resolves the publish
    version through the version scripts (never a hard-coded dev build)."""
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "on:\n  push:\n    branches:\n      - main\n    tags:\n      - \"v*\"" in workflow
    assert "workflow_dispatch:" in workflow
    assert 'target_ref:\n        description: "Commit SHA, branch, or tag to publish from"' in workflow
    assert "actions/checkout@v6" in workflow
    assert "ref: ${{ github.event.inputs.target_ref || github.ref }}" in workflow
    assert 'node-version: "24"' in workflow
    assert 'python-version: "3.12"' in workflow

    # Version resolution: dispatch exact version, tag-derived version, and the
    # prerelease resolver for main pushes; the resolved version is captured.
    assert 'version="${DISPATCH_VERSION}"' in workflow
    assert 'version="${GITHUB_REF_NAME#v}"' in workflow
    assert 'version="$(node npm/scripts/resolve-prerelease-version.js' in workflow
    assert 'node npm/scripts/set-package-version.js "$version"' in workflow
    assert 'echo "version=$version" >> "$GITHUB_OUTPUT"' in workflow
    assert 'echo "tag=$tag" >> "$GITHUB_OUTPUT"' in workflow


def test_publish_workflow_exact_version_duplicate_guard():
    """A version that already exists on npm must be skipped, never republished
    or mutated; the publish step only runs for versions verified as absent."""
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert 'npm view "${{ steps.package.outputs.name }}@${{ steps.package.outputs.version }}' in workflow
    assert 'echo "published=true" >> "$GITHUB_OUTPUT"' in workflow
    assert 'echo "published=false" >> "$GITHUB_OUTPUT"' in workflow
    assert "already exists; skipping publish" in workflow
    # The publish step runs only after the exact-version check proves the
    # version absent; the skip step runs only when it is already published.
    assert (
        "if: steps.stable-bump.outputs.skip != 'true' "
        "&& steps.npm-version.outputs.published != 'true'" in workflow
    )
    assert (
        "if: steps.stable-bump.outputs.skip != 'true' "
        "&& steps.npm-version.outputs.published == 'true'" in workflow
    )


def test_publish_workflow_token_and_provenance_wiring_without_credential_output():
    """npm authentication uses a granular access token wired through
    NODE_AUTH_TOKEN with the matching setup-node registry-url, provenance uses
    the OIDC id-token permission, and the workflow never prints or writes a
    credential anywhere. A verification command that could publish or disclose
    a credential must fail this static contract."""
    workflow = WORKFLOW.read_text(encoding="utf-8")

    # OIDC and GitHub token permissions for provenance and release creation.
    assert "permissions:" in workflow
    assert "contents: write" in workflow
    assert "id-token: write" in workflow

    # npm registry wiring: setup-node registry-url + publish-time token.
    assert 'registry-url: "https://registry.npmjs.org"' in workflow
    assert "NODE_AUTH_TOKEN: ${{ secrets.NPM_TOKEN }}" in workflow
    assert workflow.count("NODE_AUTH_TOKEN") == 1, "token must be wired exactly once"
    assert "--provenance" in workflow

    # No credential disclosure anywhere in the workflow.
    assert "echo \"${{ secrets" not in workflow
    assert "printenv" not in workflow
    assert "cat ~/.npmrc" not in workflow
    assert "cat $HOME/.npmrc" not in workflow
    # The token is wired in the same step as the npm publish command (env
    # block directly above it), never in a step that could print its value.
    after_token = workflow.split("NODE_AUTH_TOKEN: ${{ secrets.NPM_TOKEN }}")[1]
    assert "run: npm publish" in after_token.split("- name:")[0]
    # NPM_TOKEN is the only secret the workflow references, and it is never
    # rendered into output or passed to a command that prints its value.
    secret_refs = sorted(
        {token for token in re.findall(r"secrets\.[A-Z0-9_]+", workflow)}
    )
    assert secret_refs == ["secrets.NPM_TOKEN"], secret_refs


def test_publish_workflow_release_side_effect_controls():
    """GitHub release creation is explicit and skippable, prerelease versions
    are never marked latest, and the stable bump commit skips the whole
    publish-and-release path (the matching v* tag owns it)."""
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "Detect stable release bump commit" in workflow
    assert r"chore\(release\)" in workflow
    assert "stable-bump.outputs.skip != 'true'" in workflow
    assert 'CREATE_GITHUB_RELEASE: ${{ github.event.inputs.create_github_release }}' in workflow
    assert "Skipping GitHub release because create_github_release=false." in workflow
    assert "gh release create" in workflow
    assert "gh release edit" in workflow
    assert "--prerelease" in workflow
    assert 'notes_file=".github/releases/v${version}.md"' in workflow
    assert 'notes_footer="$(printf' in workflow
    assert "GH_TOKEN: ${{ github.token }}" in workflow
    # Prerelease versions must never use the latest dist-tag.
    assert "Refusing to publish prerelease version" in workflow
    assert 'if [[ "$tag" == "latest" && "$version" == *-* ]]' in workflow


def _imported_top_level_modules(src_dir: Path) -> set[str]:
    """Return the lowercased set of top-level modules imported under src_dir."""
    imported: set[str] = set()
    for path in sorted(src_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0].lower())
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0].lower())
    return imported


def test_declared_runtime_dependencies_are_imported():
    """Every declared runtime dependency must be imported under src/smart_search.

    Guards against re-adding V1-era dependencies (InquirerPy, pyfiglet, rich)
    that are declared in pyproject.toml but never imported by the package.
    """
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = pyproject["project"]["dependencies"]

    # Distribution names ignore extras and version specifiers.
    names: set[str] = set()
    for spec in declared:
        name = re.split(r"[\[\]><=!~;]", spec)[0].strip().lower()
        names.add(name)

    imported = _imported_top_level_modules(ROOT / "src" / "smart_search")

    missing = sorted(names - imported)
    assert not missing, (
        f"Declared runtime dependencies are never imported under src/smart_search: {missing}"
    )
