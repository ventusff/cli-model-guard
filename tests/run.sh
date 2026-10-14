#!/usr/bin/env bash
# model-guard test suite: drives guard-hook.sh / recover.sh / statusline.sh with
# synthetic Claude Code payloads in an isolated state dir. Run: tests/run.sh
set -u
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
export MODEL_GUARD_STATE_DIR="$tmp/state" MODEL_GUARD_CONF="$tmp/model-guard.conf" \
       MODEL_GUARD_SETTINGS="$tmp/settings.json" MODEL_GUARD_SESSIONS_DIR="$tmp/sessions" \
       MODEL_GUARD_CREDENTIALS="$tmp/credentials.json" MODEL_GUARD_INSTALL_DIR="$tmp/.claude/model-guard"
unset KITTY_LISTEN_ON KITTY_WINDOW_ID TMUX TMUX_PANE ZELLIJ ZELLIJ_SESSION_NAME ZELLIJ_PANE_ID \
      CLAUDE_CODE_OAUTH_TOKEN CLAUDE_CONFIG_DIR
mkdir -p "$tmp/sessions"
printf 'LANGUAGE=en\nRECOVER_CHANNEL=dryrun\nRECOVER_MODEL=claude-opus-5[1m]\nRECOVER_EFFORT=max\nRECOVER_MAX=2\n' > "$MODEL_GUARD_CONF"
printf '{"model":"claude-fable-5-1[1m]","effortLevel":"xhigh"}\n' > "$MODEL_GUARD_SETTINGS"
. "$root/scripts/lib.sh"

pass=0; fail=0
ok()   { pass=$((pass+1)); printf '  ok   %s\n' "$1"; }
bad()  { fail=$((fail+1)); printf '  FAIL %s\n    %s\n' "$1" "${2:-}"; }
check(){ if eval "$2"; then ok "$1"; else bad "$1" "$2"; fi; }
hook() { jq -nc "$1" | "$root/scripts/guard-hook.sh"; }
switch(){ hook "{hook_event_name:\"PostModelSwitch\",session_id:\"$1\",from_model:\"$2\",to_model:\"$3\",requested_model:null,source:\"$4\"}"; }
pretool(){ hook "{hook_event_name:\"PreToolUse\",session_id:\"$1\",tool_name:\"Bash\",tool_input:{command:\"ls\"}}"; }
prompt(){ hook "{hook_event_name:\"UserPromptSubmit\",session_id:\"$1\",prompt:\"go on\"}"; }
status(){ mg_state_get "$1" status; }
wait_status(){ local i; for i in $(seq 1 100); do [ "$(status "$1")" = "$2" ] && return 0; sleep 0.1; done; return 1; }

echo "== automatic downgrade: stop, switch, restore default, continue"
s=s1
out=$(switch $s 'claude-fable-5-1[1m]' claude-opus-4-8 auto)
check "no hook output on PostModelSwitch(auto)" '[ -z "$out" ]'
check "state pending or switching" 'st=$(status $s); [ "$st" = pending ] || [ "$st" = switching ]'
check "default model captured" '[ "$(mg_state_get $s default_model)" = "claude-fable-5-1[1m]" ]'
out=$(pretool $s)
check "PreToolUse stops the turn" 'jq -e ".continue==false and (.hookSpecificOutput.permissionDecision==\"deny\")" <<<"$out" >/dev/null'
check "stop reason names both models" 'jq -r .stopReason <<<"$out" | grep -q "Fable 5.1 (1M).*Opus 4.8"'
check "driver reached switching" 'wait_status $s switching'
out=$(hook "{hook_event_name:\"PreModelSwitch\",session_id:\"$s\",from_model:\"claude-opus-4-8\",to_model:\"claude-opus-5[1m]\",requested_model:\"claude-opus-5[1m]\",source:\"command\"}")
check "PreModelSwitch allows the recovery switch (skips confirm dialog)" 'jq -e ".hookSpecificOutput.permissionDecision==\"allow\"" <<<"$out" >/dev/null'
out=$(hook "{hook_event_name:\"PreModelSwitch\",session_id:\"$s\",from_model:\"claude-opus-4-8\",to_model:\"claude-sonnet-5\",requested_model:\"sonnet\",source:\"command\"}")
check "PreModelSwitch stays silent for other targets" '[ -z "$out" ]'
out=$(prompt $s)
check "prompt blocked while switching" 'jq -e ".decision==\"block\"" <<<"$out" >/dev/null'
# wait for the driver to type /model, then play Claude Code: persist the model and fire PostModelSwitch(command)
for i in $(seq 1 60); do grep -q 'TEXT /model' "$MG_STATE_DIR/$s.log" 2>/dev/null && break; sleep 0.1; done
check "driver sent Esc then /model" 'grep -q "KEY escape" "$MG_STATE_DIR/$s.log" && grep -q "TEXT /model claude-opus-5\[1m\]" "$MG_STATE_DIR/$s.log"'
jq '.model="claude-opus-5[1m]"' "$MODEL_GUARD_SETTINGS" > "$tmp/x" && mv "$tmp/x" "$MODEL_GUARD_SETTINGS"
out=$(switch $s claude-opus-4-8 'claude-opus-5[1m]' command)
check "status recovered by auto" '[ "$(status $s)" = recovered ] && [ "$(mg_state_get $s recovered_by)" = auto ]'
check "settings.json default restored" '[ "$(jq -r .model "$MODEL_GUARD_SETTINGS")" = "claude-fable-5-1[1m]" ]'
for i in $(seq 1 100); do grep -q 'sent continue prompt' "$MG_STATE_DIR/$s.log" 2>/dev/null && break; sleep 0.1; done
check "driver sent /effort max then the prompt" 'grep -q "TEXT /effort max" "$MG_STATE_DIR/$s.log" && grep -q "TEXT Continue\." "$MG_STATE_DIR/$s.log"'
check "prompt allowed after recovery" '[ -z "$(prompt $s)" ]'
check "tools allowed after recovery" '[ -z "$(pretool $s)" ]'
out=$(switch $s 'claude-opus-5[1m]' 'claude-fable-5-1[1m]' command)
check "switching back to Fable clears the episode" '[ ! -e "$(mg_state_file $s)" ]'

