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
    _under_data_artifact,
    load_data_artifact_dirs,
    scan,
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
    """Объявлен `pilot` — `src/` обязан проверяться как прежде."""
    repo = _repo(tmp_path, {"pilot/b.yaml": NEEDLE_LINE, "src/app.py": NEEDLE_LINE})
    hits, _, _ = scan(repo, ["pilot"])
    assert [str(h[0]) for h in hits] == ["src/app.py"]


# --- сама ступень ------------------------------------------------------------


def test_declared_dir_is_treated_as_prose(tmp_path: Path) -> None:
    """Живой случай impresario: текст стандартов, записанный в evidence."""
    brief = (
        "brief_id: BRF-1\n"
        "standards: \"...STD-5. Канон-first / SSOT\\n\\n`_cowork_output/` — dev-only,\"\n"
    )
    repo = _repo(tmp_path, {"pilot/briefs/brf-1.yaml": brief})
    assert scan(repo)[0], "без объявления это находка — иначе тест ничего не доказывает"
    assert scan(repo, ["pilot"])[0] == []


def test_declared_single_file_is_treated_as_prose(tmp_path: Path) -> None:
    """Объявить можно и одиночный файл — в зонтике это `epics.toml`."""
    repo = _repo(tmp_path, {"epics.toml": 'notes = "Основание: _cowork_output/adr.md"\n'})
    assert scan(repo)[0]
    assert scan(repo, ["epics.toml"])[0] == []


@pytest.mark.parametrize("code_file", ["pilot/runtime.py", "pilot/deep/tool.sh"])
def test_code_inside_declared_dir_is_still_scanned(tmp_path: Path, code_file: str) -> None:
    """Находка приёмочного ревью (major): объявление КАТАЛОГА не смеет
    превращаться в дыру. Репо, положив исполняемый файл внутрь уже
    разрешённого каталога, расширило бы себе исключение на настоящий резолв —
    ровно то, чего ступень обязана не допускать. Объявление снимает вопрос
    «проза внутри данных», а не «инвариант по коду»."""
    repo = _repo(tmp_path, {code_file: NEEDLE_LINE, "pilot/b.yaml": NEEDLE_LINE})
    hits = scan(repo, ["pilot"])[0]
    assert [str(h[0]) for h in hits] == [code_file], "код внутри data-каталога обязан ловиться"


def test_declaration_exempts_only_serialization_formats(tmp_path: Path) -> None:
    """Позитивная половина той же пары: данные внутри объявленного пути
    исключены, код — нет, и оба факта проверяются одним прогоном."""
    repo = _repo(
        tmp_path,
        {"pilot/b.yaml": NEEDLE_LINE, "pilot/c.json": NEEDLE_LINE,
         "pilot/d.toml": NEEDLE_LINE, "pilot/app.py": NEEDLE_LINE},
    )
    assert [str(h[0]) for h in scan(repo, ["pilot"])[0]] == ["pilot/app.py"]


def test_declaration_matches_by_segments_not_string_prefix(tmp_path: Path) -> None:
    """`pilot` не смеет накрывать `pilotage/` — иначе объявление тихо
    расширяется на соседа с общим началом имени."""
    repo = _repo(tmp_path, {"pilotage/runtime.py": NEEDLE_LINE})
    assert [str(h[0]) for h in scan(repo, ["pilot"])[0]] == ["pilotage/runtime.py"]


@pytest.mark.parametrize(
    "rel,decls,expected",
    [
        ("pilot/briefs/b.yaml", ["pilot"], True),
        ("pilot", ["pilot"], True),
        ("pilotage/x.py", ["pilot"], False),
        ("src/pilot/x.py", ["pilot"], False),  # объявление якорится в КОРНЕ репо
        ("a/b/c.yaml", ["a/b"], True),
        ("a/bb/c.yaml", ["a/b"], False),
    ],
)
def test_under_data_artifact_matrix(rel: str, decls: list[str], expected: bool) -> None:
    assert _under_data_artifact(Path(rel), decls) is expected


# --- политика: fail-closed на каждом пути ------------------------------------


def test_policy_reads_declared_dirs(tmp_path: Path) -> None:
    pol = _policy(tmp_path, '[data-artifacts]\nimpresario = ["pilot"]\n')
    assert load_data_artifact_dirs(pol, "impresario") == ["pilot"]


def test_policy_without_entry_for_repo_declares_nothing(tmp_path: Path) -> None:
    pol = _policy(tmp_path, '[data-artifacts]\nimpresario = ["pilot"]\n')
    assert load_data_artifact_dirs(pol, "maestro") == []


def test_policy_without_table_declares_nothing(tmp_path: Path) -> None:
    pol = _policy(tmp_path, '[pin]\nref = "x"\n')
    assert load_data_artifact_dirs(pol, "impresario") == []


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
    ],
)
def test_bad_declaration_is_policy_error(tmp_path: Path, body: str) -> None:
    """Негодное объявление — отказ, а не «объявлений нет». Особенно `"."`:
    оно выключило бы GOV-003 целиком и молча, ради чего эта ступень и
    отделена от входа `runtime-scan`."""
    with pytest.raises(PolicyError):
        load_data_artifact_dirs(_policy(tmp_path, body), "impresario")


def test_unreadable_policy_is_policy_error(tmp_path: Path) -> None:
    with pytest.raises(PolicyError):
        load_data_artifact_dirs(tmp_path / "нет-такого.toml", "impresario")


def test_malformed_toml_is_policy_error(tmp_path: Path) -> None:
    with pytest.raises(PolicyError):
        load_data_artifact_dirs(_policy(tmp_path, "[data-artifacts\n"), "impresario")


# --- живая политика флота ----------------------------------------------------


def test_real_policy_declares_impresario_pilot() -> None:
    """Объявление в реальном caller-policy.toml читается и не разъехалось."""
    pol = Path(__file__).resolve().parents[1] / "governance" / "caller-policy.toml"
    assert load_data_artifact_dirs(pol, "impresario") == ["pilot"]
    assert load_data_artifact_dirs(pol, "steward") == []
