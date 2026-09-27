# Codex 路由检查：调研与实测

核实时间：2026-09-09 至 09-10（2026-09-22 对照 0.155.1 源码复核，结论不变：官方仍丢弃响应正文的 `model` 标签，唯一的官方警告仍只看响应头；本月实测看到的标签是请求 `gpt-6-astra` 时正文标注 `gpt-5.6-luna`，属另一家族，走橙色提示）。机器：nomad-u。官方 Codex 0.153.4，源码提交 `3d2ee51ca2d5db578f328aa75e20aa22c0197c9a`。[完整英文技术说明](ROUTING.md)。

## 结论

**有可行的辅助检测办法。1.5 版已经接入恰好 516 个推理 token 的被动监测和告警，1.7 版把响应正文里的模型标签作为第二层披露信号接入，统计指纹也能在缺少模型响应头时检查行为是否偏离基线。**逐请求准确指出未披露的 GPT-4o 身份仍未解决，但这不妨碍先把有用的异常信号显示出来。

服务端重路由确实有公开案例：[Codex #11189](https://github.com/openai/codex/issues/11189) 中有请求 5.3-Codex、收到 5.2 标识的复现及 OpenAI 维护者回应。但它不能证明当前存在统一的 GPT-4o 回退。

## 516：可以立即采用的被动信号

#30364 原始报告分析了 390,195 条 token-count 记录，发现 GPT-5.5 标签下的 516 峰值明显偏高。[作者的 Reddit 说明](https://www.reddit.com/r/codex/comments/1ujqo09/the_gpt55_516_reasoning_tokens_issue_is_not/)同时明确：统计异常本身尚未证明截断。[GitHub issue](https://github.com/openai/codex/issues/30364) 在本次检查时已关闭；关闭状态本身不说明原因，也不能证明所有服务部署已经恢复。

现成实现是 [bentoner/codex-516-hook](https://github.com/bentoner/codex-516-hook/tree/3729959d72544ef3f9db62b696306b1dbdae04e9)，通过 Stop 钩子读取已完成响应的推理用量并警告。作者报告：某个触发谜题在 Codex 订阅认证和 API-key 认证下各有 18/20 次命中 516，而直接调用 Responses API 为 0/20；另一个较难谜题的 6 次 516 响应中有 5 次答错。样本较小、任务特定，但足以支持把这个信号用于实用提示。[^hook]

同一项目也记录了重要的对照：1034／1552／2070／… 会出现在正确回答中。因此，**恰好 516 应比整个 `518n − 2` 序列获得更高告警权重**。自然结束也可能恰好落在 516；选工具的中间响应短，也不必然有错。推理 token 为正同样不能区分旗舰推理模型与较小的推理模型。

[NickalasLight 的分析仓库](https://github.com/NickalasLight/codex-reasoning-bug-512-token/tree/aaa5995d5ebcd8dabc64c95dcc32a3350183e357)提供用量统计脚本和前后对照资料。[后续 Reddit 帖](https://www.reddit.com/r/codex/comments/1upxyjl/psa_for_anyone_that_thinks_they_have_solved_the/)指出，糖果题改善并没有证明普通工作负载的平均推理改善。合理的做法是保留被动提示，同时用有代表性的真实任务评估任何修复。

### Model Guard 的实现规则

原生界面使用 `rawResponse/completed` 提供的响应 ID 和推理用量，按唯一 ID 去重，避免重复快照、上下文重算造成误计数。显式独立探针还兼容官方 `thread/tokenUsage/updated`，按累计用量增量保守计数。

| 观察结果 | 原生显示或处理 |
|---|---|
| 合法推理计数，包括 0 | 放在 `/status` |
| high 及以上强度下单次恰好 516 | 保留诊断，正常页脚不变色 |
| 上述强度下，最近 5 次有效响应至少 3 次恰好 516 | 黄色推理异常提示，用完整句子表达 |
| 较低或未知强度下的 516 | 计数，不告警 |
| 更高的 `518n − 2` 档位 | 原生界面不告警；独立探针导出阶梯计数 |
| 缺失或无效用量 | 不补造计数，正常页脚不显示占位符 |

3/5 没有声称经过误报率标定，也不意味着三次回答都错了。统计覆盖最近最多 20 次有效响应，包含工具调用中间轮次；模型、服务商、强度、服务档位或账号变化时清空。账号变化时正在执行的旧请求会被隔离到下一轮。这是诊断信号，不用于账单核算。

`probe --json` 在 `reasoning` 中标明 `evidence: "heuristic"`，路由退出码保持独立。服务端披露一致时仍可能有推理异常；原生页脚优先显示已披露的模型差异。不自动注入提示、续推理、重试或切换模型。

### 本机复现

对 2026-09-08、2026-09-09 两个会话日期目录的 50 个文件做元数据审计，只计 `token_usage_record`，跨文件按响应 ID 去重，得到下表。模型列均为记录中的请求标签。

| 请求标签 | 强度 | 响应数 | 恰好 516 | 恰好 1034 | 恰好 1552 |
|---|---|---:|---:|---:|---:|
| gpt-6-astra | high | 964 | 101（10.48%） | 34 | 15 |
| gpt-6-astra | max | 1,224 | 127（10.38%） | 42 | 10 |

[汇总结果](research/reasoning-counts-20260909.json)不含账号、会话 ID、路径或对话正文。[独立读取脚本](research/reasoning_counts.py)只接受明确给出的文件或目录；对仍在写入的文件，读取范围限制在打开时的大小：

```sh
python3 research/reasoning_counts.py /明确指定的会话日期目录
```

这批自然工作负载混合了不同任务和工具轮次，没有逐题正确性标注，也没有独立的底层模型真值。比例表示 516 的出现率，**不是换模型的比例**。它验证了当前请求标签下确实存在该信号，值得显示；不能据此把 GPT-5.5 的因果解释直接套到 GPT-6。

随后通过原有官方登录、`gpt-6-astra / max` 发出的一次独立探针，成功读到 29 个推理 token、计数 1 次，仍未收到有效模型披露。自动化测试则让真实官方二进制经过 SSE、WebSocket 假服务端接收 `[516, 0, 516, 2000, 516]`，验证三次命中、五次观测和独立的路由结论。假服务端证明检测链路正确，本机会话汇总证明实际存在这个现象。

## 响应正文里的模型标签

每次 Responses 流的 `response.created` 和 `response.completed` 里都写着一个 `model`。它由服务端写入，属于服务端披露的信息，但不是有效模型响应头：官方 [PR #12061](https://github.com/openai/codex/pull/12061)（2026-02-18）删掉了客户端对它的比对，理由是「减少误报」和「使用正确的模型标识」，没有公开具体误报案例。合理的推断是：正文标签可能带日期或后缀，与请求的写法不完全一样。

ChatGPT 登录下服务端通常不发有效模型响应头（见下文本机实测），守卫因此对日常请求一直说不出话。1.7 版把这个标签作为 `responseLabel` 放进 `model/routing/updated`，并用一条专门针对官方所担心的误报类型设计的规则来比对：

| 请求 | 正文标签 | 结果 |
|---|---|---|
| `gpt-6-astra` | `gpt-6-astra`、`GPT-6-Astra` | 一致 |
| `gpt-6-astra` | `gpt-6-astra-2026-09-01`、`gpt-6-astra-codex` | 一致：标签在分隔符处延长了请求 |
| `gpt-6-astra` | `gpt-6` | 一致：标签是光秃秃的家族名 |
| `gpt-6-astra` | `gpt-6-astra-mini`、`-nano`、`-lite`、`-small`、`-fast`、`-flash`、`-turbo` | 不同：缩小档位是另一个模型 |
| `gpt-6-astra` | `gpt-4o`、`gpt-5.6-sol`、`gpt-6-astrax` | 不同 |

这条规则在原生界面和 Python 探针里各实现一次，共用同一张测试表。标签不同时页脚下方出现一行橙色提示（`响应标注 gpt-4o · 请求 gpt-6-astra（正文标签，非响应头）`），级别低于红色的有效模型差异；响应头存在时永远以响应头为准。`/status` 无论是否不同都显示标签。探针导出 `body_label` 和 `body_label_consistent`，没有披露而标签不同时退出码为 `5`；有响应头时严格结论仍由响应头决定。

标签能证明什么，边界和响应头一样：它只是服务端选择写下的内容。服务商完全可以不管背后权重、照请求写标签，所以标签一致不是验证，导出里 `weights_verified` 保持 `false`。但标签换了一代，是这个后端上日常能拿到的最强证据，因为它是服务端对这次响应的自述，不是从行为推断出来的。

## 有实际潜力的统计指纹

统计检测可以检查端点行为是否与已知基线一致，不需要先看见服务端权重。候选库内分类、验证某个声明模型、检查同一端点相较昨天是否变化，是不同实验，不能共用未经验证的置信度解释。

### KBF：知识边界探针

[KBF](https://arxiv.org/abs/2605.29524) 的 2026 年 7 月修订版利用模型对冷门数值的稳定回忆，包括重复出现的错误值。论文报告在 16 个生产端点上检测出 155 组经济上有意义的替换。[^kbf][局限一节](https://arxiv.org/html/2605.29524v2#S4.SS7)明确把结论限定为统计不一致，并指出同模型对照数量有限、题目相关性会影响二项检验。

[源码与 16 套参考题](https://github.com/Ooo0ption/KBF/tree/481c78da14df4f2b02b43d344dae7199ae08cea0)提供自校准检验和保守的参考误差上界，包含 GPT-5.4、GPT-4.1 mini/nano，没有 GPT-6 Astra 或 GPT-4o 参考。[附录 C](https://arxiv.org/html/2605.29524v2#A3)还报告包括 Codex 在内的四项 agent 界面对照通过。这使它成为建立目标模型基线后值得采用的主动审计方案；GPT-5.4 题库不能直接改名为 GPT-6。

工程判断：使用新生成的私有题，并保留独立验证样本。自行建立的 Codex 基线能检测未来变化，但其最初的模型标签仍需要可信参照。本次核查了评分源码与题库清单，没有重跑论文全部 155 组实验。

### PAMELA 与 Verify LLM API

[One Token Is Enough](https://arxiv.org/html/2607.10252v1)研究 165 个模型、40 个任务／语言组合的单 token 分布，报告验证 AUC 为 0.971、等错误率为 7.3%；跨服务商 AUC 降至 0.880。家族分类是另一项较弱结果，准确率为 59.5%。[^pamela]这些数字支持统计验证有用，也说明不能只看最近的候选标签。

[Verify LLM API](https://github.com/udtu/verifyllmapi/tree/6f84113c49d503793f0aca69964cfad516121577)已有直接运行 Codex 的采样器：每次使用独立临时会话、思考强度 `none`，与 OpenRouter 来源的参考分布比较。[作者的 20 样本验收](https://verifyllmapi.com/blog/verify-llm-api-skill-test/)对未收录的 GPT-5.6 Sol 正确返回了无结论。快速阈值在用于选题、校准的数据上约有 11.8% 等错误率，并非独立留出验证；harness 和强度差异仍会影响分布。

它是能运行、成本有限、会表达不确定性的 Codex 采样方案。要给 Astra/max 的状态栏附上模型候选，需要补齐相同协议的参考和验证。独立发题得到的结果属于那些探针请求，不能追溯认证终端里所有历史请求。

### fpverify：序贯检测

[fpverify](https://github.com/Mohamed7415/fpverify/tree/bcd60d955c92efdc6419a628f10de07a6d123ee5)实现序贯下注检验、预算限制、提前结束、基线登记和未知候选处理。随附九模型参考来自 Cursor 的十题套卷。[协议实测](https://github.com/Mohamed7415/fpverify/blob/bcd60d955c92efdc6419a628f10de07a6d123ee5/experiments/frontier/PROTOCOL.md)显示，同一个模型在整套题和单独提问下，偏好的硬币面或数字会反转，直接证明了协议对齐的重要性。

其[校准源码](https://github.com/Mohamed7415/fpverify/blob/bcd60d955c92efdc6419a628f10de07a6d123ee5/fpverify/calibrate.py)比 README 的统一「1%」保证更谨慎：用后验预测模拟吸收有限参考样本误差与良性漂移。严格下注保证依赖合适的零假设分布，不能直接保证任意 Codex 部署的实际误报率为 1%。[对抗评估](https://github.com/Mohamed7415/fpverify/blob/bcd60d955c92efdc6419a628f10de07a6d123ee5/experiments/run_evaluation.py)使用模拟端点。序贯设计和主动放弃判定值得采用，置信度数字需要经过实际匹配参考的验证。

### 其他可借鉴的实现

| 来源 | 具体办法 | 对 Codex 的适用性 |
|---|---|---|
| [LLMmap，USENIX Security 2025](https://www.usenix.org/conference/usenixsecurity25/presentation/pasquini) | 学习式指纹，报告八次交互区分 42 个版本、准确率超过 95% | 少量查询识别的强原始证据；新模型需要补参考和训练 |
| [RouteLens](https://github.com/AI45Lab/RouteLens/tree/b6bbaff753999320c28f170513793473ab0bad29) | 自适应选题、总变差距离、置换检验、保存基线 | 基线管理与观测方式可借鉴；[最终 confidence](https://github.com/AI45Lab/RouteLens/blob/b6bbaff753999320c28f170513793473ab0bad29/apps/proxy/src/fingerprint/enhanced-audit.ts)是组合分数，没有完成模型身份概率标定 |
| [BazaarLink probe-engine](https://github.com/Bazaarlinkorg/LLMprobe-engine/tree/5c41136741ca52b5637879cca7bd0cae07404646) | 短选择题分布；绝对拟合下限及放弃判定机制 | 比强制选最近标签更谨慎；本次审计的公开 V3H 偏好参考有 GPT-5.6 系列，没有 Astra/4o 对照，已有簇准确率不能直接迁移 |
| [APIMaster 的 Astra 检测说明](https://apimaster.ai/blog/verify-gpt-6-api-real) | 带时间的行为验证记录和候选排序 | 已有商业方案，但公开数据缺完整题目／配置，未在本次独立复现其标定 |
| [codex-skill 的 routing verifier](https://github.com/Mauriciog87/codex-skill/blob/bcd48d3b8582f8999fb5c7899f782e3d7ccde83a/.agents/skills/sol-luna-orchestration/scripts/codex-app-server-client.mjs) | 核对 `thread/settings/updated` 是否接受指定执行配置 | 配置验证有用；代码中的 effectiveRouting 来自已接受设置，不是响应背后的有效模型 |

[Real Money, Fake Models](https://arxiv.org/abs/2603.01919)测量了 shadow API 的模型声明问题，支持用行为证据调查中间商。[^shadow]其市场样本不能用来估计官方 ChatGPT 登录 Codex 的替换率。

## 续推理代理与其他线索

[CodexCont](https://github.com/neteroster/CodexCont)和 [codexcomp](https://github.com/dzshzx/codexcomp)已有更强干预：检测选定的 `518n − 2` 计数，暂存答案，保留加密推理项、发起续推理，然后把多次上游响应合并。它们是具体可运行的修复实验，但会改变延迟、token 统计及答案／工具行为，其更宽的固定档位规则与 516 钩子的健康高档位对照存在冲突。Model Guard 采用被动检测；本次没有安装这些代理，也没有把它们当成已证明的通用修复。

[llmsort 作者的 Codex logprobs 实验](https://github.com/XyraSinclair/llmsort/blob/main/docs/LOGPROBS.md)报告在 `none` 强度下能读取已采样 token 的概率，但拿不到多个候选 token 的概率。相同协议下独立复现后，它可以丰富指纹；它不直接给出模型名，本次也尚未复现这项能力。

`system_fingerprint` 是[后端配置指纹](https://developers.openai.com/api/reference/resources/chat)，没有公开的模型权重查找表。功能支持情况、错误格式、延迟、用词、推理计数、加密推理项的存在都可以作为诊断线索，但单项不能确定具体模型。客户端 attestation 的方向也不同：是客户端向服务端证明其执行身份，不是服务端向用户证明生成模型。

## 主动审计应如何落地

主动审计应针对相同 Codex 版本、harness、思考强度、服务档位和提问协议，登记目标模型及合理的替代候选，支持拒绝未知模型，分别报告质量分、相对目标基线的偏离、候选排序，以及探针数量和时间。KBF 的稳定题是值得优先尝试的起点，序贯采样可控制开销。

验证集应包含独立留出的同模型样本、已知替换、良性配置变化、不同任务和混合路由。基线记录日期及更新策略；最终验收数据不能同时用于选题和调阈值。独立探针应标明有效范围，因为服务端可能对短探针和真正的编程任务采用不同路由。

目标 Astra 部署的这些参考与验证尚未建立时，当前已经落实的组合仍然有用：即时的已披露路由监测、被动 516 提示、滚动统计及可导出的比较依据。它为主动统计审计保留接口，同时如实区分观测、告警与具体模型身份。

## 最近“疑似 4o”案例的依据

[9 月 5 日原帖](https://linux.do/t/topic/2858863) 已由作者标注原方法有偏差、不再适用。[后续研究](https://linux.do/t/topic/2861622) 仍主要通过 SVG 用词和画面判断，回复中也有反例。[9 月 9 日所谓强力证据](https://linux.do/t/topic/2877589) 则从 Plan Mode 的表现推测模型。这些报告值得作为质量问题线索，但没有提供足以确认 GPT-4o 的服务端模型标识。

不能把“内嵌 SVG”“循环”、短回答、模型自称、知识截止日期或者某道题答错，直接映射成一个实际模型名。

## 社区现成方案

| 方案 | 实际作用 | 本次采用情况 |
|---|---|---|
| [codex-statusline](https://github.com/sh-ai-x/codex-statusline) | 配置官方内置状态项 | 模型选择值不能验证路由 |
| [mullller/codex-hud](https://github.com/mullller/codex-hud) | 通过 tmux 显示状态 | 借鉴显示方式；不用“最新会话文件”判断当前终端 |
| [brandonwie/codex-hud](https://github.com/brandonwie/codex-hud) | 可使用带补丁的原生页脚 | 本插件使用官方二进制 |
| [Every Code](https://github.com/just-every/code) | 从响应正文读取模型，比较并告警 | 1.7 采纳了读正文标签的思路，但比对更严（缩小档位算不同）、级别低于响应头，且标签始终在 `/status` 可见 |
| [ModelTrace](https://github.com/xqy2006/ModelTrace) | 三轮数字输出指纹，推测库内候选模型 | 做了源码审计和实测；不作为确定性路由证据 |
| [hlwy-ai-checker](https://github.com/hanlinwenyuan/hlwy-ai-checker) | 与基准渠道比较统计指纹 | 作者也说明不能单独证明实际模型身份 |
| [糖果测试及 516 讨论](https://github.com/router-for-me/CLIProxyAPI/discussions/3937) | 检查推理表现、观察 token 数 | 1.5 已采纳 516 为独立启发式信号；不将其直接映射成模型名 |

ModelTrace 审计版本为 `60949ef522a84f66b1236b459308b48028d36949`。其候选库有 13 个模型，**没有 GPT-4o**；即使输入来自未知模型，它仍会选择一个已有候选。作者明确说明了这个边界。

我们用原版算法、NumPy 2.3.3、该版本的 unified bank 做了离线反例，每种输入三段、每段 310 个整数，全程没有调用模型：

| 输入 | 工具首选候选 | 库内概率 |
|---|---|---|
| Python 均匀伪随机数，种子 20260909 | gpt-6-astra | 87.59% |
| 全部为 42 | claude-opus-4-8 | 58.33% |
| 从 1 数到 310 | gpt-5.4 | 99.94% |

这些输入故意超出正常模型挑战协议，用于验证未知输入的处理；它们不是对正常测试准确率的测量。结果说明，库内概率不能当作“实际用了这个模型”的概率。相关论文 [One Token Is Enough](https://arxiv.org/abs/2607.10252) 也研究统计识别，并未提供无误差的逐轮身份判定。

## 本机实测

1. 默认 WebSocket 连接、请求 `gpt-6-astra`：没有拿到有效模型头或官方路由事件。
2. 单独做 SSE 诊断：响应正文是 `gpt-6-astra`，有效模型头仍缺失。只对该诊断进程设置临时服务商别名及传输选项，没有修改日常配置。
3. 通过官方 Codex 执行三轮 ModelTrace 数字挑战，保留日常 `max` 强度：首选候选为 `gpt-6-astra`，库内概率 99.12%；三轮都没有有效模型披露。这是行为相似结果，不能验证底层权重。

4. 2026-09-10 用装好的 1.7 原生构建再探针一次（`gpt-6-astra / max`）：仍没有有效模型响应头；响应正文标签是 `gpt-6-astra`，与请求一致，探针结论 `unverified`、`body_label_consistent: true`。这就是这个后端的日常形态：每次响应都有正文标签，响应头没有。

因此，本次没有证实本机被路由到 GPT-4o，也没有足够证据保证不存在隐藏路由。公开记录不包含账号、凭据、会话文本或原始传输日志。

## 客户端自己换模型（1.10 处理的失效方式）

官方 0.155.1 有一条服务端披露看不出来的客户端切换：账号额度读取返回 `luna_reserve` 类型的 `rate_limit_upsell` 横幅时，TUI 的 `apply_backend_banner_fallback`（[`tui/src/app/backend_banner_fallback.rs`](https://github.com/openai/codex/blob/be2951ea34f0d295ed0becf97079f92fa5f6950e/codex-rs/tui/src/app/backend_banner_fallback.rs)）会发 `thread/settings/update` 把会话模型改成 `gpt-reserve`（[`tui/src/model_catalog.rs`](https://github.com/openai/codex/blob/be2951ea34f0d295ed0becf97079f92fa5f6950e/codex-rs/tui/src/model_catalog.rs) 里的 `LUNA_RESERVE_MODEL`，在模型列表里隐藏；模型目录称它为「快而便宜的编码模型」，其系统提示自称「基于 GPT-5 的 agent」），只在历史里加一行提示，之后某次读取报告常规额度恢复时再换回去。带 `blocked_model_slug` 和 `fallback_model_slugs` 的横幅同样会换到候选列表里的第一个，排队中的提示也被改写到新模型（`apply_reserve_fallback_to_pending_turn`）。此后每次请求本身就是发给 `gpt-reserve` 的，服务端标签也是 `gpt-reserve`，1.7–1.9 的「请求对披露」比较因此一直是绿的。2026-09-27 对会话文件的核查发现五个交互会话分别在 2026-09-26 08:31–16:13 UTC 和 2026-09-27 06:42–20:39 UTC 以这种方式跑在 `gpt-reserve` 上，每次进入前的 `token_count` 记录里额度都是 `limit_id: premium`、`has_credits: false`，派生出的子 agent 会话继承了同一个模型。Model Guard 现在同时比对请求和用户的选择、拒绝这些切换，并在任何差异上停住会话；见下节。

## 1.7 的原生实现（1.10 起会停止会话）

官方 [PR #12061](https://github.com/openai/codex/pull/12061) 删除了正文 `response.model` 的判断，以减少误报并使用正确模型标识。Model Guard 沿用现有传输层的有效模型头，添加带会话、轮次和采样请求 ID 的 `model/routing/updated` 元数据事件。请求模型、服务商、强度和服务档位来自该次实际请求；可选的服务端模型和可选的正文标签各自独立记录，缺失就保留缺失，标签永远不会被当成服务端模型。

此前的 tmux 方案影响了鼠标滚轮，并绕过 resume/fork，已被原生扩展替代。当前实现固定官方源码提交，附可审查补丁、协议生成文件、同版本辅助程序和发布校验值；维护代价在[构建说明](native/README.md)明确列出。

正常显示合入原有页脚，不额外占行。服务端披露不同模型时展开红色提示；正文标签换了一代或带缩小档位时展开橙色提示；high 及以上强度下，最近 5 次有效响应中至少 3 次恰好用了 516 推理 token，展开黄色启发式提示。单次命中和缺少披露都放在 `/status`，使用完整句子解释。这一阈值是产品策略，没有经过模型身份或截断概率的校准。

1.10 起 guard 还记住**已选模型**：配置的默认模型，或最近一次手动 `/model` 的选择；配置了 `review_model` 时它是请求可以使用的唯一另一个模型。guard 生效期间，Codex 的自动换模（Luna Reserve 降级和横幅宣布的降级）一律拒绝，历史里写一条警告说明被限和被推荐的模型，页脚常驻琥珀色提示直到额度恢复；额度恢复后 Codex 换回原模型时会恢复这个选择。请求了别的模型、有效模型响应头是别的模型、正文标签是别的一代或缩小档位，任一种都是**停止**：通过正常的 `turn/interrupt` 打断正在跑的一轮，历史里写一条红色错误，页脚常驻一行加粗红色的「已停止」，之后每一轮（提问、评审、压缩）都被拦住，输入的内容退回输入框，直到用户重新选定模型。三种原因按「请求 > 响应头 > 正文标签」排序，更强的原因替换更弱的但不再打断第二次；516 启发式只在打开 `halt_on_reasoning_anomaly` 时才停止。会话配置完成时模型不是已选模型的，会在第一轮之前就停住——在 `gpt-reserve` 上恢复的会话就是这样被抓住的；只要备用模型在用，页脚就有红色提示。已选模型和停止状态按线程记在进程里：切线程、开 side 对话重建界面时原样恢复；停住的线程上经任何别的路径又开了一轮都会被打断；没有界面在看的线程，它的路由通知也按该线程记住的选择判定，并通过同一个请求打断它的那一轮；服务端推进线程设置里的模型、账号切换期间还在飞的请求同样照判。Codex 从 Reserve 换回来只允许换回已选模型，而且不解除停止。这一切都不改模型：guard 自己不发设置更新、不重试、不发推理请求。

每个原生窗口只消费自己当前会话的事件；后台 `threadSource=system` 标题和子代理不会覆盖主模型。回放记录不计入实时检测。推理用量来自 `rawResponse/completed`，按响应 ID 去重，并与模型、服务商、强度、服务档位、账号绑定。新采样请求清除正向披露，本轮已发现的差异保留，结束后标明「上轮」。账号使用 Codex 自身状态，不额外轮询、不读登录文件或会话文件、不记录传输日志。

`resume`、`fork`、profile、本地模型和显式远端都沿用原生命令、配置加载及目录语义；显式远端服务也需要元数据扩展才能提供完整观测。输入和终端事件处理保持原生。安装时原子切换现有可执行文件软链接，不增加 shell PATH 区块。

## 独立诊断

在相应会话内使用 `/status` 查看实时观测。旧的外部 `status`、`check` 命令现在会指向这个原生入口。

`model-guard-codex probe --json` 发起独立、临时、只读请求，使用官方认证和当前目录配置，消耗服务商用量；可用 `-m 模型 -r 强度` 仅调整探针。它不能替已有会话证明路由。私有 stdio／Unix-WebSocket 适配器只用于显式探针，不参与正常交互界面。

退出码：`0` 有效模型披露一致，`2` 不同，`3` 缺少披露，`4` 探针不可用或失败，`5` 没有披露但正文标签与请求不符。JSON 不含账号或对话内容，`weights_verified` 始终为 `false`。

回归验证使用隔离目录和本机假服务端，覆盖 HTTP/SSE、WebSocket、后台标题隔离、账号和设置变化、重复响应 ID、原生布局，以及真实 PTY 的启动、恢复、派生、粘贴和缩放。测试中 GPT-4o 是明确构造的假响应（正文标签不同，不算已验证的路由），只验证差异检测，不是本机遭到隐藏路由的证据。

## 主要研究来源

各节同时链接了所审计的具体源码和原始社区报告；可固定版本的仓库链接已固定到提交。以下记录主要测量来源的作者和日期。

[^kbf]: Yijia Fang, Yiqing Feng, Bingyu Li and Mingxun Zhou. [KBF: Knowledge Boundary as Fingerprint for Language Model and Black-Box API Auditing](https://arxiv.org/abs/2605.29524v2). arXiv, revised 2026-07-25.
[^pamela]: Tomas Bruckner. [One Token Is Enough: Fingerprinting and Verifying Large Language Models from Single-Token Output Distributions](https://arxiv.org/abs/2607.10252). arXiv, 2026-07-11. [Dataset and reproduction archive](https://zenodo.org/records/21278557).
[^shadow]: Yage Zhang, Yukun Jiang, Zeyuan Chen, Michael Backes, Xinyue Shen and Yang Zhang. [Real Money, Fake Models: Deceptive Model Claims in Shadow APIs](https://arxiv.org/abs/2603.01919v2). arXiv, revised 2026-03-05.
[^hook]: bentoner. [codex-516-hook: measurements and implementation](https://github.com/bentoner/codex-516-hook/tree/3729959d72544ef3f9db62b696306b1dbdae04e9). Author measurements dated 2026-07-05; source inspected 2026-09-09.
