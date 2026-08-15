"""Release-readiness harness: npm packed tarball, isolated install, 0.1.0 upgrade.

This module verifies the publishable artifact without publishing anything:

- ``npm pack`` builds the exact tarball npm would publish; no registry, tag,
  version, or release mutation happens anywhere in this module.
- Package contents are checked against the ``package.json`` ``files``
  whitelist (nothing extra leaks in; every whitelisted entry exists).
- The packaged Skill tree under the tarball is byte-for-byte identical to the
  repository public skill, and the shipped Python sources compile.
- The tarball is installed into an isolated virtual environment and the
  installed CLI is driven through the published ``0.1.0`` persisted-data
  migration contract: deterministic legacy route materialization, secret
  non-copy, environment-ownership rejection with byte preservation, rollback
  read, Unicode/argument pass-through, and a config directory containing
  spaces and non-ASCII characters. No live provider is ever called.
- Atomic-failure and Windows legacy-location behavior are re-validated against
  the installed package through an in-process driver script.

The only network-dependent step is the isolated ``pip install`` of the
tarball; it is skipped with a clear reason when PyPI is unreachable. All other
checks are deterministic and offline.
"""

from __future__ import annotations

import fnmatch
import json
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.fixtures import legacy_migration as lm

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_SKILL_DIR = ROOT / "skills" / "smart-search-cli"
PACKAGED_SKILL_REL = "src/smart_search/assets/skills/smart-search-cli"
VENV_PYTHON = ROOT / ".smart-search-python" / "bin" / "python"


def _venv_python(venv_dir: Path) -> Path:
    """Locate the venv Python executable portably.

    POSIX venvs place the interpreter at ``bin/python``; Windows venvs use
    ``Scripts/python.exe``. The test harness must not hardcode the POSIX path
    because ``python -m venv`` on a Windows runner creates the Windows layout.
    """
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"
V010_SECRETS = (
    "xai-0-1-0-secret",
    "openai-0-1-0-secret",
    "exa-0-1-0-secret",
    "tavily-0-1-0-secret",
    "route-primary-secret",
    "env-only-secret",
)
# The typed Control result contract forbids V1 projection keys inside results.
V1_RESULT_KEYS = (
    "ok",
    "error_type",
    "error",
    "network_attempted",
    "elapsed_ms",
    "schema_version",
    "data",
)


def _run(argv, **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault("encoding", "utf-8")
    kwargs.setdefault("errors", "strict")
    return subprocess.run(argv, capture_output=True, text=True, **kwargs)


def _pypi_reachable() -> bool:
    try:
        urllib.request.urlopen("https://pypi.org/simple/", timeout=5)
        return True
    except Exception:
        return False


def _cli_env(config_dir: Path, home: Path) -> dict[str, str]:
    env = {
        "SMART_SEARCH_CONFIG_DIR": str(config_dir),
        "HOME": str(home),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    env.pop("PYTHONPATH", None)
    return env


def _run_cli(
    python: Path, argv: list[str], env: dict[str, str], cwd: Path, *, expect_fail: bool = False
) -> dict:
    result = _run([str(python), "-m", "smart_search.cli", *argv], env=env, cwd=cwd)
    if expect_fail:
        assert result.returncode != 0, result.stdout + result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# Fixtures: one pack, one extraction, one isolated install per session
# ---------------------------------------------------------------------------


@dataclass
class PackedPackage:
    tarball: Path
    package_dir: Path  # extracted `package/` root
    rel_files: list[str]  # tarball member paths relative to package/


@pytest.fixture(scope="session")
def packed_package(tmp_path_factory) -> PackedPackage:
    npm = shutil.which("npm")
    if npm is None:
        pytest.skip("npm is not on PATH; cannot build the publish tarball")
    tmp = tmp_path_factory.mktemp("packed-install")
    packed = _run([npm, "pack", "--pack-destination", str(tmp)], cwd=ROOT)
    assert packed.returncode == 0, packed.stdout + packed.stderr
    tarballs = list(tmp.glob("*.tgz"))
    assert len(tarballs) == 1, f"expected exactly one tarball, got {tarballs}"
    tarball = tarballs[0]
    extracted = tmp / "extracted"
    extracted.mkdir()
    with tarfile.open(tarball, "r:gz") as tf:
        tf.extractall(extracted)
    package_dir = extracted / "package"
    assert package_dir.is_dir(), "npm tarball must contain a package/ root"
    with tarfile.open(tarball, "r:gz") as tf:
        rel_files = sorted(
            m.name[len("package/") :]
            for m in tf.getmembers()
            if m.isfile() and m.name.startswith("package/")
        )
    return PackedPackage(tarball=tarball, package_dir=package_dir, rel_files=rel_files)


@dataclass
class InstalledRuntime:
    python: Path
    venv_dir: Path
    site_packages: str


def test_venv_python_path_matches_platform_layout() -> None:
    """The cross-platform venv layout assumption is explicit.

    POSIX venvs use ``bin/python``; Windows venvs use ``Scripts/python.exe``.
    The installed_runtime fixture relies on this mapping so the packed-install
    tests run on every OS instead of failing on Windows runners.
    """
    expected = "python.exe" if sys.platform == "win32" else "python"
    assert _venv_python(Path("/tmp/venv")).name == expected
    assert _venv_python(Path("C:/venv")).name == expected


@pytest.fixture(scope="session")
def installed_runtime(tmp_path_factory, packed_package) -> InstalledRuntime:
    if not _pypi_reachable():
        pytest.skip("PyPI unreachable; isolated pip install of the tarball skipped")
    tmp = tmp_path_factory.mktemp("installed-runtime")
    venv_dir = tmp / "venv"
    created = _run([sys.executable, "-m", "venv", str(venv_dir)])
    assert created.returncode == 0, created.stdout + created.stderr
    python = _venv_python(venv_dir)
    assert python.exists(), f"venv python missing at {python}"
    install = _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--quiet",
            str(packed_package.tarball),
        ],
        timeout=600,
    )
    assert install.returncode == 0, install.stdout + install.stderr
    where = _run([str(python), "-c", "import smart_search; print(smart_search.__file__)"])
    assert where.returncode == 0, where.stdout + where.stderr
    site_packages = Path(where.stdout.strip()).parent.parent
    assert str(site_packages).startswith(str(venv_dir)), (
        f"installed package must resolve inside the isolated venv: {where.stdout.strip()}"
    )
    assert str(ROOT / "src") not in str(Path(where.stdout.strip())), (
        "installed CLI must not resolve to the repository source tree"
    )
    return InstalledRuntime(python=python, venv_dir=venv_dir, site_packages=str(site_packages))


