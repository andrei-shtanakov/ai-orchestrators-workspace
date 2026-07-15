# ci/ — вендоренная копия чекера для самодостаточного CI

`check-release-drift.py` живёт в `devtools/`; здесь лежит **пиненая копия**
(@ devtools `bde8cbe`, 2026-07-15) — философия «vendored pinned copy» из CLAUDE.md
экосистемы. CI зонтика поэтому не клонирует приватный devtools на каждый PR.

Обновить копию при изменении чекера в devtools (редко):

```bash
# из корня зонтика, devtools склонирован соседом (после bootstrap.sh)
cp ../devtools/check-release-drift.py ci/check-release-drift.py
git add ci/check-release-drift.py
git commit -m "vendor: check-release-drift.py @ $(git -C ../devtools rev-parse --short HEAD)"
```

Альтернатива без вендоринга — клонировать devtools в CI по deploy-key и звать
`devtools/check-release-drift.py`; дороже и требует секрета. Для manifest-only
проверки вендоринг проще.

⚠ Ограничение чекера: он валидирует только `[cores.*]`/`[apps.*]` — секцию
`[tools.*]` манифеста CI не проверяет, её пины поддерживаются вручную.
