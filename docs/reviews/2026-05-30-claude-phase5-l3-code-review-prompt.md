你是独立代码审查员。只读审查，不要修改文件、不要运行命令、不要安装依赖、不要联网。

请审查 /tmp/a-stock-tracker-phase5-l3.diff 里的全部变更。仓库上下文在 /home/lin/a-stock-tracker。

范围：3b9d4f7..6c2e3bc，Phase 5 L3 买点层完整实现与收尾。

请优先读取这些上下文文件（只读）：
/home/lin/a-stock-tracker/docs/specs/2026-05-30-phase5-l3-entry-signal-spec.md
/home/lin/a-stock-tracker/docs/plans/2026-05-30-phase5-l3-entry-signal-implementation-plan.md
/home/lin/a-stock-tracker/lib/entry_signal.py
/home/lin/a-stock-tracker/lib/cache.py
/home/lin/a-stock-tracker/pipeline.py
/home/lin/a-stock-tracker/telegram_push.py
/home/lin/a-stock-tracker/tests/test_l3_entry_signal.py
/home/lin/a-stock-tracker/tests/test_pipeline.py
/home/lin/a-stock-tracker/tests/test_telegram_push.py

合同重点：
- L3 v1 = close > MA60 AND close > MA120 AND volume_5d_avg > volume_20d_avg。
- normalized schema: date/close/volume；纯函数不得依赖 AKShare 中文列名。
- 不足 120 日或缺列：entry_signal=NULL, entry_signal_version='v1'。
- 旧历史 pre-L3 保持 NULL/NULL；迁移只能 ADD COLUMN，不得 DROP/重建/改写历史评分。
- daily 写入 L3，但 L3 失败不得阻断基础评分。
- Telegram 推送条件：total_score >= buy_strong AND entry_signal=1；NULL 不得通过；发送失败不阻断 daily。
- accuracy-report 区分 NULL/NULL、NULL/v1、0/v1、1/v1；L3 30d 命中率只统计 Framework A、entry_signal=1/v1、已结案，命中 alpha_30d > 0；样本 <30 提示不足。
- 不得真实访问 AKShare/Telegram/Gemini/Google Sheets，不得自动交易/cron/凭据写入。

已知验证：pytest tests/ -q => 109 passed, 1 skipped；pytest tests/test_spec_structure.py -q => 5 passed；git diff --check 通过；git status 干净。
静态扫描：仅 tests/test_telegram_push.py 出现 monkeypatch 占位 token；未发现 os.system/shell=True/eval/exec/pickle.loads。

输出不超过 120 行，中文：
1. 结论：APPROVE / REQUEST_FIXES / BLOCKED
2. Blocking / Important / Nice-to-have
3. 每条问题给路径+函数/测试名+影响+建议
4. 明确列出检查了什么
5. 不要虚构行号；不确定则用函数/片段定位
