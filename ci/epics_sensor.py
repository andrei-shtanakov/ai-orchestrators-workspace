#!/usr/bin/env python3
"""epics_sensor.py — Ф1b of ADR-ECO-010: the fleet sensor for the stream axis.

Home: ai-orchestrators-workspace, ``ci/``. Two jobs that deliberately do NOT share an
exit policy, because they answer different questions:

* ``--registry-only`` — validate ``epics.toml`` against the pinned ``epics/v1`` shape.
  Blocking, and cheap enough to be a pull-request gate: it needs no fleet on disk. Until
  this existed the registry was checked by review alone, so a typo in an epic key or a
  dangling ``moved_to`` reached every consumer unannounced.
* default — the nightly fleet pass: parse every frozen ``TODO.md``, resolve each item's
  epic against the registry, and report coverage.

Exit policy mirrors ADR-ECO-010 D8, which splits "old debt" from "new ambiguity":

* structural findings (``EP-GRAMMAR``, ``EP-UNKNOWN``, ``EP-MULTIPLE``, ``EP-MOVED``,
  ``EP-DEFECT-*``, every ``EP-REG-*``) fail the run **now**. The adoption period exists
  to clear items that carry no epic, not to license items that carry a broken one.
* ``EP-MISSING`` is a warning until ``coverage_policy.missing_error_after``, then an
  error. The date lives in the registry, so hardening the fleet is a values change in
  one PR — not a code change here.

Coverage honesty (D10): this sensor observes exactly ONE plane, ``TODO.md``. Issues and
pull requests need ``snapshot/v2`` from github-checker (Ф2) and are reported as
``not_observed`` — never as 0, and never quietly folded into a single fleet percentage.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

try:
    from plan_fields import (
        RepoInput,
        apply_registry,
        checkout_map,
        load_registry,
        manifest_index,
        parse_fleet,
    )
except ImportError:  # pragma: no cover - the workflow runs under `uv run --frozen`
    print(
        "epics_sensor: the pinned `plan-fields` package is not importable; "
        "run under `uv run --frozen` (see pyproject.toml).",
        file=sys.stderr,
    )
    raise

# Structural codes fail the run from the moment a validator exists. EP-MISSING is the
# single deferred code and is handled by policy date, EP-UNAVAILABLE is environmental.
_DEFERRED = {"EP-MISSING"}
_ENVIRONMENTAL = {"EP-UNAVAILABLE"}


@dataclass
class EpicCoverage:
    """One plane's coverage, or an honest statement that it was not measured."""

    plane: str
    observed: bool
    tagged: int = 0
    missing: int = 0
    invalid: int = 0
    ratio: float | None = None
    verdict: str = "not_observed"


@dataclass
class Snapshot:
    generated_at: str
    registry_path: str
    registry_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    programs: dict[str, str] = field(default_factory=dict)
    epics: dict[str, dict[str, Any]] = field(default_factory=dict)
    per_epic: dict[str, int] = field(default_factory=dict)
    per_defect: dict[str, int] = field(default_factory=dict)
    unclassified: int = 0
    coverage: list[dict[str, Any]] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    repos_read: list[str] = field(default_factory=list)
    repos_absent: list[str] = field(default_factory=list)


def _coverage(tagged: int, missing: int, invalid: int, min_sample: int,
              threshold: float | None) -> EpicCoverage:
    """Coverage for the TODO plane, with the small-denominator guard.

    A ratio over a handful of items is noise that one untagged line drives to zero, so
    below ``min_sample`` the plane reports ``insufficient_sample`` instead of a number.
    Reporting 0.67 over three items and calling it coverage is how a threshold starts
    measuring luck.
    """
    total = tagged + missing + invalid
    cov = EpicCoverage("todo", True, tagged, missing, invalid)
    if total < min_sample:
        cov.verdict = "insufficient_sample"
        return cov
    cov.ratio = round(tagged / total, 4)
    if threshold is None:
        cov.verdict = "measured"
    else:
        cov.verdict = "at_or_above_cutover" if cov.ratio >= threshold else "below_cutover"
    return cov


