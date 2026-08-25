# Governance gates — batch 1 (ADR-ECO-004)

Working skeleton of the **vendored governance enforcer** (ADR-ECO-004 D5). One canonical
set of checks lives here; each subproject opts in via a thin pinned caller. The umbrella is
the meta-enforcer.

Canon: `../../../prograph-vault/authored/decisions/2026-07-18-adr-eco-004-governance-plane.md`
and the registry `prograph-vault/authored/registry/governance.yaml`.

## What runs where

| Gate | Rule | Where it runs | Script | Maturity |
|---|---|---|---|---|
| name/alias resolve | GOV-004 / GAP-7 | **umbrella only** (`manifest-drift.yml`) | `../check_manifest_resolve.py` | ci-blocking |
| no `_cowork_output` in runtime | GOV-003 | **each repo** (reusable gate) | `no_cowork_in_runtime.py` | ci-blocking |
| authority-root guard | GOV-009 / I2 | repos with authority-root paths | `authority_root_guard.py` | advisory→ruleset |

Not in this batch (ADR sequencing): GAP-5 tool-pins, GAP-6 cowork-dup, `human_merge` /
`agent_merge` evidence, WS-006 gates-in-DAG.

## The gates

- **`check_manifest_resolve.py` (GAP-7).** Catches "green PR ≠ working layout": every
  `workspace-manifest.toml` entry's `git_dir` must equal the repo-URL basename (the
  maestro/Maestro class) unless `member` / `dir_alias`; `pyproject_path` under `git_dir`;
  no duplicate `git_dir`; and — when siblings are on disk — the on-disk `origin` matches
  `repo_url`. Manifest-only in CI (degrades to info for absent repos), full disk check in a
  workspace run. Same exit/severity contract as `check-release-drift.py`.
- **`no_cowork_in_runtime.py` (GOV-003).** Hard invariant on *path resolution*: shipped/runtime
  code never *resolves* `_cowork_output` paths. A real resolve is always blocking; only
  **documented mentions** are exempt (they aren't resolves), and each exemption is explicit and
  greppable, not a silent waiver. Scans code extensions only; ignores **tests** (they create the
  dir to test its exclusion), single-line **comments** (per-language `#`/`//`, with `://` URLs
  not mistaken for comments), and any line carrying the inline `gov:allow-cowork` marker.
  Whole meta-tooling files opt out with `gov:allow-cowork-file` in the header; docstring /
  block-comment mentions (beyond the single-line heuristic) use the inline marker. File-level
  opt-outs are printed by name; comment/test/inline-marked mentions are reported as a count.
  **Not for KB/docs repos** (e.g. prograph-vault): run them with `runtime-scan: false`.
- **`authority_root_guard.py` (GOV-009 / I2).** Flags PRs touching authority-defining paths
  (`governance.yaml`, rulesets, required-check defs, `ci/governance/**`, `CODEOWNERS`) — they
  must land via `human_merge`, never `agent_merge`. Advisory by default; `--strict` blocks.
  Real blocking comes from the GitHub ruleset / CODEOWNERS on those paths.
  **Agent-merge paths were added 2026-08-20** (ADR-ECO-008a, first landed in steward):
  `.github/workflows/merge-broker.yml`, `.github/workflows/codex-review.yml`,
  `.github/codex/**`, `profiles/approval-policy.yaml`. They define *what the agent may merge
  and under which conditions* — the same class as everything above. The list is fleet-wide
  rather than per-repo because the reusable gate takes no globs input and the paths are
  identical everywhere; a repo without a broker simply has no such files. Note the guard is
  the *second* line: the broker also refuses these paths itself, but that check lives inside
  a file which is itself authority-root, so it cannot be its own witness.

## Merge broker (ADR-ECO-008a)

`.github/workflows/merge-broker.yml` в ЭТОМ репо — канонический исполнитель агентского
мержа, вызываемый тонким каллером (`vendor/merge-broker.yml`). Восемь предусловий, каждое
доказывается положительно: PR открыт и не черновик; `mergeable == MERGEABLE` (с опросом до
определённости — `UNKNOWN` после сдвига base это неизвестность, а не разрешение); rollup
чеков `SUCCESS` (пустой rollup = «чеков нет», не «прошли»); ноль неразрешённых review
threads; App не аппрувил этот PR (I3); PR не трогает authority-root (I2); файлы, threads и
reviews непагинированы — иначе полноту трёх проверок выше не доказать. Мерж — прямым
`PUT /pulls/{n}/merge` с `sha`: `gh pr merge` читает `mergeStateStatus` на своей стороне и
при `BLOCKED` отказывает, не дойдя до эндпоинта, где проверяется bypass App.

Почему исполнитель здесь, а не копией в каждом репо: за первый день боя нашлось три
дефекта, и двадцать копий превратили бы каждую находку в двадцать PR. Плюс **I2 начинает
выполняться конструкцией**: пока предусловия лежали в репозитории, который брокер же и
мержит, проверка не могла быть себе свидетелем.

Предпосылки в репозитории (действия владельца, не кода): App установлен; `MERGE_BROKER_APP_KEY`
+ `MERGE_BROKER_APP_ID` заведены; App внесён в bypass ruleset'а ветки.

## Rollout (per subproject) — one PR each

1. `cp ci/governance/vendor/governance.yml <repo>/.github/workflows/governance.yml`
2. Replace both `<PINNED-SHA>` with the canonical pin from `caller-policy.toml`
   (`pin.ref`; policy: a 40-hex **commit SHA** of this repo — never a branch, and,
   once `require_sha = true`, not a tag either).
3. Inputs `strict` / `runtime-scan` / `authority-guard`: policy defaults from
   `caller-policy.toml`, deviations only via a documented per-repo override there
   (now: `prograph-vault`, `robin-toolkit`). An undocumented deviation is a
   meta-enforcer finding.
4. Add `governance / gate` as a **required check** in the repo's branch ruleset (ADR-ECO-004
   D4 — rulesets, not required-owner-review).

## Meta-enforcer (batch 2 — реализован 2026-08-25)

`check_caller_pins.py` + политика `caller-policy.toml` + workflow `caller-pins.yml`
(вт cron + dispatch + PR по своим путям). Для каждого члена `workspace-manifest.toml`
(кроме `fleet.exempt`) проверяет: каллер существует; `uses` == `umbrella-ref` ==
канонический `pin.ref`; при `require_sha` ref — 40-hex commit SHA; inputs равны
дефолтам политики с учётом документированных overrides. Отсутствующий или
дрейфанувший каллер — находка, exit 1. Локальный прогон: `--fleet-dir ..`.

Переходное состояние: `pin.ref = governance-v2` — тег, защищённый tag-ruleset'ом
`governance-rule`; у ruleset'а есть **bypass actors** (RepositoryRole, DeployKey,
владелец — always), поэтому целевое состояние — SHA. Порядок (решение владельца
2026-08-25): после мержа этого контроля маленький PR ставит `pin.ref = <merge-SHA>`
и `require_sha = true`, затем 22 caller-PR переводят `uses`/`umbrella-ref` на тот же
SHA, не трогая inputs.

## Local run

```sh
python ci/check_manifest_resolve.py --workspace .. --strict          # full disk check
python ci/governance/no_cowork_in_runtime.py --repo ../<some-repo>
python ci/governance/authority_root_guard.py --repo . --base origin/main --strict
```
