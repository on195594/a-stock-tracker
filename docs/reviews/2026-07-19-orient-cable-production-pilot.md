# 东方电缆定性评分 v2 生产闭环记录

## 结论

来源授权 `qualitative-v2-orient-cable-pilot-20260719-01` 完成 fail-closed 采集后，后续一次性授权 `qualitative-v2-orient-cable-hybrid-20260719-01` 已完成真实、受限、可回滚的 hybrid 生产闭环。603606 当前在 production canary 中使用 moat=7、market_pos=4 的 v2 分数；sentiment 因缺少近 30 天持续性证据而保持 v1。

这次结果证明了传输、官方 PDF 全文提取、context 合同、逐维 fallback 和 off 回滚路径；不构成 v2 投资有效性声明。

## 首次来源授权与用量

- 标的：东方电缆（603606），仅此一股。
- 截止日：2026-07-19；sentiment 窗口为 2026-06-19 至 2026-07-19。
- 来源：CNINFO、SSE、东方电缆官方网站。
- 实际 HTTP attempts：11/12。
- 实际 PDF downloads：2/3，其中一次公司站 PDF 传输失败、一次 CNINFO 官方 PDF 成功。
- Gemini：0/1 个逻辑调用、0/3 HTTP attempts。
- 数据库：只读基本面；`qualitative_scores_v2` 对 603606 写入 0 行；历史 `predictions` 未修改。
- 依赖：固定 `pypdf==6.14.2`，仅用于离线 PDF 文本提取。

授权已在 `qualitative_v2_production_authorizations.json` 中退休，不能复用。剩余 1 次 HTTP attempt 不再执行。

## 首次证据质量分层

- 传输可用性：通过。CNINFO 官方 PDF、东方电缆公司简介/新闻页、CNINFO 全文查询均可形成带 hash 的响应记录。
- 全文能力：通过。CNINFO 年报摘要 PDF 可由 pypdf 离线提取并通过本地 context validator。
- 证据质量：未通过。财务表现 supporting evidence 可用；初版提取器生成了壁垒和行业地位 direct 候选，但提交前复核发现截取规则命中了行业通用表述，不能把该 artifact 当作已审定的公司直接证据；sentiment 同时不满足 freshness 与 persistence 联合门禁。
- 模型阶段：未开始。缺失维度在确定性预检中阻断，未读取模型凭证。

初版 artifact 保持 create-only，不回写。代码中的 PDF 规则已改为只接受公司特定能力/资质和公司特定行业地位措辞，并移除无可验证发布日期的官网简介 fallback；这项修正不追溯改变本次运行结论，也不授权复用已退休额度重新采集。

最终 context input SHA-256：`5430f1acfa77ffe2945aacca4e0a81425dc8f8fae210fbba7a715a2fb77376fa`。

最终 manifest SHA-256：`bee40eb10052338bf40f60b4f55b4de21960454cfc19b5134fe428f4091af430`。

隔离 artifact：`artifacts/qualitative-v2-orient-cable/orient-cable-context-20260719-04/`。该目录被 Git 忽略，不进入源码提交。

## 首次执行行为验证

- `QUALITATIVE_V2_MODE=canary` 时，603606 属于 v2 eligible 集合；因为不存在合法 v2 scored 行，读取路径回退 v1。
- `QUALITATIVE_V2_MODE=off` 时，603606 无条件使用 v1。
- 执行 `score --execute` 时，缺失 sentiment 在创建 Gemini artifact、打开写数据库或调用 Gemini 之前被阻断。
- 授权退休后重复运行，在创建 artifact、读取数据库或发起网络前被拒绝。

## 路线 2 决定

2026-07-19 后续采用路线 2：不放宽 sentiment 合同，改为维度级 `hybrid_v2`。离线复验已确认收紧后的规则能从已封存 PDF 的第 5 页定位公司特定研发生产能力、从第 3 页定位公司特定行业地位；sentiment 继续为 `insufficient_data`。该候选 context 通过本地 validator，input SHA-256 为 `299efe53f541c8ca4f0165c4276607c00bcc5a4ada09466528456ba3b14d6b1f`。生产适配器可保存已评分的 moat/market_pos，并仅让 sentiment 回退 v1。

## Hybrid 执行结果

- 授权：`qualitative-v2-orient-cable-hybrid-20260719-01`，执行后立即退休。
- 输入 SHA-256：`299efe53f541c8ca4f0165c4276607c00bcc5a4ada09466528456ba3b14d6b1f`。
- 派生 context manifest SHA-256：`290a895ef512f6794dc3acc592beabf7e78ff24e1697d2e438d91cf394ee9d73`。
- 来源请求/PDF 下载：0/0；复用前序已封存且 hash 复验通过的官方 PDF。
- Gemini：`gemini-2.5-flash`，1 个逻辑调用、1 个 HTTP attempt。
- 本地状态：`VALID_INSUFFICIENT_DATA`；生产采用状态：`READY_HYBRID`。
- v2 维度：moat=7、market_pos=4；sentiment=`NULL`，生产读取回退 v1。
- 写入：`qualitative_scores_v2` 恰好 1 行；历史 predictions 行数 1,859，执行前后内容 SHA-256 均为 `78af6488707788e817515afc154a77d127c9a456d090ccc98ed890c1c898cbc4`。
- 实际读取：canary 为 `7/4/3`，其中 moat/market_pos 来源为 v2、sentiment 来源为最新 v1；`off` 为完整 v1 `7/4/3`。本次切换改变了可审计来源，因 v1 最新值恰好相同，数值未变化。
- 凭证：context/model artifacts 扫描未发现 API key。

该 v2 行按 30 天 TTL 使用；到期、内容/hash 漂移或 `QUALITATIVE_V2_MODE=off` 时自动回退 v1。任何刷新都必须使用新的授权和 create-only run ID。
