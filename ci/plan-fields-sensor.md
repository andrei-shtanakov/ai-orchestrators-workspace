# plan-fields fleet sensor — Phase 0a

Umbrella-owned nightly sensor for the plan-fields contract (ADR-ECO-005, decided in
`ecosystem-kb`/prograph-vault `authored/decisions/`). It answers the drift class where a
single repo is locally green while the fleet view disagrees.

## What it does

1. Reads `workspace-manifest.toml` (SSOT of the repo set + pins).
2. **Freezes** each repo's HEAD SHA before analysis, recording the manifest pin and the
   resolved SHA (and flagging pin↔checkout drift).
3. Resolves the plan-fields graph over the frozen roots with the shared **`plan-fields`**
   package (`parse_fleet`/`check_fleet` canonical + `check_legacy_fleet` transitional).
4. Emits `plan-fields-snapshot.json` (machine-readable, with per-repo provenance,
   structured canonical + legacy diagnostics, and the resolved `@id` edge count) and
   `plan-fields-report.md` (human).

**Frozen set == analysed set.** The sensor analyses **exactly** the manifest fleet it
freezes — one `RepoInput` per manifest checkout, handed to the package (which does no
discovery of its own). A repo on disk but **not in the manifest** (e.g.
`spec-runner-vscode`) is out of scope by design. This differs from the previous sensor,
which shelled the old checker with `--root <workspace>` and let it `iterdir`-discover
*all* on-disk repos — analysing a superset of what it froze. It also differs from the
local `devtools` `make plan-check`, which is intentionally all-on-disk for developer
convenience.

## Phase 0a scope (hard boundary)

**Warnings-only.** No GitHub issue mutation, no `first_seen` persistence, no error
escalation on a second snapshot. Exit code is always 0 — the sensor reports, it does not
gate. Diagnostic-issue lifecycle and escalation are **Phase 0b** (ADR D7/D8).

## Shared parser (no more vendored checker)

The vendored `ci/check-plan-fields.py` copy is **gone** (PF-7). There is now one
implementation of the contract — the `plan-fields` package — so the sensor imports it and
gets **structured** diagnostics with stable codes instead of re-parsing a checker's text.
The package needs **Python 3.12**, so it is a pinned dependency (`pyproject.toml` +
`uv.lock`, an **immutable dispatcher commit** via git+subdirectory — never a workspace
path), and the sensor runs under `uv run --frozen`. The other `ci/` scripts stay stdlib
and are untouched. Bump the pin in its own PR.

## Run locally

```bash
# from a workspace that already has the fleet on disk as siblings:
uv run --frozen python ci/plan_fields_sensor.py \
  --manifest workspace-manifest.toml \
  --workspace .. \
  --out-dir artifacts
```

`--workspace` is the directory whose children are the repo checkouts. In CI, `bootstrap.sh`
clones the pinned fleet there first. Tests: `uv run --frozen pytest`.

## CI credentials

The scheduled workflow (`.github/workflows/plan-fields-sensor.yml`) clones the private fleet
via `bootstrap.sh`, which needs read access. Provide a `WORKSPACE_FLEET_TOKEN` secret
(fine-grained PAT / classic token, read scope on the fleet). Without it, clones are skipped,
every repo is reported as "not checked out", and the run stays green.
