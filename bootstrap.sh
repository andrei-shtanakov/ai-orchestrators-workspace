#!/usr/bin/env bash
# ============================================================
#  bootstrap.sh — восстановить командный воркспейс из workspace-manifest.toml.
#  Клонирует каждый репо набора СОСЕДОМ рядом с этим зонтиком и ставит на пин.
#  macOS-совместимо (bash 3.2: без mapfile/associative arrays).
#
#  Запуск (из корня зонтика):
#     ./bootstrap.sh          # пиненый набор (воспроизводимо: sha/tag из манифеста)
#     ./bootstrap.sh --head   # дефолтные ветки вместо пинов (для активной разработки)
#     ./bootstrap.sh --deps   # + uv sync / cargo build после клонирования
# ============================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS="$(dirname "$HERE")"                    # воркспейс = родитель зонтика
MANIFEST="$HERE/workspace-manifest.toml"

MODE_HEAD=0; DO_DEPS=0
for a in "$@"; do
  case "$a" in
    --head) MODE_HEAD=1 ;;
    --deps) DO_DEPS=1 ;;
    *) echo "usage: ./bootstrap.sh [--head] [--deps]"; exit 2 ;;
  esac
done

command -v git >/dev/null 2>&1 || { echo "нужен git"; exit 1; }
PY="$(command -v python3 || true)"; [ -n "$PY" ] || { echo "нужен python3"; exit 1; }
[ -f "$MANIFEST" ] || { echo "манифест не найден: $MANIFEST"; exit 2; }

# Парсер манифеста → строки TSV: git_dir <TAB> repo_url <TAB> ref
#   ref = sha | tag(если !='-') | @HEAD;  при --head → всегда @HEAD.
#   Пустой repo_url (незаполненный tools.*) — пропускается с предупреждением.
read_manifest() {
"$PY" - "$MANIFEST" "$MODE_HEAD" <<'PYEOF'
import sys
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
path, head = sys.argv[1], (sys.argv[2] == "1")
data = tomllib.loads(open(path, encoding="utf-8").read())
seen = set()
for section in ("cores", "apps", "tools"):
    for cid, m in (data.get(section) or {}).items():
        if m.get("member"):
            continue                       # member делит git_dir с родителем — не клоним отдельно
        gd = m.get("git_dir")
        if not gd or gd in seen:
            continue
        seen.add(gd)
        if head:
            ref = "@HEAD"
        else:
            ref = m.get("sha") or ""
            if not ref:
                t = m.get("tag", "-")
                ref = t if t not in ("-", None) else "@HEAD"
        print("\t".join([gd, m.get("repo_url", ""), ref]))
PYEOF
}

echo "Воркспейс: $WS"
echo "Манифест:  $MANIFEST  (mode=$([ $MODE_HEAD -eq 1 ] && echo head || echo pinned))"
echo

read_manifest | while IFS=$'\t' read -r gd url ref; do
  [ -n "$gd" ] || continue
  if [ -z "$url" ]; then
    echo "!! $gd: пустой repo_url в манифесте — заполни и повтори (см. секцию tools)"
    continue
  fi
  dest="$WS/$gd"
  if [ -d "$dest/.git" ]; then
    echo "== $gd: уже клонирован, пропуск (обновляй через devtools/repos.sh pull) =="
    continue
  fi
  echo "== clone $gd <= $url =="
  if ! git clone "$url" "$dest"; then
    echo "!! clone $gd не удался — проверь доступ (SSH-ключ / права репо)"
    continue
  fi
  if [ "$ref" != "@HEAD" ]; then
    git -C "$dest" checkout --quiet "$ref" \
      || echo "!! checkout $ref в $gd не удался (пин мог быть перезаписан force-push)"
  fi
done

if [ "$DO_DEPS" -eq 1 ]; then
  echo
  echo "== зависимости: uv sync (python) + cargo build (rust) =="
  if [ -x "$WS/devtools/repos.sh" ]; then
    "$WS/devtools/repos.sh" bootstrap
  else
    echo "!! devtools/repos.sh не найден — пропускаю deps"
  fi
fi

echo
echo "Готово. Дальше:"
echo "   cd $WS/devtools && make morning     # ветка / ahead-behind / грязь по всем репо"
echo "Пиненый режим оставляет репо в detached HEAD — для работы: git switch -c <branch>."
