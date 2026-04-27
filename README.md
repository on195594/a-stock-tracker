# a-stock-tracker

A 股自动评分管道。每日盘后对 watchlist 股票运行 Framework A 定量评分，
记录到 SQLite predictions 表，追踪 30/60/90 天收益率，输出 benchmark 相对命中率报告。

## 首次运行步骤

### 1. 创建虚拟环境并安装依赖

```bash
cd ~/a-stock-tracker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置 API Keys（Phase 3）

复制模板并填入真实密钥：

```bash
cp .env.example .env   # 如无 .env.example，直接创建 .env
```

`.env` 内容格式（三项均为可选，未填写时自动降级到固定评分 / 跳过推送）：

```
GEMINI_API_KEY=你的_Gemini_API_Key
TELEGRAM_BOT_TOKEN=你的_Bot_Token
TELEGRAM_CHAT_ID=你的_Chat_ID
```

**获取方式：**
- `GEMINI_API_KEY`：前往 [Google AI Studio](https://aistudio.google.com/) 创建 API Key
- `TELEGRAM_BOT_TOKEN`：在 Telegram 中向 [@BotFather](https://t.me/BotFather) 发送 `/newbot`
- `TELEGRAM_CHAT_ID`：向 [@userinfobot](https://t.me/userinfobot) 发 `/start` 获取你的 chat_id

> **注意：** `.env` 已加入 `.gitignore`，不会被提交。

### 3. 初始化 watchlist 数据

为 watchlist 中的每只股票预填财报数据：

```bash
python3 pipeline.py init
```

**预计耗时：** 15 分钟左右（取决于网络和 watchlist 规模）

### 4. 配置定时任务

```bash
bash cron-setup.sh
```

自动配置以下两条 cron 规则：
- **daily**：每个工作日 16:30 运行评分
- **outcome-update**：每个工作日 17:00 更新到期预测结果

### 5. 查看准确率报告（可选）

等待 30 天后有结案记录：

```bash
python3 pipeline.py accuracy-report
```

---

## Phase 3 功能说明

### Gemini 定性评分

`daily` 运行时自动调用 Gemini API，对每只股票评估三个维度：

| 字段 | 说明 | 值域 |
|------|------|------|
| `moat` | 护城河（品牌/专利/网络效应等竞争壁垒） | 1–10 整数 |
| `market_pos` | 市场地位（行业排名/份额） | 1–5 整数 |
| `sentiment` | 近期市场情绪 / 消息面 | 1–5 整数 |

**fallback 机制：** 任何字段校验失败（或 API 超时/未配置），三个字段全部回退到固定值 `{moat: 5, market_pos: 2, sentiment: 3}`，保证评分始终产出。

**30 天缓存：** 评分结果缓存在 `qualitative_scores` 表，同一股票 30 天内不重复调用 API，降低成本。

### Telegram 每日推送

`daily` 完成后，若有股票总分 ≥ 55 分（`buy_strong` 阈值），自动推送信号到配置的 Telegram 聊天。

未配置 `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` 时静默跳过，不影响评分流程。

### 评分阈值（Phase 3）

| 信号级别 | 阈值 | 说明 |
|----------|------|------|
| 强买入 `buy_strong` | ≥ 55 | 触发 Telegram 推送 |
| 中买入 `buy_moderate` | ≥ 45 | 报告中标注 |
| 轻买入 `buy_light` | ≥ 35 | 报告中标注 |

> Phase 3 接入 Gemini 后定性分由 AI 填写，total_score 上限从约 64 提升到约 80，阈值已从 65/55/45 下调至 55/45/35。

---

## 日常使用

### 手动运行评分

```bash
source .venv/bin/activate
python3 pipeline.py daily
```

### 手动更新预测结果

```bash
python3 pipeline.py outcome-update
```

### 查看准确率报告

```bash
python3 pipeline.py accuracy-report
```

### 查看执行日志

```bash
tail -f ~/a-stock-tracker/logs/daily.log
tail -f ~/a-stock-tracker/logs/outcome.log
```

## Watchlist 更新

### 添加新股票

1. 编辑 `config.py` 中的 `WATCHLIST` 列表，添加新股票代码（如 `'600000'`）
2. 重新运行初始化命令为新股票预填数据：

```bash
python3 pipeline.py init
```

### 删除股票

需要两步操作，缺一不可：

1. 编辑 `config.py`，从 `WATCHLIST` 中删除对应条目
2. 清理数据库中的历史数据：

```bash
python3 pipeline.py remove <股票代码>
# 例如：
python3 pipeline.py remove 601857
```

该命令会同时删除 `stock_fundamentals`（基本面缓存）和 `predictions`（历史预测）中该股票的所有记录，并输出删除行数确认。

> **注意：** 删除后历史预测不可恢复。如需保留历史数据仅停止跟踪，只改 `config.py` 不执行 remove 即可——后续 daily 不会再对该股评分，历史记录仍保留在准确率报告中。

## 重要注意事项

### Phase 1 规模限制

- **Watchlist 规模：** ≤ 20 只股票
- **预填耗时：** 约 15 分钟（init 命令）
- **日运行耗时：** 约 5-10 分钟（daily 命令）
- **确保完成时间：** 17:00 outcome-update 运行前必须完成 daily

### 数据统计声明

1. **样本不足**：20 只股票 × 3 个月 ≈ 60 条 30d 结案记录，不具备统计显著性
2. **选择性偏差**：watchlist 是手动维护的已知标的，命中率不代表框架泛化能力
3. **牛市通胀**：`hit_rate_30d_abs > 50%` 不代表框架有效，看 `hit_rate_30d_vs_300`（相对沪深300）
4. **Phase 1 目标**：数据积累与方法论验证，不是得出投资结论

### 禁止事项

- ❌ 直接修改 `alpha_*d` 列（SQLite Generated Column，自动计算）
- ❌ 手动 UPDATE predictions 表中的 `total_score` 或 `weights_hash`（破坏可比性）
- ❌ 修改 lib/cache.py 指回 `~/.claude/skills/a-stock-research/cache.db`
- ❌ 测试中发起真实 AKShare 网络请求（必须 Mock）

## 文件说明

| 文件 | 说明 |
|------|------|
| `pipeline.py` | 主编排器（init / daily / outcome-update / accuracy-report） |
| `scorer.py` | 评分引擎（breakpoints 线性插值） |
| `gemini_scorer.py` | Gemini 定性评分（30天缓存，all-or-nothing fallback） |
| `telegram_push.py` | Telegram 每日信号推送（≥55分触发） |
| `weights.json` | 模型权重 + 评分阈值配置 |
| `config.py` | watchlist / 数据库路径 / 日志目录 |
| `.env` | API Keys（不提交 git） |
| `cron-setup.sh` | 自动配置 crontab |
| `lib/fetcher.py` | 数据获取（从 a-stock-research skill 复制） |
| `lib/cache.py` | SQLite 缓存管理（从 a-stock-research skill 复制） |
| `tracker.db` | SQLite 数据库（自动生成） |

## 测试

```bash
# 全部测试（36 个用例）
pytest tests/ -v

