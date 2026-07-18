#!/usr/bin/env python3
# gov:allow-cowork-file — this checker names the token by design (meta-tooling).
"""no_cowork_in_runtime.py — GOV-003 no-cowork-output-in-runtime gate.

Инвариант (ADR-ECO-004, CLAUDE.md §1.4): shipped/runtime-код НИКОГДА не резолвит пути
под `_cowork_output/`. Дев-тулинг внутри самого `_cowork_output/devtools/` — исключение,
но он и так лежит под исключаемым каталогом. Документация (`.md`, CLAUDE.md) вправе
упоминать `_cowork_output` — сканируем только КОД, не прозу.

exception: none (hard invariant) — любое совпадение валит гейт (exit 1).

Reusable: гоняется per-repo из governance-gate.yml (`--repo .`). Stdlib-only.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# «Runtime/shipped» расширения кода. Проза (.md/.rst/.txt) намеренно НЕ сканируется.
CODE_EXT = {
    ".py", ".rs", ".ts", ".tsx", ".js", ".jsx", ".go", ".rb",
    ".toml", ".yaml", ".yml", ".json", ".sh", ".bash",
}
# Каталоги, где ссылка на _cowork_output легитимна или нерелевантна.
SKIP_DIRS = {".git", "_cowork_output", "node_modules", ".venv", "venv",
             "dist", "build", "target", "__pycache__", ".mypy_cache"}
NEEDLE = "_cowork_output"
# Файл с этим маркером в первых строках — governance/meta-тулинг, который вправе
# НАЗЫВАТЬ токен (сам этот чекер). Это не waiver инварианта (runtime всё равно не
# резолвит cowork), а директива сканеру. Пропуски печатаются — тихого обхода нет.
OPTOUT = "gov:allow-cowork-file"


def _opted_out(text: str) -> bool:
    return OPTOUT in "\n".join(text.splitlines()[:6])


def scan(repo: Path) -> tuple[list[tuple[Path, int, str]], list[Path]]:
    hits: list[tuple[Path, int, str]] = []
    skipped: list[Path] = []
    for path in repo.rglob("*"):
        if path.is_dir():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(repo).parts):
            continue
        if path.suffix.lower() not in CODE_EXT:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _opted_out(text):
            skipped.append(path.relative_to(repo))
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if NEEDLE in line:
                hits.append((path.relative_to(repo), lineno, line.strip()))
    return hits, skipped


def main() -> int:
    ap = argparse.ArgumentParser(description="GOV-003 no _cowork_output in runtime code")
    ap.add_argument("--repo", default=".", type=Path)
    ap.add_argument("--strict", action="store_true",
                    help="accepted for interface symmetry; this gate is always blocking")
    args = ap.parse_args()

    hits, skipped = scan(args.repo)
    for rel in skipped:
        print(f"[skip ] {rel}: {OPTOUT} (governance/meta-tooling)")
    for rel, lineno, snippet in hits:
        print(f"[error] {rel}:{lineno}: runtime code references {NEEDLE!r}: {snippet[:100]}")
    if hits:
        print(f"\nGOV-003 FAILED: {len(hits)} runtime reference(s) to {NEEDLE!r} "
              f"(hard invariant, no waiver).")
        return 1
    print(f"GOV-003 OK: no runtime references to {NEEDLE!r} "
          f"({len(skipped)} meta-tooling file(s) opted out).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
