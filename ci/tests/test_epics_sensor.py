"""Ф1b — сенсор оси эпиков: что он обязан ловить и что обязан НЕ ронять.

Проверяется не механика парсинга (она в пакете `plan-fields` и покрыта его тестами),
а решения, которые принимает именно сенсор: что блокирует прогон, что откладывается
до даты из реестра, и какие плоскости он вправе называть измеренными.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from epics_sensor import _blocking, _coverage, _escalate, build

_REGISTRY = Path(__file__).resolve().parent.parent.parent / "epics.toml"


def _manifest(tmp: Path, body: str) -> Path:
    p = tmp / "workspace-manifest.toml"
    p.write_text('schema_version = "0.3.0"\n' + body, encoding="utf-8")
    return p


def _repo(root: Path, name: str, todo: str) -> None:
    d = root / name
    (d / ".git").mkdir(parents=True, exist_ok=True)
    (d / "TODO.md").write_text(todo, encoding="utf-8")


def test_the_real_registry_is_valid() -> None:
    """Реестр зонтика обязан проходить собственный гейт — иначе гейт бессмыслен."""
    snap = build(_REGISTRY, None, None, "2026-08-25T00:00:00Z", date(2026, 8, 25))
    assert snap.registry_diagnostics == []
    assert _blocking(snap) == []
    assert snap.programs["eco"] == "ecosystem"
    assert snap.programs["airun"] == "external"


def test_unobserved_planes_are_named_not_zeroed() -> None:
    """issues и PR не читаются этим сенсором — и это должно быть видно.

    Ноль по неизмеренной плоскости неотличим от нуля по измеренной, а решение о
    cutover принимается по этим числам.
    """
    snap = build(_REGISTRY, None, None, "2026-08-25T00:00:00Z", date(2026, 8, 25))
    planes = {c["plane"]: c for c in snap.coverage}
    for name in ("issues", "pull_requests"):
        assert planes[name]["observed"] is False
        assert planes[name]["verdict"] == "not_observed"
        assert planes[name]["ratio"] is None


def test_small_denominator_reports_insufficient_sample_not_a_ratio() -> None:
    """Доля по трём пунктам — шум: один неразмеченный обваливает её до нуля."""
    cov = _coverage(tagged=2, missing=1, invalid=0, min_sample=10, threshold=0.98)
    assert cov.verdict == "insufficient_sample"
    assert cov.ratio is None


def test_coverage_compares_against_the_cutover_threshold() -> None:
    at = _coverage(tagged=98, missing=2, invalid=0, min_sample=10, threshold=0.98)
    below = _coverage(tagged=90, missing=10, invalid=0, min_sample=10, threshold=0.98)
    assert (at.verdict, at.ratio) == ("at_or_above_cutover", 0.98)
    assert below.verdict == "below_cutover"


def test_ep_missing_is_deferred_until_the_registry_date() -> None:
    findings = [{"code": "EP-MISSING", "severity": "warning", "message": "x"}]
    before = _escalate(list(findings), "2026-11-01", date(2026, 8, 25))
    after = _escalate(list(findings), "2026-11-01", date(2026, 11, 1))
    assert before[0]["severity"] == "warning"
    assert after[0]["severity"] == "error"
    assert after[0]["escalated_by"] == "2026-11-01"


def test_structural_codes_are_not_touched_by_the_policy_date() -> None:
    """Переходный период чистит старый долг, а не разрешает новую двусмысленность."""
    findings = [{"code": "EP-GRAMMAR", "severity": "error", "message": "x"}]
    assert _escalate(list(findings), "2099-01-01", date(2026, 8, 25))[0]["severity"] == "error"


def test_a_malformed_epic_blocks_while_a_missing_one_does_not(tmp_path: Path) -> None:
    """Ровно та развилка, ради которой у сенсора два класса исходов."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _repo(ws, "demo", "- [ ] broken @id:a @epic:eco\n- [ ] untagged @id:b\n")
    manifest = _manifest(tmp_path, '[apps.demo]\ngit_dir = "demo"\n')
    snap = build(_REGISTRY, manifest, ws, "2026-08-25T00:00:00Z", date(2026, 8, 25))

    codes = {f["code"] for f in snap.findings}
    assert codes == {"EP-GRAMMAR", "EP-MISSING"}
    blocking = {f["code"] for f in _blocking(snap)}
    assert blocking == {"EP-GRAMMAR"}


def test_unknown_epic_blocks_because_the_registry_is_closed(tmp_path: Path) -> None:
    """Опечатка в ключе — единственное, что стоит между ней и разъехавшимся агрегатом."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _repo(ws, "demo", "- [ ] typo @id:a @epic:eco.dark-factroy\n")
    manifest = _manifest(tmp_path, '[apps.demo]\ngit_dir = "demo"\n')
    snap = build(_REGISTRY, manifest, ws, "2026-08-25T00:00:00Z", date(2026, 8, 25))
    assert [f["code"] for f in _blocking(snap)] == ["EP-UNKNOWN"]


def test_closed_items_carry_no_obligation(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    _repo(ws, "demo", "- [x] done long ago @id:a\n")
    manifest = _manifest(tmp_path, '[apps.demo]\ngit_dir = "demo"\n')
    snap = build(_REGISTRY, manifest, ws, "2026-08-25T00:00:00Z", date(2026, 8, 25))
    assert snap.findings == []
    assert snap.unclassified == 0


def test_tagged_items_are_counted_by_epic_and_by_defect(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    _repo(
        ws,
        "demo",
        "- [ ] work @id:a @epic:eco.ops\n- [ ] fix @id:b @epic:eco.ops @defect:pipeline\n",
    )
    manifest = _manifest(tmp_path, '[apps.demo]\ngit_dir = "demo"\n')
    snap = build(_REGISTRY, manifest, ws, "2026-08-25T00:00:00Z", date(2026, 8, 25))
    assert snap.per_epic == {"eco.ops": 2}
    assert snap.per_defect == {"pipeline": 1}
    assert snap.unclassified == 0