echo "== driver timed out, user confirms the switch later: default still restored"
s=s1b
switch $s 'claude-fable-5-1[1m]' claude-opus-4-8 auto >/dev/null; wait_status $s switching
for i in $(seq 1 60); do grep -q 'TEXT /model' "$MG_STATE_DIR/$s.log" 2>/dev/null && break; sleep 0.1; done
mg_state_update $s '.status="stopped" | .note="switch_not_observed" | .turn_stopped=true'
jq '.model="claude-opus-5[1m]"' "$MODEL_GUARD_SETTINGS" > "$tmp/x" && mv "$tmp/x" "$MODEL_GUARD_SETTINGS"
switch $s claude-opus-4-8 'claude-opus-5[1m]' command >/dev/null
check "late confirmation still counts as automatic" '[ "$(mg_state_get $s recovered_by)" = auto ]'
check "default restored after late confirmation" '[ "$(jq -r .model "$MODEL_GUARD_SETTINGS")" = "claude-fable-5-1[1m]" ]'

echo "== resume-restored downgrade: switch without continue"
s=s2
switch $s 'claude-fable-5-1[1m]' claude-opus-4-8 resume >/dev/null
check "continue flag off for resume" '[ "$(mg_state_get $s continue)" = false ]'
wait_status $s switching
for i in $(seq 1 60); do grep -q 'TEXT /model' "$MG_STATE_DIR/$s.log" 2>/dev/null && break; sleep 0.1; done
switch $s claude-opus-4-8 'claude-opus-5[1m]' command >/dev/null
for i in $(seq 1 100); do grep -q ' done' "$MG_STATE_DIR/$s.log" 2>/dev/null && break; sleep 0.1; done
check "no continue prompt after resume" '! grep -q "TEXT Continue" "$MG_STATE_DIR/$s.log" && grep -q " done" "$MG_STATE_DIR/$s.log"'

echo "== the recovery model itself gets flagged: stop once, then hands off"
s=s3
switch $s claude-opus-5 claude-opus-4-8 auto >/dev/null
check "stopped with note target_flagged" '[ "$(status $s)" = stopped ] && [ "$(mg_state_get $s note)" = target_flagged ]'
out=$(pretool $s)
check "first tool call after the downgrade is stopped" 'jq -e ".continue==false" <<<"$out" >/dev/null'
check "stop reason says pick one with /model" 'jq -r .stopReason <<<"$out" | grep -q "pick one with /model"'
check "prompts are never blocked once stopped" '[ -z "$(prompt $s)" ]'
check "later tool calls pass" '[ -z "$(pretool $s)" ]'

