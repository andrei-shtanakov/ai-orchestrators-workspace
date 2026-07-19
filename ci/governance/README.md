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
- **`no_cowork_in_runtime.py` (GOV-003).** Hard invariant, no waiver: shipped/runtime code
  never *resolves* `_cowork_output` paths. Scans code extensions only; ignores **tests**
  (they create the dir to test its exclusion), single-line **comments** (per-language `#`/`//`
  marker), and any line carrying the inline `gov:allow-cowork` marker. Whole meta-tooling files
  opt out with `gov:allow-cowork-file` in the header. Docstring / block-comment mentions (not
  handled by the single-line heuristic) use the inline marker. Every opt-out is printed — no
  silent bypass. **Not for KB/docs repos** (e.g. prograph-vault): run them with
  `runtime-scan: false`.
- **`authority_root_guard.py` (GOV-009 / I2).** Flags PRs touching authority-defining paths
  (`governance.yaml`, rulesets, required-check defs, `ci/governance/**`, `CODEOWNERS`) — they
  must land via `human_merge`, never `agent_merge`. Advisory by default; `--strict` blocks.
  Real blocking comes from the GitHub ruleset / CODEOWNERS on those paths.

## Rollout (per subproject) — follow-up, one PR each

1. `cp ci/governance/vendor/governance.yml <repo>/.github/workflows/governance.yml`
2. Replace both `<PIN>` with a **SHA or tag** of this repo (never a branch).
3. Set `authority-guard: true` only in `ai-orchestrators-workspace` and `prograph-vault`.
4. Add `governance / gate` as a **required check** in the repo's branch ruleset (ADR-ECO-004
   D4 — rulesets, not required-owner-review).

## Meta-enforcer (planned, batch 2)

`check-release-drift.py` (or a sibling) verifies each repo's vendored caller references the
canonical pin — so a stale or missing gate is itself a drift finding. Not wired yet.

## Local run

```sh
python ci/check_manifest_resolve.py --workspace .. --strict          # full disk check
python ci/governance/no_cowork_in_runtime.py --repo ../<some-repo>
python ci/governance/authority_root_guard.py --repo . --base origin/main --strict
```
