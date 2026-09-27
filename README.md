<div align="center">

<img src="assets/hero.svg" alt="model-guard statusline — four states" width="880">

# 🚨 model-guard

**Model and account visibility for Claude Code and Codex CLI. Codex flags routing differences and suspicious reasoning usage.**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Made for Claude Code](https://img.shields.io/badge/made%20for-Claude%20Code-d97757)](https://claude.com/claude-code)
[![Deps](https://img.shields.io/badge/deps-bash%20%2B%20jq%20%2B%20curl-4EAA25)](#install)
[![Languages](https://img.shields.io/badge/band%20languages-8-8A2BE2)](#configuration)

English · [简体中文](README.zh-CN.md)

[Codex CLI](#codex-cli) · [Claude Code](#the-problem)

</div>

---

## The problem

You hit a usage limit mid-session. Claude Code falls back to a weaker model — **quietly**. No flash, no bell, just a subtly dumber pair programmer. You keep prompting for another hour, wondering why the code got sloppy, while the tokens keep burning.

This plugin exists because exactly that happened to us: half a working session on a silently-downgraded model before anyone noticed the tiny model name in the corner. Never again.

## What you get

One always-on, full-width color band at the bottom of every session. You don't read it — you *notice* it.

| Band | Meaning |
|:---:|---|
| 🟩 `✔` | Model matches what this machine expects |
| 🟦 `⬆` | Running **above** your default — FYI, no alarm |
| 🟥 `🚨` | **Silent downgrade** — the whole bar turns red and tells you to `/model` back |
| 🟧 `🔁` | **Recovered** — a flag downgraded the session, the plugin switched it to the recovery model |
| 🟦 `●` | No expectation configured — neutral display |

And model identity is only half the story. Red **inline patches** catch the other silent downgrades:

- ⚡ **Reasoning effort** dropped below the current model's saved `modelSettings[model].effortLevel`, falling back to the global `effortLevel`
- 🧠 **Extended thinking** switched off
- ⏳ **5-hour rate-limit window ≥ 80 %** — the precondition for a forced fallback, flagged *before* it happens

Plus the useful everyday bits: current model & effort, context-window usage, your account's 5-hour and 7-day usage (`⏳ 5h 37% · 7d 18%`), the session's working directory (`📁 ~/code/my-repo`), and which account you're logged in with (multi-account users know the pain).

The effort baseline is reread on every update. Saving `high` in `/model` or `/effort` takes effect even if the old global field still says `xhigh`; a later drop to `medium` still alarms. Canonical Claude model IDs share their saved effort with their `[1m]` context variants.

## Auto-recovery

Showing a downgrade is half the job. The most common silent downgrade today is a **safeguard flag**: Fable's (or Opus 5's) safeguards flag a message, Claude Code re-runs it on Opus 4.8 — and keeps the whole session there. With the 1.1 hooks the session instead

1. **stops** — the next tool call is denied and the turn ends (`PreToolUse`);
2. **switches** — a detached driver presses Esc, types `/model <recovery model>` and `/effort <level>` into the session's own terminal, and waits for Claude Code's `PostModelSwitch` event to confirm the switch (the plugin's `PreModelSwitch` hook answers *allow*, so no cache-miss dialog gets in the way);
3. **continues** — sends the continue prompt, and the interrupted task resumes on the recovery model.

Default: `claude-opus-5[1m]` at `max` effort, continue prompt `Continue.` (or `继续` when the band language is Chinese). Measured round trip: about 8 seconds from the downgrade to the first token on the recovery model.

Steps 2 and 3 only exist where the terminal can be driven: a tmux pane (`send-keys`), a zellij pane (`action write-chars`), or kitty with remote control over a socket (`allow_remote_control socket-only` + `listen_on unix:@kitty` in `kitty.conf`, then restart kitty — `setup` offers to add both lines). tmux and zellij need no configuration at all. Anywhere else the plugin does step 1 and nothing more: the turn stops once, the band names the `/model` to run, and the hooks stay out of your way.

Inside a multiplexer the keystrokes address one pane by id, never "the focused window", so a recovery in one pane cannot type into the session next door.

Two details worth knowing:

- `/model <id>` in an interactive session also saves `<id>` as your default for new sessions. The plugin puts your previous default back after its own switch, so a recovery never changes what tomorrow's sessions start with.
- If the recovery model itself gets flagged (Opus 5 → Opus 4.8), or a session is downgraded more than `RECOVER_MAX` times, the plugin only stops. The band says so; pick a model with `/model`.

## Install

```
/plugin marketplace add ventusff/cli-model-guard
/plugin install model-guard@cli-model-guard
/model-guard:setup
```

The last step is **interactive** — arrow keys, two questions, done. It copies the script, writes the config, and registers the `statusLine` in `~/.claude/settings.json` with a timestamped backup. No copy-pasting shell commands from a README.

<div align="center"><img src="assets/setup.svg" alt="interactive setup" width="880"></div>

> **Why is there a setup step at all?** Claude Code plugins can't register a main statusline by themselves (plugin `settings.json` only supports `agent` and `subagentStatusLine`). `setup` is the one honest extra step, and it is a one-time one: when a later plugin update ships a newer script, the session-start hook refreshes the installed copy itself and says so.

**Requirements:** bash 4+, [`jq`](https://jqlang.github.io/jq/) and `curl`; the recovery hooks also use `flock` and `setsid` (util-linux) and, when present, `notify-send` for a desktop notice. No daemon. The one network call is the statusline asking Claude Code's own usage endpoint (the one behind `/usage`) for your account's rate-limit usage, with the login token Claude Code stored, at most once every 30 s per machine; nothing else leaves the machine. Plugin hooks load at session start — restart your sessions after installing or updating.

## Codex CLI

**Model Guard uses Codex's native status line, and it stops a downgraded thread.** Model, requested reasoning effort and account share the existing footer. A request for a model other than the one you selected, a disclosed routing difference and a response body labeled with another model each interrupt the turn at once and hold the thread until you choose a model again; repeated 516-token reasoning signals appear as a warning. Normal use has no extra terminal layer and no permanent warning banner.

<img src="assets/codex.svg" alt="Illustrative native Codex footer and conditional routing warning" width="880">

Run `codex` or `cx` normally, including `resume` and `fork`. Native input handling, scrollback, paste, resizing, profiles and directory selection remain owned by Codex. Background title generation and agent threads cannot replace the visible conversation's model.

The footer says **selected** until live request metadata arrives; it then says **request**, using the model and effort pinned to that request. An attachment never guesses the settings of a request that started before it subscribed. A disclosed different model shows both names in red.

**Codex changes models on its own, and the guard refuses.** Codex 0.155 moves a thread to its hidden reserve model (`gpt-reserve`, a GPT-5-class "fast and affordable" fallback) when the account's ordinary usage is exhausted, announces it with one info line, and moves back hours later. Every request in between is made *for* `gpt-reserve` and labeled `gpt-reserve` by the server, so comparing the request with the disclosure sees nothing. Model Guard therefore keeps the model **you selected** — the configured default (`model` in `config.toml` or `-m`) or your last `/model` choice — and holds Codex to it:

- The usage-limit switch to Luna Reserve, and the fallback switches that backend banners announce, are declined. A warning names the blocked and the offered model, an amber `usage limit on … · automatic switch to … refused` line stays under the footer until usage recovers, and your input is not held for a switch that will not happen: requests on the exhausted model fail loudly, or you pick another model yourself. Codex's own switch back to the saved model when usage returns goes through only when that model is your selection, and it never lifts a stop.
- A sampling request for a model other than your selection (a configured `review_model` is accepted during a review only), an effective-model header naming another model, or a response body labeled with another family or size tier **stops the thread**: the running turn is interrupted immediately, a red error explains the cause, a bold red `STOPPED …` line stays under the footer, and every further turn is held — the prompt you typed stays in the composer — until you select a model with `/model` (selecting the same model again counts). The three causes rank request > header > label; a stronger one replaces a weaker one without a second interruption. The stop belongs to the thread for the life of the process: switching threads, side conversations and the agents overview keep it, any turn that still starts on a stopped thread (`/compact`, a review target, a command queued before the stop, a turn restored while running) is interrupted, and a thread running in the background while you look at another one is judged and interrupted the moment its request or disclosure differs. A model pushed into the thread's settings by the server, and a request in flight across an account change, are judged the same way.
- A thread restored on a different model than the configured one stops before its first turn, so a session that went to sleep on `gpt-6-astra` and `resume`s on `gpt-reserve` cannot send a request. Whenever the active model is `gpt-reserve`, whoever chose it, the footer carries a bold red `RESERVE MODEL` line. (A custom collaboration-mode preset that carries its own model trips the same stop; `/model` clears it.)

Every response body also carries a `model` label written by the server. The official client stopped comparing it in February 2026 ([PR #12061](https://github.com/openai/codex/pull/12061)) because slug variants produced false positives, and on a ChatGPT login the effective-model header is usually absent, so the label is the one routing fact the server states on every everyday request. Model Guard keeps it as a **second-tier signal**: a label of the request's own family stays quiet (`gpt-6-astra-2026-09-01`, bare `gpt-6`), while another family or a size tier (`gpt-4o`, `gpt-6-astra-mini`) turns an orange line on under the footer, ranked below the red header difference. `/status` always shows the label. At high or greater effort, at least three of the last five measured responses with exactly 516 reasoning tokens show a reasoning anomaly warning. Single hits and sample counts are available in **`/status`**. Statistics use unique response IDs and reset when the model, provider, effort, service tier or account changes.

These are three distinct signals: the effective-model header and the body label record what the server disclosed, at two levels of trust; 516 is a community heuristic with no calibrated false-positive rate, so it warns but does not stop unless `halt_on_reasoning_anomaly` is on. None of them proves the underlying weights. Missing disclosure is explained in `/status` and does not turn the normal footer yellow. The guard interrupts and holds; it never sends inference requests, retries or changes the model itself. See the [routing research](codex/model-guard/ROUTING.md) for inspected Reddit/GitHub approaches and their limits.

After installing and enabling the Codex plugin from your personal marketplace, install its native runtime:

```sh
python3 codex/model-guard/scripts/install.py --language en
model-guard-codex doctor
```

The prebuilt runtime supports **Linux x86_64, glibc 2.36+, Python 3.12+ and the official standalone Codex 0.155.1**. The installer verifies a release checksum, prepares a versioned package and atomically switches the existing `~/.local/bin/codex` symlink. Your next `codex` or `cx resume` invocation picks it up in the current shell.

A running Codex process keeps the executable it started with, so an open session never gains Model Guard by installation alone; that is how every native program behaves, and no installer can change it. Instead of leaving you to wonder, the installer and `doctor` list the sessions still on another executable, with their directories: finish or `/quit` each one, then `codex resume` there. Earlier versioned packages no session uses any more are removed.

This is a disclosed **custom build of official Codex**, pinned to one source commit, with reviewable patches and a [build recipe](codex/model-guard/native/README.md). Stock Codex does not currently expose a plugin footer renderer. Model Guard adds metadata and native rendering inside that source; it adds no tmux server, terminal proxy, provider proxy or transport logging. Account and quota handling use Codex's existing state. Model/provider defaults and login files are not changed. The supplied package includes the official code-mode host and sandbox helpers from the same release.

Native builds must be updated together with the plugin, and the plugin tracks one official Codex version at a time. **Update with `model-guard-codex update`**, never with the official installer alone: it downloads the plugin's current release, brings the official standalone package to the Codex version that release tracks (with OpenAI's own installer), then installs the native package on top. Inside a Model Guard build, `codex update` and the update banner run exactly that command, and the banner compares against the plugin's release rather than upstream's latest tag, so a stock update can no longer replace the guard behind your back. If an official installer did replace the entry (`curl … install.sh | sh` does), `doctor` says so and names the command. An explicit remote app-server must run the same metadata extension for complete routing details; connection and CLI semantics remain native.

Preferences live in `~/.local/share/model-guard-codex/config.json`: `language` (`en` or `zh`), `show_account` (boolean) and `halt_on_reasoning_anomaly` (boolean, default `false`: also stop on the 3-of-5 exact-516 heuristic). `MODEL_GUARD_CODEX_HOME` selects another installation root. Disable the plugin in Codex to disable its display, its stops and its refusal of automatic switches together.

```sh
model-guard-codex probe --json   # A separate read-only request; consumes provider quota
model-guard-codex doctor        # Verify the native executable, compare it with the official package, list sessions on another one
model-guard-codex update        # Move to the plugin's current release, official Codex version included
model-guard-codex remove        # Restore the original official executable symlink
```

Live session details are accessed inside Codex with `/status`, which starts with the selected model and the reason for a stop or a refused switch; the old external `status` and `check` commands now direct you there. `probe` accepts `-m MODEL -r EFFORT` for that request only. Its exit codes are `0` for matching effective-model disclosure, `2` for a difference, `3` for missing disclosure, `4` for an unavailable/failed probe, and `5` for a body label that differs while nothing was disclosed. JSON omits account identity and conversation text. A separate probe cannot certify an existing conversation.

Removal preserves running sessions, packages and preferences. Legacy managed shell blocks are removed during migration; no new shell PATH blocks are needed. Validation uses isolated Codex homes, local HTTP/SSE and WebSocket fixtures, Rust state/layout tests, and real native PTYs. See the build recipe for commands.

## How "downgraded" is decided

**Expected model** — first hit wins:

1. `EXPECTED_MODEL` in `~/.claude/model-guard.conf` (a `grep -Ei` pattern, manual override)
2. `~/.claude/statusline-expected-model` (legacy override file)
3. The `model` you pinned in `~/.claude/settings.json` (`opus[1m]` → `opus`; `default` = no expectation)

**Strength ranking:** family `fable/mythos > opus > sonnet > haiku`, then version within the family (`claude-opus-5 > claude-opus-4-8`).

- Actual model ranks **below** expected → 🟥 full-width alarm.
- Unknown ids score zero → 🟥. We can't prove it isn't weaker, so we don't guess. Conservative by design.
- Actual ranks **above** expected → 🟦 calm blue. Free upgrades are not emergencies.
- Automatic downgrades (Claude Code's `PostModelSwitch` with source `auto` or `resume`) use the same ranking to decide whether a recovery starts.

## Where the usage numbers come from

The 5-hour and 7-day numbers are your **account's**, asked from Claude Code's own usage endpoint (the one behind `/usage`) with the login token Claude Code stored — environment variable, macOS keychain or `~/.claude/.credentials.json`, in that order. One reading is shared by every session on the machine for 30 s.

They are deliberately not the `rate_limits` Claude Code hands to statuslines. That value is what one session last read from a response header: it stands still until that session gets another response, and it survives `/login`. After an account switch it keeps reporting the previous account — and keeps climbing while requests already in flight finish on the old token. Here a new login is a new cache key, so the next refresh asks again immediately; until the answer is in, the segment stays empty rather than showing another account's number. Sessions without a login token (API key, Bedrock, Vertex) fall back to the payload value.

## Configuration

Everything lives in `~/.claude/model-guard.conf` (created by `setup`, safe to edit by hand):

| Key | Default | What it does |
|---|---|---|
| `LANGUAGE` | `auto` | Band language. `auto` follows Claude Code's `language` setting, else English. Available: `en` `zh` `ja` `ko` `es` `fr` `de` `pt` |
| `SHOW_ACCOUNT` | `true` | Show the logged-in account email (reads `~/.claude.json` live — switching accounts updates the band) |
| `SHOW_CONTEXT` | `true` | Show context-window usage, e.g. `◔ 13%` |
| `SHOW_LIMIT` | `true` | Show the logged-in account's 5-hour and 7-day usage, e.g. `⏳ 5h 37% · 7d 18%` (see above) |
| `SHOW_CWD` | `true` | Show the session's working directory, home shortened to `~`, e.g. `📁 ~/code/my-repo` |
| `LIMIT_WARN_AT` | `80` | Red patch when the 5-hour rate-limit usage reaches N %. `off` disables |
| `EXPECTED_MODEL` | *(auto)* | Manual expected-model pattern, e.g. `opus\|fable` |
| `RECOVER` | `on` | Master switch for the recovery hooks (`off` disables stop + switch entirely) |
| `RECOVER_MODEL` | `claude-opus-5[1m]` | Model the session is switched to after an automatic downgrade |
| `RECOVER_EFFORT` | `max` | `/effort` level applied on the recovery model (`off` to leave it alone) |
| `RECOVER_PROMPT` | *(by language)* | Prompt sent to resume the interrupted task |
| `RECOVER_CHANNEL` | `auto` | How keystrokes reach the session: `auto` (tmux pane, then zellij pane, then kitty), `tmux`, `zellij`, `kitty`, `dryrun` (log only), `none` |
| `RECOVER_MAX` | `3` | Automatic recoveries allowed per session; beyond that the plugin only stops |
| `DEBUG` | *(off)* | `true` appends every hook input to `$XDG_RUNTIME_DIR/model-guard/debug.log` |

Re-run `/model-guard:setup` anytime to reconfigure interactively.

## Design notes

<details>
<summary><b>Why truecolor instead of normal ANSI colors?</b></summary>

Terminal themes remap the 16 basic ANSI colors — a "red background" can render as pink, and the contrast collapses exactly when you need the alarm to be unmissable. model-guard emits **truecolor** escape codes (with a fixed 256-color-cube fallback when `COLORTERM` is unsupported), bypassing theme palettes entirely.

All three foreground/background pairs are picked for WCAG contrast ≥ 7:1 (AAA):

| State | Colors | Contrast |
|---|---|---|
| OK | `#000000` on `#3FB950` | 8.3 : 1 |
| ALARM | `#FFFFFF` on `#B00020` | 7.3 : 1 |
| INFO | `#FFFFFF` on `#0D47A1` | 8.6 : 1 |
| RECOVERED | `#000000` on `#FFB300` | 11.4 : 1 |

No blink (SGR 5) — its rendering is unpredictable across terminals.

</details>

<details>
<summary><b>How does the automatic recovery work, and why does it type into the terminal?</b></summary>

Claude Code hooks can observe a model switch (`PostModelSwitch` carries `from_model`, `to_model` and a `source` — `auto` for a fallback) and veto a user-initiated one (`PreModelSwitch`), but no hook can *set* the session model, and a running interactive session has no control channel besides its keyboard. So the plugin does exactly what you would do by hand — Esc, `/model`, `/effort`, "continue" — through the terminal's own remote-control API, and never guesses: every step waits for its echo (Claude Code's session registry going idle, the `PostModelSwitch` event, the command's line in the transcript) before the next one.

The hooks keep one small JSON file per session in `$XDG_RUNTIME_DIR/model-guard/`:

| status | meaning |
|---|---|
| `pending` | downgraded with a keystroke channel; the driver is starting |
| `switching` | the driver is typing the switch |
| `recovered` | the session model changed after the downgrade (by the driver or by you) |
| `stopped` | downgraded, no automatic switch (no channel, the recovery model itself was flagged, downgraded again while recovering, or `RECOVER_MAX` hit): the turn is stopped once, then the hooks pass everything |

`SessionStart` forgets stale state (a latched fallback re-announces itself on resume as `PostModelSwitch` source `resume`, which triggers the switch but not the continue prompt); `SessionEnd` cleans up. `tests/run.sh` drives the whole state machine with synthetic payloads and a `dryrun` channel.

</details>

<details>
<summary><b>How does the band fill the whole row at any terminal width?</b></summary>

The script pads the output with ~300 trailing spaces (or a train of 🚨 in alarm state). Anything past the terminal width is clipped by the TUI, so the colored band always spans the full row — no width detection needed.

</details>

<details>
<summary><b>What exactly does <code>setup</code> touch?</b></summary>

- Copies the statusline scripts (`statusline.sh`, `lib.sh`, `text.sh`) to `~/.claude/model-guard/`
- Writes your answers to `~/.claude/model-guard.conf`
- Merges a `statusLine` block into `~/.claude/settings.json` — after making a timestamped backup, without touching any other key
- If you had a different statusline before, it's saved and **restored on uninstall**

</details>

## Uninstall

```
/model-guard:remove
```

Unregisters the statusline (restoring whatever you had before), optionally deletes the scripts, config and per-session state, and keeps a settings backup. Then remove the plugin itself via `/plugin` if you want — the recovery hooks live in the plugin and go with it.

## License

[MIT](LICENSE) © [ventusff](https://github.com/ventusff)
