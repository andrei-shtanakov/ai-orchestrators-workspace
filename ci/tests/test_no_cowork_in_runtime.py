"""Тесты GOV-003 (no_cowork_in_runtime.py), ступень «data-артефакты».

Сторож без исполнителя маскирует дыру (шапка tests.yml): у этого чекера тестов
не было вовсе, а он — hard invariant по всему флоту. Здесь проверяются и старое
поведение (чтобы ступень его не ослабила), и новое объявление, и КАЖДЫЙ путь
отказа политики: негодное объявление обязано ронять гейт, а не вырождаться в
тихий пропуск.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from no_cowork_in_runtime import (
    PolicyError,
    _is_declared_data_file,
    load_data_artifact_files,
    scan,
    verify_declarations_exist,
)

NEEDLE_LINE = 'path = "_cowork_output/x.json"\n'


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp_path


def _policy(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "caller-policy.toml"
    p.write_text(body, encoding="utf-8")
    return p


# --- старое поведение не ослаблено -----------------------------------------


def test_resolve_in_code_still_fails_without_declarations(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"src/app.py": NEEDLE_LINE})
    hits, _, _ = scan(repo)
    assert [str(h[0]) for h in hits] == ["src/app.py"]


def test_declaration_does_not_leak_to_undeclared_paths(tmp_path: Path) -> None:
    """Объявлен один файл — `src/` обязан проверяться как прежде."""
    repo = _repo(tmp_path, {"pilot/b.yaml": NEEDLE_LINE, "src/app.py": NEEDLE_LINE})
    hits, _, _ = scan(repo, ["pilot/b.yaml"])
    assert [str(h[0]) for h in hits] == ["src/app.py"]


# --- сама ступень: объявляются ТОЛЬКО файлы, поимённо -----------------------


def test_declared_file_is_treated_as_prose(tmp_path: Path) -> None:
    """Живой случай impresario: текст стандартов, записанный в evidence."""
    brief = (
        "brief_id: BRF-1\n"
        "standards: \"...STD-5. Канон-first / SSOT\\n\\n`_cowork_output/` — dev-only,\"\n"
    )
    repo = _repo(tmp_path, {"pilot/briefs/brf-1.yaml": brief})
    assert scan(repo)[0], "без объявления это находка — иначе тест ничего не доказывает"
    assert scan(repo, ["pilot/briefs/brf-1.yaml"])[0] == []


@pytest.mark.parametrize(
    "newcomer",
    [
        "pilot/briefs/runtime.py",     # заход 1 ревью: код рядом с объявленным
        "pilot/briefs/action.yml",     # заход 2 ревью: исполняемый YAML
        "pilot/briefs/brf-2.yaml",     # просто необъявленный сосед
    ],
)
def test_file_appearing_next_to_a_declaration_is_not_covered(
    tmp_path: Path, newcomer: str
) -> None:
    """Обе находки приёмочного ревью (major/high) закрываются одним свойством:
    покрыт РОВНО объявленный файл. Объявленный КАТАЛОГ дырявился файлом,
    которого в момент объявления не было, — сперва `runtime.py`, потом
    `action.yml` с `run: cat _cowork_output/...`, потому что расширение не
    доказывает прозу. Точное совпадение снимает вопрос целиком: гадать «проза
    или код» больше не нужно."""
    repo = _repo(tmp_path, {"pilot/briefs/brf-1.yaml": NEEDLE_LINE, newcomer: NEEDLE_LINE})
    hits = scan(repo, ["pilot/briefs/brf-1.yaml"])[0]
    assert [str(h[0]) for h in hits] == [newcomer]


def test_declaration_does_not_cover_the_parent_directory(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"pilot/other.yaml": NEEDLE_LINE})
    assert [str(h[0]) for h in scan(repo, ["pilot/briefs/brf-1.yaml"])[0]] == ["pilot/other.yaml"]


@pytest.mark.parametrize(
    "rel,decls,expected",
    [
        ("pilot/briefs/b.yaml", ["pilot/briefs/b.yaml"], True),
        ("pilot/briefs/b.yaml", ["pilot"], False),          # каталог не покрывает
        ("pilot/briefs/b.yaml", ["pilot/briefs"], False),
        ("pilot/briefs/bb.yaml", ["pilot/briefs/b.yaml"], False),  # не префикс строки
        ("epics.toml", ["epics.toml"], True),
    ],
)
def test_declared_match_is_exact(rel: str, decls: list[str], expected: bool) -> None:
    assert _is_declared_data_file(Path(rel), decls) is expected


# --- протухшее объявление — находка, а не тихий ноль -------------------------


def test_missing_declared_file_is_policy_error(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"pilot/briefs/brf-1.yaml": "a: 1\n"})
    with pytest.raises(PolicyError):
        verify_declarations_exist(repo, ["pilot/briefs/УДАЛЁН.yaml"])


def test_declared_path_that_is_a_directory_is_policy_error(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"pilot/briefs/brf-1.yaml": "a: 1\n"})
    (repo / "pilot/dir.yaml").mkdir(parents=True)
    with pytest.raises(PolicyError):
        verify_declarations_exist(repo, ["pilot/dir.yaml"])


def test_declared_symlink_is_policy_error(tmp_path: Path) -> None:
    """Цель symlink'а может лежать вне объявленного набора."""
    repo = _repo(tmp_path, {"pilot/briefs/brf-1.yaml": "a: 1\n", "src/app.yaml": "a: 1\n"})
    (repo / "pilot/link.yaml").symlink_to(repo / "src/app.yaml")
    with pytest.raises(PolicyError):
        verify_declarations_exist(repo, ["pilot/link.yaml"])


