现在我已有足够的交叉验证信息。写审查结果。

---

```text
VERDICT: REQUEST_CHANGES

CHECKED:
- README.md（全文）
- docs/evolution-roadmap.md（全文）
- docs/specs/2026-05-30-phase5-l3-entry-signal-spec.md（全文）
- docs/plans/2026-05-30-phase5-l3-entry-signal-implementation-plan.md（全文）
- 交叉验证：weights.json（buy_strong 实际值）
- 交叉验证：telegram_push.py（当前推送条件与默认阈值）
- 交叉验证：lib/fetcher.py（_fetch_price_history 存在但为死代码；无 volume 列引用）
- 交叉验证：pipeline.py（Tencent 日线路径为内联 3 日窗口；push_daily_signals 实际传入 buy_threshold）
- 交叉验证：lib/cache.py（迁移模式：ALTER TABLE ADD COLUMN + try/except pass）

BLOCKING FINDINGS:
- 无真正硬性阻断项，但以下两项如不澄清将导致 P2/P3 实现歧义或静默错误。

IMPORTANT FINDINGS:

1. [spec R5 / plan P2] L3 v1 规则操作符未声明 AND/OR
   位置：spec §6 R5，plan §3 "规则"部分
   问题：三个条件（close>MA60 / close>MA120 / volume_5d>volume_20d）仅列举，未
   明确"三者全部满足→1"（AND）还是"任一满足→1"（OR）。若误实现为 OR，信号
   覆盖率将远高于预期（≈30-40% vs 目标 3-5%），且测试 AC2 的用例写法也将随
   之变化。
   建议：spec R5 明确加一句"三条件均须满足（AND）才输出 entry_signal=1"。

2. [plan P3] 日线历史 API 路径及 DataFrame schema 未指定
   位置：plan §4 "设计" → "数据读取：复用/扩展现有 Tencent 日线路径"
   实际代码状况：
   - lib/fetcher.py::_fetch_price_history → ak.stock_zh_a_hist（AKShare源），
     但此函数从未被调用，是死代码。
   - pipeline.py::cmd_backfill_price → ak.stock_zh_a_hist_tx（Tencent源），
     内联，日期窗口硬编码 score_date±3 天，不可直接复用于 120 日窗口。
   - ak.stock_zh_a_hist 返回中文列名（收盘/成交量）；
     ak.stock_zh_a_hist_tx 返回英文列名（close/date），但 volume 列是否存在
     未在现有代码中验证过。
   风险：P2 纯函数的 daily_bars 接口不知道应期待哪种列名；P3 不知调用哪个
   API；若列名假设错误，"缺列→NULL" fallback 会静默地让所有 entry_signal
   变成 NULL，不报错却形同虚设。
   建议：spec/plan 中明确指定使用 ak.stock_zh_a_hist（或 _tx），写明期望的
   DataFrame 列名（close/volume 或 收盘/成交量），并在 P2 接口注释中锁定。

3. [spec R2 / R11] entry_signal_version=NULL 歧义：无法区分"pre-L3"与"数据不足"
   位置：spec §5 R2，plan §3 EntrySignalResult（signal=None, version=None）
   问题：pre-L3 历史记录 → entry_signal=NULL, version=NULL；L3 实装后日线
   数据不足 → 按计划也写 signal=None, version=None，两者落库后完全相同。
   accuracy-report "entry_signal 覆盖率" 和 "version=v1 记录数" 统计将无法
   区分这两类 NULL，导致覆盖率虚低、v1 记录数虚低。
   建议：数据不足时写 entry_signal=NULL, entry_signal_version="v1"，与 pre-L3
   记录区分。spec R2 和 plan P2 的 EntrySignalResult 中统一。

4. [roadmap Phase 5 / spec R7] 路线图硬编码 score >= 55 与实际阈值不一致
   位置：evolution-roadmap.md §三 Phase 5 实施方案第 2 条
   原文："推送条件从 score >= 55 改为 score >= 55 AND entry_signal=1"
   实际：weights.json buy_strong = 44；pipeline.py 调用 push_daily_signals
   时已正确传入 buy_threshold = weights["thresholds"]["buy_strong"]（即 44）。
   spec R7 正确写了"buy_strong 仍来自现有阈值配置"，但 roadmap 与 spec 不一致
   ——工程师若仅看 roadmap 实施，将落地 score >= 55，与当前运行阈值矛盾。
   建议：roadmap 改为"score >= buy_strong"，删除硬编码数值。

5. [spec §8 R11 / AC5] accuracy-report L3 30d 命中率定义不完整
   位置：spec §8 R11，§9 AC5
   问题：R11 要求"L3 子集的 30d 命中率观察"，但未定义：
   - 分母：entry_signal=1 AND outcome_30d IS NOT NULL？还是 framework='A' 过滤？
   - 判断：alpha_30d > 0（跑赢 300）还是 outcome_30d > 0（绝对正收益）？
   Framework A 现有命中率用的是"alpha_30d > 0 = 命中"，若 L3 section 换用
   绝对收益，同一个报告里会出现两种不兼容的"命中"定义，造成解读混乱。
   建议：spec 明确"L3 30d 命中率定义与 Framework A 保持一致：
   alpha_30d > 0 = 命中，分母 = entry_signal=1 AND outcome_30d IS NOT NULL
   AND framework='A'"。

6. [plan P1] 迁移方式未指定，DROP+RECREATE 风险未隔离
   位置：plan §2 P1 GREEN 步骤
   问题：cache.py 存在两种迁移模式：
   (a) ALTER TABLE ADD COLUMN + try/except pass（analysis_results/holdings）
   (b) DROP TABLE + RECREATE（qualitative_scores，旧数据"无漂移价值"）
   predictions 表有 326 条历史数据，不能用模式(b)。但 plan 只说"补充旧库迁移
   逻辑"，未明确必须用模式(a)，新工程师有误用 DROP 的风险。
   建议：plan 明确写"沿用 ALTER TABLE ADD COLUMN + try/except 模式，
   禁止对 predictions 表使用 DROP+RECREATE"。

MINOR SUGGESTIONS:

- telegram_push.py 默认参数 threshold=55.0 与 weights.json buy_strong=44 不一致。
  虽然 pipeline.py 调用时传入正确值，但默认值是陷阱。建议改为 threshold=44.0 或
  从 weights 动态读取，或加注释标明"仅 fallback"。
- spec R4 "最近至少 120 个交易日"——应明确是交易日（约 6 个月）还是自然日。
  AKShare 按日历日传参，测试用 mock 时需要模拟正确的 bar 数量。
- plan P2 EntrySignalResult.reason 字段：建议文档中给出 3-4 个固定原因字符串
  常量（如 "INSUFFICIENT_DATA", "BELOW_MA60", "LOW_VOLUME"）而非自由文本，
  便于 accuracy-report 分类统计不可计算原因。
- spec §9 AC2 缺少"close > MA60 但 close <= MA120"的测试用例，即部分通过场景
  验证 AND 语义（这在发现 Finding #1 后更重要）。
- Phase 5 成功标准"strong 信号降至 3-5%"是观测性目标，无法成为自动化通过条件；
  建议在 roadmap 中标注为"预期观测范围"，避免实施者误以为这是 CI 门禁指标。

ENGINEERING READINESS SUMMARY:
文档整体架构清晰、TDD 步骤合理、迁移/报告/推送的主要流程均有 Stop 条件保护。
但有两处会导致 P2/P3 实施时无法直接动手：L3 v1 规则的 AND/OR 逻辑未声明（Finding
#1）、日线 API 路径与 DataFrame schema 未指定（Finding #2）。此外，entry_signal
NULL 歧义（Finding #3）会导致 L3 覆盖率报告数据质量问题，路线图阈值硬编码
（Finding #4）会导致推送阈值落地错误。建议在 spec 中修复这四处后即可开始实施。
```

`★ Insight ─────────────────────────────────────`
- 这份文档集展示了一个成熟的"spec-first"工程流程：spec 描述 **what**，plan 描述 **how**，roadmap 描述 **why now**。三层分离使审查可以独立检查每层的完整性，而不是在同一文档里找"是否矛盾"。
- SQLite 的 VIRTUAL Generated Column（`alpha_*d`）是一个隐形约束：只要做 DDL 变更，就必须先问"这个操作会让 SQLite 重新验证 generated column 定义吗"。Finding #6 展示了这类约束不写进计划会如何产生风险。
- `entry_signal=NULL` 的两种含义（pre-L3 vs 数据不足）是典型的"NULL 过载"问题——当一个 nullable 字段需要区分"不适用"和"有界条件下不可用"两种状态时，通常应引入 version/flag 列作为区分手段。
`─────────────────────────────────────────────────`