echo "== no keystroke channel: plain stop"
s=s4
printf 'LANGUAGE=zh\nRECOVER_CHANNEL=none\n' > "$MODEL_GUARD_CONF"
switch $s 'claude-fable-5-1[1m]' claude-opus-4-8 auto >/dev/null
check "stopped with note no_channel" '[ "$(status $s)" = stopped ] && [ "$(mg_state_get $s note)" = no_channel ]'
check "no driver spawned" '[ ! -e "$MG_STATE_DIR/$s.log" ]'
out=$(pretool $s)
check "zh stop reason asks for /model" 'jq -r .stopReason <<<"$out" | grep -q "请 /model 切到 Opus 5 (1M)"'
check "turn marked stopped" '[ "$(mg_state_get $s turn_stopped)" = true ]'
check "next tool call passes" '[ -z "$(pretool $s)" ]'
check "prompts pass" '[ -z "$(prompt $s)" ]'
s=s4b
switch $s 'claude-fable-5-1[1m]' claude-opus-4-8 auto >/dev/null
hook "{hook_event_name:\"Stop\",session_id:\"$s\",stop_hook_active:false}" >/dev/null
check "a turn that ends by itself counts as stopped" '[ "$(mg_state_get $s turn_stopped)" = true ] && [ -z "$(pretool $s)" ]'
s=s4c
switch $s 'claude-fable-5-1[1m]' claude-opus-4-8 resume >/dev/null
check "resume without channel: nothing to stop" '[ "$(status $s)" = stopped ] && [ -z "$(pretool $s)" ]'
switch $s claude-opus-4-8 'claude-opus-5[1m]' picker >/dev/null
check "manual switch after a plain stop: recovered by user" '[ "$(status $s)" = recovered ] && [ "$(mg_state_get $s recovered_by)" = user ]'
printf 'LANGUAGE=en\nRECOVER_CHANNEL=dryrun\nRECOVER_MODEL=claude-opus-5[1m]\nRECOVER_EFFORT=max\nRECOVER_MAX=2\n' > "$MODEL_GUARD_CONF"

echo "== attempts cap"
s=s5
switch $s 'claude-fable-5-1[1m]' claude-opus-4-8 auto >/dev/null; wait_status $s switching
switch $s claude-opus-4-8 'claude-opus-5[1m]' command >/dev/null
switch $s 'claude-fable-5-1[1m]' claude-opus-4-8 auto >/dev/null; wait_status $s switching
switch $s claude-opus-4-8 'claude-opus-5[1m]' command >/dev/null
switch $s 'claude-fable-5-1[1m]' claude-opus-4-8 auto >/dev/null
check "third automatic downgrade only stops (RECOVER_MAX=2)" '[ "$(status $s)" = stopped ] && [ "$(mg_state_get $s note)" = too_many_recoveries ]'

echo "== non-downgrades are ignored"
s=s6
switch $s claude-sonnet-5 claude-opus-4-8 auto >/dev/null
check "automatic upgrade leaves no state" '[ ! -e "$(mg_state_file $s)" ]'
switch $s 'claude-fable-5-1[1m]' claude-opus-5 command >/dev/null
check "user switch without episode leaves no state" '[ ! -e "$(mg_state_file $s)" ]'

echo "== session lifecycle"
s=s7
switch $s 'claude-fable-5-1[1m]' claude-opus-4-8 auto >/dev/null
hook "{hook_event_name:\"SessionStart\",session_id:\"$s\",source:\"compact\"}" >/dev/null
check "compact keeps state" '[ -e "$(mg_state_file $s)" ]'
out=$(hook "{hook_event_name:\"SessionStart\",session_id:\"$s\",source:\"startup\"}")
check "startup clears state" '[ ! -e "$(mg_state_file $s)" ]'
check "session start is silent" '[ -z "$out" ]'
switch $s 'claude-fable-5-1[1m]' claude-opus-4-8 auto >/dev/null
hook "{hook_event_name:\"SessionEnd\",session_id:\"$s\",reason:\"other\"}" >/dev/null
check "session end clears state" '[ ! -e "$(mg_state_file $s)" ]'
check "a driver outliving its session writes nothing back" '! mg_episode_update $s ".status=\"switching\"" && [ ! -e "$(mg_state_file $s)" ] && [ ! -e "$(mg_state_file $s).lock" ]'
printf 'RECOVER=off\n' > "$MODEL_GUARD_CONF"
switch $s 'claude-fable-5-1[1m]' claude-opus-4-8 auto >/dev/null
check "RECOVER=off disables everything" '[ ! -e "$(mg_state_file $s)" ]'
printf 'LANGUAGE=en\n' > "$MODEL_GUARD_CONF"
out=$(hook "{hook_event_name:\"SessionStart\",session_id:\"$s\",source:\"startup\"}")
check "session start is silent without a channel too" '[ -z "$out" ]'

