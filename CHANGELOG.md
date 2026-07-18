# CHANGELOG

所有重大变更按时间倒序记录。

## 2026-07-18 — MILESTONE-004 v1.3.2 capability approval draft

- 起草未签署、不可执行的外部审批请求、历史/用户事实输入记录和 fresh external decision template；以独立 packet checksum 绑定三份草案字节，不生成 capability authorization JSON 或 authorization sidecar。
- 提出 fresh IDs、`2026-07-17` probe trade date、`2026-07-20 18:00–20:00 +08:00` 执行窗口及规范化目标路径；固定 v3 schema、35-call 上限、四类 API entitlement/quota/cost/terms 门禁、单次无重试和 PASS/FAIL 后续处置。
- 明确 v1.3.1 的 `stock_st`/3000-point blocker 不再属于 v1.3.2 矩阵，同时要求外部 approver 显式接受 name-only `ST|*ST` 无法识别名称未标记风险警示股的残余风险。
- 草案验收通过：packet checksum、freeze attestation、双 golden oracle、proposed matrix/window/path 检查及 v1.3.2 directed 148 项测试。外部 approver、当前账户侧 entitlement/剩余额度证据仍 pending；未读取 token、联网、执行 provider 或创建 attempt/control artifacts。

## 2026-07-17 — MILESTONE-004 segmented REST v1.3.2 frozen

- 以 `f494c1…88042` v1.3.1 为直接前驱冻结完整 v1.3.2 协议，frame/date 矩阵为 35/2；删除独立 `stock_st` 请求、parser、gate 和 ledger source，daily 固定为 ordinal 35。
- authorization/artifact/supervisor schema 升为 v3；`stock_basic.name` 仅做 NFKC、outer trim、casefold 后的 `ST|*ST` 前缀排除，并明确无法识别名称未标记风险警示股的剩余风险；provider `count` 与 rows 必须精确相等，包括零。
- 新增五个独立 `_v132.py` generator 文件、canonical schema contract、30 个逐文件 golden vectors、两个不导入项目实现的标准库 oracle、历史 v1.3.1 inventory 和 candidate/review/attestation 无环冻结链。
- fresh read-only 首审发现 supervisor outcome 大小写 P1；修复 runtime/recovery/verifier 为规范大写 `PASS|FAIL` 后，新的 fresh reviewer 以 P0–P3 全 NONE、strict PASS 批准 exact bytes。
- 最终审查门禁：v1.3.2 定向 148 项、历史 v1.3.1 定向 133 项、M4 448 项、全仓 926 项；Ruff lint/F401/format、mypy、双 oracle、checksum/inventory/import closure、diff/status 全部 PASS。
- 本轮未创建 capability authorization，未读取 token、联网或执行 Tushare，未实现 assembler、组装 frame/sample、访问生产 DB 或接入 pipeline/cron/Telegram/Gemini。

## 2026-07-17 — MILESTONE-004 segmented REST v1.3.1

