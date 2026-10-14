#!/usr/bin/env bash
# model-guard recovery driver. Started detached by guard-hook.sh after an
# automatic downgrade; types into the session's own terminal:
#   Esc                         interrupt the turn the downgraded model is serving
#   /model <RECOVER_MODEL>      switch the session model
#   /effort <RECOVER_EFFORT>    raise reasoning effort on the recovery model
#   <RECOVER_PROMPT>            resume the interrupted task
# Keystroke channels: tmux (send-keys into $TMUX_PANE), zellij (action write
# into pane terminal_$ZELLIJ_PANE_ID of $ZELLIJ_SESSION_NAME), kitty (remote
# control over $KITTY_LISTEN_ON into window $KITTY_WINDOW_ID), dryrun (log only).
# The continue prompt is only sent after the PostModelSwitch hook has marked the
# session recovered, i.e. after Claude Code itself confirmed the switch.
# Log: <state dir>/<session id>.log
set -u
command -v jq >/dev/null 2>&1 || exit 0
plugin_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib.sh
. "$plugin_root/scripts/lib.sh"

sid="${1:-}"
[ -n "$sid" ] || exit 2
mkdir -p "$MG_STATE_DIR"
exec >>"$MG_STATE_DIR/$sid.log" 2>&1
say() { printf '%s %s\n' "$(date '+%H:%M:%S')" "$*"; }

state=$(mg_state_read "$sid")
status=$(jq -r '.status // empty' <<<"$state")
channel=$(jq -r '.channel // "none"' <<<"$state")
target=$(jq -r '.target_model // empty' <<<"$state")
effort=$(jq -r '.target_effort // empty' <<<"$state")
cont=$(jq -r '.continue // false' <<<"$state")
from=$(jq -r '.from_model // empty' <<<"$state")
to=$(jq -r '.to_model // empty' <<<"$state")
prompt=$(mg_conf_get RECOVER_PROMPT)
[ -n "$prompt" ] || prompt=$(mg_text prompt)
dfrom=$(mg_display_name "$from"); dto=$(mg_display_name "$to"); dtarget=$(mg_display_name "$target")

if [ "$status" != pending ] || [ -z "$target" ]; then
  say "nothing to do: status=$status target=$target"; exit 0
fi
kit=""; zpane=""
case "$channel" in
  kitty) kit=$(mg_kitten) || { say "kitten not found"; exit 1; };;
  tmux)  command -v tmux >/dev/null 2>&1 || { say "tmux not found"; exit 1; };;
  zellij)
    command -v zellij >/dev/null 2>&1 || { say "zellij not found"; exit 1; }
    zpane=$(mg_zellij_pane) || { say "no zellij pane id"; exit 1; }
    [ -n "${ZELLIJ_SESSION_NAME:-}" ] || { say "no zellij session name"; exit 1; };;
  dryrun) ;;
  *) say "no keystroke channel ($channel)"; exit 1;;
esac
say "recovery start: $from -> $to, target=$target effort=$effort channel=$channel continue=$cont"

# Interrupting a turn puts the interrupted prompt back into the input box, so
# a line typed next would be appended to it and submitted as one prompt. Every
# typed line therefore starts by emptying the box.
clear_input() {
  local i
  for i in 1 2 3; do send_key C-u ctrl+u 21; sleep 0.05; done
  send_key C-a ctrl+a 1; sleep 0.05
  send_key C-k ctrl+k 11; sleep 0.1
}

# send_key <tmux key name> <kitty key name> <byte zellij writes>
send_key() {
  case "$channel" in
    tmux)   tmux send-keys -t "$TMUX_PANE" "$1";;
    zellij) zellij --session "$ZELLIJ_SESSION_NAME" action write --pane-id "$zpane" "$3";;
    kitty)  "$kit" @ --to "$KITTY_LISTEN_ON" send-key --match "id:$KITTY_WINDOW_ID" "$2";;
    dryrun) say "KEY $2";;
  esac
}
send_text() {
  local text="${1//\\/\\\\}"
  case "$channel" in
    tmux)   tmux send-keys -t "$TMUX_PANE" -l "$1";;
    zellij) zellij --session "$ZELLIJ_SESSION_NAME" action write-chars --pane-id "$zpane" -- "$1";;
    kitty)  "$kit" @ --to "$KITTY_LISTEN_ON" send-text --match "id:$KITTY_WINDOW_ID" "$text";;
    dryrun) say "TEXT $1";;
  esac
}
send_line() { clear_input; send_text "$1"; sleep 0.15; send_key Enter enter 13; }

