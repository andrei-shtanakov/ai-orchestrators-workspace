#!/usr/bin/env python3
"""authority_root_guard.py — GOV-009 / ADR-ECO-004 I2 authority-root guard.

Инвариант I2: агент НЕ вправе менять артефакты, определяющие его же authority
(governance.yaml, branch rulesets, определения required-check, own identity/scope).
Их меняет только человек (human_merge).

CI не различает «агент vs человек» надёжно, поэтому этот скрипт — детектор: если PR
трогает authority-root пути, он это помечает. Жёсткое блокирование даёт GitHub ruleset /
CODEOWNERS на тех же путях; здесь — advisory по умолчанию, `--strict` → exit 1
(для репо, где authority-root реально лежит: зонтик, prograph-vault).

Default globs покрывают общий случай; переопределяются `--glob` (повторяемый).
"""
from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
from pathlib import Path

DEFAULT_GLOBS = [
    "**/governance.yaml",
    "authored/registry/governance.yaml",
    "**/rulesets/**",
    "**/required-check-defs/**",
    ".github/workflows/governance-gate.yml",
    "ci/governance/**",
    "CODEOWNERS",
    # Пути агентского мержа (ADR-ECO-008a, заведены в steward 2026-08-20). Они
    # authority-root по той же причине, что и всё выше: определяют, ЧТО агенту
    # позволено мержить и при каких условиях. Агент, правящий их, расширяет
    # собственные полномочия.
    #
    # Почему список общий, а не per-repo: у reusable-гейта нет входа для своих
    # globs, а пути во всех репо одинаковые — иначе каллеры перестали бы быть
    # байт-идентичными. Репо без брокера просто не имеет этих файлов.
    ".github/workflows/merge-broker.yml",
    # Гейт ревью: определяет проверку, через которую агент обязан пройти, держит
    # ключ модели, и при событии `pull_request` исполняется версия ИЗ PR.
    ".github/workflows/codex-review.yml",
    ".github/codex/**",
    # Закрытая классификация merge-акторов: `agent_identities` и
    # `agent_merge_allowed` — это буквально «своя личность и объём прав» из I2.
    "profiles/approval-policy.yaml",
]


def changed_files(repo: Path, base: str) -> list[str] | None:
    """Files changed vs `base` (three-dot: merge-base..HEAD).

    Returns None (NOT []) when git itself fails — the caller must fail closed
    rather than report a clean guard it never actually evaluated.
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "diff", "--name-only", f"{base}...HEAD"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def matches(path: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, g) or fnmatch.fnmatch(path, f"**/{g}")
               for g in globs)


def main() -> int:
    ap = argparse.ArgumentParser(description="GOV-009 / I2 authority-root guard")
    ap.add_argument("--repo", default=".", type=Path)
    ap.add_argument("--base", default="origin/main",
                    help="base ref for the PR diff (e.g. origin/<base_ref>)")
    ap.add_argument("--glob", action="append", default=None,
                    help="authority-root glob (repeatable); overrides defaults")
    ap.add_argument("--strict", action="store_true",
                    help="fail (exit 1) instead of advisory when authority-root changes")
    args = ap.parse_args()

    globs = args.glob if args.glob else DEFAULT_GLOBS
    changed = changed_files(args.repo, args.base)
    if changed is None:
        # fail CLOSED: could not evaluate the guard, so do not report it clean.
        sev = "error" if args.strict else "notice"
        print(f"[{sev}] GOV-009: could not compute diff vs {args.base!r} "
              f"(git failed) — cannot verify authority-root; failing closed under --strict.")
        return 1 if args.strict else 0
    hits = [p for p in changed if matches(p, globs)]

    if not hits:
        print("GOV-009 OK: no authority-root paths changed.")
        return 0

    for p in hits:
        print(f"[{'error' if args.strict else 'notice'}] authority-root modified: {p} "
              f"— requires human_merge (I2)")
    verb = "FAILED" if args.strict else "ADVISORY"
    print(f"\nGOV-009 {verb}: {len(hits)} authority-root path(s) changed. "
          f"These must land via human_merge, never agent_merge.")
    return 1 if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
