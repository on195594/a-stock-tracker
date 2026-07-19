# 指定 5 股定性评分 v2 生产闭环记录

## 结论

一次性授权 `qualitative-v2-selected-five-20260719-01` 已完成招商银行（600036）、长江电力（600900）、中国神华（601088）、华东医药（000963）和三花智控（002050）的官方来源采集、封存 context、Gemini 评分和生产写入。5/5 结果通过本地合同并进入 `qualitative_scores_v2`，生产状态为 `READY_HYBRID`。

5 股的 moat 与 market_pos 使用 v2；近 30 天没有满足 direct、fresh 和 multi-quarter/structural persistence 联合门禁的 sentiment，因此 sentiment 保持 v1。该结果证明生产闭环与逐维 fallback 可用，不构成投资有效性声明。

## 封存输入与来源用量

- 截止日：2026-07-19；sentiment 检索窗口为 2026-06-19 至 2026-07-19。
- 来源：CNINFO、港交所披露文件及公司官方投资者关系文件；PDF 仅离线提取，不把模型用于检索。
- 实际累计来源 HTTP attempts：24/60。
- 实际累计 PDF downloads：8/8；其中包含不可用文件和替代官方文件的受控尝试。
- 最终 context manifest SHA-256：`c1a22828da357013d44a96f4bb2c5b531a0f514e53554f063fac2dddd80f4f24`。
- 最终 source artifact：`artifacts/qualitative-v2-selected-five/qualitative-v2-selected-five-contexts-20260719-08/`，目录和文件权限分别为 `0700`/`0600`。

最终 context input SHA-256：

- 000963：`9f3273678536c9b21d2d435fe23ffedc4c02bc64d771f01b93cf6a88b403907f`
- 002050：`f2106d2149b6590a1067bcbbb61ab0f360996e1f463ebcb478f8ecc81ab4a896`
- 600036：`c597cd578638fa80760c5fa34032bc52fd4a59a586ed18f57047acc6637757f0`
- 600900：`6a96e25d984b3a8d7bbcba54a6eb25bd0295f6140bfb367fa0c3a17df00ac5d5`
- 601088：`0e0d6c00428e7b7e937f36a23972af639470535b909da4bda786647cf98e58bd`

## 模型与生产结果

- 固定模型：`gemini-2.5-flash`。
- 实际 Gemini 用量：5/5 个逻辑调用、5/15 HTTP attempts；无重试。
- JSONL SHA-256：`10194c06e002ad85a88d02ee54cf1f19150f68732b2ac0e742ceccc04762cfb3`。
- 5 条记录均为 `VALID_INSUFFICIENT_DATA`，原因仅为 sentiment 缺少合格证据；每股都有两个合法 scored 维度。
- 写入：`qualitative_scores_v2` 新增恰好 5 行，总行数从 1 增至 6；`predictions` 行数保持 1,859，评分写路径仅插入独立 v2 表。

| 股票 | moat v2 | market_pos v2 | sentiment |
|---|---:|---:|---|
| 华东医药 000963 | 7 | 4 | v1 fallback |
| 三花智控 002050 | 8 | 5 | v1 fallback |
| 招商银行 600036 | 7 | 4 | v1 fallback |
| 长江电力 600900 | 8 | 5 | v1 fallback |
| 中国神华 601088 | 8 | 5 | v1 fallback |

## 上线、隔离与回滚验证

- `.env` 保持 `QUALITATIVE_V2_MODE=canary`，以上 5 股均在 canary 资格集合中。
- 只读验证表明，canary 返回 v2 moat/market_pos 与 v1 sentiment；`off` 返回完整 v1。
- context、manifest、模型结果和冗余分数每次读取均重新验证；漂移、过期或损坏会逐股回退 v1。
- 模型 artifact 为 create-only，权限为 `0700`/`0600`；API key 扫描为 0 命中。
- 一次性授权已在 `qualitative_v2_selected_five_authorizations.json` 中退休。采集和评分复用均在创建 artifact、读取凭证或发起网络请求前被拒绝。
- 回滚仍只需设置 `QUALITATIVE_V2_MODE=off`，不删除 v2 数据、不修改历史 prediction。
