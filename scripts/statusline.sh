#!/usr/bin/env bash
# model-guard — a Claude Code statusline that catches silent model downgrades.
# https://github.com/ventusff/cli-model-guard
#
# One full-width, theme-proof color band: model + reasoning effort + context + usage +
# working directory + account.
#   green ✔   actual model matches what this machine expects
#   blue  ⬆   actual model is ABOVE your default (gentle FYI, no alarm)
#   red   🚨  actual model is BELOW your default (silent downgrade) — full-width alarm
#   blue  ●   no expectation configured — neutral display
# Red inline patches for the other silent downgrades: reasoning effort lowered
# below the active model's saved default, thinking switched off, and a heads-up
# when the 5-hour rate-limit window fills up (which is exactly when forced
# fallbacks happen). The usage numbers are the logged-in account's own, asked
# from Claude Code's usage endpoint (see "usage of the logged-in account" below).
#
# Expected-model resolution (first hit wins):
#   1. EXPECTED_MODEL in ~/.claude/model-guard.conf   (grep -Ei pattern, manual override)
#   2. ~/.claude/statusline-expected-model            (legacy override file)
#   3. "model" in ~/.claude/settings.json             ("[1m]"-style suffix stripped;
#                                                      "default" counts as no expectation)
# Model and effort strength, languages, config keys: lib.sh and text.sh, which
# sit next to this script and are sourced below.
#
# Colors are truecolor, with a fixed 256-color-cube fallback when COLORTERM says no.
# Plain ANSI 16-color codes get remapped by terminal themes and the contrast collapses
# (red turns pink). All fg/bg pairs are WCAG >= 7:1 (AAA):
#   OK    #000000 on #3FB950 = 8.3:1    ALARM #FFFFFF on #B00020 = 7.3:1
#   INFO  #FFFFFF on #0D47A1 = 8.6:1    RECOV #000000 on #FFB300 = 11.4:1
#
# Recovery episodes (plugin hooks, see lib.sh) are read from the per-session state
# file and get their own band:
#   red    🚨 downgraded by a flag: stopped, or switching to the recovery model
#   amber  🔁 recovered: running on the recovery model after a flag
#
# Debug: add   printf '%s' "$input" > ~/.claude/model-guard-last-input.json
# right after the `input=$(cat ...)` line to inspect the full stdin payload
# (model / effort / thinking / context_window / rate_limits / workspace / fast_mode / ...).

set -u
# shellcheck source=lib.sh
. "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lib.sh"
input=$(cat 2>/dev/null || true)

R=$'\033[0m'
case "${COLORTERM:-}" in
  truecolor|24bit)
    OK=$'\033[1;38;2;0;0;0;48;2;63;185;80m'         # black on #3FB950
    ALARM=$'\033[1;38;2;255;255;255;48;2;176;0;32m' # white on #B00020
    INFO=$'\033[1;38;2;255;255;255;48;2;13;71;161m' # white on #0D47A1
    RECOV=$'\033[1;38;2;0;0;0;48;2;255;179;0m'      # black on #FFB300
    ;;
  *)  # fixed 256-color-cube approximations (also theme-proof):
      # 16=#000 231=#fff 77=#5fd75f 124=#af0000 25=#005faf 214=#ffaf00
    OK=$'\033[1;38;5;16;48;5;77m'
    ALARM=$'\033[1;38;5;231;48;5;124m'
    INFO=$'\033[1;38;5;231;48;5;25m'
    RECOV=$'\033[1;38;5;16;48;5;214m'
    ;;
esac

if ! command -v jq >/dev/null 2>&1; then
  printf '%s' "${INFO} ● model-guard: jq is required$(printf '%300s' '')${R}"
  exit 0
fi

# Claude Code keeps .claude.json next to its config dir's parent and the credentials
# inside the config dir; both move with CLAUDE_CONFIG_DIR.
CLAUDE_JSON="${CLAUDE_CONFIG_DIR:-$HOME}/.claude.json"
CREDENTIALS="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.credentials.json"

