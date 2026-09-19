#!/usr/bin/env python3
# gov:allow-cowork-file — this checker names the token by design (meta-tooling).
"""no_cowork_in_runtime.py — GOV-003 no-cowork-output-in-runtime gate.

Инвариант (ADR-ECO-004, CLAUDE.md §1.4): shipped/runtime-код НИКОГДА не резолвит пути
под `_cowork_output/`. Сканируем только КОД (не прозу), и только реальный РЕЗОЛВ пути:
- проза (`.md`/`.rst`/`.txt`) не сканируется;
- ТЕСТЫ исключены — они создают `_cowork_output/`, чтобы проверить его пропуск;
- упоминание ТОЛЬКО в комментарии (по маркеру языка) не считается резолвом;
- meta-тулинг с `gov:allow-cowork-file` в шапке пропускается целиком;
- точечный escape hatch на строке — `gov:allow-cowork`;
- ФАЙЛ, поимённо объявленный data-артефактом в `caller-policy.toml`, получает то
  же обращение, что `.md`: это не код, и проза внутри него не резолв.

Ступень «репо runtime, но подкаталог — data» заведена потому, что между «весь
репо» (`runtime-scan: off`) и «весь файл»/«строка» не было ничего, а замороженное
evidence нельзя править руками: брифы impresario цитируют текст стандартов, где
STD-5 НАЗЫВАЕТ `_cowork_output`, и гейт падал на фразе, формулирующей сам
инвариант. Объявление живёт в ПОЛИТИКЕ ЗОНТИКА, не в сканируемом дереве: репо не
может расширить себе исключение, это может только PR в зонтик.

exception: none (hard invariant) — реальный резолв в коде валит гейт (exit 1).

Reusable: гоняется per-repo из governance-gate.yml (`--repo .`). Stdlib-only.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tomllib
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
# `//`-комментарий, но НЕ схема URL (`://`) — иначе https:// глотал бы остаток строки
# и прятал реальный резолв после него (false negative).
_SLASH_COMMENT = re.compile(r"(?<!:)//")
# Таблица объявлений в caller-policy.toml: repo -> список путей (файл/каталог).
DATA_ARTIFACTS_TABLE = "data-artifacts"
# Объявляются ТОЛЬКО ФАЙЛЫ, поимённо. Каталог объявить нельзя — и это главное
# свойство ступени, а не ограничение реализации.
#
# Два захода приёмочного ревью показали, почему (обе находки major/high). Заход
# 1: объявление каталога накрывало любого потомка, поэтому репо, положив
# `pilot/runtime.py` внутрь разрешённого каталога, расширяло себе исключение на
# настоящий резолв. Заход 2 (после сужения до сериализационных форматов):
# расширение НЕ доказывает прозу — `pilot/action.yml` с `run: cat
# _cowork_output/x.json` это исполняемая конфигурация, а `.yml` уже в списке
# «данных». Оба раза дыру открывал ФАЙЛ, КОТОРОГО В МОМЕНТ ОБЪЯВЛЕНИЯ НЕ БЫЛО.
#
# Поимённое объявление закрывает класс целиком, без эвристик «проза или код»:
# новый файл рядом с объявленным не покрыт ничем, что бы в нём ни лежало.
# Цена — PR в зонтик при появлении нового evidence; для ЗАМОРОЖЕННЫХ записей
# (а объявляются только такие) это не издержка, а тот же ревью, что у всякой
# governance-правки.
#
# Форматы, которые вообще можно объявить: только сериализационные. Объявить
# `.py` нельзя — отказ на загрузке политики.
DATA_EXT = {".yaml", ".yml", ".json", ".toml"}


class PolicyError(RuntimeError):
    """Политика нечитаема или негодна — факт не установлен, значит не пропускаем."""


def load_data_artifact_files(policy_path: Path, repo_name: str) -> list[str]:
    """Файлы, поимённо объявленные data-артефактами для этого репо.

    Fail-closed по всей дороге: нечитаемая политика, не-список, не-строка,
    пустая строка, абсолютный путь, `.`/`..` в любом сегменте — PolicyError, а
    не «объявлений нет». Негодное объявление обязано ронять гейт, а не тихо
    вырождаться в сканирование (и уж тем более не в пропуск): неустановленный
    факт трактуется против исключения.

    `.` и `..` отсекаются ОТДЕЛЬНО от прочей валидации: запись `"."` объявила бы
    data-артефактом весь репозиторий, то есть выключила бы GOV-003 целиком и
    молча — ровно то, ради чего эта ступень и отделена от `runtime-scan: off`.

    Расширение обязано быть сериализационным: объявить `.py` или `.sh`
    невозможно в принципе, а не «не рекомендуется».
    """
    try:
        raw = tomllib.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise PolicyError(f"{policy_path}: политика нечитаема — {exc}") from exc
    table = raw.get(DATA_ARTIFACTS_TABLE, {})
    if not isinstance(table, dict):
        raise PolicyError(f"{policy_path}: [{DATA_ARTIFACTS_TABLE}] обязан быть таблицей")
    entries = table.get(repo_name, [])
    if not isinstance(entries, list):
        raise PolicyError(
            f"{policy_path}: [{DATA_ARTIFACTS_TABLE}].{repo_name} обязан быть списком строк"
        )
    files: list[str] = []
    for entry in entries:
        if not isinstance(entry, str) or not entry.strip():
            raise PolicyError(
                f"{policy_path}: [{DATA_ARTIFACTS_TABLE}].{repo_name}: "
                f"запись обязана быть непустой строкой, получено {entry!r}"
            )
        norm = entry.strip().strip("/")
        parts = norm.split("/")
        if entry.startswith("/") or not norm or any(p in ("", ".", "..") for p in parts):
            raise PolicyError(
                f"{policy_path}: [{DATA_ARTIFACTS_TABLE}].{repo_name}: "
                f"{entry!r} — объявлять можно только путь внутри репо "
                f"(без абсолютных путей, `.` и `..`); весь репо выключается "
                f"входом `runtime-scan`, а не этой таблицей"
            )
        if Path(norm).suffix.lower() not in DATA_EXT:
            raise PolicyError(
                f"{policy_path}: [{DATA_ARTIFACTS_TABLE}].{repo_name}: "
                f"{entry!r} — объявить можно только сериализационный файл "
                f"({', '.join(sorted(DATA_EXT))}); исполняемый код data-артефактом "
                f"не объявляется"
            )
        files.append(norm)
    return files


def _is_declared_data_file(rel: Path, data_files: list[str]) -> bool:
    """rel ЕСТЬ объявленный файл — ТОЧНОЕ совпадение, не «лежит под».

    Никакого покрытия потомков: сосед объявленного файла не покрыт ничем, и
    файл, появившийся после объявления, — тоже. Именно это свойство закрывает
    обе находки ревью, а не догадки о том, проза внутри или код.
    """
    return any(tuple(f.split("/")) == rel.parts for f in data_files)


def verify_declarations_exist(repo: Path, data_files: list[str]) -> None:
    """Каждый объявленный файл обязан существовать и быть обычным файлом.

    Протухшее объявление (файл удалён/переименован/оказался каталогом) — это
    находка, а не «нечего исключать»: молча выродившись в ноль, оно оставило бы
    в политике зонтика строку, которая ничего не значит, и следующий читатель
    счёл бы исключение действующим. Symlink тоже отказ: цель может лежать вне
    объявленного набора.
    """
    for rel in data_files:
        target = repo / rel
        if target.is_symlink() or not target.is_file():
            raise PolicyError(
                f"объявление data-артефакта протухло: {rel} — не обычный файл "
                f"в этом репо (удалён, переименован, каталог или symlink). "
                f"Поправьте [{DATA_ARTIFACTS_TABLE}] в caller-policy.toml зонтика."
            )


def _opted_out(text: str) -> bool:
    return OPTOUT in "\n".join(text.splitlines()[:6])


def _is_test_file(name: str) -> bool:
    return any(fnmatch(name, g) for g in TEST_GLOBS)


def _code_part(line: str, ext: str) -> str:
    """Line minus its trailing comment (per-language marker) — lets a documented
    mention in a comment pass while a path literal in code still fails.

    For `//` languages a scheme `://` is NOT a comment, so a URL never truncates
    the line and hides a real `_cowork_output` after it."""
    mark = COMMENT_MARK.get(ext)
    if mark is None:
        return line
    if mark == "//":
        m = _SLASH_COMMENT.search(line)
        return line[: m.start()] if m else line
    return line.split(mark, 1)[0]


def scan(
    repo: Path, data_files: list[str] | None = None
) -> tuple[list[tuple[Path, int, str]], list[Path], int]:
    hits: list[tuple[Path, int, str]] = []
    skipped: list[Path] = []
    ignored = 0  # comment-only / inline-allowed mentions
    data_files = data_files or []
    # os.walk with in-place dir pruning — never descends into .git/tests/etc.
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            path = Path(root) / name
            ext = path.suffix.lower()
            if ext not in CODE_EXT or _is_test_file(name):
                continue
            # Поимённо объявленный файл — проза, как `.md`: не сканируется.
            # Точное совпадение: сосед не покрыт, новый файл не покрыт.
            if _is_declared_data_file(path.relative_to(repo), data_files):
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
    ap.add_argument("--policy", type=Path, default=None,
                    help="caller-policy.toml зонтика: источник объявлений "
                         "[data-artifacts] (файлы, которые НЕ runtime-код)")
    ap.add_argument("--repo-name", default=None,
                    help="имя репо в таблице [data-artifacts] политики")
    args = ap.parse_args()

    # Объявления читаются ТОЛЬКО когда названы оба аргумента: политика без
    # имени репо (и наоборот) — неполный вызов, а не «объявлений нет».
    data_files: list[str] = []
    if (args.policy is None) != (args.repo_name is None):
        print("[error] --policy и --repo-name задаются только вместе", file=sys.stderr)
        return 2
    if args.policy is not None and args.repo_name is not None:
        try:
            data_files = load_data_artifact_files(args.policy, args.repo_name)
            verify_declarations_exist(args.repo, data_files)
        except PolicyError as exc:
            print(f"[error] {exc}", file=sys.stderr)
            return 2

    hits, skipped, ignored = scan(args.repo, data_files)
    for decl in data_files:
        print(f"[data ] {decl}: объявлен data-артефактом для {args.repo_name} "
              f"(caller-policy.toml) — обращение как с прозой, не с кодом")
    for rel in skipped:
        print(f"[skip ] {rel}: {OPTOUT} (governance/meta-tooling)")
    for rel, lineno, snippet in hits:
        print(f"[error] {rel}:{lineno}: runtime code references {NEEDLE!r}: {snippet[:100]}")
    if hits:
        print(f"\nGOV-003 FAILED: {len(hits)} runtime reference(s) to {NEEDLE!r} "
              f"(hard invariant on path resolution). If a line is a documented mention, "
              f"not a resolve, mark it with `{INLINE_ALLOW}`.")
        return 1
    print(f"GOV-003 OK: no runtime references to {NEEDLE!r} "
          f"({len(skipped)} opted-out file(s), {ignored} comment/allow mention(s) ignored, "
          f"{len(data_files)} declared data-artifact file(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
