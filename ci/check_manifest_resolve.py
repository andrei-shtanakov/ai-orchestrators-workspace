#!/usr/bin/env python3
# gov:allow-cowork-file — names _cowork_output in a doc reference (meta-tooling).
"""check_manifest_resolve.py — GAP-7 / GOV-004 name-alias-resolve gate.

Дом: зонтик ai-orchestrators-workspace, `ci/`. Вендоренная копия — гоняется в
manifest-drift.yml рядом с check-release-drift.py (тот же шов intended↔observed).

Ловит класс «зелёный PR ≠ рабочая раскладка» (`_cowork_output/2026-07-17-workspace-rename-risks.md`):
имена в workspace-manifest.toml должны резолвиться в реальные каталоги/remote-ы, а не
разъезжаться (maestro vs Maestro и т.п.).

Проверки (per-entry, cores/apps/tools):
  * обязательные поля git_dir + repo_url;
  * git_dir == basename(repo_url) — если нет, это alias-дрейф (error), кроме entries
    с `member = true` (сознательно делят git_dir с владельцем) или явным `dir_alias = "…"`;
  * pyproject_path (если задан) начинается с "<git_dir>/";
  * дубль git_dir между не-member entries → error;
  * disk-режим (когда сосед лежит на диске, workspace-run): git_dir существует и
    `git -C git_dir remote get-url origin` == repo_url. Нет на диске → info
    (manifest-only CI деградирует, как check-release-drift).

Контракт severity/exit — как у check-release-drift.py: error→2, warn→1 (или --strict),
чисто→0. --json для dispatcher.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # 3.10 fallback
    import tomli as tomllib  # type: ignore

SECTIONS = ("cores", "apps", "tools")


def repo_basename(url: str) -> str:
    """`git@github.com:org/maestro.git` / `https://…/maestro` → `maestro`."""
    return url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")


def git_remote(repo: Path) -> str | None:
    """origin URL of a repo on disk, or None if not a git repo / git absent."""
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def check_entry(cid: str, meta: dict, ws: Path) -> list[dict]:
    """Findings for one manifest entry. Each: {id, severity, code, msg}."""
    out: list[dict] = []

    def add(sev: str, code: str, msg: str) -> None:
        out.append({"id": cid, "severity": sev, "code": code, "msg": msg})

    git_dir = meta.get("git_dir")
    repo_url = meta.get("repo_url")
    if not git_dir:
        add("error", "missing_git_dir", "no git_dir")
        return out
    if not repo_url:
        add("error", "missing_repo_url", "no repo_url")
        return out

    basename = repo_basename(repo_url)
    is_member = bool(meta.get("member", False))
    dir_alias = meta.get("dir_alias")
    if git_dir != basename and not is_member and dir_alias != basename:
        add(
            "error", "alias_drift",
            f"git_dir '{git_dir}' != repo basename '{basename}' "
            f"(add `dir_alias = \"{basename}\"` if intentional)",
        )

    pp = meta.get("pyproject_path")
    if pp and not pp.startswith(f"{git_dir}/"):
        add("error", "pyproject_outside_git_dir",
            f"pyproject_path '{pp}' not under git_dir '{git_dir}/'")

    # disk-режим: сосед на диске → сверяем remote; нет → info (manifest-only CI)
    on_disk = ws / git_dir
    if (on_disk / ".git").exists():
        remote = git_remote(on_disk)
        if remote is None:
            add("info", "no_remote", f"{git_dir}/ present but origin unreadable")
        elif remote != repo_url:
            add("error", "remote_mismatch",
                f"{git_dir}/ origin '{remote}' != manifest repo_url '{repo_url}'")
    else:
        add("info", "no_repo_on_disk", f"{git_dir}/ not on disk (manifest-only)")
    return out


def check_manifest(manifest: dict, ws: Path) -> list[dict]:
    findings: list[dict] = []
    seen_git_dir: dict[str, str] = {}
    for sect in SECTIONS:
        for name, meta in manifest.get(sect, {}).items():
            if not isinstance(meta, dict):
                continue
            cid = f"{sect}.{name}"
            findings.extend(check_entry(cid, meta, ws))
            gd = meta.get("git_dir")
            if gd and not meta.get("member", False):
                if gd in seen_git_dir:
                    findings.append({
                        "id": cid, "severity": "error", "code": "dup_git_dir",
                        "msg": f"git_dir '{gd}' also used by {seen_git_dir[gd]}",
                    })
                else:
                    seen_git_dir[gd] = cid
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description="GAP-7 manifest name/alias resolve gate")
    ap.add_argument("--workspace", default=".", type=Path)
    ap.add_argument("--manifest", default="workspace-manifest.toml", type=Path)
    ap.add_argument("--strict", action="store_true", help="warn also fails the gate")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        manifest = tomllib.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as err:
        print(f"error: cannot read manifest: {err}", file=sys.stderr)
        return 2

    findings = check_manifest(manifest, args.workspace)
    errors = [f for f in findings if f["severity"] == "error"]
    warns = [f for f in findings if f["severity"] == "warn"]

    if args.json:
        print(json.dumps({"findings": findings}, indent=2, ensure_ascii=False))
    else:
        for f in findings:
            if f["severity"] == "info":
                continue
            print(f"[{f['severity']:5}] {f['id']}: {f['msg']} ({f['code']})")
        print(f"\nmanifest-resolve: {len(errors)} error, {len(warns)} warn "
              f"({len(findings)} findings total)")

    if errors:
        return 2
    if warns and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
