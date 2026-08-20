"""GOV-009 / I2 authority-root guard — какие пути считаются authority-root.

Проверяется именно СПИСОК, а не механика сопоставления: механика тривиальна,
а список — это и есть содержание инварианта I2. Пропущенный в нём путь не
проявится как поломка: сторож отработает, ничего не найдёт и покажет зелёное.
"""

from __future__ import annotations

from authority_root_guard import DEFAULT_GLOBS, matches

# Пути агентского мержа (ADR-ECO-008a). Каждый определяет, ЧТО агенту позволено
# мержить и при каких условиях, поэтому агент не вправе их менять.
AGENT_MERGE_PATHS = [
    ".github/workflows/merge-broker.yml",
    ".github/workflows/codex-review.yml",
    ".github/codex/review-prompt.md",
    ".github/codex/review-schema.json",
    "profiles/approval-policy.yaml",
]

CLASSIC_PATHS = [
    "authored/registry/governance.yaml",
    ".github/workflows/governance-gate.yml",
    "ci/governance/authority_root_guard.py",
    "CODEOWNERS",
    ".github/CODEOWNERS",
]

# Обычная работа: сторож не должен цеплять её, иначе его перестанут читать.
ORDINARY_PATHS = [
    "src/steward/loader.py",
    ".github/workflows/ci.yml",
    "profiles/team.yaml",
    "docs/codex/notes.md",
    "tests/test_approval.py",
]


def test_agent_merge_paths_are_authority_root() -> None:
    for path in AGENT_MERGE_PATHS:
        assert matches(path, DEFAULT_GLOBS), path


def test_classic_authority_root_still_matches() -> None:
    for path in CLASSIC_PATHS:
        assert matches(path, DEFAULT_GLOBS), path


def test_ordinary_paths_are_not_flagged() -> None:
    for path in ORDINARY_PATHS:
        assert not matches(path, DEFAULT_GLOBS), path


def test_codex_dir_is_matched_by_prefix_not_by_name() -> None:
    """`.github/codex/**` покрывает каталог целиком, включая файлы, которых
    ещё нет: промпт и схема — вход ревьюера, и любой новый файл рядом с ними
    тоже определяет поведение гейта."""
    assert matches(".github/codex/whatever-lands-here.yaml", DEFAULT_GLOBS)
    # Но не любой каталог с таким именем в произвольном месте дерева.
    assert not matches("docs/codex/notes.md", DEFAULT_GLOBS)
