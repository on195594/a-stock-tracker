# a-stock-tracker

面向数周至数月的 **A 股波段机会助手**：**看候选 → 看懂 AI 解释与反证 → 等待价格条件**。帮助减少无效信息，不承诺收益、不提供仓位管理或自动交易。

## 核心范围

- **默认首页 `/`**：少量波段观察候选，先解释为什么值得等、什么情况放弃，再展示价格条件；公司详情在 `/opportunity/代码`。没有合格候选就明确不推荐。笔记、主动更新和已阅退出默认主流程。
- **swing-observation-v1（首版）**：显式指定 1–5 家沪深主板公司，以完整日线计算趋势、相对强弱和观察条件；AI 解释并经另一次模型调用复核。**不是全市场自动精选，也未覆盖板块资金、公告催化和完整财务风险，不可当作综合荐股。**
- **兼容入口 `/research`**：保留个人关注、笔记、主动更新与已阅；原 peer-screen-v1 资格、PB/ROE 公式和历史快照不改。旧财报排名不是波段推荐。
- **market-research-v1**：原独立全市场财报研究批处理仍可使用，不自动转成波段候选或写入个人工作区。

不恢复 QFQ、收益回测、分红物化、交易账户、Framework A 或 Agent 调度平台。价格相对强弱仅作筛选事实，不证明未来收益。

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

## 波段观察批处理

批处理与 Web 分离；打开页面不请求行情或模型。demo 默认展示明确标记的合成机会与解释，不冒充真实 AI 结果。生产未配置/未发布批次时显示空状态。

真实运行需另行授权，并准备环境中的 `TUSHARE_TOKEN`、已安装的 Hermes runtime 与模型授权；不自动读取 `.worker.env`。还需 TuShare `daily/index_daily/stock_basic/trade_cal` 权限。首次 root 须全新、私有（0700），不要指向个人工作区或旧机器研究目录。

```bash
export AUTOMATION_HERMES_RUNTIME=/absolute/path/to/installed/hermes-agent
# 用最近7天内已完成的交易日替换 YYYY-MM-DD；当日批次须在上海时间18:00以后
# PROVIDER/MODEL 替换为本人已授权的身份；示例不是执行授权
.venv/bin/python -m a_stock_tracker.opportunities \
  --root /private/swing --codes 600519.SH,600900.SH --as-of YYYY-MM-DD \
  --provider PROVIDER --model MODEL --request-budget 20 --seconds 3000
```

每次调用最多 50 次行情请求、3600 秒，最多 5 家、10 次模型调用；生成/复核分别计数，每次沿用 240 秒 / 6000 输出 token 限制。默认请求预算 20、时间 3000 秒；没有隐式重试或 resume，重新运行会产生新批次与新费用。生成失败或语义复核未通过不展示价格条件。退出码 0 表示没有证据/模型缺口（可零入选），2 表示至少一家为资料或解读缺口，1 表示整次运行异常；单家不支持或缺资料的公司记为缺口，不丢弃其他公司；不代表策略有效。

当前透明规则（工程起点，未验证投资收益）：

- 61 个完整交易日日线，收盘价 > 20日均价 > 60日均价，近20日价格涨幅强于沪深300；缺日线、停牌零量、身份冲突、名称含 ST/退 或除权造成价格基准不连续均拒绝。名称筛查不等于已核查全部退市风险。
- 观察区间下沿为最近20日最高价加0.01元，上沿为下沿的1.02倍；后续完整日收盘在区间内、成交量至少为批次20日均量的1.5倍，才值得重新评估，**不是买入指令或已触发信号**。
- 近10日最低价为失效条件；区间上沿到失效价距离超过8%不入选。距离不是最大损失承诺；T+1、跳空、停牌和涨跌停可能阻止退出。
- 条件观察有效期为后续5个交易日，不等于持有期；到期后主区域和展开依据均隐藏触发/失效条件及距离，历史解释与量价依据仍可查，原始批次不改写。盘中信号、自动触发追踪与跨批次变化判断尚未实现。