- 保留 v1.3 冻结字节及 `0ad8c6…a12df` hash，新增直接链接该前驱的 v1.3.1 协议/checksum 和隔离 artifact roots；全部 authorization、receipt、stop、ledger、date-evidence、manifest schema 升级为 v2。
- 新增 capability/capture 精确 36 调用与 date-evidence 精确 SSE/SZSE 两调用矩阵；公共 API 仅暴露不可变边界类型、授权加载、执行、离线验证和 capture eligibility guard。
- 新增父 supervisor/worker hard deadline、attempt 外 phase journal、raw-first create-only blob/receipt/supporting artifact、candidate self-verify、manifest 预绑定的 parent-only positive commit 与发布后复验；token 只从环境读取精确 64 位小写十六进制，不读取 `.env`。
- 离线 verifier 从 raw bytes 重建 ordinal、数据 gates、exact-number exclusion ledger、calendar/date derivation、closed set、权限/link/path、authorization/protocol/五文件 generator provenance；date 失败态不发布 evidence。
- retained v1.1 assembler 增加统一 v1.3.1 拒绝 guard；未创建真实授权、未执行 Tushare、未读取生产 DB、未实现 frame/sample assembler。
- 合成、禁 socket 验收通过：v1.3.1 定向 133 项、MILESTONE-004 300 项、全仓 778 项；Ruff、F401、format、mypy、冻结 hash/golden checksum 与 `git diff --check` 通过；后续审查发现并修复 provider `count: 0` 与非空 rows 的矛盾接收，同时清理未使用的私有符号和参数。
- 准备 capability 外部审批请求与未签署 decision template，明确最大 36 次只读请求、既往 `40203` 风险、待审批字段、单次无重试/失败封存边界和批准后机械发布流程；材料不创建 canonical authorization，也不授权 token 或网络访问。
- 归档 Claude 首次只读外部审批：协议、实现基线和 36-call 边界通过，但因身份、IDs、日期/窗口、路径及 provider entitlement/quota 证据缺失而 `DEFER`；阻断解除前不得发布授权或执行请求。
- 归档 `lin` 补充的 capability 候选决策输入并完成无 token 核验：IDs/目标路径无冲突，官方资料支持四类 API 的 2000 积分门槛；因未来证据时间戳与 `stock_st` entitlement 尚未澄清，外部复审和授权发布继续阻断。
- 只读核对用户指定的公开 `积分权限表`，确认 `ST股票列表` 从 3000 积分档开始而当前账户为 2000 积分；因此冻结矩阵 ordinal 35 `stock_st` 的 entitlement 不满足，维持 DEFER 并避免执行可预见失败的真实 36-call attempt。
- 按项目所有者决定撤回未执行的 v1.3.1 capability 请求并起草 v1.3.2 no-stock-ST repair plan：冻结历史不变，新候选矩阵删除 `stock_st` 后为 35 calls，移除对应 gate/ledger source，仅保留 `stock_basic.name` 的本地风险前缀排除；尚未冻结协议、实现或授权执行。

## 2026-07-16 — MILESTONE-004 capture-first exporter

- 将历史混合导出拆为 `capture`、`recover`、`assemble`：原始响应内容寻址即时落盘、canonical receipt、complete/incomplete attempt 隔离、resume 离线重验、stale staging 只封存不采用。
- 新增 canonical authorization + SHA-256 绑定、固定 33-call matrix、HTTPS/host/method/参数/重定向门禁、token 回显写前阻断、非阻塞 capture-root 锁和 sealed-attempt TOCTOU 校验。
- `assemble` 完全离线重验 authorization/resume lineage、receipt/blob/code/SZSE hashes，再原子发布 frame、excluded、36 股 sample、provenance 和 checksums；未访问真实数据源、生产 DB 或 Reviewer。
- 13:34 的旧 5,525 行响应在旧导出器 schema failure 后未即时落盘，现已明确为不可恢复；下一次真实 capture 仍需新的机器可验证授权。
- 合成验收和全仓质量门禁通过：审查加固后 M4 定向 `115 passed`、全仓 `593 passed`，Ruff lint/format/F401、mypy、协议 hashes 和 diff check 全绿。
- 审查加固：永久禁用绕过 canonical authorization 的四组 legacy API/CLI/直连 SZSE 入口；逐 live call 与 raw-first 发布点重验授权时间，receipt 改记实际捕获时间；进程控制异常会将已发布 receipt/blob 纳入 incomplete manifest、封存后原样抛出；删除未使用的 `CaptureManifest` 和导入。
- 17:15 的首次 capture-first 实执按授权只运行一次：`daily_basic` 原始回应命中账户 `5次/天` 限频，SSE summary 有效，首个 SWS 请求严格 TLS 验证发生 `SSLError`；两份已收响应和 31 项 missing matrix 已封存为 incomplete，未重试、未组装。
- 17:44 的单次 strict-TLS 诊断确认 SWS 只发送有效叶证书，未发送 GeoTrust/DigiCert 中间证书，验证返回 code 20；未发送 HTTP 数据或重试，不降级 `verify=False`。
- 对 AIA 提示的派生 DigiCert HTTPS 地址仅执行一次 GET，TLS handshake failure（curl 35），未下载证书、未尝试备用 URL、未重试。

## 2026-07-15 — MILESTONE-004 静态输入执行授权