session_id=$(printf '%s' "$input" | jq -r '.session_id // empty' 2>/dev/null || true)
model_id=$(printf '%s' "$input" | jq -r '.model.id // "unknown"' 2>/dev/null || echo unknown)
model_name=$(printf '%s' "$input" | jq -r '.model.display_name // .model.id // "unknown"' 2>/dev/null || echo unknown)
effort_level=$(printf '%s' "$input" | jq -r '.effort.level // empty' 2>/dev/null || true)
thinking_off=$(printf '%s' "$input" | jq -r 'if .thinking.enabled == false then "1" else "" end' 2>/dev/null || true)
ctx_pct=$(printf '%s' "$input" | jq -r '.context_window.used_percentage // empty | if type=="number" then floor else empty end' 2>/dev/null || true)
seen_5h=$(printf '%s' "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty | if type=="number" then floor else empty end' 2>/dev/null || true)
seen_7d=$(printf '%s' "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty | if type=="number" then floor else empty end' 2>/dev/null || true)
# The session's working directory, on one line whatever the path contains.
cwd=$(printf '%s' "$input" | jq -r '.workspace.current_dir // .cwd // empty | gsub("[[:cntrl:]]"; "?")' 2>/dev/null || true)
email=$(jq -r '.oauthAccount.emailAddress // empty' "$CLAUDE_JSON" 2>/dev/null || true)
expected_effort=$(mg_settings_effort "$model_id")
state=""
[ -n "$session_id" ] && state=$(mg_state_read "$session_id")
[ "$state" = '{}' ] && state=""

# ---- usage of the logged-in account ----
# Asked from Claude Code's own usage endpoint (the one behind /usage) with the login
# token Claude Code will use for its next request, so the number always belongs to the
# account that request bills. The payload's rate_limits is not that: it is what this
# one process last read from a response header, so it stands still until this session
# gets another response, and it survives /login — after an account switch it keeps
# reporting the previous account.
# One cache per machine, shared by every session and keyed by the token (a truncated
# hash, never the token itself): a new login is a new key, so the next refresh asks
# again at once, and until the answer is in the segment stays empty rather than showing
# another account's number. The token is read where Claude Code stores it and is never
# refreshed here: refresh tokens rotate, and racing Claude Code for one logs it out.
USAGE_URL="https://api.anthropic.com/api/oauth/usage"
USAGE_CACHE="$MG_STATE_DIR/usage.json"
USAGE_TTL=30    # seconds a reading is shared before it is asked again
USAGE_WAIT=3    # seconds one fetch may hold up the statusline

sha256_16() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum; else shasum -a 256; fi | cut -c1-16
}

# The login token for the next request: MODEL_GUARD_CREDENTIALS (tests), else the
# environment, the macOS keychain item Claude Code writes, the credentials file.
oauth_token() {
  local blob=""
  if [ -n "${MODEL_GUARD_CREDENTIALS:-}" ]; then
    blob=$(cat "$MODEL_GUARD_CREDENTIALS" 2>/dev/null || true)
  elif [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
    printf '%s' "$CLAUDE_CODE_OAUTH_TOKEN"; return 0
  else
    if [ "$(uname -s)" = Darwin ] && command -v security >/dev/null 2>&1; then
      local service="Claude Code-credentials"
      [ -n "${CLAUDE_CONFIG_DIR:-}" ] && service+="-$(printf '%s' "$CLAUDE_CONFIG_DIR" | sha256_16 | cut -c1-8)"
      blob=$(security find-generic-password -a "${USER:-claude-code-user}" -w -s "$service" 2>/dev/null || true)
    fi
    [ -n "$blob" ] || blob=$(cat "$CREDENTIALS" 2>/dev/null || true)
  fi
  [ -n "$blob" ] && printf '%s' "$blob" | jq -r '.claudeAiOauth.accessToken // empty' 2>/dev/null
  return 0
}

# Writes the cache for one login. HTTP 200 with a JSON object: the two utilisations (null
# for a window the plan does not have) and the whole answer as `body`, every window the
# endpoint reports, for other tools on the machine. Anything else: only the status (a
# failure is not asked again before the TTL runs out either).
usage_fetch() {  # <token> <key> <now>
  local body="$USAGE_CACHE.$$.body" tmp="$USAGE_CACHE.$$" code
  code=$(printf 'header = "Authorization: Bearer %s"\n' "$1" | curl -sS -K - --max-time "$USAGE_WAIT" \
           -A "model-guard/$MG_VERSION" -o "$body" -w '%{http_code}' "$USAGE_URL" 2>/dev/null) || code=000
  if [ "$code" = 200 ] && jq -e 'type == "object"' "$body" >/dev/null 2>&1; then
    jq -c --arg key "$2" --argjson at "$3" \
      'def pct: if type == "number" then floor else null end;
       {key: $key, at: $at, http: 200, five_hour: (.five_hour.utilization | pct),
        seven_day: (.seven_day.utilization | pct), resets_at: .five_hour.resets_at, body: .}' "$body" > "$tmp" 2>/dev/null
  else
    jq -nc --arg key "$2" --argjson at "$3" --arg http "$code" \
      '{key: $key, at: $at, http: (($http | tonumber?) // 0)}' > "$tmp" 2>/dev/null
  fi
  mv -f "$tmp" "$USAGE_CACHE" 2>/dev/null; rm -f "$body" "$tmp"
}

# Prints the cached reading of the current login (JSON), asking first when the cache is
# missing, belongs to another login, or is older than USAGE_TTL. Prints nothing while
# another session is asking for a new login. Returns 1 when there is no login token
# (API key, third-party providers).
usage_reading() {
  local tok key now cached at lock lock_at
  tok=$(oauth_token); [ -n "$tok" ] || return 1
  key=$(printf '%s' "$tok" | sha256_16)
  now=$(date +%s)
  cached=$(cat "$USAGE_CACHE" 2>/dev/null || true)
  at=$(printf '%s' "$cached" | jq -r '.at // 0' 2>/dev/null)
  if [ "$(printf '%s' "$cached" | jq -r '.key // empty' 2>/dev/null)" != "$key" ] || \
     [ $(( now - ${at:-0} )) -ge "$USAGE_TTL" ]; then
    lock="$USAGE_CACHE.lock"
    mkdir -p "$MG_STATE_DIR" 2>/dev/null && chmod 700 "$MG_STATE_DIR" 2>/dev/null
    # a fetch killed half-way leaves its lock behind; older than a fetch can take = stale
    lock_at=$(cat "$lock/at" 2>/dev/null || true)
    if [ -d "$lock" ] && [ $(( now - ${lock_at:-0} )) -gt $(( USAGE_WAIT + 2 )) ]; then
      rm -rf "$lock"
    fi
    if mkdir "$lock" 2>/dev/null; then
      printf '%s' "$now" > "$lock/at"
      usage_fetch "$tok" "$key" "$now"
      rm -rf "$lock"
      cached=$(cat "$USAGE_CACHE" 2>/dev/null || true)
    fi
  fi
  printf '%s' "$cached" | jq -c --arg key "$key" 'select(.key == $key)' 2>/dev/null
  return 0
}

# What the band shows: the account's own numbers when a login token is at hand, else
# what this session last saw in a response header.
if usage=$(usage_reading); then
  limit_5h=$(printf '%s' "$usage" | jq -r '.five_hour // empty' 2>/dev/null)
  limit_7d=$(printf '%s' "$usage" | jq -r '.seven_day // empty' 2>/dev/null)
else
  limit_5h=$seen_5h; limit_7d=$seen_7d
fi

# ---- expected model ----
expected=$(mg_conf_get EXPECTED_MODEL)
if [ -z "$expected" ] && [ -f "$HOME/.claude/statusline-expected-model" ]; then
  expected=$(head -n1 "$HOME/.claude/statusline-expected-model" | tr -d '[:space:]')
fi
if [ -z "$expected" ]; then
  cfg=$(mg_settings_model)
  cfg=${cfg%%\[*}
  case "$cfg" in ""|default) ;; *) expected="$cfg";; esac
fi

# ---- pick the band + main text ----
# A recovery episode (plugin hooks) overrides the plain model comparison.
is_alarm=""; band=""; head_txt=""
if [ -n "$state" ]; then
  st_status=$(printf '%s' "$state" | jq -r '.status // empty' 2>/dev/null || true)
  st_from=$(mg_display_name "$(printf '%s' "$state" | jq -r '.from_model // empty')")
  st_to=$(mg_display_name "$(printf '%s' "$state" | jq -r '.to_model // empty')")
  st_target=$(mg_display_name "$(printf '%s' "$state" | jq -r '.target_model // empty')")
  st_note=$(printf '%s' "$state" | jq -r '.note // empty')
  st_recovered_to=$(printf '%s' "$state" | jq -r '.recovered_to // empty')
  case "$st_status" in
    pending|switching) band=$ALARM; is_alarm=1; head_txt=$(mg_text band_switch "$st_from" "$st_to" "$st_target");;
    stopped)   band=$ALARM; is_alarm=1
               case "$st_note" in
                 target_flagged|downgraded_again|too_many_recoveries|target_not_stronger)
                   head_txt=$(mg_text band_halted "$st_to");;
                 *) head_txt=$(mg_text band_manual "$st_from" "$st_to" "$st_target");;
               esac;;
    recovered) if mg_same_model "$model_id" "$st_recovered_to"; then
                 band=$RECOV; head_txt=$(mg_text band_recov "$st_from" "$(mg_display_name "$st_recovered_to")")
               fi;;
  esac
