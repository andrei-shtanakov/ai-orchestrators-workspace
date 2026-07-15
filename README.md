# ai-orchestrators-workspace

Командный **зонтик** экосистемы AI-оркестраторов. Это не монорепа и не submodules —
тонкая обёртка, которая хранит только: `workspace-manifest.toml` (какой набор репо и
версий = наш воркспейс), `bootstrap.sh` (восстановить набор одной командой), политики
и CI-гейты. Сами репозитории — независимые клоны-соседи, здесь не трекаются.

Разделение ответственности (без дублей):

| Поверхность | Владелец | Что держит |
|---|---|---|
| Набор репо + пины | **этот зонтик** `workspace-manifest.toml` | SSOT набора |
| Тулинг над флотом | `devtools` | `repos.sh`, drift/conformance; читает манифест зонтика |
| Знания/ADR/контракты | `ecosystem-kb` (`prograph-vault`) | дом командной координации |
| Гейт спек | `steward` | profiles/gate-check, git-PR |
| Витрина версий | `dispatcher` | read-model, НЕ второй SSOT |

## Онбординг (каждый сотрудник)

```bash
# 1. Клонируешь ТОЛЬКО зонтик
git clone git@github.com:andrei-shtanakov/ai-orchestrators-workspace.git
cd ai-orchestrators-workspace

# 2. Восстанавливаешь весь набор репо соседями (по пинам из манифеста)
./bootstrap.sh --deps         # клон всех репо + uv sync / cargo build
#   ./bootstrap.sh            # только клон, без зависимостей
#   ./bootstrap.sh --head     # дефолтные ветки вместо пинов (для активной разработки)

# 3. Дальше день-2-операции — через devtools
cd ../devtools
make morning                  # fetch + сводка по всем репо
```

Требования: `git`, `python3` (3.11+ со stdlib `tomllib`; на 3.10 — `pip install tomli`),
`uv`, для `arbiter`/`prograph` — `cargo`/`maturin`. Доступ к репо по SSH-ключу.

После `bootstrap.sh` (пиненый режим) репо в **detached HEAD** — это воспроизводимая точка.
Чтобы начать работу в репо: `git switch -c <branch>` или `git switch main && git pull`.

## Изменить набор/пины

Правка `workspace-manifest.toml` идёт **только через PR** (CODEOWNERS-аппрув). CI гоняет
`check-release-drift` — см. `CONTRIBUTING.md`.