- 已记录用户授权：仅使用带 provenance 的只读静态 dataset 冻结 frame/sample/corpus。
- 当前仓库尚无合格 dataset 字节或 provenance manifest，因此未创建真实 frame/sample/corpus stage。
- 新增 Tushare、AKShare 及 AKShare+直连官方 SZSE HTTPS 的 audit-only frame 导出器和离线测试；三次获批真实执行分别因账户频率和 SZSE HTTPS transport fail closed，未发布半成品。
- 2026-07-16 通过隔离 Microsoft Playwright MCP/Chromium 补获两份 2026-07-15 SZSE 市场总貌 XLSX；原始字节、provenance 和 SHA-256 已只读封存，但因缺同日市值/行业数据仍未冻结 frame/sample。
- 新增 2026-07-15 历史混合导出器：严格复用 SZSE 静态包，只允许 Tushare `daily_basic`、SSE 总貌和 31 个申万成分查询，并输出确定性 sample manifest；13:34 请求取得 5,525 行但发现 `count=0` 哨兵，仍未触达申万或发布半成品。
- 分页校验现以 `has_more=false` 为截断门槛，允许 `count=0` 未知哨兵或不小于当前页的非负总数并写入 provenance；全仓测试增至 `577 passed`，其余质量门槛保持全绿。
- Reviewer、生产 `tracker.db`、live provider 和生产 pipeline 仍未授权。

## 2026-07-15 — MILESTONE-004 v1.1 离线审计工具链
- 冻结 evidence-feasibility audit v1.1：保留原 sampling seed，新增官方 SW2021 31 行业映射、source-aware URL 归一化和 binary64 Wilson 展示契约。
- 新增纯 Python frame/sample/corpus、technical-attempt ledger、create-only artifact hash chain、双 reviewer seal/index、disagreement/adjudication 和 coverage report API。
- Reviewer 默认禁用；执行授权绑定 bundle/prompt/model/backend/command/environment，隔离 HOME/cache/state/tmp，增量限制 stdout/stderr 并在超时或超限时终止整个进程组。
- 严格审查后补强报告 lineage、company-scoped relationship evidence、原子 no-replace 发布、跨进程 stage 锁、manifest/seal TOCTOU 和完整裁决 schema。
- 验证基线：76 项 M4 定向测试、554 项全量测试；Ruff lint/format、mypy、协议 v1/v1.1 SHA-256 和 `git diff --check` 全部通过。
- 本轮未读取真实数据库、未检索真实公司、未运行真实 Reviewer；工具链完成不等同于 M4 coverage audit 完成或 MILESTONE-005 授权。

## 2026-07-15 — 运维门禁、PM 监控与质量基线修复
- 刷新当天 Tushare capability probe：daily/index/calendar/close cross-check 全部 PASS，恢复 `READY_CRON` 并重新安装 managed cron。
- 修复 weekly PM loop 将中文“失败 0 只”和降级 WARNING 误判为 FAIL：零失败忽略，降级 warning 保持 WARN，明确 ERROR/非零失败仍为 FAIL。
- 修复全库 mypy 24 个错误；对 31 个历史文件执行一次性 Ruff format 基线化。
- 新基线：`373 passed`，Ruff lint/format、mypy、`git diff --check` 全部通过。

## 2026-07-15 — 定性评分 v2 fixture-first 合同（进行中）
- 批准 `docs/specs/2026-07-14-source-grounded-structured-qualitative-scoring-spec.md`，授权范围仅限 MILESTONE-002 fixture-first。
- task 2.1 已实现 `qualitative_v2_types.py` 与 `qualitative_v2_taxonomy.py`：版本化 dataclass、input hash 和 `evidence_type × claim_category` 两层 taxonomy。
- task 2.2 已实现 `qualitative_v2_validator.py`：shape、版本、引用、freshness、rubric 和 all-or-nothing 的纯本地 fail-closed 校验。
- 尚未实现 schema/prompt builder，也未接真实 Gemini、生产 DB、pipeline、cron、Telegram 或权重。

## 2026-07-12 — L3 v2 QFQ 生产接入与推送切换
- QFQ 日线完成 35/35 股票回填并增加工作日 16:00 采集 cron，生产 wrapper 优先使用完整 QFQ 窗口。
- daily 已写入 L3 v2 审计字段；Telegram 主推条件从 v1 `entry_signal=1` 切换为 `l3_v2_signal=1`。
- v1 继续保留用于历史审计，L3 v2 不修改 L1/L2 `total_score`。

