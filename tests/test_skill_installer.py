"""Skill installer safety tests.

Covers symlink no-follow containment, per-target atomic staging with
rollback, and exclusive standalone selectors in ``parse_skill_targets``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from smart_search import skill_installer as si
from smart_search.skill_installer import (
    SKILL_TARGETS,
    SkillInstallError,
    install_skill_targets,
    parse_skill_targets,
    status_skill_targets,
)


def _source_tree(tmp_path: Path) -> Path:
    src = tmp_path / "source"
    src.mkdir()
    (src / "SKILL.md").write_text("# skill v2\n")
    (src / "scripts").mkdir()
    (src / "scripts" / "run.sh").write_text("#!/bin/sh\necho run\n")
    return src


def _dest(tmp_path: Path) -> Path:
    # "generic" target maps to .agents/skills/smart-search-cli
    return tmp_path / ".agents" / "skills" / "smart-search-cli"


def _target_ids():
    return [target.target_id for target in SKILL_TARGETS]


# ---------------------------------------------------------------------------
# parse_skill_targets: exclusive standalone selectors
# ---------------------------------------------------------------------------


def test_parse_standalone_skip_and_none_return_empty():
    for raw in ("skip", "none", "no", "n", "跳过", "无"):
        assert parse_skill_targets(raw) == []


def test_parse_standalone_all_returns_every_target():
    assert parse_skill_targets("all") == _target_ids()
    assert parse_skill_targets("全部") == _target_ids()


def test_parse_empty_and_blank_return_empty():
    assert parse_skill_targets("") == []
    assert parse_skill_targets("   ") == []


@pytest.mark.parametrize(
    "raw",
    [
        "skip,codex",
        "codex,skip",
        "skip,generic",
        "none,codex",
        "all,codex",
        "codex,all",
        "all,generic",
        "skip,all",
        "skip,skip",
        "skip codex",
    ],
)
def test_parse_mixed_standalone_selector_rejected(raw):
    with pytest.raises(SkillInstallError):
        parse_skill_targets(raw)


def test_parse_valid_targets_keep_order_and_dedupe():
    assert parse_skill_targets("codex,generic") == ["codex", "generic"]
    assert parse_skill_targets("generic,codex,generic") == ["generic", "codex"]
    assert parse_skill_targets("claude-code,cursor") == ["claude", "cursor"]


def test_parse_unknown_target_raises():
    with pytest.raises(SkillInstallError, match="Unknown skill target"):
        parse_skill_targets("codex,bogus")


# ---------------------------------------------------------------------------
# Symlink no-follow containment
# ---------------------------------------------------------------------------


def test_install_planted_target_file_symlink_leaves_victim_untouched(tmp_path):
    src = _source_tree(tmp_path)
    victim = tmp_path / "victim.txt"
    victim.write_text("victim-bytes")

    dest = _dest(tmp_path)
    dest.mkdir(parents=True)
    (dest / "SKILL.md").symlink_to(victim)

    result = install_skill_targets(["generic"], project_root=tmp_path, source_root=src)

    assert victim.read_text() == "victim-bytes"
    assert os.path.islink(dest / "SKILL.md")
    assert result["installed_count"] == 0
    assert result["failed_count"] == 1
    failed = result["failed"][0]
    assert failed["target"] == "generic"
    assert failed["target_failed"] is True
    assert failed["error_type"] == "symlink_error"
    assert "symlink" in failed["error"].lower()


def test_install_rejects_symlinked_destination_root(tmp_path):
    src = _source_tree(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("old")

    dest = _dest(tmp_path)
    dest.parent.mkdir(parents=True)
    dest.symlink_to(outside, target_is_directory=True)

    result = install_skill_targets(["generic"], project_root=tmp_path, source_root=src)

    assert result["installed_count"] == 0
    assert result["failed"][0]["error_type"] == "symlink_error"
    assert os.path.islink(dest)
    assert (outside / "SKILL.md").read_text() == "old"


def test_install_rejects_symlinked_intermediate_component(tmp_path):
    src = _source_tree(tmp_path)
    outside = tmp_path / "outside-skills"
    outside.mkdir()

    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "skills").symlink_to(outside, target_is_directory=True)

    result = install_skill_targets(["generic"], project_root=tmp_path, source_root=src)

    assert result["installed_count"] == 0
    assert result["failed"][0]["error_type"] == "symlink_error"


def test_status_marks_symlinked_destination_error(tmp_path):
    src = _source_tree(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()

    dest = _dest(tmp_path)
    dest.parent.mkdir(parents=True)
    dest.symlink_to(outside, target_is_directory=True)

    result = status_skill_targets(["generic"], project_root=tmp_path, source_root=src)

    assert result["ok"] is False
    target = result["targets"][0]
    assert target["status"] == "error"
    assert "symlink" in target["error"].lower()


def test_status_marks_tree_containing_symlink_error(tmp_path):
    src = _source_tree(tmp_path)
    victim = tmp_path / "victim.txt"
    victim.write_text("victim-bytes")

    dest = _dest(tmp_path)
    dest.mkdir(parents=True)
    (dest / "SKILL.md").symlink_to(victim)

    result = status_skill_targets(["generic"], project_root=tmp_path, source_root=src)

    assert result["ok"] is False
    assert result["targets"][0]["status"] == "error"
    assert victim.read_text() == "victim-bytes"


# ---------------------------------------------------------------------------
# Per-target atomicity
# ---------------------------------------------------------------------------


def test_install_staging_failure_preserves_old_tree(monkeypatch, tmp_path):
    src = _source_tree(tmp_path)
    dest = _dest(tmp_path)
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text("old-content")

    real_write = Path.write_bytes
    calls = {"n": 0}

    def flaky_write(self, data):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("injected Nth-write failure")
        return real_write(self, data)

    monkeypatch.setattr(Path, "write_bytes", flaky_write)

    result = install_skill_targets(["generic"], project_root=tmp_path, source_root=src)

    assert (dest / "SKILL.md").read_text() == "old-content"
    assert result["installed_count"] == 0
    assert result["failed_count"] == 1
    assert result["failed"][0]["target_failed"] is True
    assert result["failed"][0]["error_type"] == "filesystem_error"
    assert not list(dest.parent.glob(".smart-search-skill-stage-*"))


def test_install_publish_failure_rolls_back_old_tree(monkeypatch, tmp_path):
    src = _source_tree(tmp_path)
    dest = _dest(tmp_path)
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text("old-content")

    real_rename = os.rename

    def flaky_rename(src_path, dst_path):
        if Path(src_path).name.startswith(".smart-search-skill-stage-") and Path(dst_path) == dest:
            raise OSError("injected publish failure")
        return real_rename(src_path, dst_path)

    monkeypatch.setattr(si.os, "rename", flaky_rename)

    result = install_skill_targets(["generic"], project_root=tmp_path, source_root=src)

    assert (dest / "SKILL.md").read_text() == "old-content"
    assert result["installed_count"] == 0
    assert result["failed_count"] == 1
    assert result["failed"][0]["target_failed"] is True
    assert "restored" in result["failed"][0]["error"]
    assert not list(dest.parent.glob(".smart-search-skill-backup-*"))
    assert not list(dest.parent.glob(".smart-search-skill-stage-*"))


def test_install_move_aside_failure_cleans_staging(monkeypatch, tmp_path):
    src = _source_tree(tmp_path)
    dest = _dest(tmp_path)
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text("old-content")

    real_rename = os.rename

    def flaky_rename(src_path, dst_path):
        if Path(src_path) == dest and Path(dst_path).name.startswith(".smart-search-skill-backup-"):
            raise OSError("injected move-aside failure")
        return real_rename(src_path, dst_path)

    monkeypatch.setattr(si.os, "rename", flaky_rename)
    result = install_skill_targets(["generic"], project_root=tmp_path, source_root=src)

    assert (dest / "SKILL.md").read_text() == "old-content"
    assert result["installed_count"] == 0
    assert result["failed_count"] == 1
    assert result["write_attempted"] is True
    assert not list(dest.parent.glob(".smart-search-skill-stage-*"))
    assert not list(dest.parent.glob(".smart-search-skill-backup-*"))


def test_install_publish_and_rollback_failure_reports_partial_commit(monkeypatch, tmp_path):
    src = _source_tree(tmp_path)
    dest = _dest(tmp_path)
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text("old-content")

    real_rename = os.rename

    def flaky_rename(src_path, dst_path):
        source = Path(src_path)
        destination = Path(dst_path)
        if source.name.startswith(".smart-search-skill-stage-") and destination == dest:
            raise OSError("injected publish failure")
        if source.name.startswith(".smart-search-skill-backup-") and destination == dest:
            raise OSError("injected rollback failure")
        return real_rename(src_path, dst_path)

    monkeypatch.setattr(si.os, "rename", flaky_rename)
    result = install_skill_targets(["generic"], project_root=tmp_path, source_root=src)

    assert not dest.exists()
    assert result["installed_count"] == 0
    assert result["failed_count"] == 1
    failed = result["failed"][0]
    assert failed["partial_commit"] is True
    backup = Path(failed["backup_path"])
    assert backup.is_dir()
    assert (backup / "SKILL.md").read_text() == "old-content"
    assert not list(dest.parent.glob(".smart-search-skill-stage-*"))


def test_install_success_publishes_staged_tree_and_cleans_up(tmp_path):
    src = _source_tree(tmp_path)
    dest = _dest(tmp_path)
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text("old-content")

    result = install_skill_targets(["generic"], project_root=tmp_path, source_root=src)

    assert result["installed_count"] == 1
    assert result["failed_count"] == 0
    assert result["ok"] is True
    assert (dest / "SKILL.md").read_text() == "# skill v2\n"
    assert (dest / "scripts" / "run.sh").read_text() == "#!/bin/sh\necho run\n"
    assert not list(dest.parent.glob(".smart-search-skill-backup-*"))
    assert not list(dest.parent.glob(".smart-search-skill-stage-*"))


def test_install_multiple_targets_commits_only_successful(tmp_path):
    src = _source_tree(tmp_path)
    victim = tmp_path / "victim.txt"
    victim.write_text("victim-bytes")

    codex_dest = tmp_path / ".codex" / "skills" / "smart-search-cli"
    codex_dest.mkdir(parents=True)
    (codex_dest / "SKILL.md").symlink_to(victim)

    result = install_skill_targets(["codex", "generic"], project_root=tmp_path, source_root=src)

    assert victim.read_text() == "victim-bytes"
    assert result["installed_count"] == 1
    assert result["failed_count"] == 1
    assert result["installed"][0]["target"] == "generic"
    assert result["failed"][0]["target"] == "codex"
    assert result["failed"][0]["error_type"] == "symlink_error"
    assert (tmp_path / ".agents" / "skills" / "smart-search-cli" / "SKILL.md").read_text() == "# skill v2\n"