echo "== keystroke channel detection"
mkdir -p "$tmp/bin"
printf '#!/bin/sh\nexit 0\n' > "$tmp/bin/tmux"; chmod +x "$tmp/bin/tmux"
printf '#!/bin/sh\nexit 0\n' > "$tmp/bin/zellij"; chmod +x "$tmp/bin/zellij"
zj(){ PATH="$tmp/bin:$PATH" ZELLIJ_SESSION_NAME=cc ZELLIJ_PANE_ID="${1-2}" mg_channel; }
printf 'LANGUAGE=en\n' > "$MODEL_GUARD_CONF"
check "no multiplexer at all: none" '[ "$(mg_channel)" = none ]'
check "pane target is terminal_<id>" '[ "$(ZELLIJ_PANE_ID=3 mg_zellij_pane)" = terminal_3 ]'
check "a non-numeric pane id is refused" '! ZELLIJ_PANE_ID=plugin_2 mg_zellij_pane >/dev/null 2>&1'
check "zellij session plus pane id: zellij" '[ "$(zj 2)" = zellij ]'
check "zellij without a pane id: none" '[ "$(zj "")" = none ]'
check "tmux wins over zellij" '[ "$(PATH="$tmp/bin:$PATH" TMUX=/s TMUX_PANE=%1 ZELLIJ_SESSION_NAME=cc ZELLIJ_PANE_ID=2 mg_channel)" = tmux ]'
printf 'RECOVER_CHANNEL=none\n' > "$MODEL_GUARD_CONF"
check "RECOVER_CHANNEL=none overrides a live zellij" '[ "$(zj 2)" = none ]'
printf 'RECOVER_CHANNEL=kitty\n' > "$MODEL_GUARD_CONF"
check "RECOVER_CHANNEL=kitty ignores zellij" '[ "$(zj 2)" = none ]'

echo "== zellij channel: the keystrokes the driver actually sends"
s=s9
printf '#!/bin/sh\nprintf "%%s\\n" "$*" >> "$ZJ_LOG"\nexit 0\n' > "$tmp/bin/zellij"; chmod +x "$tmp/bin/zellij"
export ZJ_LOG="$tmp/zellij.log"; : > "$ZJ_LOG"
printf 'LANGUAGE=en\nRECOVER_MODEL=claude-opus-5[1m]\nRECOVER_EFFORT=max\n' > "$MODEL_GUARD_CONF"
printf '{"model":"claude-fable-5-1[1m]","effortLevel":"xhigh"}\n' > "$MODEL_GUARD_SETTINGS"
PATH="$tmp/bin:$PATH" ZELLIJ_SESSION_NAME=cc ZELLIJ_PANE_ID=2 \
  switch $s 'claude-fable-5-1[1m]' claude-opus-4-8 auto >/dev/null
check "episode records the zellij channel" '[ "$(mg_state_get $s channel)" = zellij ]'
for i in $(seq 1 150); do grep -qF -- "write --pane-id terminal_2 13" "$ZJ_LOG" 2>/dev/null && break; sleep 0.1; done
check "Esc goes to this pane as byte 27" 'grep -qxF -- "--session cc action write --pane-id terminal_2 27" "$ZJ_LOG"'
check "/model is typed into this pane" 'grep -qxF -- "--session cc action write-chars --pane-id terminal_2 -- /model claude-opus-5[1m]" "$ZJ_LOG"'
check "Enter goes to this pane as byte 13" 'grep -qxF -- "--session cc action write --pane-id terminal_2 13" "$ZJ_LOG"'
check "no keystroke addresses another pane" '! grep -v "terminal_2" "$ZJ_LOG" | grep -q pane-id'
mg_state_update $s '.status="stopped" | .note="test_teardown"'
unset ZJ_LOG
printf '#!/bin/sh\nexit 0\n' > "$tmp/bin/zellij"; chmod +x "$tmp/bin/zellij"