def _write_v010_config(config_dir: Path, payload: str = lm.V010_CONFIG_JSON) -> bytes:
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.json"
    config_file.write_text(payload, encoding="utf-8")
    return config_file.read_bytes()


# ---------------------------------------------------------------------------
# Package contents and Skill parity (deterministic, offline)
# ---------------------------------------------------------------------------


def _whitelist_patterns(package_dir: Path) -> list[str]:
    package_json = json.loads((package_dir / "package.json").read_text(encoding="utf-8"))
    return package_json["files"]


def _matches_whitelist(pattern: str, rel: str) -> bool:
    if pattern.endswith("/"):
        return rel.startswith(pattern)
    if "/**/" in pattern:
        head, _, tail = pattern.partition("/**/")
        if not rel.startswith(head + "/"):
            return False
        return fnmatch.fnmatch(rel[len(head) + 1 :], tail)
    return fnmatch.fnmatch(rel, pattern)


def test_packed_tarball_contents_match_package_whitelist(packed_package) -> None:
    pkg = packed_package
    patterns = _whitelist_patterns(pkg.package_dir)
    assert patterns, "package.json files whitelist must not be empty"

    matched = {rel for rel in pkg.rel_files if any(_matches_whitelist(p, rel) for p in patterns)}
    matched.add("package.json")  # npm always includes package.json
    extra = sorted(set(pkg.rel_files) - matched)
    assert not extra, f"tarball contains files outside the package whitelist: {extra}"

    for required in (
        "package.json",
        "pyproject.toml",
        "README.md",
        "README.zh-CN.md",
        "LICENSE",
        "npm/bin/smart-search.js",
        "src/smart_search/cli.py",
        "src/smart_search/config.py",
        "docs/migration.md",
        "docs/commands.md",
        "docs/providers.md",
        "docs/getting-started.md",
        "docs/concepts/evidence.md",
        f"{PACKAGED_SKILL_REL}/SKILL.md",
    ):
        assert required in pkg.rel_files, f"required packaged file missing: {required}"

    for banned_prefix in (
        "tests/",
        ".github/",
        "build/",
        "skills/",
        "node_modules/",
        ".trellis/",
        "scripts/",
        ".smart-search-python/",
    ):
        assert not any(rel.startswith(banned_prefix) for rel in pkg.rel_files), banned_prefix
    assert "package-lock.json" not in pkg.rel_files
    assert not any(rel.endswith(".pyc") or "__pycache__" in rel for rel in pkg.rel_files)
    assert not any(rel.startswith("docs/development.md") for rel in pkg.rel_files)


