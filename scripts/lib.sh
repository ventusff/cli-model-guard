#!/usr/bin/env bash
# model-guard shared helpers, sourced by statusline.sh, guard-hook.sh and
# recover.sh. Requires bash 4+ and jq. Everything is namespaced mg_*.
#
# Config: ~/.claude/model-guard.conf (KEY=VALUE per line). Band keys:
#   LANGUAGE=auto|en|zh|ja|ko|es|fr|de|pt   band and hook language; auto follows
#                               the "language" key in settings.json, else English
#   EXPECTED_MODEL=<pattern>    grep -Ei pattern the session model must match
#                               (default: the "model" saved in settings.json)
#   SHOW_ACCOUNT=true|false     logged-in account email on the band (default true)
#   SHOW_CONTEXT=true|false     context-window usage (default true)
#   SHOW_LIMIT=true|false       the account's 5-hour and 7-day usage (default true)
#   SHOW_CWD=true|false         the session's working directory, home as ~ (default true)
#   LIMIT_WARN_AT=<0-100|off>   red patch when 5-hour usage reaches N (default 80)
# Recovery keys:
#   RECOVER=on|off              master switch for the hooks (default on)
#   RECOVER_MODEL=<model id>    model to switch to after an automatic downgrade
#                               (default claude-opus-5[1m])
#   RECOVER_EFFORT=<level|off>  effort applied on the recovery model (default max)
#   RECOVER_PROMPT=<text>       prompt sent to resume the interrupted task
#                               (default: the band language's "Continue.")
#   RECOVER_CHANNEL=auto|tmux|zellij|kitty|dryrun|none
#                               how keystrokes reach the session (default auto:
#                               tmux pane, then zellij pane, then kitty remote
#                               control)
#   RECOVER_MAX=<n>             automatic recoveries per session (default 3)
#   DEBUG=true                  append every hook input to <state dir>/debug.log
#
# State: one JSON file per session under $XDG_RUNTIME_DIR/model-guard
# (override with MODEL_GUARD_STATE_DIR). status is one of
#   pending    downgraded with a keystroke channel; the recovery driver is starting
#   switching  the recovery driver is typing the model switch
#   recovered  the session model changed after the downgrade (driver or by hand)
#   stopped    downgraded, no automatic switch: the turn is stopped once
#              (turn_stopped) and the hooks stay out of the way afterwards;
#              note says why (no_channel, target_flagged, too_many_recoveries,
#              downgraded_again, target_not_stronger, switch_not_observed)
#
# The installed copy under ~/.claude/model-guard/ is what Claude Code runs for
# the statusline; check-install.sh refreshes it when MG_VERSION moves.

MG_VERSION="1.10.0"
MG_CONF="${MODEL_GUARD_CONF:-$HOME/.claude/model-guard.conf}"
MG_SETTINGS="${MODEL_GUARD_SETTINGS:-$HOME/.claude/settings.json}"
MG_STATE_DIR="${MODEL_GUARD_STATE_DIR:-${XDG_RUNTIME_DIR:-${TMPDIR:-/tmp}}/model-guard}"
MG_SESSIONS_DIR="${MODEL_GUARD_SESSIONS_DIR:-$HOME/.claude/sessions}"

# shellcheck source=text.sh
. "$(dirname "${BASH_SOURCE[0]}")/text.sh"

# ---- config ----
mg_conf_get() {
  [ -f "$MG_CONF" ] || return 0
  sed -n "s/^[[:space:]]*$1=//p" "$MG_CONF" | tail -n1 | tr -d '" '
}

mg_conf_or() {
  local v
  v=$(mg_conf_get "$1")
  printf '%s' "${v:-$2}"
}

# mg_conf_on KEY: false only when the key is set to false.
mg_conf_on() { [ "$(mg_conf_get "$1")" != false ]; }

mg_debug() {
  [ "$(mg_conf_get DEBUG)" = true ] || return 0
  mkdir -p "$MG_STATE_DIR"
  printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$*" >> "$MG_STATE_DIR/debug.log"
}

# ---- language: conf override > Claude Code "language" setting > English ----
# Resolved once per process; every mg_text call reads the cached answer.
mg_lang() {
  if [ -n "${MG_LANG:-}" ]; then printf '%s' "$MG_LANG"; return; fi
  local lang settings_lang
  lang=$(mg_conf_get LANGUAGE)
  [ "${lang:-auto}" = auto ] && lang=""
  if [ -z "$lang" ]; then
    settings_lang=$(jq -r '.language // empty' "$MG_SETTINGS" 2>/dev/null || true)
    case "$(printf '%s' "$settings_lang" | tr '[:upper:]' '[:lower:]')" in
      *chinese*|*中文*|zh|zh[-_]*)     lang=zh;;
      *japanese*|*日本語*|ja|ja[-_]*)  lang=ja;;
      *korean*|*한국*|ko|ko[-_]*)      lang=ko;;
      *spanish*|*español*|es|es[-_]*)  lang=es;;
      *french*|*français*|fr|fr[-_]*)  lang=fr;;
      *german*|*deutsch*|de|de[-_]*)   lang=de;;
      *portug*|pt|pt[-_]*)             lang=pt;;
      *)                               lang=en;;
    esac
  fi
  MG_LANG=$lang
  printf '%s' "$lang"
}

