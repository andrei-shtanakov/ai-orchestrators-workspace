#!/usr/bin/env python3
"""check_caller_pins.py — meta-enforcer пинов governance-каллеров (ADR-ECO-004, batch 2).

Для каждого члена флота из workspace-manifest.toml проверяет его
`.github/workflows/governance.yml`:

- каллер существует (отсутствие = drift-находка, если репо не в `fleet.exempt`);
- `uses: ...governance-gate.yml@<ref>` и `umbrella-ref: "<ref>"` совпадают между
  собой и с каноническим `pin.ref` из caller-policy.toml;
- при `pin.require_sha` ref обязан быть 40-hex commit SHA (ветка/тег = находка);
- inputs (strict / runtime-scan / authority-guard) заданы явно и равны дефолтам
  политики с учётом документированных per-repo overrides.

Каллеры генерируются из единого шаблона (vendor/governance.yml), поэтому парсинг
построчный (stdlib-only, без PyYAML) — осознанно: каллер нестандартной структуры
сам по себе находка, а не повод для умного парсера.

Источники каллеров: `--fleet-dir <path>` (локальные чекауты-соседи по git_dir)
или `--github` (raw.githubusercontent.com; токен из $GITHUB_TOKEN опционален).

Exit: 0 — чисто; 1 — есть находки; 2 — ошибка среды/сети.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

OWNER = "andrei-shtanakov"
CALLER_PATH = ".github/workflows/governance.yml"
INPUT_KEYS = ("strict", "runtime-scan", "authority-guard")

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
USES_RE = re.compile(
    r"^\s*uses:\s*"
    rf"{OWNER}/ai-orchestrators-workspace/\.github/workflows/governance-gate\.yml"
    r"@(\S+)\s*$",
    re.MULTILINE,
)
UMBRELLA_RE = re.compile(r'^\s*umbrella-ref:\s*"?([^"\s#]+)"?', re.MULTILINE)
INPUT_RE = re.compile(
    r"^\s+(strict|runtime-scan|authority-guard):\s*(true|false)\b", re.MULTILINE
)


@dataclass(frozen=True)
class Policy:
    """Разобранная caller-policy.toml."""

    ref: str
    require_sha: bool
    defaults: dict[str, bool]
    overrides: dict[str, dict[str, bool]]
    exempt: frozenset[str]

    def expected_inputs(self, repo: str) -> dict[str, bool]:
        """Ожидаемые inputs репо: дефолты, перекрытые документированным override."""
        return {**self.defaults, **self.overrides.get(repo, {})}


@dataclass(frozen=True)
class FleetRepo:
    """Член флота: имя GitHub-репо и каталог локального чекаута."""

    name: str
    git_dir: str


@dataclass(frozen=True)
class Caller:
    """Извлечённые из каллера пины и inputs."""

    uses_ref: str | None
    umbrella_ref: str | None
    inputs: dict[str, bool]


def load_policy(path: Path) -> Policy:
    """Читает и валидирует caller-policy.toml."""
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    pin = raw["pin"]
    inputs = raw.get("inputs", {})
    return Policy(
        ref=pin["ref"],
        require_sha=bool(pin.get("require_sha", False)),
        defaults={k: bool(v) for k, v in inputs.get("defaults", {}).items()},
        overrides={
            repo: {k: bool(v) for k, v in ov.items()}
            for repo, ov in inputs.get("overrides", {}).items()
        },
        exempt=frozenset(raw.get("fleet", {}).get("exempt", [])),
    )


def fleet_from_manifest(path: Path) -> list[FleetRepo]:
    """Собирает членов флота из workspace-manifest.toml.

    Берётся каждая таблица с `repo_url`, кроме `member = true` (workspace-member
    живёт внутри чужого git_dir и отдельного каллера не имеет). Дубли по имени
    репо схлопываются.
    """
    manifest = tomllib.loads(path.read_text(encoding="utf-8"))
    seen: dict[str, FleetRepo] = {}

    def walk(node: object) -> None:
        if not isinstance(node, dict):
            return
        repo_url = node.get("repo_url")
        if isinstance(repo_url, str):
            if node.get("member") is True:
                return
            name = repo_url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
            git_dir = node.get("git_dir")
            seen.setdefault(
                name, FleetRepo(name=name, git_dir=git_dir if git_dir else name)
            )
            return
        for value in node.values():
            walk(value)

    walk(manifest)
    return sorted(seen.values(), key=lambda r: r.name)


def parse_caller(text: str) -> Caller:
    """Извлекает оба пина и явные inputs из текста каллера."""
    uses = USES_RE.search(text)
    umbrella = UMBRELLA_RE.search(text)
    inputs = {key: value == "true" for key, value in INPUT_RE.findall(text)}
    return Caller(
        uses_ref=uses.group(1) if uses else None,
        umbrella_ref=umbrella.group(1) if umbrella else None,
        inputs=inputs,
    )


def check_caller(repo: str, text: str, policy: Policy) -> list[str]:
    """Проверяет один каллер против политики; возвращает список находок."""
    caller = parse_caller(text)
    findings: list[str] = []

    if caller.uses_ref is None:
        findings.append("нет строки `uses: ...governance-gate.yml@<ref>`")
    elif caller.uses_ref != policy.ref:
        findings.append(
            f"uses пинует `@{caller.uses_ref}`, канон — `@{policy.ref}`"
        )
    if caller.umbrella_ref is None:
        findings.append("нет input'а `umbrella-ref`")
    elif caller.umbrella_ref != policy.ref:
        findings.append(
            f"umbrella-ref = `{caller.umbrella_ref}`, канон — `{policy.ref}`"
        )
    if (
        caller.uses_ref is not None
        and caller.umbrella_ref is not None
        and caller.uses_ref != caller.umbrella_ref
    ):
        findings.append(
            f"uses (`@{caller.uses_ref}`) и umbrella-ref "
            f"(`{caller.umbrella_ref}`) расходятся"
        )
    if policy.require_sha:
        for label, ref in (
            ("uses", caller.uses_ref),
            ("umbrella-ref", caller.umbrella_ref),
        ):
            if ref is not None and not SHA_RE.match(ref):
                findings.append(
                    f"{label} = `{ref}` — политика требует 40-hex commit SHA"
                )

    expected = policy.expected_inputs(repo)
    for key in INPUT_KEYS:
        if key not in caller.inputs:
            findings.append(f"input `{key}` не задан явно (контракт шаблона)")
        elif caller.inputs[key] != expected.get(key):
            findings.append(
                f"input `{key}` = {str(caller.inputs[key]).lower()}, "
                f"ожидается {str(expected.get(key)).lower()} "
                "(недокументированное отклонение — см. caller-policy.toml)"
            )
    return findings


def read_local(fleet_dir: Path, repo: FleetRepo) -> str | None:
    """Читает каллер из локального чекаута; None — файла нет."""
    path = fleet_dir / repo.git_dir / CALLER_PATH
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def read_github(repo: FleetRepo, token: str | None) -> str | None:
    """Читает каллер с raw.githubusercontent.com; None — 404 (каллера нет)."""
    url = f"https://raw.githubusercontent.com/{OWNER}/{repo.name}/HEAD/{CALLER_PATH}"
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def main() -> int:
    """CLI: сверяет каллеры флота с политикой пинов."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--fleet-dir", type=Path, help="каталог с локальными чекаутами флота"
    )
    source.add_argument(
        "--github", action="store_true", help="читать каллеры через raw.github"
    )
    here = Path(__file__).resolve().parent
    parser.add_argument(
        "--policy", type=Path, default=here / "caller-policy.toml"
    )
    parser.add_argument(
        "--manifest", type=Path, default=here.parent.parent / "workspace-manifest.toml"
    )
    args = parser.parse_args()

    policy = load_policy(args.policy)
    fleet = fleet_from_manifest(args.manifest)
    token = os.environ.get("GITHUB_TOKEN")

    total_findings = 0
    for repo in fleet:
        if repo.name in policy.exempt:
            print(f"  skip  {repo.name}: exempt по политике")
            continue
        try:
            text = (
                read_local(args.fleet_dir, repo)
                if args.fleet_dir
                else read_github(repo, token)
            )
        except (OSError, urllib.error.URLError) as error:
            print(f"ERROR {repo.name}: {error}", file=sys.stderr)
            return 2
        if text is None:
            total_findings += 1
            print(f"  DRIFT {repo.name}: каллер {CALLER_PATH} отсутствует")
            continue
        findings = check_caller(repo.name, text, policy)
        if findings:
            total_findings += len(findings)
            for finding in findings:
                print(f"  DRIFT {repo.name}: {finding}")
        else:
            print(f"  ok    {repo.name}")

    print(
        f"caller-pins: {len(fleet)} репо, канон `@{policy.ref}`"
        f" (require_sha={str(policy.require_sha).lower()}),"
        f" находок: {total_findings}"
    )
    return 1 if total_findings else 0


if __name__ == "__main__":
    sys.exit(main())
