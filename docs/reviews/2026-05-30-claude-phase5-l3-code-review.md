已完整读取 diff 及全部上下文文件，开始审查。

---

## 代码审查结论

### 1. 结论：**APPROVE**

所有 Spec AC1–AC6 均已覆盖；无 blocking 问题；3 条 Nice-to-have 建议。

---

### 2. 检查了什么

| 文件 | 检查重点 |
|---|---|
| `lib/entry_signal.py` | 纯函数逻辑、三项 AND 语义、严格 `>` vs `<=`、NULL/v1 返回路径 |
| `lib/cache.py` | DDL 新字段、`_ensure_columns` 迁移、事务提交顺序 |
| `pipeline.py` | 归一化 seam、异常 fallback、INSERT 列/值对齐、框架循环外计算 L3 |
| `telegram_push.py` | SQL WHERE 过滤 NULL 语义、label 格式 |
| `accuracy-report` SQL | 分母定义（framework='A', entry_signal=1, v1, outcome IS NOT NULL）、alpha_30d > 0 命中、样本门槛 |
| `tests/test_l3_entry_signal.py` | 6 条纯函数用例的边界验证 |
| `tests/test_pipeline.py` | schema 迁移、daily 写入、历史不回写、legacy 保持 NULL/NULL |
| `tests/test_telegram_push.py` | 推送条件、NULL/0 过滤、失败不抛 |
| Spec 符合性 | R1–R13、AC1–AC6 逐项比对 |

---

### 3. Blocking（无）

---

### 4. Important

**I-1 `_compute_stock_entry_signal` 异常路径缺测试覆盖**
- 路径：`pipeline.py::_compute_stock_entry_signal` 的 `except Exception` 分支
- 影响：规则本身正确（返回 `EntrySignalResult(None, v1, "L3_ERROR")`，不抛出，基础评分继续写入），但没有测试证明 AKShare 抛异常时 daily 仍写入评分。Spec 的 Stop 条件 6 要求有 fallback + 日志策略，代码有，测试无。
- 建议：增加一条 `test_daily_writes_score_even_when_l3_fetch_raises`，patch `stock_zh_a_hist` 为 `side_effect=RuntimeError`，断言 predictions 中仍有记录且 `entry_signal IS NULL`，`entry_signal_version='v1'`。

---

### 5. Nice-to-have

**N-1 `_normalize_entry_signal_bars` 中的死分支**
- 路径：`pipeline.py::_normalize_entry_signal_bars`，`else daily_bars` 分支
- `required_columns.issubset(columns)` 通过后 "date" 必然在 columns 里，`if "date" in columns else daily_bars` 的 else 永远不可达。不影响正确性，可删除 `else daily_bars`。

**N-2 `_normalize_entry_signal_bars` 返回类型标注过宽**
- 路径：`pipeline.py::_normalize_entry_signal_bars` 返回 `object`
- 建议改为 `pd.DataFrame | None`，与实际返回一致，也利于 IDE 静态分析。

**N-3 `_ensure_columns` f-string 含列名/定义**
- 路径：`lib/cache.py::_ensure_columns`
- `f"ALTER TABLE {table} ADD COLUMN {name} {definition}"` 三个参数均来自内部调用方（不是用户输入），无实际注入风险，但函数签名未做约束。若未来有使用方不慎传入外部输入，会有风险。低优先级，可在函数内加内部断言或改用白名单校验。

---

### 6. 关键逻辑验证记录

- `compute_entry_signal`：`<=` 判断对应 spec `close > MA60` 语义，正确
- 120-bar 精确边界（`< 120` 才 INSUFFICIENT）：测试用例验证正确
- `entry_signal_result` 在 framework 循环外计算一次、写入所有 framework：符合 L3 是股票级属性的设计意图
- `INSERT OR IGNORE` 语义：重复运行不回写历史，符合 spec R3
- Telegram SQL `entry_signal = 1`：NULL 值被 SQL 自动排除，符合 spec R7
- L3 命中率分母 SQL：精确匹配 spec R12 四项条件
- legacy 迁移测试：旧库补列后原有记录 `entry_signal=None, entry_signal_version=None` 保持不变 ✓
