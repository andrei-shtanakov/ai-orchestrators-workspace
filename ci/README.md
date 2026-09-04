# ci/ — вендоренная копия чекера для самодостаточного CI

`check-release-drift.py` живёт в `devtools/`; здесь лежит **пиненая копия**
(@ devtools `fec3443`, 2026-09-05) — философия «vendored pinned copy» из CLAUDE.md
экосистемы. CI зонтика поэтому не клонирует приватный devtools на каждый PR.

⚠ Единственная дивергенция от devtools master: дефолт `--manifest` адаптирован под
layout зонтика (манифест в **корне** репо, `ci/` уровнем ниже), тогда как в devtools
дефолт указывает на соседний зонтик. CI всё равно передаёт `--manifest` явно — дефолт
нужен только для голого запуска. При обновлении копии сохраняй эту правку.

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

Чекер валидирует `[cores.*]`, `[apps.*]` и `[tools.*]`. Секция `[tools.*]`
добавлена 2026-09-05 (devtools#139, приёмка запроса prograph-vault): до этого
пины пяти инструментов не проверял никто, и гейт пропускал плавающий HEAD.
Для `[tools.*]` отсутствующий `pyproject` — `info`, а не `warn`: kapelle на
Elixir, spec-runner-vscode на TypeScript, robin-toolkit «без pyproject»,
ecosystem-kb — хранилище Obsidian, и версии релиза у них нет. Понижение узкое
(секция `tools` И `publish != pypi` И файла нет), поэтому `[apps.*]` — все
настоящие Python-пакеты с `publish = "none"` — предупреждение сохраняют.