def test_packed_tarball_skill_tree_matches_public_skill(packed_package) -> None:
    pkg = packed_package
    public_files = {
        p.relative_to(PUBLIC_SKILL_DIR).as_posix()
        for p in PUBLIC_SKILL_DIR.rglob("*")
        if p.is_file()
    }
    packaged_root = pkg.package_dir / PACKAGED_SKILL_REL
    packaged_files = {
        p.relative_to(packaged_root).as_posix() for p in packaged_root.rglob("*") if p.is_file()
    }
    assert packaged_files == public_files, (
        f"skill tree mismatch: only-public={sorted(public_files - packaged_files)} "
        f"only-packaged={sorted(packaged_files - public_files)}"
    )
    for rel in sorted(public_files):
        public_bytes = (PUBLIC_SKILL_DIR / rel).read_bytes()
        packaged_bytes = (packaged_root / rel).read_bytes()
        assert public_bytes == packaged_bytes, f"skill file differs from public tree: {rel}"


def test_packed_tarball_python_sources_compile(packed_package) -> None:
    if not VENV_PYTHON.exists():
        pytest.skip("repository .smart-search-python runtime missing")
    compiled = _run(
        [str(VENV_PYTHON), "-m", "compileall", "-q", str(packed_package.package_dir / "src")]
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr


# ---------------------------------------------------------------------------
# Installed-package migration, atomicity, Windows path, Unicode (network layer)
# ---------------------------------------------------------------------------


def test_installed_cli_reads_v010_fixture_without_writing(
    installed_runtime, tmp_path
) -> None:
    config_dir = tmp_path / "cfg"
    _write_v010_config(config_dir)
    env = _cli_env(config_dir, tmp_path / "home")
    listed = _run_cli(
        installed_runtime.python,
        ["provider", "routes", "list", "--format", "json"],
        env,
        tmp_path,
    )
    assert listed["status"] == "complete"
    assert listed["result"]["route_count"] == 0
    assert listed["result"]["config_file"] == str(config_dir / "config.json")
    assert listed["side_effects"]["config"]["write_attempted"] is False

    shown = _run_cli(
        installed_runtime.python,
        ["config", "list", "--format", "json"],
        env,
        tmp_path,
    )
    assert shown["status"] == "complete"
    values = shown["result"]["values"]
    assert values["XAI_API_KEY"] != "xai-0-1-0-secret"
    assert values["XAI_MODEL"] == "grok-4-fast"
    assert "xai-0-1-0-secret" not in json.dumps(shown)
    assert shown["side_effects"]["config"]["write_attempted"] is False


def test_installed_cli_migrates_v010_fixture_and_rolls_back_read(
    installed_runtime, tmp_path
) -> None:
    config_dir = tmp_path / "cfg"
    before = _write_v010_config(config_dir)
    env = _cli_env(config_dir, tmp_path / "home")

    added = _run_cli(
        installed_runtime.python,
        [
            "provider",
            "routes",
            "add",
            "--id",
            "primary",
            "--provider",
            "openai-compatible",
            "--api-url",
            "https://primary.example/v1",
            "--api-key",
            "route-primary-secret",
            "--model",
            "primary-model",
            "--format",
            "json",
        ],
        env,
        tmp_path,
    )
    assert added["status"] == "complete"
    assert [r["id"] for r in added["result"]["routes"]] == [
        "legacy-xai-responses",
        "legacy-openai-compatible",
        "primary",
    ]
    assert "route-primary-secret" not in json.dumps(added)
    raw_bytes_after_migration = (config_dir / "config.json").read_bytes()

    raw = json.loads((config_dir / "config.json").read_text(encoding="utf-8"))
    before_data = json.loads(before)
    for key, value in before_data.items():
        assert raw[key] == value, key
    routes = raw["SMART_SEARCH_MODEL_ROUTES"]
    assert routes[0] == {
        "id": "legacy-xai-responses",
        "provider": "xai-responses",
        "api_url": "https://api.x.ai/v1",
        "api_key": "xai-0-1-0-secret",
        "model": "grok-4-fast",
        "tools": ["web_search", "x_search"],
    }
    assert routes[1]["provider"] == "openai-compatible"
    assert routes[1]["api_key"] == "openai-0-1-0-secret"
    assert routes[2]["id"] == "primary"

    # Rollback read: migrated state stays readable through the same snapshot
    # boundary, source keys remain, and no V1 projection keys appear in the
    # typed result. Read-only commands never attempt a write.
    re_listed = _run_cli(
        installed_runtime.python,
        ["provider", "routes", "list", "--format", "json"],
        env,
        tmp_path,
    )
    assert [r["id"] for r in re_listed["result"]["routes"]] == [
        "legacy-xai-responses",
        "legacy-openai-compatible",
        "primary",
    ]
    for legacy_key in V1_RESULT_KEYS:
        assert legacy_key not in re_listed["result"], legacy_key
    assert re_listed["side_effects"]["config"]["write_attempted"] is False
    serialized = json.dumps(re_listed)
    for secret in V010_SECRETS:
        assert secret not in serialized
    for secret in ("xai-0-1-0-secret", "openai-0-1-0-secret", "route-primary-secret"):
        assert secret not in serialized
    # Read-only commands after migration never touch the file: its bytes are
    # identical to the state produced by the migration write above.
    assert (config_dir / "config.json").read_bytes() == raw_bytes_after_migration


def test_installed_cli_environment_ownership_rejects_upgrade_without_write(
    installed_runtime, tmp_path
) -> None:
    config_dir = tmp_path / "cfg"
    before = _write_v010_config(config_dir)
    env = _cli_env(config_dir, tmp_path / "home")
    env["XAI_API_KEY"] = "env-only-secret"

    rejected = _run_cli(
        installed_runtime.python,
        [
            "provider",
            "routes",
            "add",
            "--id",
            "primary",
            "--provider",
            "openai-compatible",
            "--api-url",
            "https://primary.example/v1",
            "--api-key",
            "route-primary-secret",
            "--model",
            "primary-model",
            "--format",
            "json",
        ],
        env,
        tmp_path,
        expect_fail=True,
    )
    assert rejected["status"] == "failed"
    assert rejected["error"]["code"] == "INVALID_ARGUMENT"
    assert "controlled by the environment" in rejected["error"]["message"]
    assert (config_dir / "config.json").read_bytes() == before
    assert "env-only-secret" not in (config_dir / "config.json").read_text(encoding="utf-8")
    assert "env-only-secret" not in json.dumps(rejected)


def test_installed_cli_preserves_unicode_arguments_and_utf8_json(
    installed_runtime, tmp_path
) -> None:
    env = _cli_env(tmp_path / "cfg", tmp_path / "home")
    query = "深度搜索一下最近的比特币行情 日本語 émojis 🚀 a/b\\c d\"e'f"
    planned = _run_cli(
        installed_runtime.python,
        ["research", "plan", query, "--format", "json"],
        env,
        tmp_path,
    )
    assert planned["status"] == "complete"
    operations = planned["plan"]["operations"]
    assert operations, "deep research planner must produce operations offline"
    assert planned["plan"]["operations"][0]["input"]["query"] == query
    # Raw stdout must be clean UTF-8 carrying the exact non-ASCII query text
    # (bytes, not the JSON-escaped view).
    raw = _run(
        [
            str(installed_runtime.python),
            "-m",
            "smart_search.cli",
            "research",
            "plan",
            query,
            "--format",
            "json",
        ],
        env=env,
        cwd=tmp_path,
    )
    assert raw.returncode == 0, raw.stdout + raw.stderr
    # The CLI serializes with standard JSON escaping, so compare the escaped
    # query form (quotes and backslashes are escaped in the raw text).
    escaped_query = json.dumps(query, ensure_ascii=False)[1:-1]
    assert escaped_query.encode("utf-8") in raw.stdout.encode("utf-8")


def test_installed_cli_handles_config_dir_with_spaces_and_unicode(
    installed_runtime, tmp_path
) -> None:
    config_dir = tmp_path / "cfg ünicode 目录"
    _write_v010_config(config_dir)
    env = _cli_env(config_dir, tmp_path / "home")
    shown = _run_cli(
        installed_runtime.python,
        ["config", "list", "--format", "json"],
        env,
        tmp_path,
    )
    assert shown["status"] == "complete"
    assert shown["result"]["config_file"] == str(config_dir / "config.json")
    assert shown["result"]["values"]["XAI_MODEL"] == "grok-4-fast"
    assert "xai-0-1-0-secret" not in json.dumps(shown)


def test_installed_package_atomic_failure_and_windows_legacy_path(
    installed_runtime, tmp_path
) -> None:
    """Re-validates atomic-failure byte preservation and the Windows legacy
    config-directory fallback against the installed package (not the repo
    source tree) through an in-process driver script."""
    home = tmp_path / "home"
    local_appdata = tmp_path / "local-appdata"
    legacy_dir = home / ".config" / "smart-search"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "config.json").write_text(
        lm.V010_WINDOWS_LEGACY_HOME_CONFIG_JSON, encoding="utf-8"
    )
    atomic_dir = tmp_path / "atomic-cfg"
    _write_v010_config(atomic_dir)

    driver = tmp_path / "driver.py"
    driver.write_text(
        """
import asyncio
import json
import os
import sys
from pathlib import Path

# The driver patches sys.platform to simulate the Windows config-location
# fallback; DefaultEventLoopPolicy was already bound to the Linux selector
# policy at import time, so pin it before patching so asyncio never selects a
# Windows-only event loop on this platform.
asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())

import smart_search.config as config_module
from smart_search.config import config
import smart_search.control_operations as co

home = Path(sys.argv[1])
local_appdata = Path(sys.argv[2])
atomic_dir = Path(sys.argv[3])
atomic_file = atomic_dir / "config.json"

# --- Windows legacy config-directory fallback on the installed package ---
sys.platform = "win32"
os.environ["LOCALAPPDATA"] = str(local_appdata)
config._config_file = None
config._config_dir_source = None
config._config_snapshot = None
Path.home = lambda: home
legacy_config = home / ".config" / "smart-search" / "config.json"
assert config.config_file == legacy_config, config.config_file
assert config.config_dir_source == "legacy_windows_home", config.config_dir_source
assert config.xai_api_key == "xai-win-legacy-secret", config.xai_api_key
upgraded = asyncio.run(co.run_provider_routes_add(
    "primary", "openai-compatible", "https://primary.example/v1",
    "route-primary-secret", "primary-model"))
assert upgraded.status is co.ControlOperationStatus.COMPLETE, upgraded.error
raw = json.loads(legacy_config.read_text(encoding="utf-8"))
assert [r["id"] for r in raw["SMART_SEARCH_MODEL_ROUTES"]] == [
    "legacy-xai-responses", "legacy-openai-compatible", "primary"], raw
assert raw["XAI_API_KEY"] == "xai-win-legacy-secret", raw
new_default = local_appdata / "smart-search" / "config.json"
new_default.parent.mkdir(parents=True)
new_default.write_text(json.dumps({"XAI_API_KEY": "xai-new-default-secret"}), encoding="utf-8")
config.invalidate_snapshot()
config._config_file = None
config._config_dir_source = None
assert config.config_file == new_default, config.config_file
assert config.config_dir_source == "default", config.config_dir_source
assert config.xai_api_key == "xai-new-default-secret", config.xai_api_key
sys.platform = "linux"

# --- Atomic replace failure preserves source bytes on the installed package ---
before = atomic_file.read_bytes()
real_replace = os.replace

def failing_replace(src, dst):
    if str(dst).endswith("config.json"):
        raise OSError("simulated atomic replace failure")
    return real_replace(src, dst)

config_module.os.replace = failing_replace
outcome = asyncio.run(co.run_provider_routes_add(
    "primary", "openai-compatible", "https://primary.example/v1",
    "route-primary-secret", "primary-model"))
assert outcome.status is co.ControlOperationStatus.FAILED, outcome.status
assert outcome.error.type == "config_error", outcome.error
assert atomic_file.read_bytes() == before, "atomic failure must preserve source bytes"
assert not list(atomic_dir.glob("*.tmp")), "temporary files must be removed"
envelope = json.dumps(outcome.result_dict)
assert "route-primary-secret" not in envelope, envelope
assert "simulated atomic replace failure" not in envelope, envelope
print("atomic-and-windows-ok")
""",
        encoding="utf-8",
    )
    result = _run(
        [
            str(installed_runtime.python),
            str(driver),
            str(home),
            str(local_appdata),
            str(atomic_dir),
        ],
        cwd=tmp_path,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "atomic-and-windows-ok" in result.stdout


# ---------------------------------------------------------------------------
# Source checkout parity (direct Python CLI, deterministic, offline)
# ---------------------------------------------------------------------------


def test_source_cli_preserves_unicode_arguments_and_utf8_json(tmp_path) -> None:
    if not VENV_PYTHON.exists():
        pytest.skip("repository .smart-search-python runtime missing")
    query = "深度搜索一下最近的比特币行情 日本語 🚀 a/b\\c"
    planned = _run_cli(
        VENV_PYTHON,
        ["research", "plan", query, "--format", "json"],
        _cli_env(tmp_path / "cfg", tmp_path / "home"),
        tmp_path,
    )
    assert planned["status"] == "complete"
    assert planned["plan"]["operations"]
    assert planned["plan"]["operations"][0]["input"]["query"] == query