## 2026-07-10 — L3 v2 只读离线回测与复盘
- 新增 `scripts/offline_l3_v2_backtest.py`，以 SQLite `mode=ro` + `PRAGMA query_only=ON` 只读回测 Framework A predictions。
- 输出 `signals_daily.csv`、`events_raw_daily.csv`、`events_dedup_20d.csv`、`metrics_summary.json` 和 Markdown 报告。
- 支持项目 `.env` 中的 `TUSHARE_TOKEN`，但当前 Tushare `adj_factor` 返回 `1次/分钟` 限频，qfq 覆盖为 `0/38`，decision gate 保持 `NEED_QFQ`。
- 修复复审发现的回测口径问题：20 日冷却改为按市场交易日计算；qfq volume 按复权比例反向调整。
- AGY 独立复审最终 `APPROVE`；本轮结论写入 `docs/reviews/2026-07-10-l3-v2-backtest-retro.md`。

## 2026-07-07 — 工程审查与文档清理
- 修复核心文档（CLAUDE.md、test-plan.md、project-status.md）中的数据不一致问题。
- 归档历史修复计划（REPAIR-PLAN.md）并清理无用备份、空目录及缓存文件。
- 完善 `.gitignore`，增加工具缓存和 agent 配置目录。

## 2026-07-05 — 推送分层推荐与代码清理
- Telegram 推送新增三级分层推荐（Primary, Backup, Radar）并修复了由于无日期过滤导致的 JOIN 重复行。
- 清理 `pipeline.py` 中遗留的死代码（`_retry`, `_normalize`, `snapshot_data`）。

## 2026-07-04 — agy 工程审查修复（Batch A + B）

### 修复
- DB 写入改为 per-stock SAVEPOINT 隔离，崩溃重跑幂等（06f3d88/b3f1c4c）
- spot_em 改重试计数器（连续3次才今日锁定，单次抖动不再锁死）（23bd764/e056e93）
- Gemini 退避重试（429/5xx，最多3次）+ 过期缓存降级（4058688）
- subprocess TimeoutExpired 绑定异常变量，转发 stderr 日志（1e8b401）
- cache._ensure_columns 列名正则白名单，防 SQL identifier injection（ab9fdb9）
- outcome window SQL allowlist 常量（_OUTCOME_WINDOWS frozenset）（1ccc3da）

### 新增
- agent_reviewer 接入真实 Gemini REST API，任何错误回退 _fake_review_fallback（df765cb）
- cmd_init / cmd_weekly 测试覆盖（delegation + fetcher call-count 断言）（3a72a47/63e3eb4）
- cmd_outcome_update Telegram mock 测试（smoke test + window guard）（5e44ec2/165aa1b）

**测试基线：225 passed, 1 skipped（vs 修复前 211 passed）**

## 2026-07-02 — Phase 6 周期性自动化与加固
- 自动化 Phase 6 周度 PM 复核。
- 强化 cron 异常捕获与 flock 失败告警。
- 加固行情 cron 恢复门禁并清理残余进程。

## 2026-06-25 — 行情数据源演进（Market Data Boundary Refactor）
- 引入统一的 `a-stock-lib`，实现 Provider 会话复用、限流重试、本地缓存及安全回退。
- 退役 tracker 本地的行情 Provider 副本，并彻底隔离 `BaoStock` 作为 Tushare 失败后的降级。

## 2026-05-30 — Phase 5：L3 买点层接入
- 实现并验证 L3 Entry Signal（价量分析）并将过滤结果集成至 Telegram 日常推送及 accuracy-report。

## 2026-05-29 — Phase A 数据治理规划
- 新增数据源 Registry 及质量门槛，迁移 PB 分位至 `scorer.py` 减少耦合，定义只读 agent reviewer 接口。

## 2026-05-15 — REPAIR-PLAN v2.0 修复
- 对齐数据质量的 PB 历史长度判断；解决日度无实质变化的 Bug。

## 2026-04-27 — Phase 3.6 完成与重构
- 弃用 Python 内容聚合，Google Sheets 迁移使用 COUNTIFS 公式提高同步效率与稳定性；对齐精度报告阈值。

## 2026-04-20 — Phase 3 上线
- Gemini 2.5 Flash 替代定性评分，集成 30 天缓存与退避重试降级。
- 增加 .env 自动加载与基于 SQLite 的本地推送缓存机制。