def build(registry_path: Path, manifest: Path | None, workspace: Path | None,
          generated_at: str, today: date) -> Snapshot:
    registry = load_registry(registry_path)
    snap = Snapshot(
        generated_at=generated_at,
        registry_path=str(registry_path),
        registry_diagnostics=[dict(d) for d in registry.diagnostics],
        programs={k: v.get("kind", "?") for k, v in registry.programs.items()},
        epics={
            k: {"status": v.get("status"), "moved_to": v.get("moved_to")}
            for k, v in registry.epics.items()
        },
    )
    snap.coverage = [asdict(EpicCoverage(p, False)) for p in ("issues", "pull_requests")]
    if manifest is None or workspace is None:
        return snap

    index = manifest_index(manifest)
    checkouts = checkout_map(workspace, index)
    inputs: list[RepoInput] = []
    for name, root in sorted(checkouts.items()):
        todo = root / "TODO.md"
        if not todo.is_file():
            snap.repos_absent.append(name)
            continue
        inputs.append(RepoInput(name, todo.read_text(encoding="utf-8")))
        snap.repos_read.append(name)
    if not inputs:
        return snap

    doc = parse_fleet(inputs, index, generated_at)
    findings = [d for d in doc["diagnostics"] if d["code"].startswith("EP-")]
    findings.extend(apply_registry(doc, registry))

    tagged = missing = invalid = 0
    for node in doc["nodes"]:
        if node["declared_status"] != "open" or node["tombstone"]:
            continue  # closed work carries no obligation (Ф4 marks open items only)
        state = node["epic_classification"]
        if state == "tagged":
            tagged += 1
            snap.per_epic[node["epic"]] = snap.per_epic.get(node["epic"], 0) + 1
            if node["defect"]:
                snap.per_defect[node["defect"]] = snap.per_defect.get(node["defect"], 0) + 1
        elif state == "missing":
            missing += 1
        else:
            invalid += 1
    snap.unclassified = missing + invalid

    policy = registry.coverage_policy
    todo_cov = _coverage(
        tagged, missing, invalid,
        int(policy.get("min_sample", 10)),
        policy.get("robin_cutover_todo"),
    )
    snap.coverage.insert(0, asdict(todo_cov))
    snap.findings = _escalate(findings, policy.get("missing_error_after"), today)
    return snap


def _escalate(findings: list[dict[str, Any]], error_after: str | None,
              today: date) -> list[dict[str, Any]]:
    """Apply the registry's policy date to EP-MISSING, leaving every other code alone.

    The date is a value in ``epics.toml`` on purpose: hardening the fleet is then one
    reviewed PR against the registry, not an edit to this sensor. A sensor that owned
    the date would make "when do we start failing" a code change nobody reviews as a
    policy decision.
    """
    if not error_after:
        return findings
    try:
        threshold = date.fromisoformat(str(error_after))
    except ValueError:
        return findings
    if today < threshold:
        return findings
    out = []
    for f in findings:
        if f["code"] in _DEFERRED:
            f = {**f, "severity": "error", "escalated_by": str(error_after)}
        out.append(f)
    return out