echo "== an unrecognised switch source counts as automatic"
s=s10
printf 'LANGUAGE=en\nRECOVER_CHANNEL=none\n' > "$MODEL_GUARD_CONF"
switch $s 'claude-fable-5-1[1m]' claude-opus-4-8 auto_recovery >/dev/null
check "source auto_recovery starts an episode" '[ "$(status $s)" = stopped ] && [ "$(mg_state_get $s note)" = no_channel ]'
check "and it continues the task like source auto" '[ "$(mg_state_get $s continue)" = true ]'
mg_state_clear $s
switch $s 'claude-fable-5-1[1m]' claude-opus-4-8 session_resume >/dev/null
check "source session_resume is treated as a resume" '[ "$(mg_state_get $s continue)" = false ]'
mg_state_clear $s
switch $s 'claude-fable-5-1[1m]' claude-opus-4-8 slash_command >/dev/null
check "a user-driven switch never starts an episode" '[ ! -e "$(mg_state_file $s)" ]'
printf 'LANGUAGE=en\nRECOVER_CHANNEL=dryrun\nRECOVER_MODEL=claude-opus-5[1m]\nRECOVER_EFFORT=max\n' > "$MODEL_GUARD_CONF"

echo "== statusline bands"
sl="$root/scripts/statusline.sh"
printf 'LANGUAGE=en\nSHOW_ACCOUNT=false\nRECOVER_CHANNEL=dryrun\n' > "$MODEL_GUARD_CONF"
printf '{"model":"claude-fable-5-1[1m]","effortLevel":"xhigh"}\n' > "$MODEL_GUARD_SETTINGS"
s=s8
switch $s 'claude-fable-5-1[1m]' claude-opus-4-8 auto >/dev/null; wait_status $s switching
out=$(printf '{"session_id":"%s","model":{"id":"claude-opus-4-8","display_name":"Opus 4.8"},"effort":{"level":"xhigh"}}' $s | HOME="$tmp" "$sl")
check "switching band" 'grep -q "switching to Opus 5 (1M)" <<<"$out"'
mg_state_update $s '.status="stopped" | .note="no_channel"'
out=$(printf '{"session_id":"%s","model":{"id":"claude-opus-4-8","display_name":"Opus 4.8"}}' $s | HOME="$tmp" "$sl")
check "stopped band names the /model target" 'grep -q "stopped · /model to Opus 5 (1M)" <<<"$out"'
mg_state_update $s '.status="stopped" | .note="target_flagged"'
out=$(printf '{"session_id":"%s","model":{"id":"claude-opus-4-8","display_name":"Opus 4.8"}}' $s | HOME="$tmp" "$sl")
check "stopped band after a flagged recovery model" 'grep -q "pick one with /model" <<<"$out"'
mg_state_update $s '.status="switching"'
switch $s claude-opus-4-8 'claude-opus-5[1m]' command >/dev/null
out=$(printf '{"session_id":"%s","model":{"id":"claude-opus-5[1m]","display_name":"Opus 5 (1M context)"},"effort":{"level":"max"}}' $s | HOME="$tmp" "$sl")
check "recovered band is calm and names the switch" 'grep -q "Fable 5.1 (1M) flagged → switched to Opus 5 (1M)" <<<"$out" && ! grep -q "DOWNGRADED" <<<"$out"'
out=$(printf '{"session_id":"%s","model":{"id":"claude-sonnet-5","display_name":"Sonnet 5"}}' $s | HOME="$tmp" "$sl")
check "other model after recovery: normal alarm" 'grep -q "DOWNGRADED" <<<"$out"'
out=$(printf '{"session_id":"nostate","model":{"id":"claude-fable-5-1[1m]","display_name":"Fable 5.1"}}' | HOME="$tmp" "$sl")
check "green when on the expected model" 'grep -q "✔" <<<"$out"'
out=$(printf '{"session_id":"nostate","model":{"id":"claude-opus-5","display_name":"Opus 5"}}' | HOME="$tmp" "$sl")
check "opus-5 below fable alarms" 'grep -q "DOWNGRADED" <<<"$out"'
printf 'LANGUAGE=en\nSHOW_ACCOUNT=false\nEXPECTED_MODEL=claude-opus-4-8\n' > "$MODEL_GUARD_CONF"
out=$(printf '{"session_id":"nostate","model":{"id":"claude-opus-5","display_name":"Opus 5"}}' | HOME="$tmp" "$sl")
check "opus-5 above expected opus-4-8 is an upgrade, not an alarm" 'grep -q "above default" <<<"$out"'