fi
if [ -z "$band" ]; then
  if [ -z "$expected" ]; then
    band=$INFO; head_txt="● ${model_name} · ${model_id}"
  elif printf '%s' "$model_id" | grep -qiE -- "$expected"; then
    band=$OK; head_txt="✔ ${model_name} · ${model_id}"
  else
    as=$(mg_model_score "$model_id"); es=$(mg_model_score "$expected")
    if [ "$as" -gt 0 ] && [ "$es" -gt 0 ] && [ "$as" -gt "$es" ]; then
      band=$INFO
      head_txt=$(mg_text band_up "$model_name" "$model_id" "${expected^^}")
    else
      band=$ALARM; is_alarm=1
      head_txt=$(mg_text band_down "$model_name" "$model_id" "${expected^^}")
    fi
  fi
fi

# ---- effort / thinking / context / rate-limit segments ----
seg=""
if [ -n "$effort_level" ]; then
  if [ -n "$expected_effort" ] && [ "$(mg_effort_rank "$effort_level")" -gt 0 ] && \
     [ "$(mg_effort_rank "$effort_level")" -lt "$(mg_effort_rank "$expected_effort")" ]; then
    seg+=" ${ALARM} $(mg_text seg_effort "$effort_level" "$expected_effort") ${band}"
  else
    seg+=" ┃ ⚡${effort_level}"
  fi
