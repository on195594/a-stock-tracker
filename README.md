# a-stock-tracker

个人 A 股基本面研究助手：**发现候选 / 指定公司 → 核查证据 → 记录判断与反证 → 主动更新 → 复核变化 / 已阅**。研究排序不是买入建议。

## 核心范围

- **个人研究**：同业发现、关注、笔记、事实变化、下一步与已阅。按代码获取资料不自动关注；页面读取、笔记和已阅不触发行情请求。
- **peer-screen-v1**：沪深主板非金融、TuShare 粗行业、最多 50 家，以 PB / 三年 ROE 的平均名次展示前三和参照；公式与历史结果保持兼容。
- **market-research-v1**：另行显式运行的全市场发现与有界机器研究，独立私有目录，报告不写个人工作区、不自动关注或已阅。

不恢复旧评分、QFQ、收益基准、分红物化、交易账户、Framework A 或 Agent 调度平台。

## 本地开发

需要 Python ≥3.13、uv。demo 仅 loopback、合成数据，不需要真实凭据。

```bash
make setup
make demo                    # 初始化 .local/demo，启动 Web 和串行 worker
make check                   # 离线测试、lint、格式、类型与 diff 检查
.venv/bin/python -m playwright install chromium
make test-mobile             # 真实浏览器点击，含 360/390/430px
```

验证仅使用临时目录、合成数据与模拟接口，不调用真实行情或模型。中文字体随包提供，见 [字体说明](assets/fonts/README.md)。

## 运行与数据维护

生产 Web 缺配置拒绝启动。变量模板见 `.web.env.example` / `.worker.env.example`，启动边界见 `docker-compose.yml`、`docker/entrypoint.sh`。`deploy.sh` 先检查配置并运行 `make check`，然后本地构建、重建 Web、验证并重载 Nginx、重建 worker；它有线上副作用，不用于普通开发验证。

- Web 只持 OAuth 配置；worker / 日历命令持行情凭据。只有用户提交的更新请求进入 worker。
- 生产 `PUBLIC_BASE_URL` 必须是 HTTPS；Host、WS Origin、OAuth state、数字 ID 白名单与 `/upload` 限制不可绕过。
- 工作区、凭据、日志和机器研究目录均为私有，不进 Git 或静态目录。Web 只读挂载**整个日历目录**，不能只挂单个文件。
- 提交、推送、部署、重启、迁移、定时与投递均需当前会话明确授权；源码实现不等于启用授权。

```bash
# 初始化全新生产工作区；绝对路径按实际部署选择
.venv/bin/python -m a_stock_tracker.manage init --state-dir /private/research --mode production

# 显式更新日历；写入 TRACKER_DATA_DIR/calendar（默认 data/calendar），Web 挂载同一目录
.venv/bin/python -m a_stock_tracker.calendar

# 离线维护：先停止会写入的 Web / worker；输出目标必须全新
.venv/bin/python -m a_stock_tracker.manage backup --source /private/research --mode production --destination /private/backup-new
.venv/bin/python -m a_stock_tracker.manage restore --source /private/backup-new --destination /private/restored-new --mode production
.venv/bin/python -m a_stock_tracker.manage import --state-dir /private/research --mode production --snapshot /private/snapshot.json
```

备份包含一致的 SQLite 副本及其引用的已校验快照。恢复只写全新目录，重新验证哈希，并终止旧在途任务；不把迁移当成日常操作。日历证据缺失、过期、不完整或日期倒退时拒绝更新，不猜交易日。

## 自动选股与机器研究（独立批处理）

不自动读取账户配置或 `.worker.env`。需要环境中的 `TUSHARE_TOKEN`、现有模型授权、已安装的 Hermes runtime，以及 `pdfinfo/pdftotext`。

```bash
export AUTOMATION_HERMES_RUNTIME=/absolute/path/to/installed/hermes-agent
# 显式小样本验收；仍枚举全市场，但财务覆盖永远 partial
.venv/bin/python -m a_stock_tracker.automation --root /private/market-research --sample 600519.SH,600900.SH,600019.SH --request-budget 100 --seconds 600

# 全市场只扫描：不请求公告或模型
.venv/bin/python -m a_stock_tracker.automation --root /private/market-research --request-budget 4000 --seconds 14400 --max-research 0

# 恢复同一冻结批次并研究；不改范围、日期或模型，超过 7 天拒绝恢复
.venv/bin/python -m a_stock_tracker.automation --root /private/market-research --resume RUN_ID
```