# The session transcript is the acknowledgement channel: every local command
# and every submitted prompt lands there, so each keystroke group is retried
# until its echo shows up (never with the dryrun channel).
transcript=$(jq -r '.transcript_path // empty' <<<"$state")
[ -f "$transcript" ] || transcript=$(ls "$HOME"/.claude/projects/*/"$sid".jsonl 2>/dev/null | head -n1)
tcount() { if [ -f "$transcript" ]; then grep -cF -- "$1" "$transcript" || true; else printf '0'; fi; }
wait_marker() {
  local pat="$1" secs="$2" before="$3" i
  [ "$channel" = dryrun ] && return 0
  for i in $(seq 1 $((secs * 5))); do
    [ "$(tcount "$pat")" -gt "$before" ] && return 0
    sleep 0.2
  done
  return 1
}
# type_line TEXT MARKER SECS: type TEXT + Enter and wait for MARKER in the
# transcript. Without an echo, first resend Enter alone (the text may be sitting
# in the input box), then retype the whole line once.
type_line() {
  local text="$1" marker="$2" secs="$3" before
  before=$(tcount "$marker")
  send_line "$text"
  wait_marker "$marker" "$secs" "$before" && { sleep 1; return 0; }
  say "no transcript echo for '$text' within ${secs}s; resending Enter"
  send_key Enter enter 13
  wait_marker "$marker" 3 "$before" && { sleep 1; return 0; }
  say "still no echo; retyping"
  wait_idle 5
  send_line "$text"
  wait_marker "$marker" "$secs" "$before" && { sleep 1; return 0; }
  return 1
}

# Wait until Claude Code's own session registry says the session is no longer
# busy (idle, or only a background shell command running).
wait_idle() {
  local secs="$1" i st
  for i in $(seq 1 $((secs * 5))); do
    st=$(mg_session_status "$sid" 2>/dev/null) || { sleep 1.5; return 0; }
    [ "$st" != busy ] && return 0
    sleep 0.2
  done
  say "session still busy after ${secs}s; typing anyway"
}
# Wait until the state file reaches STATUS; fail when it leaves "switching".
wait_state() {
  local want="$1" secs="$2" i st
  for i in $(seq 1 $((secs * 5))); do
    st=$(mg_state_get "$sid" status)
    [ "$st" = "$want" ] && return 0
    [ "$st" = switching ] || { say "state left switching: $st"; return 1; }
    sleep 0.2
  done
  return 1
}

busy_before=$(mg_session_status "$sid" 2>/dev/null || printf unknown)
sleep 0.2
mg_episode_update "$sid" '.status="switching" | .switching_at=$at' --arg at "$(date -Is)" \
  || { say "episode ended before recovery"; exit 0; }
send_key Escape escape 27
say "sent Esc (session was $busy_before)"
wait_idle 10
sleep 0.5
model_before=$(tcount '<command-name>/model</command-name>')
mg_episode_update "$sid" '.typed_switch=true' || { say "episode ended before /model"; exit 0; }
send_line "/model $target"
say "sent /model $target"
if ! wait_state recovered 30; then
  st=$(mg_state_get "$sid" status)
  if [ "$st" = switching ]; then
    mg_episode_update "$sid" '.status="stopped" | .note="switch_not_observed" | .turn_stopped=true'
    say "model switch not observed within 30s; leaving the session stopped"
    mg_notify "$(mg_text notify_t)" "$(mg_text notify_fail "$dtarget" "switch not observed")"
  fi
  exit 1
fi
say "switch confirmed by PostModelSwitch"
wait_marker '<command-name>/model</command-name>' 10 "$model_before" || say "no /model echo in transcript"
sleep 1
if [ -n "$effort" ] && [ "$effort" != off ] && [ "$effort" != none ]; then
  wait_idle 6
  if type_line "/effort $effort" '<command-name>/effort</command-name>' 8; then
    say "sent /effort $effort"
  else
    say "effort command not acknowledged"
  fi
fi
mg_restore_default_model "$sid"
if [ "$cont" = true ]; then
  wait_idle 6
  marker="\"content\":$(jq -n --arg p "$prompt" '$p')"
  if type_line "$prompt" "$marker" 8; then
    say "sent continue prompt"
  else
    say "continue prompt not acknowledged"
    mg_notify "$(mg_text notify_t)" "$(mg_text notify_fail "$dtarget" "continue prompt not delivered")"
  fi
fi
mg_notify "$(mg_text notify_t)" "$(mg_text notify_done "$dtarget")" normal
say "done"
