# 东方电缆定性评分 v2 生产闭环记录

## 结论

授权 `qualitative-v2-orient-cable-pilot-20260719-01` 已完成一次真实、受限、可回滚的生产闭环。603606 已加入 production canary 资格，但本次没有产生 v2 分数：近 30 天窗口缺少可证明持续性的 sentiment，采集器在读取 Gemini 凭证和发起模型调用前 fail closed，生产继续使用 v1。

这次结果证明了传输、官方 PDF 全文提取、context 合同、逐股 fallback 和 off 回滚路径；不构成 v2 投资有效性或东方电缆定性评分通过的声明。

## 授权与用量

- 标的：东方电缆（603606），仅此一股。
- 截止日：2026-07-19；sentiment 窗口为 2026-06-19 至 2026-07-19。
- 来源：CNINFO、SSE、东方电缆官方网站。
- 实际 HTTP attempts：11/12。
- 实际 PDF downloads：2/3，其中一次公司站 PDF 传输失败、一次 CNINFO 官方 PDF 成功。
- Gemini：0/1 个逻辑调用、0/3 HTTP attempts。
- 数据库：只读基本面；`qualitative_scores_v2` 对 603606 写入 0 行；历史 `predictions` 未修改。
- 依赖：固定 `pypdf==6.14.2`，仅用于离线 PDF 文本提取。

授权已在 `qualitative_v2_production_authorizations.json` 中退休，不能复用。剩余 1 次 HTTP attempt 不再执行。

## 证据质量分层

- 传输可用性：通过。CNINFO 官方 PDF、东方电缆公司简介/新闻页、CNINFO 全文查询均可形成带 hash 的响应记录。
- 全文能力：通过。CNINFO 年报摘要 PDF 可由 pypdf 离线提取并通过本地 context validator。
- 证据质量：未通过。财务表现 supporting evidence 可用；初版提取器生成了壁垒和行业地位 direct 候选，但提交前复核发现截取规则命中了行业通用表述，不能把该 artifact 当作已审定的公司直接证据；sentiment 同时不满足 freshness 与 persistence 联合门禁。
- 模型阶段：未开始。缺失维度在确定性预检中阻断，未读取模型凭证。

初版 artifact 保持 create-only，不回写。代码中的 PDF 规则已改为只接受公司特定能力/资质和公司特定行业地位措辞，并移除无可验证发布日期的官网简介 fallback；这项修正不追溯改变本次运行结论，也不授权复用已退休额度重新采集。

最终 context input SHA-256：`5430f1acfa77ffe2945aacca4e0a81425dc8f8fae210fbba7a715a2fb77376fa`。

最终 manifest SHA-256：`bee40eb10052338bf40f60b4f55b4de21960454cfc19b5134fe428f4091af430`。

隔离 artifact：`artifacts/qualitative-v2-orient-cable/orient-cable-context-20260719-04/`。该目录被 Git 忽略，不进入源码提交。

## 生产行为验证

- `QUALITATIVE_V2_MODE=canary` 时，603606 属于 v2 eligible 集合；因为不存在合法 v2 scored 行，读取路径回退 v1。
- `QUALITATIVE_V2_MODE=off` 时，603606 无条件使用 v1。
- 执行 `score --execute` 时，缺失 sentiment 在创建 Gemini artifact、打开写数据库或调用 Gemini 之前被阻断。
- 授权退休后重复运行，在创建 artifact、读取数据库或发起网络前被拒绝。

## 后续条件

若要让东方电缆真正产生 v2 分数，需要新的独立授权，以及窗口内来自批准官方来源、能证明影响具有持续性的 sentiment 事件。不得通过放宽 freshness/persistence、补造片段或复用本次已退休授权来取得分数。