echo "== effort defaults follow the current model"
printf 'LANGUAGE=en\nSHOW_ACCOUNT=false\nEXPECTED_MODEL=claude-fable-5-1\n' > "$MODEL_GUARD_CONF"
effort_band(){
  jq -nc --arg model "${2:-claude-fable-5-1[1m]}" --arg effort "$1" \
    '{session_id:"effort-test",model:{id:$model},effort:{level:$effort}}' |
    CLAUDE_CONFIG_DIR="$tmp/claude-config" "$sl"
}
printf '{"effortLevel":"xhigh"}\n' > "$MODEL_GUARD_SETTINGS"
out=$(effort_band high)
check "legacy global effort still detects a reduction" 'grep -q "high < default xhigh!" <<<"$out"'
printf '{"effortLevel":"xhigh","modelSettings":{"claude-fable-5-1":{"effortLevel":"high"},"claude-fable-5":{"effortLevel":"xhigh"}}}\n' > "$MODEL_GUARD_SETTINGS"
out=$(effort_band high)
check "saved per-model high overrides stale global xhigh for a 1m session" 'grep -q "⚡high" <<<"$out" && ! grep -q "< default" <<<"$out"'
out=$(effort_band medium)
check "a real reduction below the per-model default still warns" 'grep -q "medium < default high!" <<<"$out"'
out=$(effort_band high claude-fable-5-1)
check "base model and 1m variant share the effort default" '! grep -q "< default" <<<"$out"'
out=$(effort_band high claude-fable-5)
check "another model keeps its own effort default" 'grep -q "high < default xhigh!" <<<"$out"'
jq '.modelSettings["claude-fable-5-1"].effortLevel="xhigh"' "$MODEL_GUARD_SETTINGS" > "$tmp/effort-settings" && mv "$tmp/effort-settings" "$MODEL_GUARD_SETTINGS"
out=$(effort_band high)
check "changing the saved default is visible on the next refresh" 'grep -q "high < default xhigh!" <<<"$out"'
printf '{"modelSettings":{"claude-fable-5-1":{"effortLevel":"high"},"claude-fable-5-1[1m]":{"effortLevel":"xhigh"}}}\n' > "$MODEL_GUARD_SETTINGS"
out=$(effort_band high)
check "canonical model entry wins over a context variant without a global field" '! grep -q "< default" <<<"$out"'
printf '{"effortLevel":"xhigh","modelSettings":{"claude-fable-5-1":{"effortLevel":"invalid"}}}\n' > "$MODEL_GUARD_SETTINGS"
out=$(effort_band high)
check "invalid per-model effort falls back to the valid global default" 'grep -q "high < default xhigh!" <<<"$out"'
printf '{"modelSettings":{"claude-fable-5-1[1m]":{"effortLevel":"high"}}}\n' > "$MODEL_GUARD_SETTINGS"
out=$(effort_band medium)
check "explicit context variant is accepted when no canonical entry exists" 'grep -q "medium < default high!" <<<"$out"'
printf '{"model":"claude-fable-5-1[1m]","effortLevel":"xhigh"}\n' > "$MODEL_GUARD_SETTINGS"

echo "== usage of the logged-in account"
mkdir -p "$tmp/bin"
cat > "$tmp/bin/curl" <<'EOF'
#!/usr/bin/env bash
# stand-in for curl: logs the call and the config it was handed, answers with
# $FAKE_USAGE_HTTP and $FAKE_USAGE_BODY
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out=$2; shift;;
    -K) [ "$2" = - ] && cat > "$FAKE_USAGE_CONFIG"; shift;;
  esac
  shift
