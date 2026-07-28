#!/usr/bin/env python3
"""plan_fields_sensor.py — Phase 0a fleet plan-fields sensor (warnings-only).

Home: ai-orchestrators-workspace, ``ci/``. This is the umbrella-owned wrapper that
ADR-ECO-005 (sequencing Phase 0a) asks for: read ``workspace-manifest.toml`` (the SSOT
of the repo set + pins), **freeze every repo's SHA before analysis**, run the vendored
plan-fields checker over the frozen roots, and emit a machine-readable snapshot plus a
human report.

It is deliberately *more* than the raw ``check-plan-fields.py`` (which discovers
siblings via ``iterdir`` + origin parsing, with no manifest, no SHA freeze, no JSON):
the manifest read, the frozen-SHA provenance, and the JSON snapshot are the new
Phase-0a functionality.

Phase 0a scope (hard): **warnings-only.** No GitHub issue mutation, no ``first_seen``
persistence, no error escalation on a second snapshot — all Phase 0b. The exit code is
therefore always 0: the sensor reports, it does not gate.

Vendored checker provenance: ``ci/check-plan-fields.py`` is a byte-for-byte copy of
``devtools/check-plan-fields.py`` at the manifest-pinned devtools SHA
(``tools.devtools``), mirroring the other vendored checkers here.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # py<3.11 fallback
    import tomli as tomllib  # type: ignore[no-redef]

COLLECTOR_VERSION = "plan-fields-sensor/0.1.0"
SNAPSHOT_SCHEMA = 1


@dataclass
class Pin:
    """The intended ref for a repo, per manifest checkout_policy (sha→tag→branch)."""

    kind: str  # sha | tag | branch
    value: str


@dataclass
class Source:
    """Per-repo provenance in the frozen snapshot."""

    repo: str
    git_dir: str
    pin: Pin
    present: bool
    resolved_sha: str | None = None
    pin_drift: bool = False  # checked-out HEAD disagrees with the manifest sha pin


@dataclass
class Snapshot:
    """The immutable Phase-0a observation (derived projection, never a store)."""

    schema_version: int
    phase: str
    generated_at: str
    manifest_ref: dict
    collector_version: str
    workspace_root: str
    checker: dict
    sources: list[dict]
    diagnostics: dict[str, list[str]]
    checker_summary: str | None
    checker_exit: int
    raw_checker_output: str
    warnings: list[str] = field(default_factory=list)


def load_manifest(path: Path) -> dict:
    """Parse the workspace manifest TOML."""
    return tomllib.loads(path.read_text(encoding="utf-8"))


def resolve_pin(entry: dict) -> Pin:
    """Pick the frozen ref per checkout_policy: sha → tag(!='-') → default branch."""
    sha = entry.get("sha")
    if sha:
        return Pin("sha", str(sha))
    tag = entry.get("tag", "-")
    if tag and tag != "-":
        return Pin("tag", str(tag))
    return Pin("branch", "<default>")


def manifest_repos(manifest: dict) -> list[tuple[str, dict]]:
    """Return (repo-id, entry) for each unique git_dir, skipping members/dupes.

    Mirrors bootstrap.sh: ``member = true`` entries share a git_dir with their owner and
    are not separate checkouts, and a git_dir seen twice is emitted once.
    """
    seen: set[str] = set()
    out: list[tuple[str, dict]] = []
    for section in ("cores", "apps", "tools"):
        for cid, entry in (manifest.get(section) or {}).items():
            if entry.get("member"):
                continue
            git_dir = entry.get("git_dir")
            if not git_dir or git_dir in seen:
                continue
            seen.add(git_dir)
            out.append((cid, entry))
    return out


def git_head(repo_dir: Path) -> str | None:
    """Resolve the checked-out HEAD SHA, or None if not a git checkout."""
    if not (repo_dir / ".git").exists():
        return None
    proc = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def freeze_sources(
    manifest: dict, workspace: Path
) -> tuple[list[Source], list[str]]:
    """Freeze each repo's HEAD SHA before analysis; collect provenance + warnings."""
    sources: list[Source] = []
    warnings: list[str] = []
    for repo_id, entry in manifest_repos(manifest):
        git_dir = str(entry.get("git_dir"))
        pin = resolve_pin(entry)
        repo_dir = workspace / git_dir
        # "present" means a git checkout exists — a leftover/partial-clone directory
        # without .git is NOT a frozen root and must not inflate the count.
        present = (repo_dir / ".git").exists()
        resolved = git_head(repo_dir) if present else None
        if not present:
            warnings.append(f"{repo_id}: not checked out under {workspace} — skipped")
        elif resolved is None:
            warnings.append(
                f"{repo_id}: .git present but HEAD unresolved "
                f"(partial/broken clone) — skipped"
            )
        pin_drift = (
            pin.kind == "sha"
            and resolved is not None
            and not resolved.startswith(pin.value)
        )
        if pin_drift:
            warnings.append(
                f"{repo_id}: checkout HEAD {resolved[:12]} != manifest sha pin "
                f"{pin.value} (drift between pin and frozen root)"
            )
        sources.append(
            Source(
                repo=repo_id,
                git_dir=git_dir,
                pin=pin,
                present=present,
                resolved_sha=resolved,
                pin_drift=pin_drift,
            )
        )
    return sources, warnings


