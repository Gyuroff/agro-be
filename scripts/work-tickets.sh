#!/usr/bin/env bash
# Работи по задачите от beads една по една, всяка в НОВА Claude сесия (чист контекст), с Sonnet.
#
#   bash scripts/work-tickets.sh            # бекенд, до 5 задачи
#   bash scripts/work-tickets.sh be 10      # бекенд, до 10 задачи
#   bash scripts/work-tickets.sh app 3      # приложение, до 3 задачи
#
# Спира, когато: няма готови задачи, задачата не е затворена след сесията, или claude излезе с грешка.
# Не комитва и не пуша — прегледай с `git diff` след всяка задача.
set -uo pipefail

REPO="${1:-be}"
MAX="${2:-5}"
MODEL="${CLAUDE_MODEL:-sonnet}"
MAX_TURNS="${MAX_TURNS:-120}"

export BEADS_DIR="$HOME/projects/agro-app/.beads"
case "$REPO" in
  be)  DIR="$HOME/projects/agro-be"
       EXTRA_TOOLS=',Bash(uv run:*),Bash(uv lock:*),Bash(uv sync:*),Bash(uv add:*),Bash(make check:*),Bash(make test:*),Bash(make lint:*),Bash(make fmt:*),Bash(make migrate:*),Bash(make up:*),Bash(docker compose ps:*),Bash(docker compose logs:*)' ;;
  app) DIR="$HOME/projects/agro-app"
       EXTRA_TOOLS=',Bash(flutter analyze:*),Bash(flutter test:*),Bash(flutter pub:*),Bash(dart format:*),mcp__figma' ;;
  *)   echo "Първи аргумент: be | app" >&2; exit 2 ;;
esac

# Допълнителни изисквания само за приложението: Figma е източникът на истина за вида.
PROMPT_EXTRA=""
if [ "$REPO" = "app" ]; then
  PROMPT_EXTRA="
6) UI е pixel-perfect спрямо Figma (fileKey LHa5UalWMEsQBnOgmzSBeI, рамка 402×874). За ВСЕКИ екран/компонент от задачата:
   - вземи възела от docs/screens.md / docs/figma-state.json; get_screenshot (и Full scroll варианта, ако има) и get_metadata за размери, отстояния, радиуси, текстове;
   - прочети анотациите на рамката (поведения, валидации);
   - текстовете са ТОЧНО като във Figma (български), без превод и съкращения;
   - цветове/отстояния/шрифтове само от lib/core/theme/tokens.dart (design-tokens.json), без хардкод;
   - добави widget тест със размер 402×874; след имплементацията сравни резултата с Figma скрийншота и поправи разликите (отстояния 16, радиуси, височини 48, типографика Inter).
   Ако Figma MCP не отговаря, bd note __ID__ \"ВЪПРОС: Figma недостъпна\" и спри — не измисляй вид."
fi

# Без make reset / rm / git push / git commit: тези не са в списъка и в -p режим се отказват.
TOOLS="Read,Glob,Grep,Edit,Write,Bash(bd:*),Bash(git status:*),Bash(git diff:*),Bash(git log:*),Bash(ls:*)${EXTRA_TOOLS}"

cd "$DIR" || exit 1
mkdir -p logs

first_ready() {
  bd ready --label "repo:$REPO" 2>/dev/null | grep -oE 'agro-[a-z0-9]+(\.[0-9]+)?' | head -1
}

for i in $(seq 1 "$MAX"); do
  ID="$(first_ready)"
  if [ -z "$ID" ]; then echo "Няма повече готови задачи (repo:$REPO)."; break; fi

  echo "=== [$i/$MAX] $ID ($(date +%H:%M:%S)) ==="
  LOG="logs/$(date +%Y%m%d-%H%M%S)-$ID.log"

  claude -p "Работи по задача $ID (repo:$REPO). Следвай AGENTS.md и документите, които задачата сочи.
ВАЖНО за командите: BEADS_DIR вече е зададен в средата — НЕ го export-вай и не го слагай пред командите
(AGENTS.md го споменава за ръчна работа, тук не е нужно). Пускай всяка команда проста, започваща с името си:
\`bd show ...\`, \`make check\`, \`uv run ...\` — без \`export\`, без \`VAR=... cmd\`, без \`cd\` и без \`&&\`/\`;\` вериги.
1) bd show $ID, после bd update $ID --claim.
2) Имплементирай; не измисляй колони/статуси/маршрути — при липса bd note $ID \"ВЪПРОС: …\" и спри.
3) Преди затваряне пусни проверките от AGENTS.md; затвори с bd close $ID --reason \"<файлове, тестове, брой>\" САМО ако всичко е зелено.
4) Не комитвай и не пушвай. Ако промених решение/отклонение от спецификацията, добави ред в HANDOFF.md.
5) Свърши само тази задача и спри.${PROMPT_EXTRA//__ID__/$ID}" \
    --model "$MODEL" \
    --allowedTools "$TOOLS" \
    --max-turns "$MAX_TURNS" 2>&1 | tee "$LOG"

  if [ "${PIPESTATUS[0]}" -ne 0 ]; then echo "claude излезе с грешка — спирам. Лог: $LOG"; exit 1; fi

  if ! bd show "$ID" 2>/dev/null | grep -qi 'closed'; then
    echo "Задача $ID не е затворена (грешка или ВЪПРОС) — спирам. Лог: $LOG"
    exit 1
  fi
  echo "✔ $ID затворена."
done