done
echo call >> "$FAKE_USAGE_LOG"
[ -n "$out" ] && printf '%s' "$FAKE_USAGE_BODY" > "$out"
printf '%s' "$FAKE_USAGE_HTTP"
EOF
chmod +x "$tmp/bin/curl"
export PATH="$tmp/bin:$PATH" FAKE_USAGE_LOG="$tmp/usage-calls.log" FAKE_USAGE_CONFIG="$tmp/usage-curl.config"
export FAKE_USAGE_HTTP=200 FAKE_USAGE_BODY=""
body(){ printf '{"five_hour":{"utilization":%s,"resets_at":"2026-09-08T10:30:00+00:00"},"seven_day":{"utilization":%s,"resets_at":"2026-09-13T20:00:00+00:00"}}' "$1" "$2"; }
calls(){ if [ -f "$FAKE_USAGE_LOG" ]; then wc -l < "$FAKE_USAGE_LOG" | tr -d ' '; else echo 0; fi; }
creds(){ printf '{"claudeAiOauth":{"accessToken":"%s","scopes":["user:profile"]}}' "$1" > "$MODEL_GUARD_CREDENTIALS"; }
cache="$MODEL_GUARD_STATE_DIR/usage.json"
band(){ printf '{"session_id":"nostate","model":{"id":"claude-fable-5-1[1m]","display_name":"Fable 5.1"},"rate_limits":{"five_hour":{"used_percentage":99},"seven_day":{"used_percentage":21}}}' | HOME="$tmp" "$sl"; }
printf 'LANGUAGE=en\nSHOW_ACCOUNT=false\n' > "$MODEL_GUARD_CONF"
rm -f "$cache" "$FAKE_USAGE_LOG"
creds token-A; FAKE_USAGE_BODY=$(body 37.0 18.0)
out=$(band)
check "the reading is the account's, not the payload's" 'grep -q "⏳ 5h 37% · 7d 18%" <<<"$out" && ! grep -q "99" <<<"$out"'
check "the token travels in a curl config on stdin, not on the command line" 'grep -q "Bearer token-A" "$FAKE_USAGE_CONFIG"'
check "asked once" '[ "$(calls)" = 1 ]'
out=$(band)
check "a refresh inside the TTL reuses the reading" '[ "$(calls)" = 1 ] && grep -q "5h 37%" <<<"$out"'
creds token-B; FAKE_USAGE_BODY=$(body 5.0 58.0)
out=$(band)
check "a new login is asked at once" '[ "$(calls)" = 2 ] && grep -q "⏳ 5h 5% · 7d 58%" <<<"$out"'
mkdir "$cache.lock"; date +%s > "$cache.lock/at"
creds token-C
out=$(band)
check "another session asking for a new login: empty, never the previous account" '[ "$(calls)" = 2 ] && ! grep -q "⏳" <<<"$out"'
printf '%s' $(( $(date +%s) - 60 )) > "$cache.lock/at"
out=$(band)
check "a lock left behind by a killed fetch is taken over" '[ "$(calls)" = 3 ] && grep -q "5h 5%" <<<"$out"'
check "lock released after the fetch" '[ ! -d "$cache.lock" ]'
creds token-D; FAKE_USAGE_HTTP=401; FAKE_USAGE_BODY='{"type":"error"}'
out=$(band)
check "rejected token: empty segment, no fallback to the payload" '[ "$(calls)" = 4 ] && ! grep -q "⏳" <<<"$out" && ! grep -q "99" <<<"$out"'
out=$(band)
check "a failure is not asked again inside the TTL" '[ "$(calls)" = 4 ]'
creds token-E; FAKE_USAGE_HTTP=200; FAKE_USAGE_BODY=$(body 85.0 20.0)
out=$(band)
check "5h at the warning threshold: red patch from the account's reading" 'grep -q "5h limit 85%!" <<<"$out" && ! grep -q "7d" <<<"$out"'
printf 'LANGUAGE=en\nSHOW_ACCOUNT=false\nSHOW_LIMIT=false\n' > "$MODEL_GUARD_CONF"
creds token-F; FAKE_USAGE_BODY=$(body 37.0 18.0)
out=$(band)
check "SHOW_LIMIT=false hides the plain reading" '! grep -q "⏳" <<<"$out"'
rm -f "$MODEL_GUARD_CREDENTIALS"; n=$(calls)
out=$(band)
check "no login token: the payload's own reading, nothing asked" '[ "$(calls)" = "$n" ] && grep -q "5h limit 99%!" <<<"$out"'

echo "== working directory"
cwd_band(){ jq -nc --arg d "$1" '{session_id:"nostate",model:{id:"claude-fable-5-1[1m]",display_name:"Fable 5.1"}} + (if $d == "" then {} else {cwd:"/elsewhere",workspace:{current_dir:$d}} end)' | HOME="$tmp" "$sl"; }
printf 'LANGUAGE=en\nSHOW_ACCOUNT=false\n' > "$MODEL_GUARD_CONF"
check "a directory under home is shown from ~" 'cwd_band "$tmp/work/repo" | grep -qF "📁 ~/work/repo "'
check "home itself is ~" 'cwd_band "$tmp" | grep -qF "📁 ~ "'
check "a sibling of home that shares its prefix stays absolute" 'cwd_band "${tmp}x/repo" | grep -qF "📁 ${tmp}x/repo "'
check "a directory outside home stays absolute" 'cwd_band /srv/data | grep -qF "📁 /srv/data "'
check "a line break in the path stays on the band's one line" '[ "$(cwd_band "/srv/a
b" | wc -l)" = 0 ] && cwd_band "/srv/a
b" | grep -qF "📁 /srv/a?b "'
check "no directory in the payload: no segment" '! cwd_band "" | grep -q "📁"'
printf 'LANGUAGE=en\nSHOW_ACCOUNT=false\nSHOW_CWD=false\n' > "$MODEL_GUARD_CONF"
check "SHOW_CWD=false hides it" '! cwd_band /srv/data | grep -q "📁"'

