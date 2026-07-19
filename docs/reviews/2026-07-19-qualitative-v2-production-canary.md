# 2026-07-19 定性评分 v2 生产 canary 执行记录

## 结论

`qualitative-v2-prod-canary-20260719-01` 已在当天执行完毕并按证据门禁停止。CNINFO 传输可用，但 5 股 × 3 维度的精确全文检索没有返回任何公告片段；因此 5 份 context 均只有本地 ROE supporting evidence，`score_ready=false`。

未调用 Gemini，未写 `qualitative_scores_v2`，也未触发 35 股扩展。证据执行结束后已单独启用 `QUALITATIVE_V2_MODE=canary` 生产读路径；由于 v2 表为空，5/5 canary 均确定性回退现有 v1，评分变化为零。

## 已批准边界与实际消耗

| 项目 | 上限 | 实际 |
|---|---:|---:|
| canary 逻辑槽位 | 15 | 15 |
| CNINFO HTTP attempts | 45 | 45 |
| Gemini 逻辑调用 | 5 | 0 |
| Gemini HTTP attempts | 15 | 0 |
| 基本面数据库读取 | 只读 | 每个 create-only run 1 次 |
| `qualitative_scores_v2` 写入 | 仅合法结果 | 0 行 |
| 历史 predictions 写入 | 禁止 | 0 行 |

每个 company×dimension 槽位使用三次协议形式：证券代码+证据词、公司名逗号证据词、公司名空格证据词。第一次响应中的 `announcements=null` 暴露了空结果合同差异，采集器随后将其正确解释为零结果；后两次 15/15 均为合同有效的 HTTP 200，但仍为零结果。

## 封存身份

| run | manifest SHA-256 | HTTP attempts | 合同成功 | 直接证据 |
|---|---|---:|---:|---:|
| `qualitative-v2-contexts-canary-20260719-01` | `5528c59d1d0129f24b491c47c10cf7375673a001f487115d1b7aa405a749a09c` | 15 | 0/15 | 0 |
| `qualitative-v2-contexts-canary-20260719-02` | `c8c29de699c7ce5719070aad680d94bf1da2e2475454dcefda2b1d0d39e6f1cf` | 15 | 15/15 | 0 |
| `qualitative-v2-contexts-canary-20260719-03` | `67d0526fa83ac60b0dd720ab029d2c562f45387414480911e45bf759eab6c1e8` | 15 | 15/15 | 0 |

最终 context input hashes：

- `000963`: `19a4e84b3fd3eb2855836fa5ff3683f8b787f687d5d1de5e9ce784a5dbf1ccb7`
- `002594`: `1f3ae6921bfe1ac908d0824564a4e3232fefeedbbc74e0f4d57738e6f86e29bc`
- `600036`: `c98a7e5b7edc0442e0844eb5e7004beb37f50076aa74060e8801e9ad074202d0`
- `600941`: `5697c35591a12455f6a470c7f958204ca9f6a33df0999b6a002575e76a6e8679`
- `601088`: `29e53b67e9e223a57a8ced7558b3839c152b58dd4f0a5ff8acad637474bdd801`

## Fail-closed 证据

生产 CLI 的确定性预检在读取 `.env`、创建 Gemini artifact、打开生产数据库或调用模型之前报告五股均缺少 `moat`、`market_pos`、`sentiment` 的必要直接证据，并以 exit 2 停止。

执行后状态：

- Gemini artifact：不存在；
- `qualitative_scores_v2`：0 行；
- `QUALITATIVE_V2_MODE`：证据执行阶段未设置；随后生产激活为 `canary`；
- 35 股扩展：未触发。

下一步不需要等待自然日，但必须更换证据获取路线并使用新授权；本授权的 45 次 CNINFO HTTP 预算已经耗尽。候选路线应先证明能返回公司定向正文或结构化片段，再进入新一轮模型调用，不能继续扩大无证据查询。

## 后续生产激活

同日完成了不改变评分的 canary 读路径上线：

- `.env` 设置 `QUALITATIVE_V2_MODE=canary`，生产进程启动时会加载该模式；
- 只读烟测确认 canary 身份为 5 股，`qualitative_scores_v2` 为 0 行，5/5 走 v1 fallback；
- 进程级覆盖 `QUALITATIVE_V2_MODE=off` 已验证可立即回滚；
- managed cron 已安装，下一正常交易日的 `daily` 会自动使用 canary 模式；
- 2026-07-19 为非交易日，因此未手动运行 `daily`，未新增周末 prediction。

这表示生产编排、隔离表、fallback 和回滚路径已经上线，但不表示 source-grounded v2 分数已经被采用。合法 v2 行仍为 0，后续必须以新的证据来源授权增量填充。