def render(snap: Snapshot) -> str:
    lines = [
        "# Epics sensor — fleet stream axis (ADR-ECO-010 Ф1b)",
        "",
        f"Generated: `{snap.generated_at}` · registry: `{snap.registry_path}`",
        "",
    ]
    reg_errors = [d for d in snap.registry_diagnostics if d["severity"] == "error"]
    lines += [
        "## Registry",
        "",
        f"- programs: {len(snap.programs)} · epics: {len(snap.epics)}",
        f"- diagnostics: {len(snap.registry_diagnostics)} "
        f"({len(reg_errors)} error(s))",
        "",
    ]
    for d in snap.registry_diagnostics:
        lines.append(f"  - `{d['code']}` {d.get('subject_key') or ''} — {d['message']}")
    lines += ["", "## Coverage", ""]
    for cov in snap.coverage:
        if not cov["observed"]:
            lines.append(
                f"- **{cov['plane']}** — not observed by this sensor "
                "(needs snapshot/v2 from github-checker, Ф2)"
            )
            continue
        ratio = "—" if cov["ratio"] is None else f"{cov['ratio']:.2%}"
        lines.append(
            f"- **{cov['plane']}** — {cov['verdict']}: {ratio} "
            f"(tagged {cov['tagged']}, missing {cov['missing']}, invalid {cov['invalid']})"
        )
    lines += ["", f"- **unclassified: {snap.unclassified}**", "", "## By epic", ""]
    for epic, n in sorted(snap.per_epic.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"- `{epic}` — {n}")
    if snap.per_defect:
        lines += ["", "## By defect class", ""]
        for cls, n in sorted(snap.per_defect.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"- `{cls}` — {n}")
    lines += ["", "## Findings", ""]
    if not snap.findings:
        lines.append("_none_")
    # EP-MISSING is the adoption backlog, not a list of surprises: on a fleet that has
    # not been marked yet it is every open item, and printing all of them buries the
    # findings that need a human. It is summarised by repo instead, and the per-item
    # detail stays in the JSON snapshot.
    deferred = [f for f in snap.findings if f["code"] in _DEFERRED]
    structural = [f for f in snap.findings if f["code"] not in _DEFERRED]
    for f in sorted(structural, key=lambda d: (d["code"], d.get("subject_uri") or "")):
        lines.append(
            f"- `{f['code']}` [{f['severity']}] {f.get('subject_uri') or ''} — {f['message']}"
        )
    if not structural:
        lines.append("_no structural findings_")
    if deferred:
        by_repo: dict[str, int] = {}
        for f in deferred:
            uri = f.get("subject_uri") or "todo://?/?"
            repo = uri.removeprefix("todo://").split("/", 1)[0]
            by_repo[repo] = by_repo.get(repo, 0) + 1
        severity = deferred[0]["severity"]
        lines += [
            "",
            f"### EP-MISSING — {len(deferred)} item(s), severity `{severity}`",
            "",
            "Per repo (full per-item detail is in the JSON snapshot, not truncated):",
            "",
        ]
        for repo, n in sorted(by_repo.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"- `{repo}` — {n}")
    lines += [
        "",
        "## Sources read",
        "",
        f"- TODO.md read in {len(snap.repos_read)} repo(s): "
        f"{', '.join(snap.repos_read) or '—'}",
        f"- no TODO.md / not checked out: {', '.join(snap.repos_absent) or '—'}",
        "",
    ]
    return "\n".join(lines)


def _blocking(snap: Snapshot) -> list[dict[str, Any]]:
    """Findings that must fail the run: every error that is neither deferred nor environmental."""
    out = [d for d in snap.registry_diagnostics if d["severity"] == "error"]
    out += [
        f
        for f in snap.findings
        if f["severity"] == "error" and f["code"] not in _ENVIRONMENTAL
    ]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=Path("epics.toml"))
    parser.add_argument("--registry-only", action="store_true",
                        help="validate epics.toml alone (the PR gate); no fleet needed")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--workspace", type=Path,
                        help="root holding the frozen (pinned) repo checkouts")
    parser.add_argument("--out-dir", type=Path, default=Path("."))
    parser.add_argument("--json", default="epics-snapshot.json")
    parser.add_argument("--md", default="epics-report.md")
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--today", default=None,
                        help="ISO date override for the EP-MISSING policy gate (tests)")
    args = parser.parse_args()

    if not args.registry_only and (args.manifest is None or args.workspace is None):
        parser.error("--manifest and --workspace are required unless --registry-only")

    generated_at = args.generated_at or datetime.now(timezone.utc).isoformat()
    today = date.fromisoformat(args.today) if args.today else datetime.now(timezone.utc).date()
    snap = build(
        args.registry,
        None if args.registry_only else args.manifest,
        None if args.registry_only else args.workspace,
        generated_at,
        today,
    )

    if not args.registry_only:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        (args.out_dir / args.json).write_text(
            json.dumps(asdict(snap), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (args.out_dir / args.md).write_text(render(snap), encoding="utf-8")

    blocking = _blocking(snap)
    deferred = [f for f in snap.findings if f["code"] in _DEFERRED]
    scope = "registry" if args.registry_only else "registry + fleet"
    print(
        f"epics sensor ({scope}): {len(snap.epics)} epic(s) declared, "
        f"{len(snap.registry_diagnostics)} registry diagnostic(s), "
        f"{len(deferred)} EP-MISSING, {len(blocking)} blocking finding(s)."
    )
    for f in blocking:
        subject = f.get("subject_uri") or f.get("subject_key") or ""
        print(f"  ::error:: {f['code']} {subject} — {f['message']}")
    return 1 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
