"""plan_fields_sensor — the Phase-0a wrapper glue over the shared package.

Discovery/freeze/projection are the sensor's; parsing/resolution are the package's.
These cover the new glue: the canonical/legacy split into the snapshot, the manifest
helpers, and frozen-input construction.
"""

from __future__ import annotations

from pathlib import Path

from plan_fields import ManifestIndex, RepoInput
import subprocess

import plan_fields_sensor
from plan_fields_sensor import (
    Pin,
    Source,
    build_inputs,
    freeze_sources,
    manifest_repos,
    resolve_fleet,
    umbrella_git_dir,
)

# The package resolves names through a ManifestIndex, not a bare set: a repo
# written with its declared `git_dir` locator must reach the same verdict as one
# written with the manifest key. Passing a set was the API two package revisions
# ago, and this test kept passing only because the umbrella pin was that stale.
MANIFEST_INDEX = ManifestIndex(frozenset({"maestro", "proctor"}), {})


def test_resolve_fleet_splits_canonical_and_legacy() -> None:
    maestro = "- [x] done shipped @owner:o @id:done\n- [ ] r open @owner:o @id:r\n"
    proctor = (
        "- [ ] pilot @owner:o @blocked_by:todo://maestro/done @id:pilot\n"  # canonical stale
        "- [ ] leg @owner:o @blocked_by:maestro#gone\n"  # legacy dangling
    )
    inputs = [RepoInput("maestro", maestro), RepoInput("proctor", proctor)]
    r = resolve_fleet(inputs, MANIFEST_INDEX)

    assert r["edges"] == 1
    # canonical stale (stable @id identity) -> error
    assert any("PF-BLOCKER-STALE" in e for e in r["diagnostics"]["errors"])
    # legacy dangling -> warning, marked identity-less
    assert any("[legacy source: no @id]" in w for w in r["diagnostics"]["warnings"])
    # structured records carried through to the snapshot
    assert any(d["code"] == "PF-BLOCKER-STALE" for d in r["canonical"])
    assert any(
        d["identity_grade"] == "legacy" and d["slug"] == "gone" for d in r["legacy"]
    )
    assert any("resolved cross-repo @id edge" in n for n in r["diagnostics"]["notes"])


def test_pilot_relation_is_never_double_counted() -> None:
    maestro = "- [ ] r open @owner:o @id:r\n"
    proctor = "- [ ] p @owner:o @blocked_by:todo://maestro/r @id:p\n"
    r = resolve_fleet(
        [RepoInput("maestro", maestro), RepoInput("proctor", proctor)], MANIFEST_INDEX
    )
    assert r["edges"] == 1
    assert r["legacy"] == []  # the @id'd relation is canonical only


def test_manifest_helpers_skip_members_and_dupes() -> None:
    m = {
        "cores": {"spec-runner": {"package_name": "spec-runner", "git_dir": "spec-runner"}},
        "apps": {
            "maestro": {"package_name": "maestro", "git_dir": "maestro"},
            "sdk": {"package_name": "sdk", "git_dir": "maestro", "member": True},
        },
    }
    assert {name for name, _ in manifest_repos(m)} == {"spec-runner", "maestro"}


def test_build_inputs_freezes_todo_and_head(tmp_path: Path) -> None:
    repo = tmp_path / "maestro"
    (repo / ".git").mkdir(parents=True)
    (repo / "TODO.md").write_text("- [ ] x @owner:o @id:x\n", encoding="utf-8")
    sources = [
        Source("maestro", "maestro", Pin("sha", "abc"), present=True, resolved_sha="abc123"),
        Source("ghost", "ghost", Pin("branch", "<default>"), present=False),
    ]
    by = {i.repo: i for i in build_inputs(sources, tmp_path)}
    assert by["maestro"].todo_text == "- [ ] x @owner:o @id:x\n"
    assert by["maestro"].commit == "abc123" and by["maestro"].available
    assert by["ghost"].available is False and by["ghost"].todo_text is None


def test_broken_clone_is_not_frozen_and_never_available_without_a_commit(
    tmp_path: Path,
) -> None:
    # a .git that is not a real repo -> HEAD unresolvable -> not present/frozen,
    # and build_inputs must never mark it available with a null commit.
    repo = tmp_path / "maestro"
    (repo / ".git").mkdir(parents=True)  # a directory, not a valid git repo
    (repo / "TODO.md").write_text("- [ ] x @owner:o @id:x\n", encoding="utf-8")
    manifest = {"apps": {"maestro": {"package_name": "maestro", "git_dir": "maestro"}}}
    sources, warnings = freeze_sources(manifest, tmp_path)
    assert sources[0].present is False and sources[0].resolved_sha is None
    assert any("HEAD unresolved" in w for w in warnings)
    inp = build_inputs(sources, tmp_path)[0]
    assert inp.available is False and inp.commit is None


def _git_repo(root: Path, todo: str) -> Path:
    """A real checkout: freeze_sources only reaches TODO through a resolvable HEAD."""
    root.mkdir(parents=True)
    (root / "TODO.md").write_text(todo, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "seed"],
        check=True,
    )
    return root


def test_umbrella_git_dir_is_located_by_file_not_by_directory_name(tmp_path: Path) -> None:
    """CI checks the umbrella out as `umbrella/`, a workstation as its repo name."""
    ws = tmp_path / "ws"
    (ws / "umbrella").mkdir(parents=True)
    assert umbrella_git_dir(ws, ws / "umbrella") == "umbrella"
    assert umbrella_git_dir(ws, ws / "ai-orchestrators-workspace") == "ai-orchestrators-workspace"
    # outside the measured workspace — not part of the answer
    assert umbrella_git_dir(ws, tmp_path / "elsewhere") is None


def test_umbrella_own_todo_reaches_the_snapshot(tmp_path: Path, monkeypatch) -> None:
    """The repo enforcing the discipline must not be exempt from it.

    The umbrella is not a manifest entry — the manifest lists the set it clones,
    not itself — so manifest-driven discovery skipped it and its own backlog never
    reached the snapshot. Nothing failed; the file was simply absent from the answer.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    _git_repo(ws / "maestro", "- [ ] a @owner:github:o @id:a\n")
    self_root = _git_repo(ws / "umbrella", "- [ ] own @owner:github:o @id:own\n")
    monkeypatch.setattr(plan_fields_sensor, "UMBRELLA_ROOT", self_root)

    manifest = {"apps": {"maestro": {"package_name": "maestro", "git_dir": "maestro"}}}
    sources, _ = freeze_sources(manifest, ws)
    by_repo = {s.repo: s for s in sources}
    assert "ai-orchestrators-workspace" in by_repo
    assert by_repo["ai-orchestrators-workspace"].present

    inputs = {i.repo: i for i in build_inputs(sources, ws)}
    assert inputs["ai-orchestrators-workspace"].todo_text == "- [ ] own @owner:github:o @id:own\n"


def test_umbrella_in_the_manifest_is_not_counted_twice(tmp_path: Path, monkeypatch) -> None:
    """A future manifest entry wins; the self-scan must not duplicate the repo."""
    ws = tmp_path / "ws"
    ws.mkdir()
    self_root = _git_repo(ws / "umbrella", "- [ ] own @owner:github:o @id:own\n")
    monkeypatch.setattr(plan_fields_sensor, "UMBRELLA_ROOT", self_root)

    manifest = {"tools": {"umbrella": {"package_name": "umbrella", "git_dir": "umbrella"}}}
    sources, _ = freeze_sources(manifest, ws)
    assert [s.git_dir for s in sources] == ["umbrella"]
