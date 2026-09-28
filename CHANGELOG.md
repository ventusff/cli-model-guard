# Changelog

## 1.11.1 — 2026-09-28

- **Codex: the account email is back on the footer.** Codex sends an account-updated notification at startup that names only the auth mode and plan, and the widget replaced the bootstrap's account display (with the email) by that email-less one, so the footer read `Pro (More)` without the login. The build now reads the account back after every such notification and puts the email into the display, which also restores it in `/status`.
- **Codex: context and account usage are back on the footer.** The first Codex band (1.4) ended with `ctx 18% | 5h 42% used | 7d 21% used`; the native footer of 1.6 dropped them. The guard's line now ends with the context used and the account's 5-hour and weekly usage, taken from Codex's own readings, and skips any of them that `tui.status_line` already lists.

## 1.11.0 — 2026-09-28

- **Codex: native build moved to official Codex 0.158.0** (`064c6b8c737f5b41d171fdda80bd9ef10ad06eb3`): the source patches are rebased and the package ships that release's helpers. Two of them needed hand merging (the model client's request metadata and the guard's composer restore, which now uses upstream's own `restore_user_message_to_composer`).
- **Codex: releases keep up with upstream by themselves.** The `Codex release` workflow runs daily: it compares the pinned Codex with upstream's latest stable tag and, when they differ, rebases the six patches with a three-way merge (`native/rebase.py prepare`), regenerates the lockfile and the protocol schemas (`rebase.py finish`), builds the package in the pinned Rust image, runs clippy, the guard's Rust tests, the Python fixtures and the real PTY sessions, then commits to `main`, tags and publishes the GitHub release. A patch that no longer merges stops the run and opens an issue naming the conflicting files, with the runbook for finishing by hand. The two earlier workflows (upstream watch and manual build) are folded into it.
- **Codex: installed machines update themselves.** A Model Guard build that finds a newer plugin release during Codex's own startup update check runs `model-guard-codex update` detached (output in `~/.local/share/model-guard-codex/update.log`, one attempt per release), so the next `codex` or `cx resume` uses it; running sessions keep their executable as before. Codex makes that check at most once every 20 hours. `auto_update` in `config.json` turns this off.
- Codex: the guard's footer warning is drawn by the bottom pane itself, so Codex 0.158's default alternate-screen layout, which sizes and draws the pane on its own path, shows the red `STOPPED` line as the inline viewport does; the PTY tests cover both layouts.
- Codex: the packaging patch no longer carries the plugin version; `build.py` passes it as `MODEL_GUARD_VERSION`, so a release changes no patch for the version alone.

## 1.10.0 — 2026-09-28

- **Codex: a detected downgrade now stops the thread.** Codex 0.155 switches a
  thread to its hidden reserve model (`gpt-reserve`, a GPT-5-class fallback)
  on its own when the account's ordinary usage runs out, and switches back
  hours later; the only notice was one info line, every later request was
  made *for* `gpt-reserve`, and the server labeled it consistently, so the
  1.9 guard, which compared the request with the server's disclosure, stayed
  quiet for a whole working day. The guard now also remembers the model the
  user selected (the configured default, or the last `/model` choice) and
  treats three things as a downgrade: a sampling request for another model,
  an effective-model header naming another model, and a response body labeled
  with another family or size tier. Each one interrupts the running turn at
  once, writes a red error into the history, puts a bold red `STOPPED` line
  under the footer and holds every further turn (the composed prompt stays in
  the composer) until the user selects a model with `/model`. A thread
  restored on a different model than the configured one stops before its
  first turn, so a session that went to sleep on `gpt-6-astra` and wakes up on
  `gpt-reserve` cannot send a single request. The stop is remembered per
  thread for the life of the process: switching threads, opening a side
  conversation or coming back through the agents overview neither loses it nor
  lifts it, a turn started on a stopped thread by any other route (`/compact`,
  a review target, a command queued before the stop, a turn restored while
  still running) is interrupted as well, and a thread running in the
  background while another one is shown is judged and interrupted the moment
  its request or disclosure differs. A model pushed by the server into the
  thread's settings, and a request in flight across an account change, are
  judged too. A configured `review_model` is accepted only during a review.
- **Codex: no more automatic model switches.** While the guard is active, the
  usage-limit switch to Luna Reserve and the fallback switches announced by
  backend banners are declined; a warning names the blocked and offered
  models, an amber footer line stays until usage recovers, and input is no
  longer held for a switch that will not happen. The Reserve-only `/model`
  picker is disabled too, so a thread stuck on `gpt-reserve` can be moved back
  by hand. Codex's own switch back to the saved model, when usage returns, is
  allowed only when that model is the selection, and it never lifts a stop.
