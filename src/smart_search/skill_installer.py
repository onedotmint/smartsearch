from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from hashlib import sha256
from importlib import resources
from pathlib import Path
from typing import Any

_DEFAULT_SKILL_TARGET_IDS = ("codex", "claude", "cursor")


SKILL_NAME = "smart-search-cli"
PACKAGE_ROOT_ENV = "SMART_SEARCH_PACKAGE_ROOT"


@dataclass(frozen=True)
class SkillTarget:
    target_id: str
    label: str
    relative_root: str
    default: bool = False

    @property
    def skill_relative_path(self) -> str:
        return f"{self.relative_root}/{SKILL_NAME}"


SKILL_TARGETS: tuple[SkillTarget, ...] = (
    SkillTarget("generic", "Generic Agent Skills", ".agents/skills"),
    SkillTarget("codex", "Codex", ".codex/skills", True),
    SkillTarget("claude", "Claude Code", ".claude/skills", True),
    SkillTarget("cursor", "Cursor", ".cursor/skills", True),
    SkillTarget("opencode", "OpenCode", ".opencode/skills"),
    SkillTarget("copilot", "GitHub Copilot", ".copilot/skills"),
    SkillTarget("gemini", "Gemini CLI", ".gemini/skills"),
    SkillTarget("kiro", "Kiro", ".kiro/skills"),
    SkillTarget("qoder", "Qoder", ".qoder/skills"),
    SkillTarget("codebuddy", "CodeBuddy", ".codebuddy/skills"),
    SkillTarget("droid", "Factory Droid", ".factory/skills"),
    SkillTarget("pi", "Pi Agent", ".pi/agent/skills"),
    SkillTarget("kilo", "Kilo CLI", ".kilocode/skills"),
    SkillTarget("antigravity", "Antigravity", ".agent/skills"),
    SkillTarget("windsurf", "Windsurf", ".windsurf/skills"),
    SkillTarget("hermes", "Hermes Agent", ".hermes/skills"),
)

SKILL_TARGET_BY_ID = {target.target_id: target for target in SKILL_TARGETS}
DEFAULT_SKILL_TARGET_IDS = list(_DEFAULT_SKILL_TARGET_IDS)

_TARGET_ALIASES = {
    "agents": "codex",
    "agentskills": "codex",
    "agent-skills": "codex",
    "claude-code": "claude",
    "github-copilot": "copilot",
    "gh-copilot": "copilot",
    "factory": "droid",
    "factory-droid": "droid",
    "pi-agent": "pi",
    "kilo-cli": "kilo",
    "hermes-agent": "hermes",
    "nous-hermes": "hermes",
}


class SkillInstallError(ValueError):
    pass


class _SymlinkError(SkillInstallError):
    """A managed skill entry resolved through a symlink; never followed."""


class _PartialCommitError(SkillInstallError):
    """Publish failed and rollback failed; the prior tree is retained elsewhere."""

    def __init__(self, message: str, backup: Path):
        super().__init__(message)
        self.backup = backup


_SKIP_SELECTORS = frozenset({"skip", "none", "no", "n", "跳过", "无", "否"})
_ALL_SELECTORS = frozenset({"all", "全部"})


def parse_skill_targets(raw: str) -> list[str]:
    if not raw.strip():
        return []
    tokens = [part.strip().lower() for part in raw.replace(";", ",").replace("+", ",").split(",")]
    if len(tokens) == 1 and " " in tokens[0]:
        tokens = [part.strip().lower() for part in tokens[0].split()]
    tokens = [token for token in tokens if token]

    if not tokens:
        return []
    if any(token in _SKIP_SELECTORS or token in _ALL_SELECTORS for token in tokens):
        if len(tokens) != 1:
            raise SkillInstallError(
                "skip/none/all are standalone selectors and cannot be combined with other targets."
            )
        if tokens[0] in _SKIP_SELECTORS:
            return []
        return [target.target_id for target in SKILL_TARGETS]

    selected: list[str] = []
    invalid: list[str] = []
    for token in tokens:
        target_id = _TARGET_ALIASES.get(token, token)
        if target_id not in SKILL_TARGET_BY_ID:
            invalid.append(token)
            continue
        if target_id not in selected:
            selected.append(target_id)

    if invalid:
        valid = ", ".join(target.target_id for target in SKILL_TARGETS)
        raise SkillInstallError(f"Unknown skill target(s): {', '.join(invalid)}. Valid targets: {valid}")
    return selected


