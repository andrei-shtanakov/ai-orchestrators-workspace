#!/usr/bin/env python3
"""plan_fields_sensor.py — Phase 0a fleet plan-fields sensor (warnings-only).

Home: ai-orchestrators-workspace, ``ci/``. The umbrella-owned wrapper ADR-ECO-005
(sequencing Phase 0a) asks for: read ``workspace-manifest.toml`` (the SSOT of the
repo set + pins), **freeze every repo's SHA before analysis**, resolve the fleet
plan-fields graph over the frozen roots, and emit a machine-readable snapshot plus
a human report.

The analysis no longer shells out to a vendored copy of the devtools checker and
re-parses its text. It calls the shared **``plan-fields``** package directly (PF-7):
one implementation of the contract, structured diagnostics with stable codes.

  * ``parse_fleet`` / ``check_fleet`` — the CANONICAL graph over ``@id``'d items,
    with cross-repo ``todo://`` edges resolved and ``PF-BLOCKER-STALE`` on the
    resolved graph (the only stable-identity, host-independent failure class).
  * ``check_legacy_fleet`` — the TRANSITIONAL legacy ``<repo>#<slug>`` graph over
    the un-``@id``'d items the fleet still lives on (pre-PF-2B); every finding is a
    warning marked ``[legacy source: no @id]``.

The manifest read, the frozen-SHA provenance, and the JSON snapshot are the
umbrella's own Phase-0a functionality; parsing/resolution/graph diagnostics are
the package's. Inputs are FROZEN before analysis — the sensor never lets the
package discover siblings; it hands the package exactly the pinned roots.

Phase 0a scope (hard): **warnings-only.** No GitHub issue mutation, no
``first_seen`` persistence, no error escalation on a second snapshot — all Phase 0b.
The exit code is therefore always 0: the sensor reports, it does not gate.

Runtime: the ``plan-fields`` package needs Python 3.12 and is a pinned dependency,
so this script runs under ``uv`` (see the sensor workflow / ``pyproject.toml``). The
other ``ci/`` scripts stay stdlib and are unaffected.
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
except ModuleNotFoundError:  # pragma: no cover - py<3.11 fallback
    import tomli as tomllib  # type: ignore[no-redef]

from plan_fields import (
    ManifestIndex,
    RepoInput,
    check_fleet,
    check_legacy_fleet,
    manifest_index,
    parse_fleet,
)
from plan_fields import __version__ as PLAN_FIELDS_VERSION

COLLECTOR_VERSION = "plan-fields-sensor/0.2.0"
SNAPSHOT_SCHEMA = 2

# sensor severity policy — a thin projection of the package's stable codes. A
# canonical stale (stable @id identity) is the only "error" class; everything else,
# and every legacy finding, is a warning. Phase 0a never gates on any of it.
_CANONICAL_ERROR = {"PF-BLOCKER-STALE"}
_COVERAGE_CODES = {"PF-ID-MISSING", "PF-OWNER-MISSING", "PF-OWNER-GRAMMAR"}


@dataclass
class Pin:
    """The intended ref for a repo, per manifest checkout_policy (sha→tag→branch)."""

    kind: str  # sha | tag | branch
    value: str


@dataclass
class Source:
    """Per-repo provenance in the frozen snapshot."""

    repo: str  # canonical package_name
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
    parser: dict
    sources: list[dict]
    canonical_edges: int
    canonical: list[dict]
    legacy: list[dict]
    diagnostics: dict[str, list[str]]
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
    """Return (package_name, entry) for each unique git_dir, skipping members/dupes.

    Mirrors bootstrap.sh: ``member = true`` entries share a git_dir with their owner
    and are not separate checkouts, and a git_dir seen twice is emitted once.
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
            out.append((str(entry.get("package_name") or cid), entry))
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


