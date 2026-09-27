# Codex routing: research and evidence

Verified on Linux (`nomad-u`), 2026-09-09 and 2026-09-10, using official Codex **0.153.4** and the matching source commit **3d2ee51ca2d5db578f328aa75e20aa22c0197c9a**.

[中文调研结论](ROUTING.zh-CN.md)

## What can be known

Practical client-side detection is possible. Model Guard combines disclosed routing with passive reasoning-usage signals, and statistical probes can detect inconsistency even when effective-model headers are absent. The useful distinction is between the requested model, the model identifier disclosed by the server, and the undisclosed implementation behind that identifier. A client can compare the first two. Without an independent attestation mechanism it cannot establish which weights actually generated a reply if the provider withholds or rewrites metadata. Model Guard reports this boundary in the UI: missing evidence is amber, never an assumed match.

An original community report reproduced `gpt-5.3-codex` requests returning a `gpt-5.2` identifier; an OpenAI collaborator confirmed that some cyber-safety cases were rerouted and discussed adding notifications. This establishes that rerouting can occur. It does not establish a universal GPT-4o fallback or a reliable behavioral fingerprint. [Original report and maintainer discussion](https://github.com/openai/codex/issues/11189).

A newer reporter observed missing server-model information while requesting `gpt-5.6-sol`. This is a useful example of the observability gap, not independent proof of a route change. [Report #34988](https://github.com/openai/codex/issues/34988).

**Version 1.5 adopts exact-516 reasoning telemetry as a practical warning signal, and 1.7 adds the response body's model label as a second-tier disclosed signal.** Statistical fingerprinting is a viable complementary audit method. Neither mechanism currently establishes an undisclosed GPT-4o identity for every individual request; this narrower limitation does not make anomaly detection useless.

## Passive 516 detection

The original #30364 report analyzed 390,195 token-count records and found a disproportionate exact-516 spike under the GPT-5.5 label. Its [author's Reddit explanation](https://www.reddit.com/r/codex/comments/1ujqo09/the_gpt55_516_reasoning_tokens_issue_is_not/) explicitly distinguishes the measured anomaly from proving truncation. The [GitHub issue](https://github.com/openai/codex/issues/30364) is closed as of this inspection; closure alone supplies no explanation of the phenomenon or evidence that every affected deployment was fixed.

An existing implementation is [bentoner/codex-516-hook](https://github.com/bentoner/codex-516-hook/tree/3729959d72544ef3f9db62b696306b1dbdae04e9). It reads completed response usage and warns through a Stop hook. The author reports 18/20 exact-516 runs through Codex with each of subscription and API-key authentication, versus 0/20 on a bare Responses request, for one trigger puzzle. A harder puzzle had wrong answers in 5 of 6 observed exact-516 runs. These are small, task-specific author measurements, but they provide a concrete reason to monitor the signal.[^hook]

That project's controls also matter: 1034/1552/2070/… occurred in correct runs. Exact 516 gets greater operational weight than the broader `518n − 2` ladder. A natural response can also land on 516, and a tool-selection response can be short without being defective. Counts identify suspicious execution behavior; they do not name a replacement model. A positive reasoning count also cannot distinguish a flagship reasoning model from a smaller reasoning model.

[NickalasLight's analysis repository](https://github.com/NickalasLight/codex-reasoning-bug-512-token/tree/aaa5995d5ebcd8dabc64c95dcc32a3350183e357) supplies scripts and before/after evidence. Its [Reddit follow-up](https://www.reddit.com/r/codex/comments/1upxyjl/psa_for_anyone_that_thinks_they_have_solved_the/) explains that improving the candy puzzle did not establish improved mean reasoning on ordinary workloads. This is a reason to measure representative tasks alongside a trigger puzzle, while retaining the passive warning itself.

### Model Guard's rule

The native UI consumes `rawResponse/completed`, which supplies a response ID and its reasoning usage. Unique response IDs avoid counting repeated usage snapshots or context recomputation as new responses. The explicit standalone probe also supports stock Codex's `thread/tokenUsage/updated` stream, using conservative cumulative-usage checks.

| Observation | Native display / treatment |
|---|---|
| Valid reasoning count, including zero | Available in `/status` |
| Single exact 516 at high or greater effort | Retained in diagnostics; normal footer stays quiet |
| At least 3 exact-516 hits in the last 5 valid responses at those efforts | Amber reasoning anomaly warning, expressed as a sentence |
| Exact 516 at lower or unknown effort | Counted without a warning |
| Higher `518n − 2` values | No native warning; the standalone probe exports ladder counts |
| Missing or invalid usage | No synthesized count; no placeholder in the normal footer |

The 3-of-5 threshold has no claimed false-positive probability and is not evidence that three answers were wrong. Statistics cover up to 20 valid responses, including tool rounds, and reset across model/provider/effort/tier/account changes. Responses in flight during an observed account change are quarantined until the next turn. This is a diagnostic signal, not a billing ledger.

The explicit `probe --json` exports `reasoning` with `evidence: "heuristic"`; its route exit code remains independent. A disclosed match may coexist with `reasoning.alert: "suspect"`. A disclosed difference takes visual priority in the native footer. No prompt injection, forced continuation, model selection or retry is triggered.

### Local reproduction

A metadata-only audit of 50 session files in the 2026-09-08 and 2026-09-09 date directories counted `token_usage_record` entries, deduplicated by response ID across files. The source records' requested labels produced the following aggregate:

| Requested label | Effort | Responses | Exact 516 | Exact 1034 | Exact 1552 |
|---|---|---:|---:|---:|---:|
| gpt-6-astra | high | 964 | 101 (10.48%) | 34 | 15 |
| gpt-6-astra | max | 1,224 | 127 (10.38%) | 42 | 10 |

The [aggregate artifact](research/reasoning-counts-20260909.json) contains no account identity, session ID, path or conversation text. Its [standalone reader](research/reasoning_counts.py) accepts explicitly supplied files or directories and reads each active file only through its initial size:

```sh
python3 research/reasoning_counts.py /explicit/path/to/session/date/directory
```

These natural workloads contain different tasks and tool rounds, without correctness annotations or independent backend ground truth. The percentages measure exact-516 incidence, **not substitution rates**. They establish that the signal exists under current requested labels and is worth exposing; they do not validate a GPT-5.5-specific causal explanation for GPT-6.

A subsequent separate live probe through the unchanged official login, model `gpt-6-astra`, effort `max`, reported 29 reasoning tokens and one counted response. Effective model disclosure remained absent. The integration tests also drive the real official binary through SSE and WebSocket fixtures with `[516, 0, 516, 2000, 516]`, verifying three hits, five observations and an independent routing verdict. Fixture data demonstrates the detector's plumbing, while the session aggregate demonstrates the real phenomenon.

## The response body's model label

Every Responses stream states a `model` inside `response.created` and `response.completed`. It is written by the server, so it is disclosed metadata, but it is not the effective-model header: official [PR #12061](https://github.com/openai/codex/pull/12061) (2026-02-18) removed the client's comparison against it "so that we are less likely to have false positive" and to report the "correct slug name", without publishing the offending cases. The plausible reading is that the body label can carry a dated or suffixed variant of the requested slug.

On a ChatGPT login the effective-model header is usually absent (see the live checks below), which left the guard unable to say anything about the everyday route. Version 1.7 therefore carries the label on `model/routing/updated` as `responseLabel` and compares it with a rule built for exactly the false-positive class the official client avoided:

| Requested | Body label | Result |
|---|---|---|
| `gpt-6-astra` | `gpt-6-astra`, `GPT-6-Astra` | consistent |
| `gpt-6-astra` | `gpt-6-astra-2026-09-01`, `gpt-6-astra-codex` | consistent: the label extends the request at a separator |
| `gpt-6-astra` | `gpt-6` | consistent: the label is the bare family |
| `gpt-6-astra` | `gpt-6-astra-mini`, `-nano`, `-lite`, `-small`, `-fast`, `-flash`, `-turbo` | differs: a size tier names another model |
| `gpt-6-astra` | `gpt-4o`, `gpt-5.6-sol`, `gpt-6-astrax` | differs |

The rule is implemented twice, in the native TUI and in the Python probe, with the same test table. A differing label draws an orange line under the footer (`Response labeled gpt-4o · requested gpt-6-astra (body label, not a header)`), ranked below the red effective-model difference, which always wins when present. `/status` shows the label whether or not it differs. The probe exports `body_label` and `body_label_consistent`, and exits `5` when the label differs and nothing was disclosed; a disclosed header keeps deciding the strict status in either direction.

What the label proves is bounded in the same way as the header: it is what the server chose to write. A provider can label a response with the requested slug regardless of the weights behind it, so a consistent label is not verification, and the export keeps `weights_verified: false`. A label from another family is nevertheless the strongest everyday evidence available on this backend, because it is the server's own statement about the response, not an inference from its behaviour.

## Statistical fingerprints with practical potential

The relevant question is whether an endpoint's behavior is consistent with a reference under an understood measurement protocol. A detector can answer that probabilistically without observing server weights. Closed-set ranking, verification against one claimed model, and change detection against yesterday's endpoint are different experiments and require different validation.

### KBF: knowledge-boundary probes

[KBF](https://arxiv.org/abs/2605.29524), revised July 2026, uses stable numerical recall near a model's knowledge boundary, including repeatable wrong values. It reports detecting 155 economically relevant substitutions across 16 production endpoints.[^kbf] The [paper's operational limitations](https://arxiv.org/html/2605.29524v2#S4.SS7) explicitly limit the result to statistical inconsistency, note small control counts, and acknowledge correlated probes in its binomial model.

The [released code and 16 probe sets](https://github.com/Ooo0ption/KBF/tree/481c78da14df4f2b02b43d344dae7199ae08cea0) support a self-calibrated binomial test with a conservative reference-error bound. They include GPT-5.4 and GPT-4.1 mini/nano, but no GPT-6 Astra or GPT-4o reference. The author also reports four successful agent-interface controls, including Codex, in [Appendix C](https://arxiv.org/html/2605.29524v2#A3). This makes KBF a strong candidate for an active Codex audit after enrollment of the intended model; the published GPT-5.4 baseline must not be relabeled GPT-6.

Engineering assessment: prefer private, freshly generated probes and independently held-out repetitions. A local Codex baseline can detect later change, but its initial model label still needs a trusted reference. This audit inspected the scoring code and reference inventory; it did not reproduce the paper's 155-endpoint-pair experiment.

### PAMELA and Verify LLM API

[One Token Is Enough](https://arxiv.org/html/2607.10252v1) studies repeated single-token distributions across 165 models and 40 task/language cells. It reports verification AUC 0.971 and equal-error rate 7.3%; the cross-provider AUC is lower at 0.880. Family classification is a separate, weaker result at 59.5%.[^pamela] These measurements support useful statistical verification and also show why an in-library nearest label is not sufficient validation.

[Verify LLM API](https://github.com/udtu/verifyllmapi/tree/6f84113c49d503793f0aca69964cfad516121577) packages a direct Codex runner. It uses independent ephemeral executions with reasoning `none`, compares against an OpenRouter-derived reference and exposes the actual distance. The author's [20-sample Codex acceptance run](https://verifyllmapi.com/blog/verify-llm-api-skill-test/) correctly ended inconclusive for an unenrolled GPT-5.6 Sol label. The quick threshold has about 11.8% equal-error rate on the data used for selection/calibration, not an independent holdout; wrapper and reasoning differences remain confounders.

This is a working sampling approach for Codex, with finite cost and explicit uncertainty. An Astra/max status band would need its own protocol-matched references and validation before assigning a model name. An active probe's result also applies to those probe requests, not retrospectively to every request in the terminal.

### fpverify: sequential monitoring

[fpverify](https://github.com/Mohamed7415/fpverify/tree/bcd60d955c92efdc6419a628f10de07a6d123ee5) provides sequential betting tests, a budget, early stopping, reference enrollment, and unknown-candidate handling. Its bundled nine-model library was collected through Cursor with an entire ten-question battery. Its [protocol measurements](https://github.com/Mohamed7415/fpverify/blob/bcd60d955c92efdc6419a628f10de07a6d123ee5/experiments/frontier/PROTOCOL.md) show the same model's preferred coin side or number can reverse between a battery and an isolated question. That is direct evidence that protocol matching matters.

The [calibration code](https://github.com/Mohamed7415/fpverify/blob/bcd60d955c92efdc6419a628f10de07a6d123ee5/fpverify/calibrate.py) is more qualified than the README's blanket 1% guarantee: finite-reference uncertainty and benign drift are handled by posterior-predictive simulation. The exact betting guarantee requires an appropriate null distribution; it does not establish a 1% operational false-alarm rate for arbitrary Codex deployments. Its [red-team evaluation](https://github.com/Mohamed7415/fpverify/blob/bcd60d955c92efdc6419a628f10de07a6d123ee5/experiments/run_evaluation.py) uses simulated endpoints. Adopt the sequential design and explicit abstention after measuring real matched references; do not import its confidence claims unchanged.

### Other implementations

| Source | Concrete method | Assessment for Codex |
|---|---|---|
| [LLMmap, USENIX Security 2025](https://www.usenix.org/conference/usenixsecurity25/presentation/pasquini) | Reports over 95% identification across 42 versions with eight interactions; learned fingerprints | Strong original evidence for few-query identification within its evaluated population; newer models need reference/training updates |
| [RouteLens](https://github.com/AI45Lab/RouteLens/tree/b6bbaff753999320c28f170513793473ab0bad29) | Adaptive probe selection, total variation distances, permutation tests, stored endpoint baselines | Useful enrollment and observability design; its [final confidence calculation](https://github.com/AI45Lab/RouteLens/blob/b6bbaff753999320c28f170513793473ab0bad29/apps/proxy/src/fingerprint/enhanced-audit.ts) is a score combination, not a calibrated posterior of model identity |
| [BazaarLink probe-engine](https://github.com/Bazaarlinkorg/LLMprobe-engine/tree/5c41136741ca52b5637879cca7bd0cae07404646) | Short-choice distributions; absolute-fit and abstention gates for sibling-model clusters | More careful than forced nearest-label classification; public V3H bias references inspected here contain GPT-5.6 variants but no Astra/4o pair, so the published cluster accuracy does not validate that pair |
| [APIMaster's Astra verifier description](https://apimaster.ai/blog/verify-gpt-6-api-real) | Dated behavioral verification records with ranked candidates | An existing commercial option, but the public feed omits the complete prompt/configuration protocol; no independent calibration reproduced here |
| [codex-skill's routing verifier](https://github.com/Mauriciog87/codex-skill/blob/bcd48d3b8582f8999fb5c7899f782e3d7ccde83a/.agents/skills/sol-luna-orchestration/scripts/codex-app-server-client.mjs) | Verifies accepted `thread/settings/updated` against a selected execution profile | Useful configuration assurance; the inspected `effectiveRouting` value comes from accepted settings, not the effective backend response |

Related original work, [Real Money, Fake Models](https://arxiv.org/abs/2603.01919), measures misleading model claims at shadow APIs.[^shadow] It supports investigating intermediaries with behavioral evidence; its sampled market cannot be used to infer a substitution rate for official ChatGPT-authenticated Codex.

## Continuation proxies and secondary evidence

[CodexCont](https://github.com/neteroster/CodexCont) and [codexcomp](https://github.com/dzshzx/codexcomp) implement a stronger intervention than a warning: detect selected `518n − 2` counts, hold tentative output, preserve encrypted reasoning and request continuation, then combine multiple upstream rounds. They are concrete existing mitigation experiments. They can change latency, token accounting and answer/tool behavior, and their broader ladder rule conflicts with the 516 hook's healthy higher-rung controls. Model Guard adopts passive detection; these proxies have not been installed or represented as a demonstrated general fix.

The [llmsort author's Codex logprobs experiment](https://github.com/XyraSinclair/llmsort/blob/main/docs/LOGPROBS.md) reports access to sampled-token logprobs at reasoning `none`, but no top alternatives on the ChatGPT backend. That can enrich a fingerprint if independently reproduced under the same protocol. It does not reveal a model name, and the result has not been reproduced in this investigation.

`system_fingerprint` is a [backend-configuration fingerprint](https://developers.openai.com/api/reference/resources/chat), not a public model-weight lookup. Capability errors, latency, vocabulary, reasoning-token counts and encrypted-reasoning presence can all supply diagnostics, but none individually resolves a named backend. Client attestation is similarly directional: the client authenticates its execution to the service; it is not server-to-user attestation of the model that generated an answer.

## Active audit design

A useful next active audit should enroll the expected model and plausible substitutes under the same Codex version, harness, effort, service tier and prompt protocol; include unknown-model rejection; then report separately: the quality score, discrepancy from the expected reference, ranked candidates, and the number/time of probe requests. KBF-style stable probes are a promising starting point, while sequential sampling can limit cost.

Validation must include held-out reference runs, known substitutions, benign configuration changes, different task prompts, and mixtures of substituted traffic. Baselines need a date and a refresh policy. Probe selection and threshold fitting must not use the final evaluation split. Independent probes should carry an explicit validity window because a router can treat simple probes differently from an actual coding task.

Until those references and tests exist for the intended Astra deployment, the implemented combination is useful and concrete: immediate disclosed-route monitoring, passive exact-516 warnings, a rolling history and an export that can support comparative quality investigations. It leaves a clear place for an active statistical audit without presenting an uncalibrated candidate score as fact.

## Recent GPT-4o claims and behavioral checks

The [September 5 original “100% detection” post](https://linux.do/t/topic/2858863) now marks its own method as inaccurate/inapplicable. Its [follow-up](https://linux.do/t/topic/2861622) still infers routing from SVG vocabulary and generated pictures, and includes counterexamples. The [September 9 “strong evidence” post](https://linux.do/t/topic/2877589) infers GPT-4o from Plan Mode behavior. None of these reports provides a backend model identifier establishing GPT-4o. These observations can motivate investigation of output quality; they do not identify the model.

[ModelTrace](https://github.com/xqy2006/ModelTrace), audited at `60949ef522a84f66b1236b459308b48028d36949`, uses three numeric-output probes. Its author describes the result as attribution within a fixed candidate set; that set contains 13 models and **no GPT-4o**. An unknown model is nevertheless assigned to an existing candidate. [hlwy-ai-checker](https://github.com/hanlinwenyuan/hlwy-ai-checker) also explicitly describes its output as statistical consistency, not proof of model identity.

Our offline negative controls used ModelTrace's unchanged algorithm, NumPy 2.3.3, and its pinned unified bank. No model was called for these controls:

| Input, three sequences of 310 integers each | Tool's top candidate | Displayed closed-set probability |
|---|---|---|
| Python PRNG, uniform 1–355, seed 20260909 | gpt-6-astra | 87.59% |
| Repeated number 42 | claude-opus-4-8 | 58.33% |
| Counting from 1 to 310 | gpt-5.4 | 99.94% |

These are out-of-distribution inputs, deliberately outside the model-generated challenge protocol. They demonstrate why the displayed percentage must not be interpreted as probability that a named model actually served a request. They do not measure the tool's accuracy on valid model outputs. A real three-probe run through the installed official Codex, requesting `gpt-6-astra` at the unchanged `max` effort, ranked `gpt-6-astra` first at 99.12%. Effective model metadata was absent for all three requests. This supports behavioral similarity in that experiment, not backend verification.

The separate research paper [One Token Is Enough](https://arxiv.org/abs/2607.10252) reports probabilistic identification/verification from repeated single-token samples; it does not supply an error-free per-turn model oracle. [The candy-eval discussion](https://github.com/router-for-me/CLIProxyAPI/discussions/3937) studies answer quality and repeated reasoning-token counts such as 516, rather than establishing a particular backend identity. Model Guard presents exact-516 observations as a separate heuristic and keeps the strict routing verdict independent. A quality anomaly can warrant investigation without identifying a particular replacement model.

## Community approaches considered

| Approach | Useful part | Boundary for this task |
|---|---|---|
| [sh-ai-x/codex-statusline](https://github.com/sh-ai-x/codex-statusline) | Configure stock model/context/usage footer items | A configured model is not backend evidence; no arbitrary command renderer |
| [mullller/codex-hud](https://github.com/mullller/codex-hud) | Use tmux to keep a HUD visible around stock Codex | Latest-session-file selection is insufficient for strict per-terminal routing attribution |
| [brandonwie/codex-hud](https://github.com/brandonwie/codex-hud) | Optional patched native footer and version checks | Maintaining a patched binary ties upgrades to custom runtime builds |
| [Every Code](https://github.com/just-every/code) | Community Codex fork; its author linked automatic route detection in #11189 | A full independent fork is broader than the small pinned native extension used here; 1.7 adopts its idea of reading the body label, with a stricter comparison and a lower rank than the header |
| Capture full SSE/WS TRACE and inspect model fields | Useful for reproducing old reports | Can persist prompts and tool outputs; `response.model` is not the current client's effective-model authority |

Model Guard uses a pinned native Codex source extension. A tmux layer was rejected after it altered mouse-wheel behavior and bypassed resume/fork; native rendering removes both failure mechanisms. The maintenance cost is explicit: source pins, reviewable patches, matching helper binaries and release checksums. No source code was copied from these community projects.

Every Code was additionally inspected at `07533447f713d39763047543cc19e1015a3a6a1e`: its [stream parser](https://github.com/just-every/code/blob/07533447f713d39763047543cc19e1015a3a6a1e/code-rs/core/src/client.rs) reads `response.created.response.model`, and its [comparison](https://github.com/just-every/code/blob/07533447f713d39763047543cc19e1015a3a6a1e/code-rs/core/src/codex/streaming.rs) accepts any nonempty hyphen suffix of the requested model. This can conceal a meaningfully different suffixed identifier, so 1.7 treats a size-tier suffix as a different model and shows every label in `/status`; the label never outranks the header.

## What official Codex 0.153.4 exposes

Re-checked against the 0.155.1 source (`be2951ea34f0d295ed0becf97079f92fa5f6950e`, 2026-09-22): unchanged. Stock still discards the response body's `model` label (`process_sse_ignores_response_model_field_in_payload` remains in `codex-api/src/sse/responses.rs`), and the only stock warning is the header-based reroute, which is still worded as a cyber-safety fallback whatever the actual reason. Hooks arrived in 0.155 but receive the requested model only. The label observed in the wild that month was `gpt-5.6-luna` on a `gpt-6-astra` request, another family, which the rule below shows in orange.

**The client changes the model itself (1.10).** Stock 0.155.1 carries a client-side switch that no server disclosure can reveal: when the account's rate-limit read returns a `rate_limit_upsell` banner of type `luna_reserve`, the TUI's `apply_backend_banner_fallback` ([`tui/src/app/backend_banner_fallback.rs`](https://github.com/openai/codex/blob/be2951ea34f0d295ed0becf97079f92fa5f6950e/codex-rs/tui/src/app/backend_banner_fallback.rs)) sends `thread/settings/update` with model `gpt-reserve` (`LUNA_RESERVE_MODEL` in [`tui/src/model_catalog.rs`](https://github.com/openai/codex/blob/be2951ea34f0d295ed0becf97079f92fa5f6950e/codex-rs/tui/src/model_catalog.rs), hidden from the picker, described by the catalogue as a "fast and affordable agentic coding model" whose instructions call it "an agent based on GPT-5"), adds one info line, and switches back when a later read reports ordinary usage. Banners with `blocked_model_slug` and `fallback_model_slugs` switch to the first listed candidate the same way, and the queued prompt is rewritten to the new model (`apply_reserve_fallback_to_pending_turn`). Every later request is made for `gpt-reserve` and labeled `gpt-reserve`, so the request/disclosure comparison of 1.7–1.9 stays green. A session-file audit on 2026-09-27 found five interactive threads that spent 2026-09-26 08:31–16:13 UTC and 2026-09-27 06:42–20:39 UTC on `gpt-reserve` this way, each entry preceded by a `token_count` record whose rate limits read `limit_id: premium`, `has_credits: false`, with the spawned sub-agent threads inheriting the model. The guard now compares the request with the user's selection as well, declines these switches, and stops the thread on any difference; see the runtime design below.

The official [`tui.status_line` setting](https://learn.chatgpt.com/docs/config-file/config-reference) accepts built-in item identifiers. The exact release's [status item enum](https://github.com/openai/codex/blob/3d2ee51ca2d5db578f328aa75e20aa22c0197c9a/codex-rs/tui/src/bottom_pane/status_line_setup.rs) has no external renderer or account-email item. A plugin cannot register a Claude-style native `statusLine.command`.

For routing, these are the relevant source boundaries:

- [Responses parser](https://github.com/openai/codex/blob/3d2ee51ca2d5db578f328aa75e20aa22c0197c9a/codex-rs/codex-api/src/sse/responses.rs): effective model extraction checks `response.headers` first, then top-level event `headers`. Both accept case-insensitive `openai-model` / `x-openai-model`, including array-valued headers. It does **not** use `response.model` for effective identity.
- [WebSocket transport](https://github.com/openai/codex/blob/3d2ee51ca2d5db578f328aa75e20aa22c0197c9a/codex-rs/codex-api/src/endpoint/responses_websocket.rs): handshake/model metadata becomes a `ServerModel` event. The handshake's model can be reused on that connection; this is a report, not a fresh attestation of every generated token.
- [Core session](https://github.com/openai/codex/blob/3d2ee51ca2d5db578f328aa75e20aa22c0197c9a/codex-rs/core/src/session/mod.rs): matching server models are logged at INFO; differing models produce a warning and a reroute event. The warning prose/reason is cyber-specific even though the comparison is a generic mismatch. Model Guard therefore does not infer the cause or model strength from that prose.
- [Turn processing](https://github.com/openai/codex/blob/3d2ee51ca2d5db578f328aa75e20aa22c0197c9a/codex-rs/core/src/session/turn.rs): model warnings can be suppressed after the first mismatch within a turn. Model Guard latches that mismatch through the turn; absence of another event is not proof of recovery.
- [Public model notifications](https://github.com/openai/codex/blob/3d2ee51ca2d5db578f328aa75e20aa22c0197c9a/codex-rs/app-server-protocol/src/protocol/v2/model.rs): `model/rerouted` identifies the thread, turn, requested and reported models. It provides mismatch evidence, not a positive confirmation stream for matching requests.

The choice of effective headers is deliberate: official [PR #12061](https://github.com/openai/codex/pull/12061), merged February 18, removed `response.model` checks to reduce false positives and use the correct model slug. Merely restoring that old check would not solve routing verification.

These findings are specific to the inspected release. Later versions may change log targets or protocol details. Unknown/missing fields must remain absent in diagnostics; they must not be filled from the selected model or an older turn, or turned into a permanent warning banner.

## Runtime design (1.7, stopping since 1.10)

```text
your terminal
  └─ native Codex TUI and its normal app-server
       ├─ existing status line: selected/requested model, effort, account
       ├─ conditional routing/reasoning warning
       └─ original provider, TLS, authentication and request path
```

The native source extension emits `model/routing/updated` with thread, turn and sampling-request IDs, the actual requested model/provider/effort/tier, an optional effective server model and an optional response body label. It consumes the transport's existing `ServerModel` events; the body label is carried as its own field and never becomes the server model. Missing disclosure remains missing. New sampling requests clear a positive disclosure; a mismatch is latched through the turn. Completed-turn warnings say `last turn`.

Each widget consumes only its own visible thread. Hidden `threadSource=system` title generation and child threads cannot replace the model. Replay is excluded from live observations. Reasoning usage comes from `rawResponse/completed`, deduplicated by response ID, and is scoped to model/provider/effort/tier/account. Account identity uses Codex's native state. There is no extra account polling, live protocol adapter, state-file scraping, provider proxy or transport logging.

Normal display reuses the existing footer without adding a row. A disclosed different model adds a red warning; a body label of another family or size tier adds an orange one; at high or greater effort, at least three of five recent measured responses with exactly 516 reasoning tokens add an amber heuristic warning. A single hit does not change the default interface. `/status` provides full-sentence counts and evidence boundaries. The heuristic threshold is a product policy, not a calibrated probability of routing or truncation.

Since 1.10 the guard also holds the **selected model**: the configured default, or the last manual `/model` choice; a configured `review_model` is the one other model a request may name. Codex's automatic switches (the Luna Reserve fallback and banner-announced fallbacks) are declined while the guard is active, with a warning naming the blocked and offered models and an amber footer line until usage recovers; Codex's switch back to the saved model restores the selection. A sampling request for another model, an effective-model header naming another model, or a body label of another family or size tier is a **stop**: the running turn is interrupted through the normal `turn/interrupt` path, a red error is written to the history, a bold red `STOPPED` line stays under the footer, and every further turn (prompt, review, compaction) is held with the composed text returned to the composer until the user selects a model again. The causes rank request > header > label, and a stronger cause replaces a weaker one without a second interruption; the 516 heuristic stops only with `halt_on_reasoning_anomaly`. A configured thread whose model is not the selection stops before its first turn, which is how a thread restored on `gpt-reserve` is caught, and the reserve model is flagged in red whenever it is active. Selection and stop are remembered per thread for the life of the process, so a widget rebuilt for a thread switch or a side conversation restores them; a turn that starts on a stopped thread through any other route is interrupted; routing notifications for a thread no widget is showing are judged against that thread's remembered selection and its turn is interrupted through the same request; a model the server pushes into the thread's settings and a request in flight across an account change are judged like any other. Codex's switch back from Reserve is allowed only to the selection and never lifts a stop. None of this changes the model: the guard never sends a settings update, a retry or an inference request of its own.

`resume`, `fork`, profiles, local providers and native remote connections follow Codex's original CLI, loader and directory semantics. An explicit remote server needs the metadata extension to disclose the complete request observations. Native terminal event handling is unchanged. Installation atomically switches the existing executable symlink; it does not insert shell PATH blocks. Source, schema and build details are in [native/README.md](native/README.md).

## Standalone diagnostics

`/status` inside the conversation shows live observations. The old external `status` and `check` commands now direct callers to this native entry.

`model-guard-codex probe --json` makes one separate ephemeral read-only request using the official app-server's own authentication and current workspace configuration. It consumes provider quota and cannot certify an existing session. Optional `-m MODEL -r EFFORT` applies only to the probe. Its private stdio/Unix-WebSocket adapter is used solely for this explicit diagnostic, never around the interactive TUI.

The probe returns `0` for matching effective-model disclosure, `2` for a disclosed difference, `3` for missing disclosure, `4` for an unavailable/failed probe, and `5` for a body label that differs while nothing was disclosed. JSON omits account identifiers and conversation text. `weights_verified` is always false: matching provider metadata is not independent verification of weights.

## Validation

Regression tests use isolated Codex homes and local Responses fixtures without a login. They cover headers and WebSocket metadata, metadata-free replies whose body deliberately claims GPT-4o (a label mismatch, never a verified route), visible-thread attribution, account/settings boundaries, duplicate response IDs, native layout snapshots and real PTY startup/resume/fork/paste/resize. Source build checks and limitations are recorded in the native build documentation.

GPT-4o replies in these tests are **synthetic fixtures**. The tests validate detection of disclosed differences; they do not establish that OpenAI routed this machine's traffic to GPT-4o.

## Live check on the development machine

A minimal real-account request on nomad-u selected `gpt-6-astra`. The adapter received account identity and quota data but no effective server-model report, so the route remained unverified. No downgrade was established. Account identifiers and tokens are deliberately omitted from this public record.

A 1.7 probe on 2026-09-10 through the installed native build, model `gpt-6-astra` at `max`, again received no effective-model header; the response body was labeled `gpt-6-astra`, consistent with the request, and the probe reported `unverified` with `body_label_consistent: true`. That is the everyday shape of this backend: the label is present on every response, the header is not.

An additional default WebSocket probe confirmed that neither handshake model metadata nor a core effective-model event was present. A separate diagnostic using the official app-server with an invocation-only OpenAI provider alias and WebSockets disabled returned SSE `response.model=gpt-6-astra`, with no effective model report. The diagnostic did not change the user's provider/model configuration files. Raw transport data was parsed in memory; only allowlisted results were retained. The native runtime continues to use the original provider and transport.

## Principal research sources

The sections above link the exact inspected source files and original community reports, pinning repositories where available. The following citations retain authorship and dates for the principal measurements.

[^kbf]: Yijia Fang, Yiqing Feng, Bingyu Li and Mingxun Zhou. [KBF: Knowledge Boundary as Fingerprint for Language Model and Black-Box API Auditing](https://arxiv.org/abs/2605.29524v2). arXiv, revised 2026-07-25.
[^pamela]: Tomas Bruckner. [One Token Is Enough: Fingerprinting and Verifying Large Language Models from Single-Token Output Distributions](https://arxiv.org/abs/2607.10252). arXiv, 2026-07-11. [Dataset and reproduction archive](https://zenodo.org/records/21278557).
[^shadow]: Yage Zhang, Yukun Jiang, Zeyuan Chen, Michael Backes, Xinyue Shen and Yang Zhang. [Real Money, Fake Models: Deceptive Model Claims in Shadow APIs](https://arxiv.org/abs/2603.01919v2). arXiv, revised 2026-03-05.
[^hook]: bentoner. [codex-516-hook: measurements and implementation](https://github.com/bentoner/codex-516-hook/tree/3729959d72544ef3f9db62b696306b1dbdae04e9). Author measurements dated 2026-07-05; source inspected 2026-09-09.
