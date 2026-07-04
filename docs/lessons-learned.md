# a-stock-tracker 知识库：踩坑记录

**最后更新：** 2026-06-05
**范围：** 项目立项（2026-04）至今的技术坑、设计失误、调试经验。
**用法：** 新功能开发前先检索本文档；每次踩到新坑立即补录。

---

## 目录

- [A. 数据质量陷阱](#a-数据质量陷阱)
- [B. SQLite 架构陷阱](#b-sqlite-架构陷阱)
- [C. 评分可比性陷阱](#c-评分可比性陷阱)
- [D. 外部 API 陷阱](#d-外部-api-陷阱)
- [E. 测试隔离陷阱](#e-测试隔离陷阱)
- [F. 架构设计陷阱](#f-架构设计陷阱)

---

## A. 数据质量陷阱

### A-1｜字段声明了但从未抓取（gross_margin 哑字段问题）

**现象：** 所有35只股票 `gross_margin=NULL`，持续4天评分相同，`data_quality=0.8`（比预期低）。
**根因：** `lib/fetcher.py` 的 `FIELDS` 字典将 `gross_margin` 的 source 标注为 `'web'`，但 `cmd_fetch()` 从未实现从 AKShare 获取该字段的逻辑。字段"存在于设计中"却"不存在于执行中"，静默失败无报错。
**修复：** 改用新浪利润表接口 `ak.stock_financial_report_sina` 自动计算：`(营业收入 - 营业成本) / 营业收入 × 100`，取最新3年年报均值。金融行业（银行/保险/证券等）skip。
**防复发：** 新增字段到 `FIELDS` 时，`cmd_fetch()` 中必须有对应的实际抓取逻辑，或明确标注"待实现"并加 `TODO` 注释。字段 source 值应与实现一致：`'akshare'`=已实现，`'computed'`=已实现，`'web'`=需人工搜索（不可自动获取）。

---

### A-2｜估值字段每周才更新，导致日度评分静态化（pb_percentile_10y 问题）

**现象：** 连续4天（5/12-5/15）所有股票评分完全相同，日度 cron 运行正常但毫无意义。
**根因：** `pb_percentile_10y` 从百度接口获取月度历史 PB，每周 `weekly` 任务更新一次。一周内股价变化不会反映在评分中。
**修复：** 架构改为两层：weekly 存储 `bps`（每股净资产）和 `pb_hist_monthly`（约731个月度PB点），daily 纯内存计算 `current_pb = 当日收盘价 / bps`，ranked in 历史序列 → 真正日度变化。无额外 API 调用。
**防复发：** 评分字段要区分"每日可变"和"每周才变"两类。每日可变字段必须有日度更新路径；若只有静态路径，评分系统失去日度意义。

---

### A-3｜INSERT OR REPLACE 无声覆盖旧有效值

**现象：** 某次 AKShare 接口部分失败，`gross_margin` 返回 None，整行被 `INSERT OR REPLACE` 覆盖，之前成功抓到的有效旧值消失。
**根因：** SQLite 的 `INSERT OR REPLACE` 等同于 `DELETE + INSERT`，任何字段只要 None 就会覆盖。新抓一次失败的字段会抹掉历史上成功的值。
**修复：** `set_fundamentals()` 新增 `merge=True` 参数：新值为 None 时保留旧缓存中的有效值。`cmd_fetch()` 末尾改为 `set_fundamentals(..., merge=True)`。
**防复发：** 缓存更新策略要明确区分"全量替换"（merge=False，首次写入）和"增量合并"（merge=True，重新抓取时）。API 临时失败不应覆盖历史有效数据。

---

### A-4｜未排序的 head(3) 取到最旧而非最新数据

**现象：** `_compute_gross_margin` 取"最近3年年报"，但部分股票返回的是最早3年数据，导致毛利率计算偏差。
**根因：** `df[...].head(3)` 依赖 AKShare 返回的行顺序，而接口不保证排序。若返回升序（最旧在前），head(3) 取到最旧3年。
**修复：** 在 `.head(3)` 前加 `.sort_values('报告日', ascending=False)`，明确取最新3年。
**防复发：** 任何依赖"最新N条"的逻辑，必须显式排序后再截取，不能依赖 API 返回顺序。

---

## B. SQLite 架构陷阱

### B-1｜GENERATED COLUMN 不能直接写入

---

### B-5｜批量写入无 per-stock 原子隔离，单股崩溃导致后续 stock 跳过

**现象（agy 工程审查发现，2026-07-04）：** `cmd_batch()` 在整个 daily 批次用同一个事务，若某只股票崩溃，该 stock 之后的所有写入也被回滚，导致幂等重跑仍全量重新计算，且无法精确定位哪只股票出了问题。  
**根因：** 外层 `conn.commit()` 在整批完成后才提交；异常 `ROLLBACK` 的粒度是整批次。  
**修复：** 每只股票用独立 `SAVEPOINT sp_stock` / `RELEASE sp_stock` 包裹；捕获异常时 `ROLLBACK TO sp_stock`，已成功的股票已经释放 savepoint，不受影响。引入 `savepoint_created` 布尔标志，避免双重异常时重复 `ROLLBACK TO`。`checkpoint` 批量更新加 `AND framework IN (placeholders)` 防止意外跨框架写入。  
**防复发：** 任何"批量处理 N 个独立实体 → 写 DB"的模式，都应使用 per-entity SAVEPOINT。事务粒度 = 失败恢复粒度，设计时先问："崩溃重跑的最小代价单元是什么？"

**现象：** `INSERT INTO predictions (..., alpha_30d, ...)` 报错 `table predictions has no column named alpha_30d`（或写入报错）。
**根因：** `alpha_30d / alpha_60d / alpha_90d` 是 SQLite `GENERATED ALWAYS AS (...) VIRTUAL` 列，由数据库自动计算，任何 INSERT/UPDATE 都不能包含这些列名。
**修复：** 从所有 INSERT/UPDATE 语句中删除 `alpha_*d` 列。
**防复发：** CLAUDE.md 已列为硬性禁止规则。新增 INSERT 语句时，对照 DDL 检查是否包含 VIRTUAL 列。

---

### B-2｜SQLite 版本过低导致 GENERATED COLUMN 不支持

**现象：** 系统在旧环境运行直接报错，`CREATE TABLE` 失败。
**根因：** GENERATED COLUMN 是 SQLite 3.31.0（2020年）才引入的特性，旧系统自带 SQLite 版本可能不足。
**修复：** `pipeline.py` 启动时加 `assert sqlite3.sqlite_version_info >= (3, 31, 0)`，版本不足立即终止并提示升级。
**防复发：** 保留该 assert，不得删除。

---

### B-3｜weights_hash 包含非框架字段导致误判 hash 变更

**现象：** 修改 `weights.json` 的 `updated_at` 或 `version` 字段时，`weights_hash` 变更，导致当日 daily 任务退出报"hash 冲突"。
**根因：** 最初对整个 weights.json 求 hash，`updated_at` 的每次修改都会触发 hash 变更。
**修复：** `weights_hash = md5(json.dumps(weights["frameworks"], sort_keys=True))[:8]`，只对 `frameworks` 子树求 hash，`updated_at`/`version` 不参与。
**防复发：** CLAUDE.md 已明确记录此规则。添加新顶层字段到 weights.json 时，确认其不影响 `frameworks` 子树。

---

### B-4｜新增 predictions 列未同步更新 DDL 和 INSERT

**现象：** 新增字段后，旧记录缺失该列，或 INSERT 语句因列名不匹配报错。
**根因：** `get_db()` 的 `CREATE TABLE IF NOT EXISTS` 在表已存在时不会自动加列（SQLite 无自动 schema migration）；INSERT 语句未包含新列。
**修复（已验证流程）：**
1. 在 `get_db()` 的 DDL 中加新列
2. 如表已存在则加 `ALTER TABLE predictions ADD COLUMN ...`（或 DROP+RECREATE，视数据重要性）
3. INSERT 语句同步加新列
4. 补充测试用例验证新列读写
**防复发：** CLAUDE.md 已要求："新增字段到 predictions 表：必须同步更新 get_db() DDL，并验证 INSERT 语句也包含该字段"。

---

## C. 评分可比性陷阱

### C-1｜系统性分数偏移，跨期比较失效

**现象：** 2026-05-15 前均分约 34-40，修复后均分约 44，看起来"评分系统突然变好了"。
**根因：** 2026-05-15 修复了 `gross_margin`（原来全是 NULL）和 `pb_percentile_10y`（原来月度静态），两个字段补上后评分系统性上移约 4-5 分。
**防复发：** 任何字段修复或权重调整都会引入系统性偏移。`accuracy-report` 必须按 `score_date` 分层处理，不得混合修复前后的数据计算 hit_rate。已在 CLAUDE.md 和 evolution-roadmap 中明确记录"pre-fix（≤ 2026-05-14）"和"post-fix（≥ 2026-05-15）"是不同的数据集。

---

### C-2｜Gemini Phase 3 上线引入定性分差异

**现象：** 2026-04-26 前的9条历史记录定性分为固定值（moat=5, market_pos=2, sentiment=3），之后由 Gemini 填写（3-9不等），两段数据不可直接比较。
**根因：** Phase 3 上线时未考虑历史数据迁移。
**防复发：** 每次引入影响 `total_score` 的新逻辑（Gemini评分、新字段、权重调整），都要在 CLAUDE.md 的"评分可比性注意事项"中记录偏移时间节点和幅度。`weights_hash` 本身无法区分"同 hash 但定性来源不同"的情况。

---

### C-3｜pe_percentile_10y 替换为 pb_percentile_10y 导致 hash 变更

**现象：** 2026-05-12 修改 `weights.json`（用 `pb_percentile_10y` 替换 `pe_percentile_10y`）后，当日 daily 任务检测到 hash 变更退出，需手动清理当日记录或等次日。
**根因：** 替换了 `frameworks` 子树中的字段名，`weights_hash` 正确变更，但触发了退出保护机制。
**经验：** `weights_hash` 变更保护是设计的，不是 bug——它防止在同一天混入不同权重的评分。修改权重前，确认当日没有待保留的 predictions 记录，或等次日自然切换。

---

### C-4｜Framework B 暂停后历史记录孤立

**现象：** Framework B 已有73条历史记录（2026-05-12 前积累），`SUPPORTED_FRAMEWORKS` 改为 `{"A"}` 后不再产生新记录。两段数据不连续，无法做 Framework B 的 accuracy-report。
**经验：** 暂停框架前应明确记录"最后一条记录日期"和"暂停原因"，便于日后重启时做连续性处理。已在 CLAUDE.md 记录："Framework B 当前暂停（2026-05-12），重启：在 scorer.py 加回 'B'"。

---

## D. 外部 API 陷阱

### D-1｜东方财富 index_zh_a_hist 长期不稳定

---

### D-5｜外部 API 单次失败即锁定今日，导致当天后续全量跳过

**现象（agy 工程审查发现，2026-07-04）：** `_fetch_spot_em_safe()` 初始实现在首次失败时立即设 `_spot_em_failed_today = today`，锁定整日。一次网络抖动后，当天剩余 34 只股票全部静默跳过，`price_at_score` 全部为 NULL，推送和 outcome 计算受影响。  
**根因：** 没有区分"偶发失败"和"系统性故障"——设计上只有"成功/今日锁定"两种状态，缺少"失败计数 < 阈值时允许重试"的中间状态。  
**修复：** 引入 `_spot_em_fail_count` 全局计数器和 `MAX_SPOT_EM_RETRIES=3`：连续失败 ≥ 3 次才设今日锁定；单次失败只递增计数，仍允许下一次调用重试；成功时重置计数器。日期切换时两个状态变量同步重置。  
**防复发：** "外部 API 不稳定 → 降级/跳过"类设计，必须显式区分"偶发抖动（允许重试）"和"系统性故障（锁定降级）"。锁定条件应是连续 N 次失败，而不是单次失败。

**现象：** `ak.index_zh_a_hist(symbol="000300")` 频繁返回空 DataFrame 或超时，导致 `benchmark_30d=NULL`，`alpha_30d` 无法计算。
**根因：** AKShare 依赖的东方财富接口不稳定，尤其指数历史数据接口故障率高。
**修复：** 新增腾讯 fallback：`ak.stock_zh_index_daily_tx(symbol="sh000300")`，列名 `date`/`close`（已验证）。加 `assert "date" in df.columns and "close" in df.columns` 保护，列名变更立即报错而不是静默返回错误数据。
**防复发：** 关键数据路径（指数价格、快照价格）必须有 fallback。fallback 接口列名须在代码中 assert 保护，防止接口悄悄改字段名。

---

### D-2｜AKShare 接口可能返回 str 而非 DataFrame

**现象：** 某些情况下 AKShare 接口返回字符串（如错误信息）而不是 DataFrame，后续 `df.columns` 访问报 `AttributeError`。
**根因：** AKShare 部分接口在数据不存在时返回字符串 "empty" 或空串，而非抛出异常或返回空 DataFrame。
**修复：** 所有 AKShare 调用后加类型检查：`if isinstance(result, (str, tuple)) or result is None: return None`，再做 DataFrame 操作。
**防复发：** 新增 AKShare 调用时，必须假设返回值可能是 str/None，加类型保护后再访问 DataFrame 属性。

---

### D-3｜Codex 独立审查时 stdin 未关闭导致无限等待

**现象：** 用 `Bash(run_in_background=True)` 调用 `codex exec "..."` 后，进程挂起无输出，等待超时。
**根因：** Codex 在前台等待 stdin 输入（交互模式），未关闭 stdin 时永远不会继续执行。
**修复：** 命令末尾加 `< /dev/null` 立即关闭 stdin：`codex exec "..." < /dev/null 2>&1`。
**防复发：** 在非交互环境（cron、后台任务）调用任何 CLI 工具时，若该工具可能需要交互输入，加 `< /dev/null`。

---

### D-4｜AKShare 返回的财务数据行顺序不保证

**见 A-4**（sort_values 缺失问题）。AKShare 接口返回的 DataFrame 行顺序依赖后端，不可假设为升序或降序。所有"取最新N条"逻辑必须显式排序。

---

## E. 测试隔离陷阱

### E-1｜测试使用真实 tracker.db 污染生产数据

**现象：** 测试运行后，`tracker.db` 中出现测试用的股票代码，或真实缓存被测试数据覆盖。
**根因：** 测试中没有 mock `DB_PATH`，直接读写了 `~/a-stock-tracker/tracker.db`。
**修复：** 所有涉及数据库的测试必须用 `tmp_path` fixture 隔离：
```python
@pytest.fixture
def tmp_cache_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_cache.db")
    monkeypatch.setattr(cache_mod, "DB_PATH", db_path)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    cache_mod.get_db().close()
    return db_path
```
**防复发：** CLAUDE.md 已列为必须行为："新增测试用例：必须用 tmp_path fixture 隔离数据库"。Code review 时检查是否有直接使用 `config.DB_PATH` 的测试。

---

### E-2｜测试发起真实 AKShare 网络请求

**现象：** 测试在无网络环境失败，或因 AKShare 接口变更导致测试集体失败，CI 不稳定。
**根因：** 未 mock AKShare 调用，测试直接发出 HTTP 请求。
**修复：** 使用 `unittest.mock.patch` 或 `monkeypatch` 替换 AKShare 函数：
```python
with patch("akshare.stock_financial_report_sina", return_value=df):
    result = fetcher_mod._compute_gross_margin("603606", "制造业")
```
**防复发：** CLAUDE.md 硬性规则："禁止在测试中发起真实 AKShare 网络请求——所有 AKShare 调用必须 Mock"。

---

## F. 架构设计陷阱

### F-1｜Sheets sync 失败阻断 daily cron

**现象：** Google Sheets API token 过期或网络问题导致 `sheets_sync.sync_all()` 抛出异常，整个 daily cron 中断，当日评分未写入 SQLite。
**根因：** `sheets_sync.sync_all()` 未做异常捕获，异常向上传播中断了主流程。
**修复：** 用 `try/except` 包裹所有 Sheets 调用，失败只记 `WARNING`，不影响 SQLite 写入。
**设计原则：** SQLite 是真相来源，Google Sheets 是展示层。展示层的任何失败都不得阻断数据层的写入。这一原则已推广到 Telegram 推送、Phase 4 里程碑检测等所有后置步骤。

---

### F-2｜L3 信号规则变更无版本化，回测失效

**现象（Codex 审查发现，尚未发生）：** Phase 5 实装 L3 买点信号后，若后续修改信号规则，旧记录的 `entry_signal=0` 和新规则下的 `entry_signal=0` 含义不同，回测无法区分。
**根因：** 设计文档初稿未考虑 L3 规则的版本化。
**修复（文档层面）：** `predictions` 表新增 `entry_signal_version TEXT` 列，每次规则变更时更新版本号（v1/v2/...）。`NULL` 表示规则未实装（Phase 5 前），与 `0`（主动不通过）语义不同。已在 evolution-roadmap v1.1 中明确。
**防复发：** 任何影响推送决策的规则字段，都要有版本追踪列。新增字段时先问："如果我以后修改这个规则，历史记录能区分吗？"

---

### F-3｜Phase 7 前置条件缺失 Phase 6 依赖

**现象（Codex 审查发现）：** evolution-roadmap 初稿 Phase 7 的前置条件只写"Phase 5 买点层已实现"，但 Phase 7 架构中包含"Framework A/B/C/D/E/F 精筛"，若 Phase 6 多框架未完成，Phase 7 无法按设计运行。
**根因：** 写文档时只考虑了直接前驱，未考虑间接依赖。
**修复：** Phase 7 前置条件补充"Phase 6 至少完成目标行业框架，或明确 Phase 7 期间仅用 Framework A 作为降级策略"。
**防复发：** 多阶段规划文档写完后，用独立视角（或 Codex）做依赖图审查，确认每个阶段的所有前置条件都已列出。

---

### F-4｜"不做什么"边界描述不清导致实现冲突

**现象（Codex 审查发现）：** evolution-roadmap 初稿在"不做什么"中排除了"价格动量因子"，但 Phase 5 的候选信号包含均线和成交量（属动量类信号），逻辑矛盾。
**根因：** "不追踪价格动量"的意图是"不把动量纳入公司质量评分（L1/L2）"，但表述成了"完全排除动量"，导致 L3 买点层的合理信号被意外排除。
**修复：** 澄清为："动量信号不得影响 L1/L2 的 total_score；允许作为 L3 入场过滤信号使用"。
**防复发：** "不做什么"的条目要写清楚范围边界，而不是简单列出被排除的技术名词。用"在 X 层不做 Y"替代"不做 Y"。

---

### F-5｜L3 从缓存计算时只检查行数，遗漏 freshness

---

### F-6｜公共符号重命名未同步调用方导致 ImportError

**现象：** `fake_review` 函数重命名为 `_fake_review_fallback` 后，`pytest` 在收集 `tests/test_agent_reviewer.py` 时立即报 `ImportError: cannot import name 'fake_review'`，整个测试模块无法运行。  
**根因：** 重命名只修改了 `lib/agent_reviewer.py` 的定义，没有 grep 仓库内的调用方。测试文件第 3 行仍从旧名导入。QA reviewer 识别了风险模式（"diff 外可能有调用方"）但因为只看 diff 无法确认，标注为 `unverified`，未触发强制修复流程。  
**修复：** 在测试导入行改为 `from lib.agent_reviewer import _fake_review_fallback as fake_review`。  
**防复发：** 重命名或删除任何公共符号（函数、类、常量）前，先 `grep -r 旧名 .` 确认调用方清单。collab-pipeline Step 8 的 QA rubric 已强制要求：`refactor/split` 和 `feature/new-code` 任务若涉及公共符号重命名，必须在 diff 外 grep 或标注 `unverified-needs-grep`，不得仅凭 diff 判断安全。

---

**现象：** Market Data Boundary 重构后，L3 买点层改为从本地 `daily_bars` 读取 120 条标准化日线再计算 `entry_signal`。代码最初只检查 `len(rows) >= 120`，如果当天 L3 refresh 失败但历史缓存里仍有 120 条旧数据，系统可能用几周前的 bars 计算出 `entry_signal=1/v1`，进而触发用户可见的买点信号。

**根因：** 把“有足够窗口”和“窗口足够新”混为一谈。`price_at_score` 有 5 个自然日 freshness SLA，但 L3 缓存窗口没有同步校验最新 `trade_date` 距 `score_date/today` 的新鲜度。缓存边界迁移后，网络失败不再直接表现为缺数据，旧缓存会让行数检查误判为可计算。

**修复：** `_compute_stock_entry_signal()` 在调用 `compute_entry_signal()` 前读取窗口最后一条 `date`，若最新交易日距 `today` 超过 5 个自然日，则返回：

```text
entry_signal=NULL
entry_signal_version='v1'
entry_signal_status='unavailable'
entry_signal_reason='SOURCE_STALE'
```

并保留 `source/fetched_at` 便于报告和审计追踪。新增回归测试 `test_compute_stock_entry_signal_rejects_stale_cached_bars`，构造 120 条足够但最新日期过期的 `daily_bars`，断言不得输出 pass/reject。

**防复发：**

- 任何从缓存读取行情并产出用户可见信号的路径，都必须同时检查“窗口长度”和“最新数据 freshness”。
- `compute_entry_signal()` 保持纯函数，不感知日期 SLA；freshness 属于 pipeline/cache 边界责任。
- provider refresh 失败不等于 signal 可以继续用旧缓存。旧缓存只能作为可审计降级输入，过期时必须写 `NULL/v1` + structured reason。
- 测试夹具如果要表达“今日可用行情”，最新 bar 日期必须等于 today 或在 SLA 内；不要让夹具无意中用 stale bars 通过。

---

## 附录：快速检索

| 关键词 | 对应条目 |
|--------|---------|
| NULL 字段 / 字段未抓取 | A-1 |
| 日度评分静态 / 评分不变 | A-2 |
| 缓存被覆盖 / merge | A-3 |
| head(3) 取错 / 排序缺失 | A-4 |
| alpha_30d 写入报错 | B-1 |
| SQLite 版本 | B-2 |
| weights_hash 误变更 | B-3 |
| 新增列不生效 | B-4 |
| 批量写入无 per-stock 原子隔离 | B-5 |
| 均分跳变 / 系统性偏移 | C-1 |
| Gemini 历史不可比 | C-2 |
| weights_hash 变更 | C-3 |
| Framework B 暂停 | C-4 |
| 沪深300 benchmark NULL | D-1 |
| AKShare 返回 str | D-2 |
| Codex stdin 挂起 | D-3 |
| spot_em 单次失败即今日锁定 | D-5 |
| 测试污染生产数据库 | E-1 |
| 测试发真实网络请求 | E-2 |
| Sheets 阻断 cron | F-1 |
| entry_signal 版本化 | F-2 |
| 阶段依赖缺失 | F-3 |
| 边界定义冲突 | F-4 |
| daily_bars stale / SOURCE_STALE / L3 旧缓存 | F-5 |
| 公共符号重命名未 grep 调用方 | F-6 |
