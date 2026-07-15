# CONTRIBUTING — командный зонтик

Зонтик хранит **контракт набора**, а не код проектов. Код правится в своих репо
(каждый — свой remote, свои PR). Здесь правят только манифест, политики, CI, доки.

## Ветки и PR

- Зонтик: ветка от `main` → PR → аппрув по CODEOWNERS → мерж делает человек.
- Прямой push в `main` закрыт branch protection (см. runbook по настройке).

## Как поднять пин репо в наборе

1. В самом репо выпущен новый tag / влит sha, который хотим зафиксировать.
2. В `workspace-manifest.toml` обнови `sha` (или `tag` + `lock_version`) компонента.
3. PR в зонтик. CI (`.github/workflows/manifest-drift.yml`) прогонит
   `check-release-drift --strict`: проверит воспроизводимость (нет плавающего HEAD),
   согласованность `tag ↔ lock_version`, схему.
4. Зелёный CI + аппрув CODEOWNERS → мерж. Сотрудники подтянут набор:
   `./bootstrap.sh` (новые репо) или `cd devtools && ./repos.sh pull` (обновление).

## Правила пиннинга (из ADR packaging 2026-07-12)

- `install=git-sha` **обязан** иметь `sha` — плавающий HEAD запрещён.
- `install=git-tag` **обязан** иметь `tag`; `sha` = коммит этого тега (провенанс).
- Ядра (`[cores.*]`, publish=pypi): в `pyproject` потребителя едет `pin_range`
  (диапазон), точная версия — только в `lock_version`. `==` в pyproject запрещён.
- `[tools.*]` (devtools, ecosystem-kb) — тоже пиним по sha, иначе онбординг
  невоспроизводим.

## Что НЕ коммитить в зонтик

`_cowork_output/` (личный scratch), `.claude/`, `.aider*`, кэши, вендоренный код
проектов. Обёртка `.gitignore` затягивает только своё — не обходи её `git add -f`.
