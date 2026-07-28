# plan-fields fleet sensor — Phase 0a

Umbrella-owned nightly sensor for the plan-fields contract (ADR-ECO-005, decided in
`ecosystem-kb`/prograph-vault `authored/decisions/`). It answers the drift class where a
single repo is locally green while the fleet view disagrees.

## What it does

1. Reads `workspace-manifest.toml` (SSOT of the repo set + pins).
2. **Freezes** each repo's HEAD SHA before analysis, recording the manifest pin and the
   resolved SHA (and flagging pin↔checkout drift).
3. Runs the **vendored** plan-fields checker over the frozen roots.
4. Emits `plan-fields-snapshot.json` (machine-readable, with per-repo provenance) and
   `plan-fields-report.md` (human).

## Phase 0a scope (hard boundary)

**Warnings-only.** No GitHub issue mutation, no `first_seen` persistence, no error
escalation on a second snapshot. Exit code is always 0 — the sensor reports, it does not
gate. Diagnostic-issue lifecycle and escalation are **Phase 0b** (ADR D7/D8).

## Vendored checker

`ci/check-plan-fields.py` is a **byte-for-byte copy** of `devtools/check-plan-fields.py`
at the manifest-pinned devtools SHA (`tools.devtools`, `bde8cbe2aa04` as of 2026-07-15),
mirroring how `check-release-drift.py` / `check_manifest_resolve.py` are vendored here. To
refresh: re-copy from the devtools pin and bump this note. Do not edit the vendored copy in
place. A JSON output flag (`--format json`) on the upstream checker is a separate devtools
change (sub-handoff); until then the sensor parses the checker's stdout.

## Run locally

```bash
# from a workspace that already has the fleet on disk as siblings:
python ci/plan_fields_sensor.py \
  --manifest workspace-manifest.toml \
  --workspace .. \
  --checker ci/check-plan-fields.py \
  --out-dir artifacts
```

`--workspace` is the directory whose children are the repo checkouts. In CI, `bootstrap.sh`
clones the pinned fleet there first.

## CI credentials

The scheduled workflow (`.github/workflows/plan-fields-sensor.yml`) clones the private fleet
via `bootstrap.sh`, which needs read access. Provide a `WORKSPACE_FLEET_TOKEN` secret
(fine-grained PAT / classic token, read scope on the fleet). Without it, clones are skipped,
every repo is reported as "not checked out", and the run stays green.