# ---- model identity ----
mg_strip_1m() { printf '%s' "$1" | sed 's/\[1[mM]\]//g'; }

# Strength score: family rank * 1000 + major * 100 + minor.
# fable/mythos > opus > sonnet > haiku; unknown family scores 0.
# claude-opus-4-8 -> 3408, claude-opus-5 -> 3500, claude-fable-5-1[1m] -> 4501.
mg_model_score() {
  local id fam=0 rest major=0 minor=0
  id=$(mg_strip_1m "$1" | tr '[:upper:]' '[:lower:]')
  case "$id" in
    *fable*|*mythos*) fam=4;;
    *opus*)           fam=3;;
    *sonnet*)         fam=2;;
    *haiku*)          fam=1;;
    *)                printf '0'; return;;
  esac
  rest=$(printf '%s' "$id" | sed -E 's/^.*(fable|mythos|opus|sonnet|haiku)-?//')
  if [[ "$rest" =~ ^([0-9]+)(-([0-9]{1,2}))? ]]; then
    major=${BASH_REMATCH[1]}
    minor=${BASH_REMATCH[3]:-0}
  fi
  printf '%d' $((fam * 1000 + major * 100 + minor))
}

# True when TO is weaker than FROM. Unknown FROM never counts as a downgrade;
# unknown TO always does (it cannot be proven stronger).
mg_is_downgrade() {
  local sf st
  sf=$(mg_model_score "$1"); st=$(mg_model_score "$2")
  [ "$sf" -gt 0 ] || return 1
  [ "$st" -gt 0 ] || return 0
  [ "$st" -lt "$sf" ]
}

mg_same_model() {
  [ "$(mg_strip_1m "$1" | tr '[:upper:]' '[:lower:]')" = "$(mg_strip_1m "$2" | tr '[:upper:]' '[:lower:]')" ]
}

# "claude-opus-4-8" -> "Opus 4.8", "claude-opus-5[1m]" -> "Opus 5 (1M)".
mg_display_name() {
  local id base fam ver one_m=""
  id="$1"
  case "$id" in *\[1[mM]\]*) one_m=" (1M)";; esac
  base=$(mg_strip_1m "$id" | tr '[:upper:]' '[:lower:]')
  if [[ "$base" =~ (fable|mythos|opus|sonnet|haiku)-?([0-9]+(-[0-9]{1,2})?) ]]; then
    fam=${BASH_REMATCH[1]}; ver=${BASH_REMATCH[2]//-/.}
    printf '%s %s%s' "${fam^}" "$ver" "$one_m"
  else
    printf '%s' "$id"
  fi
}

# Effort strength: xhigh(4) > high(3) > medium(2) > low(1); anything else 0.
mg_effort_rank() {
  case "${1,,}" in
    xhigh) echo 4;; high) echo 3;; medium) echo 2;; low) echo 1;; *) echo 0;;
  esac
}

mg_settings_model() { jq -r '.model // empty' "$MG_SETTINGS" 2>/dev/null || true; }

# The saved effort default for MODEL. /model and /effort save the current
# model's default in modelSettings; the old global effortLevel can stay behind.
# Context variants share the canonical key, so "[1m]" is stripped first.
mg_settings_effort() {
  jq -r --arg model "$1" '
    def saved_effort: select(. == "low" or . == "medium" or . == "high" or . == "xhigh");
    (.modelSettings | if type == "object" then . else {} end) as $models |
    ($model | sub("\\[1[mM]\\]$"; "")) as $key |
    ($models[$key].effortLevel? | saved_effort) //
    ($models[$model].effortLevel? | saved_effort) //
    (.effortLevel | saved_effort) // empty
  ' "$MG_SETTINGS" 2>/dev/null || true
}

# ---- per-session state ----
mg_state_file() { printf '%s/%s.json' "$MG_STATE_DIR" "$1"; }
mg_state_read() { cat "$(mg_state_file "$1")" 2>/dev/null || printf '{}'; }
mg_state_get() { mg_state_read "$1" | jq -r --arg k "$2" 'if has($k) and .[$k] != null then .[$k] else empty end'; }