- **Codex: `gpt-reserve` is always flagged.** Whenever the active model is the
  reserve model, whoever chose it, the footer carries a bold red
  `RESERVE MODEL` line.
- Codex: `config.json` gains `halt_on_reasoning_anomaly` (default `false`) to
  also stop on the 3-of-5 exact-516 reasoning heuristic; the heuristic keeps
  its amber warning otherwise. `/status` now starts with the selected model
  and the reason for a stop or a refused switch.
- Claude Code side: no behaviour change; the version moves with the release.

## 1.9.0 — 2026-09-22

- **The repository is now `ventusff/cli-model-guard`** — it has carried Model
  Guard for Codex CLI since 1.4, so the old Claude-only name was wrong. GitHub
  redirects the old URL, and the marketplace is now called `cli-model-guard`.
  Existing Claude Code installs migrate once:
  `claude plugin marketplace remove claude-model-guard`, then
  `claude plugin marketplace add ventusff/cli-model-guard` and
  `claude plugin install model-guard@cli-model-guard`. The installed statusline
  copy under `~/.claude/model-guard/` and `model-guard.conf` are untouched.
- **Codex: native build moved to official Codex 0.155.1**
  (`be2951ea34f0d295ed0becf97079f92fa5f6950e`); the routing metadata, status
  line and test patches are rebased and the package now ships the 0.155 voice
  runtime. The package is built in the `rust:1.95.0-bookworm` container, so the
  glibc floor drops from 2.39 to 2.36.
- **Codex: `codex update` no longer removes Model Guard.** A run of the official
  installer (which `codex update` used to be) replaced the entry symlink with
  stock Codex and the guard silently vanished. Inside a Model Guard build,
  `codex update` and the update banner now run `model-guard-codex update`, and
  the banner compares against the plugin's `release.json` instead of upstream's
  latest tag. The new `model-guard-codex update` downloads the plugin's current
  release, brings the official standalone package to the Codex version that
  release tracks with OpenAI's own installer, then installs the native package
  on top; `doctor` prints the build's and the official package's versions and
  names that command whenever the entry is not the guard.
- Codex: `probe` reports the failed turn's own error code as its reason
  (`usageLimitExceeded` instead of a bare `probe_failed`).
- Claude Code side: no behaviour change beyond the marketplace name.

## 1.8.1 — 2026-09-20

- The session-start hook no longer rolls a newer installed statusline back. A
  session that started on an earlier plugin fires the hook again on resume and
  compaction with that earlier version, and used to "refresh" the copy a plugin
  update had just installed to the older scripts; the copy is now replaced only
  when it is incomplete or older than the plugin running the hook.

## 1.8.0 — 2026-09-19

- **The band shows the session's working directory.** `📁 ~/code/my-repo` sits
  between the usage reading and the account, taken from the statusline
  payload's `workspace.current_dir`, so it follows the session when it changes
  directory. Home is shortened to `~`. `SHOW_CWD=false` hides it.
- The Codex integration is unchanged and stays at 1.7.1, the version its native
  build carries.

## 1.7.1 — 2026-09-10

- The session list after a Codex install names interactive sessions only.
  Sandbox helpers the TUI re-executes itself as, `exec` runs and app-servers
  share the executable name but are nothing to resume, so they are left out,
  and a listed command line is cut at 100 characters.
- An earlier flat `~/.claude/model-guard.sh` is handed over to the installed
  directory even when a wrapper of the user's execs it rather than
  `settings.json` naming it; a script that is not a model-guard copy is never
  rewritten.

## 1.7.0 — 2026-09-10

- **The response body's model label is a second routing signal for Codex.** The
  server writes a `model` into every response; the official client stopped
  comparing it in February because slug variants caused false positives, and on
  a ChatGPT login the effective-model header is usually absent, so until now the
  guard could never say anything about the everyday route. The native build now
  carries that label on `model/routing/updated`. A label of the request's own
  family stays quiet (`gpt-6-astra-2026-09-01`, bare `gpt-6`); another family or
  a size tier (`gpt-4o`, `gpt-6-astra-mini`) shows an orange line under the
  footer, ranked below the red effective-model difference. `/status` always
  shows the label. `probe` exits `5` for a differing label without disclosure.
- **Installation names the sessions it cannot change.** A running Codex process
  keeps the executable it started with. The installer and `doctor` list the
  sessions still on another executable, with their directories, and say to
  finish each one and `codex resume` there. Versioned packages and environments
  that no session uses any more are removed.