fi
if [ -n "$thinking_off" ]; then
  seg+=" ${ALARM} $(mg_text seg_think) ${band}"
fi

if mg_conf_on SHOW_CONTEXT && [ -n "$ctx_pct" ]; then
  seg+=" ┃ ◔ ${ctx_pct}%"
fi

warn_at=$(mg_conf_get LIMIT_WARN_AT)
case "${warn_at:-80}" in
  off|OFF|Off) warn_at="";;
  *[!0-9]*|"") warn_at=80;;
  *)           warn_at=${warn_at:-80};;
esac
if [ -n "$warn_at" ] && [ -n "$limit_5h" ] && [ "$limit_5h" -ge "$warn_at" ] 2>/dev/null; then
  seg+=" ${ALARM} $(mg_text seg_limit "$limit_5h") ${band}"
elif mg_conf_on SHOW_LIMIT && [ -n "$limit_5h" ]; then
  seg+=" ┃ ⏳ 5h ${limit_5h}%"
  [ -n "$limit_7d" ] && seg+=" · 7d ${limit_7d}%"
fi

if mg_conf_on SHOW_CWD && [ -n "$cwd" ]; then
  case "$cwd" in
    "$HOME")   cwd="~";;
    "$HOME"/*) cwd="~${cwd#"$HOME"}";;
  esac
  seg+=" ┃ 📁 ${cwd}"
fi

acct_seg=""
if mg_conf_on SHOW_ACCOUNT; then
  [ -n "${email:-}" ] || email=$(mg_text acct_unknown)
  acct_seg=" ┃ 👤 ${email}"
fi

# Over-wide padding: anything past the terminal width gets clipped by the TUI,
# so the band spans the full row at any terminal size.
if [ -n "$is_alarm" ] && [ -z "$state" ]; then
  tail_pad=" ┃ $(mg_text band_back)$(printf '🚨 %.0s' $(seq 1 80))"
elif [ -n "$is_alarm" ]; then
  tail_pad="$(printf ' 🚨%.0s' $(seq 1 80))"
else
  tail_pad=$(printf '%300s' '')
fi

printf '%s' "${band} ${head_txt}${seg}${acct_seg}${tail_pad}${R}"