每个 `swing-*` 目录保存证据、模型请求/响应和结果；发布后不改写旧批次，最后原子切换 `latest.json`。只有最终结果和指针成功落盘才报告成功。Web 用 `OPPORTUNITY_ROOT` 读取同一目录，校验结果哈希、有效期与 actor；生产 Web 只读挂载目录，不持行情/模型凭据，不将报告放入静态资源。

Docker 可选覆盖文件 `docker-compose.opportunities.yml` 提供只读挂载，宿主路径由 `OPPORTUNITY_HOST_ROOT` 指定；目录需预先创建并允许容器运行用户读取。基础 compose 不自动启用；现有 `deploy.sh` 不加载此覆盖文件，后续部署必须显式使用两个 compose 文件且另行授权，不能误称已上线。

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

不自动读取账户配置或 `.worker.env`。需要环境中的 `TUSHARE_TOKEN`、现有模型授权、已安装的 Hermes runtime，以及 `pdfinfo/pdftotext`。以下是真实请求示例，不是执行授权；运行前须确认累计请求预算、模型身份及费用边界。

```bash
export AUTOMATION_HERMES_RUNTIME=/absolute/path/to/installed/hermes-agent
# 显式小样本验收；仍枚举全市场，财务覆盖永远 partial，累计最多研究 1 家
.venv/bin/python -m a_stock_tracker.automation --root /private/market-research --sample 600519.SH,600900.SH,600019.SH --request-budget 100 --seconds 600 --max-research 1

# 全市场只扫描：不请求公告或模型
.venv/bin/python -m a_stock_tracker.automation --root /private/market-research --request-budget 4000 --seconds 14400 --max-research 0

# 恢复同一冻结批次；显式重设本次预算，批次累计最多研究 1 家
# 不改范围、日期或模型，超过 7 天拒绝恢复；预算不足时保留检查点
.venv/bin/python -m a_stock_tracker.automation --root /private/market-research --resume RUN_ID --request-budget 100 --seconds 600 --max-research 1
```

- 首次 root 必须为空且私有（0700），拒绝符号链接及其他已有数据；不要指向 `data/research`。不读取/写入个人笔记、已阅、持仓、`set-analysis` 或业务数据库。
- 逐证券记录 qualified/excluded/gap/pending 并核对总数。全市场模式只有完整行业组发布候选；在相同估值日、行业、三年年报窗口内复用 PB/ROE 平均名次，不用个人参照或 50 家 cap。每组最多前三，行业轮转，批次队列最多 5 家；不是跨行业投资总榜。抽样模式按输入顺序研究合格样本，不要求行业组完整，也不代表正式筛选结果。
- 默认请求预算 4000、时间 14400 秒、最多研究 5 家；支持范围分别为 3–10000、60–21600 秒、0–5 家。**请求/时间预算按每次调用重新计，恢复不代表批次总预算不变**；`--max-research` 是该批次累计报告数上限，包含已提交的失败报告，不是“本次再加 N 家”。恢复时可显式调整此上限；已有 1 家报告时设为 1 不再生成新报告。行情/公告请求失败最多重试一次。
- 默认模型为 `openai-codex / gpt-6-astra`，可显式传 `--provider` / `--model`，恢复必须与冻结值一致。每个冻结批次累计最多 10 次模型调用，每次 240 秒 / 6000 输出 token；生成与复核分别计数，失败或被终止的调用也计数。调用上限不是费用上限，未报告费用不能写成免费。
- 巨潮定期公告分页，冻结公告列表、PDF 哈希及逐页文本。报告年/期仍限前六页，证券代码须为原文连续完整数字。简称/代码后移时，仅允许第 7–16 页同页唯一的正式简称、代码、公司中文名称字段；简称/代码须与公告一致，公司名称完整值须在前六页同一页与对应年/期组成标题（同一行或相邻非空行），不是正文子串或其他公司名称的前缀/后缀。字段最多 64 个空白/冒号，中文名称整行最多 80 字符；冲突、前缀值、跨页拼接及全文宽松查找不接受。漏页拒绝原文，短页/空白页保守记 incomplete；证据包受字符预算限制，未送入模型的页码必须公开。重大临时公告、诉讼、新闻未全面覆盖，不能声称通读全部资料。
- `scripts/automation_model.py` 只运行无工具推理、返回固定 JSON。证券、页码、数字/单位和逐字引文先程序校验，再另一次模型调用复核语义；未通过不发布确定结论。原文/模型输出不具备命令或个人状态写入权限。
- `summary.md` 是批次入口，`report-代码.md` 展示判断、估值假设、反证、下一步及缺口；先发布/校验报告，再提交候选状态与游标。相同证据/模型/模板不重复推理。
- CLI JSON 与 `invocation.json` 的 `report_counts` / `failed_reports` 区分报告状态与复核失败。退出码 0 要求覆盖完成、无待研究且复核通过；2 表示部分完成或存在失败；运行异常为 1。只扫描也会因候选待研究返回 2。已提交的失败报告是终态，`--resume` 不重跑；修复原因后须显式新建批次。通过复核不代表全文完整、投资可用或获得 `a-stock-qa` 标签。
- 代码交付不启用周度定时或投递。一次性验收不等于生产切换；未来确认预算、时间和目的地后，原生定时器调用同一入口即可。

