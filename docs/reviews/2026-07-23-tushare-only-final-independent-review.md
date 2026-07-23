# TuShare-only 行情强切：最终独立只读复审

日期：2026-07-23
审查器：Claude Code（替代 AGY）
状态：APPROVE

## 审查器替代说明

原计划使用 AGY；实际调用返回个人配额已耗尽，约 155 小时后重置，未产生审查输出且仓库 hash 漂移为 0。随后使用 Claude Code `plan` 权限模式、禁用 Edit/Write/Web 工具完成同范围独立复审。

Claude Code 在仓库外 `~/.claude/plans/` 自动生成了一份报告副本，违反了提示词的“不得创建文件”约束；该副本已立即删除。审查前后当前仓库所有 tracked changed/untracked 文件 SHA-256 均无漂移，Git status 项目集合无变化。

## 独立结论

**APPROVE。P0：none；P1：none。**

独立审查直接核验了代码 diff、删除入口、live crontab、生产 SQLite `mode=ro` 查询、候选/切换/回滚 JSON 和历史 audit，而非仅采信父级摘要。

## 核验结果

- 活动默认与 backfill provider 只能返回 TuShare 或 disabled provider；旧 BaoStock-only 环境变量不能恢复 fallback。
- 旧 BaoStock QFQ 采集、文件缓存采集和双源对账入口均已删除，live 16:00 cron 指向 `fetch_qfq_daily_bars_tushare.py`。
- L3 v2 对纯非 TuShare QFQ 和 mixed source 均返回 `QFQ_SOURCE_MISMATCH`。
- 生产 DB：`quick_check=ok`；非 TuShare `daily_bars=0`；active QFQ 35/35，每只 139 行；全表 BaoStock 文本命中 0。
- predictions：1,999 行；canonical SHA-256 为 `158cbc0ec65a20d0719017704ddfa580d5001861b559ce17512b714d16eee6e3`，与切换及隔离回滚三阶段一致。
- outcome 报告正确区分 14 次可证明的成功 BaoStock fallback 下界与 450/1,767 个 TuShare shadow 差异，没有把后者错误归因于 BaoStock，也没有改写历史字段。

## P2 及处置

1. `requirements.txt` 仍直接列出 `baostock`，`tests/test_market_data.py` 保留 a-stock-lib BaoStock/Composite 构件级测试。
   - 处置：**保留**。这是已批准 spec 的明确非目标；测试不经过 tracker 生产 provider factory，不是生产 blocker。删除依赖需要单独验证 a-stock-lib 安装合同，不能在本次收尾扩 scope。
2. `CHANGELOG.md` 缺少本次切换记录。
   - 处置：**已修复**，新增 2026-07-23 条目并明确 2026-07-22 的旧脚本回滚方案已被快照/隔离演练取代。
3. 离线 QFQ cache 使用 `tushare.daily+adj_factor`，生产 QFQ 使用 `tushare.pro_bar.qfq`，通用常量名可能混淆。
   - 处置：**已澄清**。两者是不同数据合同，不能伪装成同一来源；离线常量重命名为 `OFFLINE_TUSHARE_DAILY_ADJ_FACTOR_SOURCE`，来源值保持真实。

## 最终父级复验

在 P2 收敛后重新执行：

- `pytest -q`：1,069 passed；
- Ruff lint：PASS；
- Ruff format：161 files already formatted；
- mypy：151 source files，no issues；
- pip check：no broken requirements；
- shell syntax、`git diff --check`：PASS；
- 生产 SQLite 与 live cron 只读复验：PASS。

因此本次 TuShare-only 活动行情切换没有待关闭的 blocker。历史 outcome provenance 治理仍是独立 P1 后续项目，不属于本次就地修复范围。
