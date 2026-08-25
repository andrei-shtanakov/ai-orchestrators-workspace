"""Тесты meta-enforcer'а пинов governance-каллеров (check_caller_pins.py).

Сторож без исполнителя маскирует дыру (см. шапку tests.yml): здесь проверяются
и парсер, и правила, и то, что fleet_from_manifest видит реальный манифест.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from check_caller_pins import (
    Policy,
    check_caller,
    fleet_from_manifest,
    load_policy,
    parse_caller,
)

WORKSPACE = Path(__file__).resolve().parents[2]
SHA = "a" * 40


def caller_text(ref: str, *, strict: str = "true", runtime: str = "true",
                guard: str = "false", umbrella: str | None = None) -> str:
    """Каллер по канонической форме шаблона vendor/governance.yml."""
    return f"""name: governance
permissions:
  contents: read
on:
  pull_request:
  workflow_dispatch:

jobs:
  governance:
    uses: andrei-shtanakov/ai-orchestrators-workspace/.github/workflows/governance-gate.yml@{ref}
    with:
      umbrella-ref: "{umbrella if umbrella is not None else ref}"
      strict: {strict}
      runtime-scan: {runtime}        # runtime code — GOV-003 applies
      authority-guard: {guard}
"""


def tag_policy(**overrides: dict[str, bool]) -> Policy:
    return Policy(
        ref="governance-v2",
        require_sha=False,
        defaults={"strict": True, "runtime-scan": True, "authority-guard": False},
        overrides=overrides,
        exempt=frozenset(),
    )


def test_parse_caller_extracts_refs_and_inputs() -> None:
    caller = parse_caller(caller_text("governance-v2"))
    assert caller.uses_ref == "governance-v2"
    assert caller.umbrella_ref == "governance-v2"
    assert caller.inputs == {
        "strict": True, "runtime-scan": True, "authority-guard": False,
    }


def test_canonical_caller_is_clean() -> None:
    assert check_caller("maestro", caller_text("governance-v2"), tag_policy()) == []


def test_documented_overrides_are_clean() -> None:
    policy = tag_policy(**{
        "prograph-vault": {
            "strict": False, "runtime-scan": False, "authority-guard": True,
        },
        "robin-toolkit": {"runtime-scan": False},
    })
    vault = caller_text(
        "governance-v2", strict="false", runtime="false", guard="true"
    )
    toolkit = caller_text("governance-v2", runtime="false")
    assert check_caller("prograph-vault", vault, policy) == []
    assert check_caller("robin-toolkit", toolkit, policy) == []


def test_undocumented_input_deviation_is_a_finding() -> None:
    findings = check_caller(
        "maestro", caller_text("governance-v2", runtime="false"), tag_policy()
    )
    assert any("runtime-scan" in f for f in findings)


def test_ref_mismatch_and_split_pins_are_findings() -> None:
    wrong = check_caller("maestro", caller_text("governance-v1"), tag_policy())
    assert any("канон" in f for f in wrong)
    split = check_caller(
        "maestro",
        caller_text("governance-v2", umbrella="governance-v1"),
        tag_policy(),
    )
    assert any("расходятся" in f for f in split)


def test_require_sha_rejects_tags_and_accepts_sha() -> None:
    policy = Policy(
        ref=SHA, require_sha=True,
        defaults={"strict": True, "runtime-scan": True, "authority-guard": False},
        overrides={}, exempt=frozenset(),
    )
    assert check_caller("maestro", caller_text(SHA), policy) == []
    findings = check_caller("maestro", caller_text("governance-v2"), policy)
    assert any("40-hex" in f for f in findings)


def test_missing_explicit_input_is_a_finding() -> None:
    text = caller_text("governance-v2").replace("      strict: true\n", "")
    findings = check_caller("maestro", text, tag_policy())
    assert any("не задан явно" in f for f in findings)


def test_fleet_from_real_manifest() -> None:
    fleet = fleet_from_manifest(WORKSPACE / "workspace-manifest.toml")
    names = {repo.name for repo in fleet}
    assert len(fleet) == len(names), "дубли по имени репо должны схлопываться"
    assert {"maestro", "spec-runner", "prograph-vault", "kapelle"} <= names
    assert "atp-platform-sdk" not in names, "member=true не имеет своего каллера"
    assert len(names) == 22


POLICY_TEMPLATE = """
[pin]
ref = "{ref}"
require_sha = {require_sha}

[inputs.defaults]
{defaults}

[inputs.overrides.robin-toolkit]
{override}

[fleet]
exempt = []
"""

FULL_DEFAULTS = 'strict = true\nruntime-scan = true\nauthority-guard = false'


def write_policy(tmp_path: Path, **kwargs: str) -> Path:
    values = {
        "ref": "governance-v2", "require_sha": "false",
        "defaults": FULL_DEFAULTS, "override": 'runtime-scan = false',
    } | kwargs
    path = tmp_path / "caller-policy.toml"
    path.write_text(POLICY_TEMPLATE.format(**values), encoding="utf-8")
    return path


def test_policy_missing_default_key_fails_fast(tmp_path: Path) -> None:
    path = write_policy(tmp_path, defaults="strict = true")
    with pytest.raises(ValueError, match="inputs.defaults"):
        load_policy(path)


def test_policy_unknown_override_key_fails_fast(tmp_path: Path) -> None:
    path = write_policy(tmp_path, override="runtime_scan = false")
    with pytest.raises(ValueError, match="неизвестные ключи"):
        load_policy(path)


def test_policy_require_sha_demands_sha_ref(tmp_path: Path) -> None:
    path = write_policy(tmp_path, require_sha="true")
    with pytest.raises(ValueError, match="40-hex"):
        load_policy(path)
    assert load_policy(write_policy(tmp_path, require_sha="true", ref=SHA)).require_sha


def test_load_real_policy_matches_contract() -> None:
    policy = load_policy(WORKSPACE / "ci" / "governance" / "caller-policy.toml")
    assert policy.defaults == {
        "strict": True, "runtime-scan": True, "authority-guard": False,
    }
    assert set(policy.overrides) == {"prograph-vault", "robin-toolkit"}
    assert policy.expected_inputs("prograph-vault")["authority-guard"] is True