def _resource_skill_root() -> Any:
    try:
        root = resources.files("smart_search").joinpath("assets", "skills", SKILL_NAME)
        if root.is_dir():
            return root
    except (FileNotFoundError, ModuleNotFoundError, AttributeError):
        pass
    return None


def _filesystem_skill_root() -> Path | None:
    candidates: list[Path] = []
    package_root = os.getenv(PACKAGE_ROOT_ENV, "").strip()
    if package_root:
        base = Path(package_root)
        candidates.extend([
            base / "src" / "smart_search" / "assets" / "skills" / SKILL_NAME,
            base / "skills" / SKILL_NAME,
        ])

    repo_root = Path(__file__).resolve().parents[2]
    candidates.extend([
        repo_root / "src" / "smart_search" / "assets" / "skills" / SKILL_NAME,
        repo_root / "skills" / SKILL_NAME,
    ])

    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def _iter_resource_files(root: Any) -> list[tuple[str, bytes]]:
    files: list[tuple[str, bytes]] = []

    def visit(node: Any, prefix: str = "") -> None:
        for child in node.iterdir():
            rel = f"{prefix}/{child.name}" if prefix else child.name
            if child.is_dir():
                visit(child, rel)
            elif child.is_file():
                files.append((rel, child.read_bytes()))

    visit(root)
    return files


def _assert_no_symlinks_in_tree(root: Path) -> None:
    """Raise if the tree root or any entry under it is a symlink.

    Uses ``os.walk(followlinks=False)`` so symlinked directories are never
    descended into; symlinked files are detected before any read. Directory
    entries are sorted so the reported path is deterministic.
    """
    if os.path.islink(root):
        raise _SymlinkError(f"Managed skill path is a symlink: {root}")
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        dir_path = Path(dirpath)
        for name in sorted(dirnames):
            entry = dir_path / name
            if os.path.islink(entry):
                raise _SymlinkError(f"Managed skill path contains a symlink: {entry}")
        for name in sorted(filenames):
            entry = dir_path / name
            if os.path.islink(entry):
                raise _SymlinkError(f"Managed skill path contains a symlink: {entry}")


def _iter_filesystem_files(root: Path) -> list[tuple[str, bytes]]:
    _assert_no_symlinks_in_tree(root)
    files: list[tuple[str, bytes]] = []
    for dirpath, _, filenames in os.walk(root, followlinks=False):
        dir_path = Path(dirpath)
        for name in sorted(filenames):
            entry = dir_path / name
            files.append((str(entry.relative_to(root)).replace("\\", "/"), entry.read_bytes()))
    return files


def _load_skill_files(source_root: Path | None = None) -> list[tuple[str, bytes]]:
    if source_root is not None:
        if not source_root.is_dir():
            raise SkillInstallError(f"Skill source directory not found: {source_root}")
        return _iter_filesystem_files(source_root)

    resource_root = _resource_skill_root()
    if resource_root is not None:
        files = _iter_resource_files(resource_root)
        if files:
            return files

    filesystem_root = _filesystem_skill_root()
    if filesystem_root is not None:
        files = _iter_filesystem_files(filesystem_root)
        if files:
            return files

    raise SkillInstallError("Bundled smart-search-cli skill files were not found.")


