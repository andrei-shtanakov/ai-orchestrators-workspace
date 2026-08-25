"""plan_fields_sensor — the Phase-0a wrapper glue over the shared package.

Discovery/freeze/projection are the sensor's; parsing/resolution are the package's.
These cover the new glue: the canonical/legacy split into the snapshot, the manifest
helpers, and frozen-input construction.
"""

from __future__ import annotations

from pathlib import Path

from plan_fields import ManifestIndex, RepoInput
from plan_fields_sensor import (
    Pin,
    Source,
    build_inputs,
    freeze_sources,
    manifest_repos,
    resolve_fleet,
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
