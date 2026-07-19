# 定性评分 v2 生产上线阶段复盘

日期：2026-07-19
范围：从 M5 fixture-first、来源能力探测，到东方电缆与指定五股生产写入，再到全局 `on` 读路径切换。

## 结论

本阶段已经完成“可回滚的生产上线”，但尚未完成“35 股全部具备 source-grounded v2 三维评分”。当前 `.env` 为 `QUALITATIVE_V2_MODE=on`，35 股全部进入 v2 选择器；数据库中 6 股存在通过合同复验的 partial v2 行，实际采用 moat/market_pos v2 与 sentiment v1，其余 29 股逐股回退完整 v1。

截至本复盘的只读事实：

- watchlist：35 股；
- `qualitative_scores_v2`：6 行，均由 `gemini-2.5-flash` 于 2026-07-19 生成；
- hybrid 股票：000963、002050、600036、600900、601088、603606；
- 6 行状态均为 `insufficient_data`，因为 sentiment 为 `NULL`；
- 历史 `predictions`：1,859 行，最近日期 2026-07-17，本次 v2 生产写入未改写历史记录；
- `off` 仍是一键回滚路径；过期、hash/身份漂移、损坏或无合法 v2 行均逐股 fail closed 到 v1。

“全局上线”的准确含义是生产选择器和回退机制已经面向 35 股启用，不是 35 股 v2 数据覆盖完成，也不是预测有效性已经得到证明。

## 阶段轨迹

1. M2/M3/M5 建立了 schema、evidence validator、fixture-first 编排、artifact sealing 和失败语义，为生产适配提供了可复用合同。
2. M4/M5 研究路线逐渐叠加 frame、36 股分层、coverage、双模型审计和多轮授权，工程安全性提高，但被错误地当成生产上线的串行前置，推进速度明显下降。
3. source capability probe 证明 CNINFO 传输可用，但精确全文搜索片段对最初 canary 5 股返回零直接证据；45/45 attempts 消耗后 Gemini 0 调用、v2 0 写入，系统正确 fail closed。
4. 东方电缆 pilot 通过官方 PDF + 离线 `pypdf` 提取证明全文路线可行；严格 sentiment 门禁仍无法满足，因此采用维度级 hybrid，不降低证据标准。
5. 东方电缆及指定五股完成受限 Gemini 调用与独立表写入，共形成 6 行合法 partial v2；历史 predictions 保持不变。
6. 用户批准直接上线后，生产模式从 `canary` 切到 `on`。只读选择器验证为 35/35 有效返回：6 股 hybrid、29 股 v1 fallback。

## 做对了什么

- 把 v2 持久化放在独立表，没有覆盖 v1 cache 或历史 prediction。
- validator、input hash、context/result 冗余复验、TTL 和逐股异常边界形成了可靠的 fail-closed 读路径。
- hybrid 只采用已评分维度；`insufficient_data` 没有伪装成中性 v2 分数。
- 真实授权均有调用上限、create-only artifact、退休账本和凭证隔离。
- Claude 上线审查发现的授权复用和异常边界问题已在生产切换前修复。
- 全局 `on` 没有触发新的网络或模型调用；它只改变合法预计算结果的读取资格。

## 推进过慢的根因

主要根因不是单一数据源故障，而是控制面把不同目标串成了一条路径：

- 把“研究级 36 股代表性与模型 agreement”误当成“生产适配器可安全上线”的必要条件；
- 每次来源失败都生成新的日级授权与计划版本，传输能力、全文能力和证据质量没有尽早拆开；
- 过多 P0/P1/P2/P3 门禁同时进入关键路径，低风险、可回滚的读取开关也被高成本研究审计阻塞；
- 直到后期才明确“预计算 v2 + 逐维 hybrid + 逐股 v1 fallback”这一最小生产切片。

这些门禁本身多数有价值，但优先级使用过重。它们适合保护不可逆写入、未知外部调用和投资有效性声明，不应阻止一个已有 `off` 回滚、无历史改写且能逐股 fallback 的读路径。

## 风险分级调整

后续不再使用四级优先级把所有事项都排进上线关键路径，改成三类：

- 上线阻断：凭证泄漏、历史数据改写、无法回滚、validator/hash 可绕过、单股失败能中断全批、错误 v2 被静默采用。
- 上线后限时修复：缺少自然 cron 运行证据、监控不足、部分股票 coverage 缺口、artifact 运维体验问题。
- 研究增强：36 股分层 agreement、Claude blind reference、support audit、完整 sentiment 来源、预测有效性研究。

研究增强继续保留，但不静默升级为生产阻断项。只有当它要改变评分合同、权重、历史数据、自动交易或取消 fallback 时，才重新进入上线 gate。

## 当前遗留风险

1. 2026-07-19 是非交易日；全局 `on` 已通过本地只读选择器验证，但尚未经历下一次自然 `daily` cron。上线状态应称为“配置已生效、待自然运行确认”。
2. 6 股都缺少合格的近 30 天持续性 sentiment，当前第三维仍由 v1 提供。
3. 29 股没有合法 v2 行，虽然行为安全，但 v2 实际覆盖率仅 6/35。
4. 结构/evidence 合法不证明评分能带来正 alpha；不得据此修改投资结论或权重。
5. M5 real bundle、blind-reference agreement 和 support audit 尚未完成，只影响研究置信度，不影响当前 fallback 安全性。

## 下一阶段路线

### P0：下一交易日自然运行确认

- 已实现 `scripts/check_qualitative_v2_production.py`：当前真实库预检返回 `PASS`，并复验 35/35 eligibility、6 股 hybrid、29 股 fallback、1,859 条历史评分 seal 和 `off` 零数据库访问；
- 观察 QFQ 16:00、daily 16:30、outcome 17:00 的现有 cron，不手工补写周末 prediction；
- 确认 daily 35/35 完成、6 股出现 `hybrid_v2` 来源日志、29 股正常 v1 fallback；
- 确认 Telegram/Sheets 后置失败不会阻断 SQLite，且 predictions 只新增当日正常记录；
- 若出现批量异常，立即设 `QUALITATIVE_V2_MODE=off`，保留 v2 表供诊断。

### P1：扩大可用数据覆盖，不再扩张编排层

- 复用已证明有效的“官方 PDF → 离线文本 → context validator → 单股评分”路径；
- 分小批补齐其余 29 股的 moat/market_pos，允许合法 partial，禁止为了覆盖率降低 sentiment 门槛；
- 每批只要求来源身份、hash、validator、调用上限、独立表写入和回滚验证，不再为同类批次创建新的框架或协议版本。

### P2：上线后研究与效果评估

- M5 36 股 real bundle、blind reference 和 support audit 作为独立研究工作流继续；
- 分别报告结构有效性、证据支持度、v2 覆盖率和预测有效性，不互相替代；
- 至少积累 30/60 天 outcome 后，才讨论 v2 对总分、权重或推送门槛的影响。

## 完成定义

本轮任务的工程上线目标与自动化预检已完成；运维确认要在下一交易日使用 `--require-score-date` 验收自然 cron 后关闭。项目下一阶段不应再以“继续设计 M5”为主线，而应以“自然运行确认 → 批量扩大合法 v2 覆盖 → 独立效果研究”为主线。