def _skill_digest(files: list[tuple[str, bytes]]) -> str:
    digest = sha256()
    for rel_path, content in sorted(files, key=lambda item: item[0]):
        digest.update(rel_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def _target_installed_files(path: Path) -> list[tuple[str, bytes]]:
    if not path.is_dir():
        return []
    return _iter_filesystem_files(path)


def _check_no_symlink_components(base: Path, relative: str) -> None:
    """Reject any existing managed path component that is a symlink."""
    current = base
    for part in Path(relative).parts:
        current = current / part
        if os.path.islink(current):
            raise _SymlinkError(f"Managed skill path component is a symlink: {current}")


def _remove_tree(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _stage_skill_files(dest: Path, files: list[tuple[str, bytes]]) -> Path:
    """Write the full skill tree into a fresh staging dir next to dest.

    Returns the staged directory only after every file is written and the
    staged tree digests to the source tree. Any failure removes the staging
    dir and re-raises; the destination tree is never touched here.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".smart-search-skill-stage-", dir=dest.parent))
    os.chmod(stage, 0o755)
    try:
        for rel_path, content in files:
            file_path = stage / Path(rel_path)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(content)
        staged_files = _iter_filesystem_files(stage)
        if _skill_digest(staged_files) != _skill_digest(files):
            raise SkillInstallError("Staged skill tree verification failed: content mismatch.")
        return stage
    except BaseException:
        _remove_tree(stage)
        raise


def _publish_staged(dest: Path, stage: Path) -> None:
    """Atomically swap the staged tree into dest with rollback on failure."""
    backup: Path | None = None
    if dest.exists():
        backup = dest.parent / f".smart-search-skill-backup-{uuid.uuid4().hex}"
        try:
            os.rename(dest, backup)
        except OSError:
            try:
                _remove_tree(stage)
            except OSError:
                pass
            raise SkillInstallError(f"Could not move existing skill tree aside: {dest}") from None
    try:
        os.rename(stage, dest)
    except OSError:
        try:
            _remove_tree(stage)
        except OSError:
            pass
        if backup is not None:
            try:
                os.rename(backup, dest)
            except OSError:
                raise _PartialCommitError(
                    f"Skill publish failed and rollback failed; previous tree retained at {backup}",
                    backup,
                ) from None
            raise SkillInstallError(
                f"Skill publish failed and the previous tree was restored: {dest}"
            ) from None
        raise
    if backup is not None:
        try:
            _remove_tree(backup)
        except OSError:
            pass  # leftover hidden backup is non-fatal; dest is committed


def status_skill_targets(
    target_ids: list[str],
    *,
    project_root: str | Path | None = None,
    source_root: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve() if project_root else Path.home().expanduser().resolve()
    selected = [SKILL_TARGET_BY_ID[target_id] for target_id in target_ids]
    source = Path(source_root).expanduser().resolve() if source_root is not None else None
    source_files = _load_skill_files(source)
    source_by_path = {rel_path: content for rel_path, content in source_files}
    bundled_digest = _skill_digest(source_files)
    targets: list[dict[str, Any]] = []

    for target in selected:
        dest = root / Path(target.skill_relative_path)
        item: dict[str, Any] = {
            "target": target.target_id,
            "label": target.label,
            "path": str(dest),
            "status": "missing",
            "files": len(source_files),
            "installed_files": 0,
            "bundled_hash": bundled_digest,
            "installed_hash": "",
            "hash_match": False,
            "managed_hash_match": False,
            "extra_files": [],
            "missing_files": sorted(source_by_path),
            "stale_files": [],
        }
        try:
            _check_no_symlink_components(root, target.skill_relative_path)
            if os.path.islink(dest):
                raise _SymlinkError(f"Managed skill path is a symlink: {dest}")
            if not dest.exists():
                targets.append(item)
                continue
            if not dest.is_dir():
                item["status"] = "error"
                item["error"] = "Installed skill path exists but is not a directory."
                targets.append(item)
                continue

            installed_files = _target_installed_files(dest)
            installed_by_path = {rel_path: content for rel_path, content in installed_files}
            item["installed_files"] = len(installed_files)
            installed_digest = _skill_digest(installed_files)
            extra_files = sorted(rel_path for rel_path in installed_by_path if rel_path not in source_by_path)
            missing_files = sorted(rel_path for rel_path in source_by_path if rel_path not in installed_by_path)
            stale_files = sorted(
                rel_path
                for rel_path, content in source_by_path.items()
                if rel_path in installed_by_path and installed_by_path[rel_path] != content
            )
            managed_hash_match = not missing_files and not stale_files
            hash_match = managed_hash_match and not extra_files
            item.update(
                {
                    "installed_hash": installed_digest if installed_files else "",
                    "hash_match": hash_match,
                    "managed_hash_match": managed_hash_match,
                    "extra_files": extra_files,
                    "missing_files": missing_files,
                    "stale_files": stale_files,
                }
            )
            if missing_files or stale_files:
                item["status"] = "stale"
            elif extra_files:
                item["status"] = "extra_files"
            else:
                item["status"] = "up_to_date"
        except (OSError, SkillInstallError) as e:
            item["status"] = "error"
            item["error"] = str(e)
        targets.append(item)

    status_counts: dict[str, int] = {}
    for item in targets:
        status = str(item.get("status", "error"))
        status_counts[status] = status_counts.get(status, 0) + 1

    return {
        "ok": not any(item.get("status") == "error" for item in targets),
        "root": str(root),
        "selected": [target.target_id for target in selected],
        "skill": SKILL_NAME,
        "bundled_files": len(source_files),
        "bundled_hash": bundled_digest,
        "targets": targets,
        "status_counts": status_counts,
    }


def install_skill_targets(
    target_ids: list[str],
    *,
    project_root: str | Path | None = None,
    source_root: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve() if project_root else Path.home().expanduser().resolve()
    selected = [SKILL_TARGET_BY_ID[target_id] for target_id in target_ids]
    if not selected:
        return {
            "ok": True,
            "root": str(root),
            "installed": [],
            "skipped": [],
            "failed": [],
            "selected": [],
            "installed_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "write_attempted": False,
        }

    source = Path(source_root).expanduser().resolve() if source_root is not None else None
    files = _load_skill_files(source)
    installed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    write_attempted = False

    for target in selected:
        dest = root / Path(target.skill_relative_path)
        entry: dict[str, Any] = {
            "target": target.target_id,
            "label": target.label,
            "path": str(dest),
            "files": len(files),
        }
        try:
            _check_no_symlink_components(root, target.skill_relative_path)
            if dest.is_dir():
                _assert_no_symlinks_in_tree(dest)
            write_attempted = True
            stage = _stage_skill_files(dest, files)
            _publish_staged(dest, stage)
            installed.append(entry)
        except _PartialCommitError as exc:
            failed.append(
                {
                    **entry,
                    "target_failed": True,
                    "error_type": "filesystem_error",
                    "partial_commit": True,
                    "backup_path": str(exc.backup),
                    "error": str(exc),
                }
            )
        except _SymlinkError as exc:
            failed.append(
                {
                    **entry,
                    "target_failed": True,
                    "error_type": "symlink_error",
                    "error": str(exc),
                }
            )
        except (SkillInstallError, OSError) as exc:
            failed.append(
                {
                    **entry,
                    "target_failed": True,
                    "error_type": "filesystem_error",
                    "error": str(exc),
                }
            )

    return {
        "ok": not failed,
        "root": str(root),
        "installed": installed,
        "skipped": [],
        "failed": failed,
        "selected": [target.target_id for target in selected],
        "installed_count": len(installed),
        "skipped_count": 0,
        "failed_count": len(failed),
        "write_attempted": write_attempted,
    }