- **Claude statusline scripts share one library.** The statusline sources
  `lib.sh` next to it; model ranking, saved-effort lookup, language detection and
  all eight languages of text (`text.sh`) exist once. `setup` installs the three
  files under `~/.claude/model-guard/`; the session-start hook refreshes that
  directory and turns a registered `~/.claude/model-guard.sh` into a hand-off,
  so no settings change is needed on update.
- The Python probe package keeps only what the probe needs: account and
  rate-limit polling left with the terminal band they served. `model-guard-codex`
  has proper subcommand help; `doctor` reports a missing installation plainly.

## 1.6.0 — 2026-09-09

- **Native Codex status line.** Replace the terminal multiplexer and live protocol
  adapter with a pinned, auditable Codex source build. Reuse the existing footer
  and input handling; `codex`, `cx`, `resume`, `fork` and profiles use the same CLI.
- Keep normal display quiet. Show model, request effort and account; only
  disclosed routing differences and repeated 516 signals expand a warning.
  Missing disclosure, individual hits and detailed counts are in `/status`.
- Bind typed route events to the visible thread, turn and sampling request.
  Hidden system title threads cannot replace the main model. Count unique
  response IDs; isolate reasoning observations across account/settings changes.
- Install a checksum-verified native package by atomically switching the existing
  executable symlink. Restore the original entry on removal, preserving running
  sessions, defaults and login files. Include the matching official helper tools.
- Move live external `status`/`check` diagnostics to native `/status`. Keep the
  separate explicit `probe` and its strict disclosure-only exit codes.
- Preserve Claude Code behavior and its per-model saved-effort handling.

## 1.5.1 — 2026-09-09

- **Enable the Codex launcher in Fish.** Add a reversible PATH block to the
  existing Fish configuration (or create it when Fish is the login shell),
  respecting `XDG_CONFIG_HOME`. New terminals resolve Model Guard before stock
  Codex, including when its directory was already later in PATH. No persistent
  Fish universal variables, model defaults or running sessions are changed.
- Preserve custom shell content and dotfile symlinks on install/removal.
  Regression checks execute real Fish startup with quoted paths and verify
  that removal restores the official command.

## 1.5.0 — 2026-09-09

- **Passive Codex reasoning diagnostics.** Show the last response's reasoning
  tokens and exact-516 frequency over up to 20 measured responses. At high or
  greater effort, any exact-516 hit is highlighted through the turn; at least
  three hits among the last five measurements show `REASONING SUSPECT` in red.
  These are explicit heuristics, independent of the strict model-routing verdict.
- Deduplicate usage snapshots, ignore delayed/foreign events, distinguish fresh
  threads from attached history, and keep observations scoped to the request's
  model, provider, effort, tier and account. Higher 518n-2 values remain diagnostic
  counts. No extra inference requests, forced retries or model changes.
- Export allowlisted reasoning signals in `check`/`probe` JSON and add a standalone
  metadata-only historical audit. Expanded bilingual research covers Reddit's
  516 reports, existing hooks/proxies, KBF, PAMELA, fpverify and other fingerprints.
- Regression tests include genuine official-Codex SSE/WebSocket usage handling,
  duplicate and stale events, account transitions, and a real 516 TUI warning
  retained after resizing. Claude behavior retains the 1.4 per-model effort fix.

## 1.4.0 — 2026-09-09

- **Claude effort defaults follow the model picker.** Read the active model's
  saved `modelSettings` effort before the legacy global `effortLevel`, including
  `[1m]` context variants. Saving `high` no longer produces a false `high < xhigh`
  warning when the old global setting remains `xhigh`; real reductions still warn.
- **Codex CLI integration.** A persistent two-row terminal band puts requested
  versus server-reported model routing first, followed by account, reasoning,
  context and account usage. Matching reports are green, differences red, absent
  evidence amber; disconnected or stale observers are explicitly red.
- Uses the official Codex TUI/app-server protocol and an isolated tmux server.
  No Codex fork, HTTPS interception, credential-file access or model-default edits.
  Evidence is scoped to the visible thread and current sampling request. The
  plugin does not infer hidden weights or automatically change Codex models.
- Reversible bash/zsh installation, pinned Python dependency, Codex plugin skill,
  English/Chinese display, diagnostics and source-linked routing research.
- Strict `check` and separate `probe` commands return nonzero when routing is
  unverified or unavailable, with shareable metadata that omits account identity.
  Research covers recent GPT-4o claims, community fingerprints, negative controls
  and live checks; undisclosed backend routing remains unverifiable.
