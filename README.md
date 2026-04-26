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

### 2. 初始化 watchlist 数据

为 watchlist 中的每只股票预填财报数据：

```bash
python3 pipeline.py init
```

**预计耗时：** 15 分钟左右（取决于网络和 watchlist 规模）

### 3. 配置定时任务

```bash
bash cron-setup.sh
```

自动配置以下两条 cron 规则：
- **daily**：每个工作日 16:30 运行评分
- **outcome-update**：每个工作日 17:00 更新到期预测结果

### 4. 查看准确率报告（可选）

等待 30 天后有结案记录：

```bash
python3 pipeline.py accuracy-report
```

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
| `weights.json` | 模型权重配置 |
| `config.py` | watchlist / 数据库路径 / 日志目录 |
| `cron-setup.sh` | 自动配置 crontab |
| `lib/fetcher.py` | 数据获取（从 a-stock-research skill 复制） |
| `lib/cache.py` | SQLite 缓存管理（从 a-stock-research skill 复制） |
| `tracker.db` | SQLite 数据库（自动生成） |

## 测试

```bash
# 全部测试（29 个用例）
pytest tests/ -v

# 单模块测试
pytest tests/test_scorer.py -v   # 11 个用例：scorer 插值、边界、异常
pytest tests/test_pipeline.py -v # 18 个用例：daily / outcome-update / accuracy-report
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