- 首次 root 必须为空且私有（0700），拒绝符号链接及其他已有数据；不要指向 `data/research`。不读取/写入个人笔记、已阅、持仓、`set-analysis` 或业务数据库。
- 逐证券记录 qualified/excluded/gap/pending 并核对总数。全市场模式只有完整行业组发布候选；在相同估值日、行业、三年年报窗口内复用 PB/ROE 平均名次，不用个人参照或 50 家 cap。每组最多前三，行业轮转，批次队列最多 5 家；不是跨行业投资总榜。抽样模式按输入顺序研究合格样本，不要求行业组完整，也不代表正式筛选结果。
- 默认请求预算 4000、时间 14400 秒、最多研究 5 家；支持范围分别为 3–10000、60–21600 秒、0–5 家。**请求/时间预算按每次调用重新计，恢复不代表批次总预算不变**；`--max-research` 是冻结批次累计报告数上限，包含已提交的失败报告。行情/公告请求失败最多重试一次。
- 默认模型为 `openai-codex / gpt-6-astra`，可显式传 `--provider` / `--model`，恢复必须与冻结值一致。每个冻结批次累计最多 10 次模型调用，每次 240 秒 / 6000 输出 token；生成与复核分别计数，失败或被终止的调用也计数。调用上限不是费用上限，未报告费用不能写成免费。
- 巨潮定期公告分页，冻结公告列表、PDF 哈希及逐页文本。报告年/期仍限前六页，证券代码须为原文连续完整数字。简称/代码后移时，仅允许第 7–16 页同页唯一的正式简称、代码、公司中文名称字段；简称/代码须与公告一致，公司名称完整值须在前六页同一页与对应年/期组成标题（同一行或相邻非空行），不是正文子串或其他公司名称的前缀/后缀。字段最多 64 个空白/冒号，中文名称整行最多 80 字符；冲突、前缀值、跨页拼接及全文宽松查找不接受。漏页拒绝原文，短页/空白页保守记 incomplete；证据包受字符预算限制，未送入模型的页码必须公开。重大临时公告、诉讼、新闻未全面覆盖，不能声称通读全部资料。
- `scripts/automation_model.py` 只运行无工具推理、返回固定 JSON。证券、页码、数字/单位和逐字引文先程序校验，再另一次模型调用复核语义；未通过不发布确定结论。原文/模型输出不具备命令或个人状态写入权限。
- `summary.md` 是批次入口，`report-代码.md` 展示判断、估值假设、反证、下一步及缺口；先发布/校验报告，再提交候选状态与游标。相同证据/模型/模板不重复推理。
- CLI JSON 与 `invocation.json` 的 `report_counts` / `failed_reports` 区分报告状态与复核失败。退出码 0 要求覆盖完成、无待研究且复核通过；2 表示部分完成或存在失败；运行异常为 1。只扫描也会因候选待研究返回 2。已提交的失败报告是终态，`--resume` 不重跑；修复原因后须显式新建批次。通过复核不代表全文完整、投资可用或获得 `a-stock-qa` 标签。
- 代码交付不启用周度定时或投递。一次性验收不等于生产切换；未来确认预算、时间和目的地后，原生定时器调用同一入口即可。

## 当前阶段与下一步

本节记录 `d3b1ab5` 及其前序提交的时点验收；后续 PDF 全称标题绑定修复与清理见 [CHANGELOG](CHANGELOG.md#2026-10-09身份核验与清理)。源码验证不等于生产已切换。

截至 2026-10-09，本轮授权已执行生产备份/全新目录恢复、匿名登录与 Host/upload/WS Origin 边界核验，以及合成移动浏览器真实点击。本人 OAuth 和手机研究闭环仍待本人验收，不能用模拟结果代替。

有界真实小样本及全市场候选派生的新批次各有 1 家报告通过程序校验、另一次模型调用复核与原始 PDF 引文逐条核对；报告仍为 incomplete，未覆盖页和新闻/诉讼缺口不隐藏。全市场 5,573 家均已归类：2,218 合格、3,310 排除、45 资料缺口，发布 74 个完整行业/年度组；冻结身份、恢复检查点和平均名次重算已核对。全市场旧失败报告保持终态，派生单公司批次不冒充新的全市场验收。这些时点证据不证明未来运行健康、全市场资料完整或长期投资价值；未启用定时/投递。

下一步：

1. **完成本人真机闭环**：本人 OAuth + 手机完成指定公司 → 核查财报 → 写判断/反证 → 主动更新 → 已阅，检查退出、断线与迟到结果不覆盖输入。
2. **消化缺口与判断价值**：核查 PB、年报 ROE 与公告日期缺口，人工审阅机器报告的估值假设、反证和下一步；不为消除 partial 放宽规则，不把通过引用检查当作投资可用或 `a-stock-qa`。后续真实请求须重新明确累计预算、模型身份及费用边界。
3. **长期价值成立后再决定周度任务**：先证明有用、稳定且可恢复，再明确授权时间/投递目的地；不先造调度平台、不重开评分实验，代码修改只跟已确认问题走。

## 文档入口

- [开发约束](AGENTS.md)：范围、安全边界与验证要求。
- [架构与数据合同](docs/architecture.md)：模块归属、持久化及兼容边界。
- [peer-screen-v1 规则](docs/specs/2026-09-22-peer-screen-spec.md)：资格、公式、并列与历史口径。
- [变更与历史恢复](CHANGELOG.md)：历史入口，不作为运行手册。
