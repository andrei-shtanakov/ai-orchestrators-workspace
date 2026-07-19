#!/usr/bin/env python3
# gov:allow-cowork-file — this checker names the token by design (meta-tooling).
"""no_cowork_in_runtime.py — GOV-003 no-cowork-output-in-runtime gate.

Инвариант (ADR-ECO-004, CLAUDE.md §1.4): shipped/runtime-код НИКОГДА не резолвит пути
под `_cowork_output/`. Сканируем только КОД (не прозу), и только реальный РЕЗОЛВ пути:
- проза (`.md`/`.rst`/`.txt`) не сканируется;
- ТЕСТЫ исключены — они создают `_cowork_output/`, чтобы проверить его пропуск;
- упоминание ТОЛЬКО в комментарии (по маркеру языка) не считается резолвом;
- meta-тулинг с `gov:allow-cowork-file` в шапке пропускается целиком;
- точечный escape hatch на строке — `gov:allow-cowork`.

exception: none (hard invariant) — реальный резолв в коде валит гейт (exit 1).

Reusable: гоняется per-repo из governance-gate.yml (`--repo .`). Stdlib-only.
"""
from __future__ import annotations

import argparse
import os
import sys
from fnmatch import fnmatch
from pathlib import Path

# «Runtime/shipped» расширения кода. Проза (.md/.rst/.txt) намеренно НЕ сканируется.
CODE_EXT = {
    ".py", ".rs", ".ts", ".tsx", ".js", ".jsx", ".go", ".rb",
    ".toml", ".yaml", ".yml", ".json", ".sh", ".bash",
}
# Каталоги, куда НЕ спускаемся: инфраструктура + ТЕСТЫ. Тесты создают `_cowork_output/`,
# чтобы проверить, что runtime его пропускает — это не shipped/runtime-код.
SKIP_DIRS = {".git", "_cowork_output", "node_modules", ".venv", "venv",
             "dist", "build", "target", "__pycache__", ".mypy_cache",
             "tests", "test", "__tests__", "testdata"}
# Тест-файлы по имени (лежат не под tests/, но всё равно тесты).
TEST_GLOBS = ("test_*", "*_test.*", "conftest.py", "*.test.*", "*.spec.*")
NEEDLE = "_cowork_output"
# Маркер комментария по расширению: совпадение ТОЛЬКО в комментарии — не резолв пути
# (документация инварианта в коде допустима). .json — без комментариев.
COMMENT_MARK = {
    ".py": "#", ".toml": "#", ".yaml": "#", ".yml": "#", ".sh": "#", ".bash": "#",
    ".rb": "#", ".rs": "//", ".ts": "//", ".tsx": "//", ".js": "//", ".jsx": "//",
    ".go": "//",
}
# Файл с этим маркером в первых строках — governance/meta-тулинг, который вправе
# НАЗЫВАТЬ токен (сам этот чекер). Не waiver инварианта, а директива сканеру.
OPTOUT = "gov:allow-cowork-file"
INLINE_ALLOW = "gov:allow-cowork"  # на конкретной строке — точечный escape hatch


def _opted_out(text: str) -> bool:
    return OPTOUT in "\n".join(text.splitlines()[:6])


def _is_test_file(name: str) -> bool:
    return any(fnmatch(name, g) for g in TEST_GLOBS)


def _code_part(line: str, ext: str) -> str:
    """Line minus its trailing comment (per-language marker) — lets a documented
    mention in a comment pass while a path literal in code still fails."""
    mark = COMMENT_MARK.get(ext)
    return line.split(mark, 1)[0] if mark else line


def scan(repo: Path) -> tuple[list[tuple[Path, int, str]], list[Path], int]:
    hits: list[tuple[Path, int, str]] = []
    skipped: list[Path] = []
    ignored = 0  # comment-only / inline-allowed mentions
    # os.walk with in-place dir pruning — never descends into .git/tests/etc.
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            path = Path(root) / name
            ext = path.suffix.lower()
            if ext not in CODE_EXT or _is_test_file(name):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            rel = path.relative_to(repo)
            if _opted_out(text):
                skipped.append(rel)
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if NEEDLE not in line:
                    continue
                if INLINE_ALLOW in line or NEEDLE not in _code_part(line, ext):
                    ignored += 1
                    continue
                hits.append((rel, lineno, line.strip()))
    return hits, skipped, ignored


def main() -> int:
    ap = argparse.ArgumentParser(description="GOV-003 no _cowork_output in runtime code")
    ap.add_argument("--repo", default=".", type=Path)
    ap.add_argument("--strict", action="store_true",
                    help="accepted for interface symmetry; this gate is always blocking")
    args = ap.parse_args()

    hits, skipped, ignored = scan(args.repo)
    for rel in skipped:
        print(f"[skip ] {rel}: {OPTOUT} (governance/meta-tooling)")
    for rel, lineno, snippet in hits:
        print(f"[error] {rel}:{lineno}: runtime code references {NEEDLE!r}: {snippet[:100]}")
    if hits:
        print(f"\nGOV-003 FAILED: {len(hits)} runtime reference(s) to {NEEDLE!r} "
              f"(hard invariant, no waiver). Legit mention? add `{INLINE_ALLOW}` on the line.")
        return 1
    print(f"GOV-003 OK: no runtime references to {NEEDLE!r} "
          f"({len(skipped)} opted-out file(s), {ignored} comment/allow mention(s) ignored).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