- Real Codex integration tests cover HTTP/SSE, WebSocket metadata, missing
  effective headers and a gpt-4o fixture mismatch; PTY tests exercise the actual
  terminal band and resizing. Claude regression tests cover per-model defaults,
  legacy settings and changes during a running session.

## 1.3.0 — 2026-09-08

- **The rate-limit reading is the logged-in account's.** The 5-hour and 7-day
  usage is asked from Claude Code's own usage endpoint (the one behind `/usage`)
  with the stored login token, one reading per machine shared by every session
  for 30 s. The `rate_limits` in the statusline payload is what one session last
  read from a response header: it stands still until that session gets another
  response and it survives `/login`, so after an account switch the band kept
  reporting — and updating — the previous account. Now a new login is asked at
  once; until the answer is in, the segment is empty rather than another
  account's number. Sessions without a login token keep the payload value.
- **`SHOW_LIMIT`** (default `true`) puts `⏳ 5h 37% · 7d 18%` in the everyday
  band. The `LIMIT_WARN_AT` red patch is unchanged and reads the same source.
- The account email and the credentials follow `CLAUDE_CONFIG_DIR`.

## 1.2.0 — 2026-09-06

- **zellij is a recovery channel.** `auto` now tries a tmux pane, then the zellij
  pane named by `ZELLIJ_PANE_ID`, then kitty remote control. A non-numeric or
  absent pane id leaves the channel unavailable rather than guessing a target.
- **The input box is cleared before every typed line.** Interrupting a turn puts
  the interrupted prompt back into the box, so the model command used to be
  appended to it and submitted as one unusable prompt, and the switch never
  happened.
- **A turn's interruption is acknowledged by the session registry leaving `busy`**,
  not by an interruption record in the transcript, which this Claude Code version
  does not always write.
- **A switch the user did not ask for always starts a recovery.** Only the
  named user-driven sources (`command`, `picker`, `sdk`, `config`, `fast_mode`,
  `slash_command`) pass through, so a source name added later cannot let a
  downgrade slip by.
- **A plugin update no longer needs `/model-guard:setup`.** The session-start
  hook refreshes the installed statusline script in place and reports the new
  version; config and settings are untouched.

## 1.1.1 — 2026-09-04

- Without a keystroke channel (or when an automatic switch is not allowed) a
  downgrade stops the running turn once and the hooks step aside: no prompt
  gate, no second-submission release, no session-start hint. A `Stop` hook
  marks a turn that ended by itself.
- Driver: transcript match counter fixed; a failed switch leaves the session
  stopped; tmux channel verified live.

## 1.1.0 — 2026-09-04

Auto-recovery from safeguard-flag downgrades.

- Hooks (`PostModelSwitch`, `PreModelSwitch`, `PreToolUse`, `Stop`,
  `UserPromptSubmit`, `SessionStart`, `SessionEnd`): an automatic downgrade
  stops the turn. Where the terminal can be driven (tmux pane or kitty socket)
  a detached driver then switches the session to `RECOVER_MODEL` at
  `RECOVER_EFFORT` and resumes the task with `RECOVER_PROMPT`, restoring the
  previous default model afterwards; anywhere else the stop is all that
  happens. Per-session state in `$XDG_RUNTIME_DIR/model-guard/`.
- Statusline: amber 🔁 band while running on the recovery model; red bands
  for stopped / switching / halted; strength ranking now compares versions
  within a family (`claude-opus-5 > claude-opus-4-8`) instead of alarming on
  every unequal id of the same family.
- Config keys `RECOVER`, `RECOVER_MODEL`, `RECOVER_EFFORT`, `RECOVER_PROMPT`,
  `RECOVER_CHANNEL`, `RECOVER_MAX`, `DEBUG`; `setup` asks about recovery and
  offers to enable kitty remote control.
- `tests/run.sh`: state-machine, driver (dryrun channel) and statusline tests.

## 1.0.0 — 2026-08-05

Initial release.

- Full-width, theme-proof (truecolor + 256-color fallback, WCAG AAA contrast)
  statusline band: model state, reasoning effort, context usage, account.
- Silent-downgrade alarms: model below expectation, effort below `effortLevel`,
  extended thinking switched off, 5-hour rate-limit pressure (pre-fallback warning).
- 8 band languages (en / zh / ja / ko / es / fr / de / pt), auto-following
  Claude Code's `language` setting.
- Interactive `/model-guard:setup` and `/model-guard:remove` skills — backup,
  register, verify, restore; no manual config editing.
- SessionStart hint hook: nudges until setup is done, flags version drift after
  plugin updates.