## 当前阶段与下一步

**当前已按用户反馈将默认页面转向波段机会，但首版只完成有界量价观察与 AI 解释，不等于完整智能荐股。** 旧工作台验收不再是主产品目标。源码落地不等于已部署，也不证明策略有效。

源码验收使用 `make check` 和 `make test-mobile`：规则、批次完整性、授权/迟到回调及旧工作区回归均用合成数据/模拟接口；浏览器在 loopback 下验证 360/390/430px 的列表 → 条件/反证 → 依据 → 返回及兼容入口。它们不替代真实行情、真实模型解释、本人 OAuth 或手机真机验收。生产版本须另核对运行容器与线上入口；发布代码不自动启用真实研究批处理或定时。

| 方向 | 当前状态 | 还缺什么 |
|---|---|---|
| 默认体验 | 机会列表、AI解释/反证、条件详情；旧笔记/已阅退到兼容入口 | 本人对新信息结构的手机验收 |
| 研究链 | 显式小池、完整日线、透明价格条件、两次模型调用与私有发布 | 有预算的真实小样本核验；板块/公告/催化与财务风险证据 |
| 持续使用 | 只读最新发布批次、到期提示、不可变历史记录 | 跨批次重要变化、条件触发/失效追踪；尚无自动关注流程 |
| 运行 | 独立CLI和可选只读容器挂载 | 明确授权后部署；定时/投递未启用 |
| 投资价值 | 未证明 | 不用可读性、引用通过或技术规则通过冒充收益验证 |

下一步：

1. **先验收新体验**：用 loopback demo 在手机尺寸浏览候选 → 解释 → 条件 → 反证；确认不需要写笔记或点已阅也能理解。不再把旧工作台闭环作为新产品验收目标。
2. **再做一次真实小样本**：确认股票池、已完成交易日、模型身份、累计请求与费用边界后，运行独立批次；逐条核对日线、价格条件与解释。通过后再补有证据的催化/板块筛选，不急于扩大到全市场。
3. **最后接入持续使用**：先实现跨批次条件变化，再决定刷新频率、预算和部署；未经授权不自动取数或安装定时任务。还需另行设计推荐效果的前瞻验证，当前未声称有收益优势。

旧版本的生产与全市场财报研究验收属于历史记录，见 [CHANGELOG](CHANGELOG.md)；不能替代新波段流程的真实验收。

## 文档入口

- [开发约束](AGENTS.md)：范围、安全边界与验证要求。
- [架构与数据合同](docs/architecture.md)：模块归属、持久化及兼容边界。
- [peer-screen-v1 规则](docs/specs/2026-09-22-peer-screen-spec.md)：资格、公式、并列与历史口径。
- [变更与历史恢复](CHANGELOG.md)：历史入口，不作为运行手册。