# 单模块测试
pytest tests/test_scorer.py -v        # 11 个用例：scorer 插值、边界、异常
pytest tests/test_pipeline.py -v      # 15 个用例：daily / outcome-update / accuracy-report
pytest tests/test_gemini_scorer.py -v # 5 个用例：正常/超时/非JSON/越界/缓存命中（全部 mock，不调真实 API）
```

## 代码质量

```bash
source .venv/bin/activate

# Lint（auto-fix 安全修复）
ruff check . --exclude .venv
ruff check . --exclude .venv --fix

# 类型检查
mypy pipeline.py scorer.py --ignore-missing-imports
```

## 设计文档

详细的设计和实现细节见：
- `docs/design.md` — 整体架构
- `docs/test-plan.md` — 测试计划
- `docs/impl-plan.md` — 实施计划

## 问题排查

### 问题：daily 执行超时

**原因：** watchlist 规模过大或网络慢

**解决：** 减少 watchlist 规模到 ≤ 20 只

### 问题：outcome-update 查询失败

**原因：** 16:30-17:00 间网络波动

**解决：** 手动重新运行 `python3 pipeline.py outcome-update`

### 问题：predictions 表有重复记录

**原因：** 通常不会发生（`code/framework/score_date` 有 UNIQUE 约束，INSERT OR IGNORE 保护）。
若出现，多为早期直接操作数据库留下的脏数据。

**检查：** 
```bash
sqlite3 tracker.db "SELECT COUNT(*), code, framework, score_date FROM predictions GROUP BY code, framework, score_date HAVING COUNT(*) > 1;"
```

### 问题：准确率报告数据过少

**原因：** 样本不足（< 100 条结案记录）

**说明：** Phase 1 预期，等待 3-6 个月数据积累

### 问题：Gemini 全部使用 fallback 评分

**可能原因：**

1. **未配置 API Key**：检查 `.env` 是否存在且 `GEMINI_API_KEY` 有值
2. **模型已停用（404）**：运行以下命令检查可用模型列表
   ```bash
   source .venv/bin/activate
   python3 -c "
   import os, json, urllib.request
   api_key = [l.split('=',1)[1].strip() for l in open('.env') if 'GEMINI_API_KEY' in l][0]
   url = f'https://generativelanguage.googleapis.com/v1beta/models?key={api_key}'
   with urllib.request.urlopen(url) as r:
       models = [m['name'] for m in json.loads(r.read())['models'] if 'generateContent' in m.get('supportedGenerationMethods',[])]
       print('\n'.join(models[:8]))
   "
   ```
   将 `gemini_scorer.py` 中 `GEMINI_MODEL` 更新为列表中可用的模型名。
3. **quota 超限（429）**：免费额度耗尽，等次日重置或升级套餐
4. **网络问题**：检查能否访问 `generativelanguage.googleapis.com`

### 问题：Telegram 推送未收到消息

**排查步骤：**

1. 确认 `.env` 中 `TELEGRAM_BOT_TOKEN` 和 `TELEGRAM_CHAT_ID` 已填写
2. 检查当日是否有 ≥ 55 分的信号（低于阈值时正常跳过，不是错误）：
   ```bash
   sqlite3 tracker.db "SELECT code, total_score FROM predictions WHERE score_date = date('now') ORDER BY total_score DESC LIMIT 5;"
   ```
3. 测试 Bot 是否正常：
   ```bash
   source .venv/bin/activate
   python3 -c "
   import os
   [os.environ.setdefault(*l.strip().split('=',1)) for l in open('.env') if '=' in l]
   import telegram_push
   # 临时降低阈值测试推送
   telegram_push.push_daily_signals('$(date +%Y-%m-%d)', threshold=0)
   "
   ```

### 问题：spot_em 快照失败（price_at_score=NULL）

**原因：** AKShare 东方财富数据源偶发故障

**影响：** 当日评分记录会写入 `price_at_score=NULL`，30 天后无法计算收益率（`outcome_30d` 置 NULL）

**处理：** 外部问题，等待 AKShare 恢复。若当日数据对你很重要，可在接口恢复后手动运行 `daily`（UNIQUE 约束会跳过已有记录，不会重复写入）