def run_checker(checker: Path, workspace: Path) -> tuple[int, str]:
    """Run the vendored plan-fields checker over the frozen root; capture output."""
    proc = subprocess.run(
        [sys.executable, str(checker), "--root", str(workspace)],
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def parse_checker_output(raw: str) -> tuple[dict[str, list[str]], str | None]:
    """Split the checker's stdout into diagnostics + the summary line."""
    errors: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []
    summary: str | None = None
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("ERROR:"):
            errors.append(stripped[len("ERROR:") :].strip())
        elif stripped.startswith("WARN:"):
            warnings.append(stripped[len("WARN:") :].strip())
        elif stripped.startswith("plan-fields on "):
            summary = stripped
        elif stripped:
            notes.append(stripped)
    return {"errors": errors, "warnings": warnings, "notes": notes}, summary


def render_report(snap: Snapshot) -> str:
    """Human-readable Markdown mirror of the snapshot."""
    lines = [
        "# plan-fields fleet sensor — Phase 0a (warnings-only)",
        "",
        f"- generated: `{snap.generated_at}`",
        f"- manifest: schema `{snap.manifest_ref.get('schema_version')}`, "
        f"generated `{snap.manifest_ref.get('generated')}`",
        f"- collector: `{snap.collector_version}`",
        f"- checker exit: `{snap.checker_exit}` (not a gate in Phase 0a)",
        "",
        "## Frozen roots",
        "",
        "| repo | pin | resolved HEAD | present |",
        "|---|---|---|---|",
    ]
    for s in snap.sources:
        head = (s["resolved_sha"] or "—")[:12]
        pin = f"{s['pin']['kind']}:{s['pin']['value']}"
        flag = " ⚠pin-drift" if s["pin_drift"] else ""
        lines.append(
            f"| {s['repo']} | `{pin}`{flag} | `{head}` | "
            f"{'yes' if s['present'] else 'NO'} |"
        )
    diag = snap.diagnostics
    lines += [
        "",
        "## Diagnostics (from vendored checker)",
        "",
        f"- errors: **{len(diag['errors'])}**  ·  "
        f"warnings: **{len(diag['warnings'])}**  ·  notes: {len(diag['notes'])}",
        f"- sensor-level warnings (freeze): **{len(snap.warnings)}**",
        "",
    ]
    if diag["errors"]:
        lines.append("### Checker errors")
        lines += [f"- {e}" for e in diag["errors"]]
        lines.append("")
    if diag["warnings"]:
        lines.append("### Checker warnings")
        lines += [f"- {w}" for w in diag["warnings"]]
        lines.append("")
    if snap.warnings:
        lines.append("### Freeze warnings")
        lines += [f"- {w}" for w in snap.warnings]
        lines.append("")
    lines.append(
        "> Phase 0a is warnings-only: no issue mutation, no escalation. "
        "Escalation and `first_seen` land in Phase 0b (ADR-ECO-005 D7/D8)."
    )
    return "\n".join(lines) + "\n"


def build_snapshot(
    manifest: dict,
    workspace: Path,
    checker: Path,
    generated_at: str,
) -> Snapshot:
    """Assemble the full Phase-0a snapshot (freeze → run → collect)."""
    sources, freeze_warnings = freeze_sources(manifest, workspace)
    exit_code, raw = run_checker(checker, workspace)
    diagnostics, summary = parse_checker_output(raw)
    # A non-zero checker exit with no ERROR: lines means the checker did not run
    # cleanly (invalid root, no TODO.md repos, crash) — surface it so the summary
    # never reads "0 errors" over a failed run.
    if exit_code != 0 and not diagnostics["errors"]:
        diagnostics["errors"].append(
            f"checker exited {exit_code} without ERROR: lines — it did not run "
            f"successfully (invalid root, no TODO.md under it, or a crash)"
        )
    return Snapshot(
        schema_version=SNAPSHOT_SCHEMA,
        phase="0a",
        generated_at=generated_at,
        manifest_ref={
            "schema_version": manifest.get("schema_version"),
            "generated": manifest.get("generated"),
            "ecosystem_release": manifest.get("ecosystem_release"),
        },
        collector_version=COLLECTOR_VERSION,
        workspace_root=str(workspace),
        checker={
            "path": str(checker),
            "vendored_from": "devtools/check-plan-fields.py",
        },
        sources=[_source_dict(s) for s in sources],
        diagnostics=diagnostics,
        checker_summary=summary,
        checker_exit=exit_code,
        raw_checker_output=raw,
        warnings=freeze_warnings,
    )


def _source_dict(s: Source) -> dict:
    d = asdict(s)
    return d


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument(
        "--workspace",
        required=True,
        type=Path,
        help="root holding the frozen (pinned) repo checkouts",
    )
    parser.add_argument(
        "--checker",
        required=True,
        type=Path,
        help="path to the vendored check-plan-fields.py",
    )
    parser.add_argument("--out-dir", type=Path, default=Path("."))
    parser.add_argument("--json", default="plan-fields-snapshot.json")
    parser.add_argument("--md", default="plan-fields-report.md")
    parser.add_argument(
        "--generated-at",
        default=None,
        help="ISO timestamp override (CI passes a frozen stamp)",
    )
    args = parser.parse_args()

    generated_at = args.generated_at or datetime.now(timezone.utc).isoformat()
    manifest = load_manifest(args.manifest)
    snap = build_snapshot(manifest, args.workspace, args.checker, generated_at)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / args.json).write_text(
        json.dumps(asdict(snap), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (args.out_dir / args.md).write_text(render_report(snap), encoding="utf-8")

    present = sum(1 for s in snap.sources if s["present"])
    print(
        f"plan-fields sensor (Phase 0a): {present}/{len(snap.sources)} repos frozen, "
        f"{len(snap.diagnostics['warnings'])} checker warning(s), "
        f"{len(snap.diagnostics['errors'])} checker error(s), "
        f"{len(snap.warnings)} freeze warning(s). Artifacts in {args.out_dir}/."
    )
    # Phase 0a is warnings-only: always exit 0 (no gating until Phase 0b).
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