def freeze_sources(manifest: dict, workspace: Path) -> tuple[list[Source], list[str]]:
    """Freeze each repo's HEAD SHA before analysis; collect provenance + warnings."""
    sources: list[Source] = []
    warnings: list[str] = []
    for name, entry in manifest_repos(manifest):
        git_dir = str(entry.get("git_dir"))
        pin = resolve_pin(entry)
        repo_dir = workspace / git_dir
        # A repo is a frozen root only when its HEAD actually resolves: a missing
        # .git, or a partial/broken clone whose HEAD cannot be read, is NOT frozen
        # and must not inflate the count or reach analysis with a null commit.
        has_git = (repo_dir / ".git").exists()
        resolved = git_head(repo_dir) if has_git else None
        present = resolved is not None
        if not has_git:
            warnings.append(f"{name}: not checked out under {workspace} — skipped")
        elif resolved is None:
            warnings.append(
                f"{name}: .git present but HEAD unresolved "
                f"(partial/broken clone) — skipped"
            )
        pin_drift = (
            pin.kind == "sha"
            and resolved is not None
            and not resolved.startswith(pin.value)
        )
        if pin_drift:
            warnings.append(
                f"{name}: checkout HEAD {resolved[:12]} != manifest sha pin "
                f"{pin.value} (drift between pin and frozen root)"
            )
        sources.append(
            Source(
                repo=name,
                git_dir=git_dir,
                pin=pin,
                present=present,
                resolved_sha=resolved,
                pin_drift=pin_drift,
            )
        )
    return sources, warnings


def build_inputs(sources: list[Source], workspace: Path) -> list[RepoInput]:
    """One frozen RepoInput per source — the pinned roots handed to the package.

    The disk read stays here (the sensor's job); ``parse_fleet`` does no discovery.
    A present source contributes its ``TODO.md`` text (or ``None`` when it keeps
    none) and its frozen HEAD as the pinned commit; an absent one is
    ``available=False``.
    """
    inputs: list[RepoInput] = []
    for s in sources:
        if not s.present:
            inputs.append(RepoInput(s.repo.lower(), available=False))
            continue
        todo = workspace / s.git_dir / "TODO.md"
        text = todo.read_text(encoding="utf-8", errors="ignore") if todo.is_file() else None
        inputs.append(
            RepoInput(s.repo.lower(), todo_text=text, commit=s.resolved_sha, available=True)
        )
    return inputs


def resolve_fleet(inputs: list[RepoInput], index: ManifestIndex) -> dict:
    """Run the package's canonical + legacy passes; return structured results.

    Takes a ``ManifestIndex``, not a bare name set: the package resolves every written
    repo name through it, so a reference spelled with a declared ``git_dir`` locator
    reaches the same verdict as one spelled with the manifest key. Handing it a plain
    set was the older API, and the umbrella kept working only because its pin was two
    package revisions behind the rest of the fleet.
    """
    snapshot = parse_fleet(inputs, index)
    canonical = list(snapshot["diagnostics"]) + check_fleet(snapshot)
    exclude = {
        (r["provenance"]["repo"], r["raw_ref"]) for r in snapshot["references"]
    }
    legacy = check_legacy_fleet(inputs, index, exclude=exclude)

    errors: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []
    canonical_out: list[dict] = []
    for d in canonical:
        canonical_out.append(
            {
                "code": d["code"],
                "severity": d["severity"],
                "subject_uri": d.get("subject_uri"),
                "related_uri": d.get("related_uri"),
                "message": d["message"],
            }
        )
        if d["code"] in _COVERAGE_CODES:
            continue  # coverage/backlog is summarised as a note, not a per-item line
        bucket = errors if d["code"] in _CANONICAL_ERROR else warnings
        bucket.append(f"{d['message']} [{d['code']}]")

    legacy_out: list[dict] = []
    for d in legacy:
        legacy_out.append(
            {
                "code": d.code,
                "source_repo": d.source_repo,
                "source_line": d.source_line,
                "target_repo": d.target_repo,
                "slug": d.slug,
                "message": d.message,
                "identity_grade": d.identity_grade,
            }
        )
        warnings.append(f"{d.message}  [legacy source: no @id]")

    edges = snapshot["edges"]
    missing = sum(1 for d in canonical if d["code"] == "PF-ID-MISSING")
    if edges:
        notes.append(f"canonical: {len(edges)} resolved cross-repo @id edge(s)")
    if missing:
        notes.append(f"{missing} open item(s) still without an @id (PF-2B backlog)")

    return {
        "canonical": canonical_out,
        "legacy": legacy_out,
        "edges": len(edges),
        "diagnostics": {"errors": errors, "warnings": warnings, "notes": notes},
    }