# mg_state_update SID JQ_FILTER [jq args...]: read-modify-write under a lock.
mg_state_update() {
  local sid="$1" filter="$2"; shift 2
  mkdir -p "$MG_STATE_DIR"; chmod 700 "$MG_STATE_DIR" 2>/dev/null || true
  local f lock tmp
  f=$(mg_state_file "$sid"); lock="$f.lock"; tmp="$f.tmp.$$"
  (
    flock -w 5 9 || exit 1
    mg_state_read "$sid" | jq "$@" "$filter" > "$tmp" && mv "$tmp" "$f"
  ) 9>"$lock"
}

mg_state_clear() {
  local f
  f=$(mg_state_file "$1")
  rm -f "$f" "$f.lock" "$f.tmp."* 2>/dev/null || true
}

# ---- session registry (Claude Code's own ~/.claude/sessions/<pid>.json) ----
mg_session_registry_file() {
  local sid="$1" f
  for f in "$MG_SESSIONS_DIR"/*.json; do
    [ -f "$f" ] || continue
    if [ "$(jq -r '.sessionId // empty' "$f" 2>/dev/null)" = "$sid" ]; then
      printf '%s' "$f"; return 0
    fi
  done
  return 1
}

mg_session_status() {
  local f
  f=$(mg_session_registry_file "$1") || return 1
  jq -r '.status // empty' "$f" 2>/dev/null
}

# ---- keystroke channel ----
mg_kitten() {
  local k
  if k=$(command -v kitten 2>/dev/null); then printf '%s' "$k"; return 0; fi
  k=$(command -v kitty 2>/dev/null) || return 1
  k=$(readlink -f "$k")
  [ -x "$(dirname "$k")/kitten" ] || return 1
  printf '%s' "$(dirname "$k")/kitten"
}

# The zellij pane this process runs in, as an "action --pane-id" target.
# Fails when the id is absent or not numeric: without a proven target the
# channel must stay unavailable rather than type into someone else's pane.
mg_zellij_pane() {
  case "${ZELLIJ_PANE_ID:-}" in
    ""|*[!0-9]*) return 1;;
    *)           printf 'terminal_%s' "$ZELLIJ_PANE_ID";;
  esac
}

# The keystroke channel for the session this hook runs in. Inner multiplexers
# win over the outer terminal: they address one pane by id, while kitty remote
# control reaches a window whose focused pane may be a different session.
mg_channel() {
  local want
  want=$(mg_conf_or RECOVER_CHANNEL auto)
  case "$want" in
    none|off|false) printf 'none'; return;;
    dryrun)         printf 'dryrun'; return;;
  esac
  if [ "$want" = auto ] || [ "$want" = tmux ]; then
    if [ -n "${TMUX:-}" ] && [ -n "${TMUX_PANE:-}" ] && command -v tmux >/dev/null 2>&1; then
      printf 'tmux'; return
    fi
  fi
  if [ "$want" = auto ] || [ "$want" = zellij ]; then
    if [ -n "${ZELLIJ_SESSION_NAME:-}" ] && mg_zellij_pane >/dev/null 2>&1 && \
       command -v zellij >/dev/null 2>&1; then
      printf 'zellij'; return
    fi
  fi
  if [ "$want" = auto ] || [ "$want" = kitty ]; then
    if [ -n "${KITTY_LISTEN_ON:-}" ] && [ -n "${KITTY_WINDOW_ID:-}" ] && mg_kitten >/dev/null 2>&1; then
      printf 'kitty'; return
    fi
  fi
  printf 'none'
}

# mg_notify TITLE BODY [urgency]: desktop notice when notify-send exists.
mg_notify() {
  command -v notify-send >/dev/null 2>&1 || return 0
  notify-send -a model-guard -u "${3:-normal}" "$1" "$2" >/dev/null 2>&1 || true
}

# ---- default-model restore ----
# "/model <id>" in an interactive session also saves <id> as the user's default
# model in settings.json. When the switch was typed by the recovery driver that
# side effect is unwanted: this puts the previously saved default back once
# Claude Code's asynchronous settings write has landed.
mg_restore_default_model() {
  local sid="$1" state want target cur="" i tmp
  state=$(mg_state_read "$sid")
  want=$(jq -r '.default_model // empty' <<<"$state")
  target=$(jq -r '.target_model // empty' <<<"$state")
  [ -n "$want" ] && [ -n "$target" ] || return 0
  [ "$(jq -r '.recovered_by // empty' <<<"$state")" = auto ] || return 0
  mg_same_model "$want" "$target" && return 0
  for i in $(seq 1 30); do
    cur=$(mg_settings_model)
    mg_same_model "$cur" "$target" && break
    sleep 0.1
  done
  mg_same_model "$cur" "$target" || return 0
  tmp="$MG_SETTINGS.mg-tmp.$$"
  if jq --arg m "$want" '.model=$m' "$MG_SETTINGS" > "$tmp" && mv "$tmp" "$MG_SETTINGS"; then
    mg_state_update "$sid" '.default_restored=true'
    mg_debug "restored settings model $cur -> $want"
  else
    rm -f "$tmp"
  fi
}