echo "== session-start install check"
ci="$root/scripts/check-install.sh"
inst="$MODEL_GUARD_INSTALL_DIR"
start(){ jq -nc '{hook_event_name:"SessionStart",session_id:"ci",source:"startup"}' | HOME="$tmp" "$ci"; }
printf 'LANGUAGE=en\n' > "$MODEL_GUARD_CONF"
printf '{"model":"claude-fable-5-1[1m]"}\n' > "$MODEL_GUARD_SETTINGS"
out=$(start)
check "unregistered statusline: setup hint" 'grep -q "run /model-guard:setup" <<<"$out"'
printf 'LANGUAGE=en\nSETUP_HINT=off\n' > "$MODEL_GUARD_CONF"
check "SETUP_HINT=off silences the hint" '[ -z "$(start)" ]'
printf 'LANGUAGE=en\n' > "$MODEL_GUARD_CONF"
jq --arg c "$inst/statusline.sh" '.statusLine={type:"command",command:$c}' "$MODEL_GUARD_SETTINGS" > "$tmp/x" && mv "$tmp/x" "$MODEL_GUARD_SETTINGS"
out=$(start)
check "registered but not installed: scripts installed and announced" 'grep -q "refreshed to $MG_VERSION" <<<"$out" && [ -x "$inst/statusline.sh" ] && [ -f "$inst/lib.sh" ] && [ -f "$inst/text.sh" ]'
check "installed copy renders a band on its own" 'printf "{\"model\":{\"id\":\"claude-fable-5-1[1m]\",\"display_name\":\"Fable 5.1\"}}" | HOME="$tmp" "$inst/statusline.sh" | grep -q "✔"'
check "a current install is left alone, silently" '[ -z "$(start)" ]'
sed -i "s/^MG_VERSION=.*/MG_VERSION=\"0.0.1\"/" "$inst/lib.sh"
out=$(start)
check "an older installed copy is refreshed in place" 'grep -q "was 0.0.1" <<<"$out" && grep -q "MG_VERSION=\"$MG_VERSION\"" "$inst/lib.sh"'
sed -i "s/^MG_VERSION=.*/MG_VERSION=\"99.0.0\"/" "$inst/lib.sh"
check "a newer installed copy is left alone by an older plugin" '[ -z "$(start)" ] && grep -q "MG_VERSION=\"99.0.0\"" "$inst/lib.sh"'
sed -i "s/^MG_VERSION=.*/MG_VERSION=\"$MG_VERSION\"/" "$inst/lib.sh"
rm -f "$inst/text.sh"
out=$(start)
check "a missing file makes the set incomplete: refreshed again" 'grep -q "refreshed to $MG_VERSION" <<<"$out" && [ -f "$inst/text.sh" ] && [ -z "$(ls "$inst"/*.mg-new 2>/dev/null)" ]'
legacy="$tmp/.claude/model-guard.sh"
printf '#!/usr/bin/env bash\nMG_VERSION="1.6.0"\necho old-copy\n' > "$legacy"; chmod +x "$legacy"
wrapper="$tmp/.claude/model-guard-wrapper.sh"
printf '#!/usr/bin/env bash\nexec "%s"\n' "$legacy" > "$wrapper"; chmod +x "$wrapper"
jq --arg c "$wrapper" '.statusLine.command=$c' "$MODEL_GUARD_SETTINGS" > "$tmp/x" && mv "$tmp/x" "$MODEL_GUARD_SETTINGS"
start >/dev/null
check "an earlier flat copy becomes a hand-off even behind a user's wrapper" 'grep -qF "exec $inst/statusline.sh" "$legacy"'
check "the hand-off renders the band" 'printf "{\"model\":{\"id\":\"claude-haiku-4-5\",\"display_name\":\"Haiku 4.5\"}}" | HOME="$tmp" "$wrapper" | grep -q "DOWNGRADED"'
printf '#!/usr/bin/env bash\necho mine\n' > "$legacy"
start >/dev/null
check "a user script at the flat path is left alone" 'grep -q "echo mine" "$legacy"'

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