def render_report(snap: Snapshot) -> str:
    """Human-readable Markdown mirror of the snapshot."""
    lines = [
        "# plan-fields fleet sensor — Phase 0a (warnings-only)",
        "",
        f"- generated: `{snap.generated_at}`",
        f"- manifest: schema `{snap.manifest_ref.get('schema_version')}`, "
        f"generated `{snap.manifest_ref.get('generated')}`",
        f"- collector: `{snap.collector_version}`",
        f"- parser: `{snap.parser['package']}` "
        f"pinned `{snap.parser['pin'][:12]}`",
        f"- canonical @id edges resolved: **{snap.canonical_edges}**",
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
        "## Diagnostics (from the plan-fields package)",
        "",
        f"- errors: **{len(diag['errors'])}**  ·  "
        f"warnings: **{len(diag['warnings'])}**  ·  notes: {len(diag['notes'])}",
        f"- sensor-level warnings (freeze): **{len(snap.warnings)}**",
        "",
    ]
    for note in diag["notes"]:
        lines.append(f"- {note}")
    if diag["notes"]:
        lines.append("")
    if diag["errors"]:
        lines.append("### Canonical errors (stable @id identity)")
        lines += [f"- {e}" for e in diag["errors"]]
        lines.append("")
    if diag["warnings"]:
        lines.append("### Warnings")
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
    generated_at: str,
    index: ManifestIndex,
) -> Snapshot:
    """Assemble the full Phase-0a snapshot (freeze → resolve → collect)."""
    sources, freeze_warnings = freeze_sources(manifest, workspace)
    inputs = build_inputs(sources, workspace)
    result = resolve_fleet(inputs, index)
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
        parser={"package": "plan-fields", "version": PLAN_FIELDS_VERSION, "pin": _pin_commit()},
        sources=[asdict(s) for s in sources],
        canonical_edges=result["edges"],
        canonical=result["canonical"],
        legacy=result["legacy"],
        diagnostics=result["diagnostics"],
        warnings=freeze_warnings,
    )


def _pin_commit() -> str:
    """The immutable dispatcher commit the package is pinned to (from uv.lock)."""
    lock = Path(__file__).resolve().parent.parent / "uv.lock"
    try:
        data = tomllib.loads(lock.read_text(encoding="utf-8"))
    except OSError:
        return "unknown"
    for pkg in data.get("package", []):
        if pkg.get("name") == "plan-fields":
            src = pkg.get("source", {})
            rev = src.get("rev") or ""
            if rev:
                return rev
            git = src.get("git", "")
            if "rev=" in git:
                return git.split("rev=", 1)[1].split("#", 1)[0]
    return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument(
        "--workspace",
        required=True,
        type=Path,
        help="root holding the frozen (pinned) repo checkouts",
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
    snap = build_snapshot(
        manifest, args.workspace, generated_at, manifest_index(args.manifest)
    )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / args.json).write_text(
        json.dumps(asdict(snap), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (args.out_dir / args.md).write_text(render_report(snap), encoding="utf-8")

    present = sum(1 for s in snap.sources if s["present"])
    print(
        f"plan-fields sensor (Phase 0a): {present}/{len(snap.sources)} repos frozen, "
        f"{snap.canonical_edges} canonical edge(s), "
        f"{len(snap.diagnostics['warnings'])} warning(s), "
        f"{len(snap.diagnostics['errors'])} error(s), "
        f"{len(snap.warnings)} freeze warning(s). Artifacts in {args.out_dir}/."
    )
    # Phase 0a is warnings-only: always exit 0 (no gating until Phase 0b).
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