def test_symlinked_parent_segment_is_policy_error(tmp_path: Path) -> None:
    """Третий заход приёмочного ревью (major): проверять только КОНЕЧНЫЙ
    компонент мало. `pilot/briefs -> ../tests/evidence` — сам файл не symlink,
    `is_file()` проходит СКВОЗЬ родительский symlink и возвращает True, а
    физически он лежит там, куда объявление не выдавалось (и куда `os.walk`
    даже не спускается: без `followlinks` и с `tests` в SKIP_DIRS)."""
    repo = _repo(tmp_path, {"tests/evidence/brf-1.yaml": "a: 1\n"})
    (repo / "pilot").mkdir(parents=True, exist_ok=True)
    (repo / "pilot/briefs").symlink_to(repo / "tests/evidence", target_is_directory=True)
    assert (repo / "pilot/briefs/brf-1.yaml").is_file(), "предпосылка сценария ревью"
    assert not (repo / "pilot/briefs/brf-1.yaml").is_symlink(), "конечный компонент чист"
    with pytest.raises(PolicyError):
        verify_declarations_exist(repo, ["pilot/briefs/brf-1.yaml"])


def test_existing_declarations_pass_verification(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"pilot/briefs/brf-1.yaml": "a: 1\n"})
    verify_declarations_exist(repo, ["pilot/briefs/brf-1.yaml"])


# --- политика: fail-closed на каждом пути ------------------------------------


def test_policy_reads_declared_dirs(tmp_path: Path) -> None:
    pol = _policy(tmp_path, '[data-artifacts]\nimpresario = ["pilot/b.yaml"]\n')
    assert load_data_artifact_files(pol, "impresario") == ["pilot/b.yaml"]


def test_policy_without_entry_for_repo_declares_nothing(tmp_path: Path) -> None:
    pol = _policy(tmp_path, '[data-artifacts]\nimpresario = ["pilot/b.yaml"]\n')
    assert load_data_artifact_files(pol, "maestro") == []


def test_policy_without_table_declares_nothing(tmp_path: Path) -> None:
    pol = _policy(tmp_path, '[pin]\nref = "x"\n')
    assert load_data_artifact_files(pol, "impresario") == []


@pytest.mark.parametrize(
    "body",
    [
        '[data-artifacts]\nimpresario = ["."]\n',       # весь репо
        '[data-artifacts]\nimpresario = ["pilot/.."]\n',  # побег вверх
        '[data-artifacts]\nimpresario = ["../maestro"]\n',
        '[data-artifacts]\nimpresario = ["/etc"]\n',    # абсолютный
        '[data-artifacts]\nimpresario = [""]\n',
        '[data-artifacts]\nimpresario = ["   "]\n',
        '[data-artifacts]\nimpresario = [42]\n',        # не строка
        '[data-artifacts]\nimpresario = "pilot"\n',     # не список
        '[data-artifacts]\nimpresario = ["a//b"]\n',    # пустой сегмент
        '[data-artifacts]\nimpresario = ["pilot/app.py"]\n',   # код объявить нельзя
        '[data-artifacts]\nimpresario = ["pilot/run.sh"]\n',
        '[data-artifacts]\nimpresario = ["pilot"]\n',          # каталог (нет расширения)
    ],
)
def test_bad_declaration_is_policy_error(tmp_path: Path, body: str) -> None:
    """Негодное объявление — отказ, а не «объявлений нет». Особенно `"."`:
    оно выключило бы GOV-003 целиком и молча, ради чего эта ступень и
    отделена от входа `runtime-scan`."""
    with pytest.raises(PolicyError):
        load_data_artifact_files(_policy(tmp_path, body), "impresario")


def test_unreadable_policy_is_policy_error(tmp_path: Path) -> None:
    with pytest.raises(PolicyError):
        load_data_artifact_files(tmp_path / "нет-такого.toml", "impresario")


def test_malformed_toml_is_policy_error(tmp_path: Path) -> None:
    with pytest.raises(PolicyError):
        load_data_artifact_files(_policy(tmp_path, "[data-artifacts\n"), "impresario")


# --- живая политика флота ----------------------------------------------------


def test_real_policy_declares_impresario_pilot() -> None:
    """Объявление в реальном caller-policy.toml читается и не разъехалось."""
    pol = Path(__file__).resolve().parents[1] / "governance" / "caller-policy.toml"
    declared = load_data_artifact_files(pol, "impresario")
    assert len(declared) == 8, "восемь брифов impresario"
    assert all(d.startswith("pilot/briefs/brf-") and d.endswith(".yaml") for d in declared)
    assert load_data_artifact_files(pol, "steward") == []
