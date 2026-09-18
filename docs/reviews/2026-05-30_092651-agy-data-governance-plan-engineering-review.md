# AGY 工程视角只读审查结论

基于对当前 A 股选股 Tracker 项目的源码、测试集、工程治理 Spec 以及所提交的 Implementation Plan 的深入只读审查，得出以下评审结论。

---

## 1. Verdict

**`REQUEST_BOUNDED_FIXES`**

> [!NOTE]
> 该执行计划（Plan）在整体设计、Phase 顺序控制以及边界防溢出上表现得非常出色，完全符合 Spec 规定的治理目标。然而，在 **Phase B 结构测试** 和 **Phase C 真实环境 Smoke 测试** 中存在两处必然导致自动执行中断或失败的硬性设计漏洞，必须针对这两处具体问题提供定位修复（Bounded Fixes）后，Hermes 才可以完全无人工干预地自动执行。

---

## 2. Blocking findings

#### 1. Phase B 注册表结构测试必然引发 AssertionError 导致中断
- **Problem**：在计划写入的 `tests/test_data_source_registry.py` 中，`REQUIRED_KEYS` 包含字符串 `"field"`。但在测试辅助函数 `_blocks()` 中，文本切分采用的是 `re.split(r"(?m)^\s*-\s+field:\s*", text)`。由于 `field` 关键字作为分割界定符在切分时被剔除，导致 block 分片文本中将永远无法匹配到 `field:`，最终导致 `assert REQUIRED_KEYS <= keys` 断言由于缺少 `"field"` 键而必然失败。
- **Evidence location**：`docs/plans/2026-05-30-data-governance-structure-boundary-execution-plan.md` -> [B2. 新增 registry 结构测试] (第 222-225 行，第 237-243 行，第 250-253 行)。
- **Why it matters**：该测试在 Phase B 自动验证时会立即抛出 AssertionError，使得自动执行过程在 Phase B 阶段即被卡死，无法推进至后续 Phase。
- **Suggested fix**：
  在 `test_registry_blocks_have_required_keys` 循环体内，显式将 `"field"` 键加入匹配到的 `keys` 集合中；或者由于 field 本身已提取为字典名，可直接从 `REQUIRED_KEYS` 列表中移除 `"field"`。
  ```python
  def test_registry_blocks_have_required_keys() -> None:
      for field, block in _blocks().items():
          keys = {m.group(1) for m in re.finditer(r"^\s*([a-zA-Z0-9_]+):", block, re.M)}
          keys.add("field")  # 补回由于 re.split 过滤掉的 field 键
          assert REQUIRED_KEYS <= keys, field
  ```

#### 2. Phase C 真实环境 Smoke 测试在结案样本超 100 时必然误报失败
- **Problem**：在 Phase C 的可选只读 Smoke 测试中，脚本断言输出必须精确包含 `f"Framework A {count} 条已结案记录"`。然而，在 `pipeline.py` 的 `cmd_accuracy_report()` 中，该警告语是在 `framework_a_closed < 100` 的条件分支下才输出的。一旦真实数据库 `tracker.db` 的 Framework A 已结案数达到 100 条以上，警告语将不被渲染，Smoke 脚本无法找到该 needle 从而导致退出码为 1。
- **Evidence location**：`docs/plans/2026-05-30-data-governance-structure-boundary-execution-plan.md` -> [C3. 可选只读 smoke] (第 409-411 行) 配合 `pipeline.py` (第 522-527 行)。
- **Why it matters**：在真实库测试时，如果数据库处于数据量更充沛的健康状态，Smoke 测试反而会产生假阴性报错，卡住自动部署流。
- **Suggested fix**：
  修改 Smoke 测试脚本中的匹配逻辑，不要依赖样本不足的警告段落，而是通过正则检查 `分 Framework 统计` 的表格输出中 Framework A 的数值。
  ```python
  import re

  # 匹配表头下的分 Framework A 结案统计数值
  match = re.search(r"A\s+\d+\s+(\d+)", out)
  found_count = int(match.group(1)) if match else -1
  print({"expected": count, "found": found_count})
  raise SystemExit(0 if count == found_count else 1)
  ```

---

## 3. Important findings

#### 1. 数据质量评估 `evaluate_data_quality` 缺少对“实时计算”与“缓存旧值”判别的能力
- **Problem**：Spec R2 明确要求数据质量结果中应包含 `pb_percentile_10y` 是“由当日价格实时计算，还是使用缓存旧值”。但在 Plan 提供的 `data_quality.py` 的 `evaluate_data_quality` 实现中，只是简单地将其作为 Required 字段判断是否存在，未对此进行特征判定与状态区分。
- **Evidence location**：Spec `docs/specs/2026-05-29-agent-engineering-governance-spec.md` -> [R2. 数据质量门禁] (第 133-144 行) 配合 Plan -> [D1. 新增 data_quality.py] (第 517-546 行)。
- **Why it matters**：虽然能满足 Phase D “最小模型”的要求，但未能完全闭环 Spec R2 中关于评估数据是否“实时计算”的细粒度治理要求，可能导致评分使用过时缓存时无法被发现。
- **Suggested fix**：
  在 `evaluate_data_quality` 内部，可额外评估计算 PB 历史分位所必需的依赖数据（`price_at_score`, `bps`, `pb_hist_monthly`）是否齐全。若缺失且回落使用缓存旧值，则将该字段的 `status` 标记为 `FieldStatus.STALE`。

#### 2. Phase C 模拟测试数据未隔离外部 Weights 变化引起的过滤变动
- **Problem**：在 `test_accuracy_report_contract_matches_framework_a_sql_anchor` 中，测试模拟插入的数据的 `total_score` 为 50。由于阈值是动态由 `weights.json` 加载的，这可能会受到 weights.json 未提交或已修改变更的轻微影响（若评分分段发生变化）。
- **Evidence location**：`docs/plans/2026-05-30-data-governance-structure-boundary-execution-plan.md` -> [C2. 新增/加固测试] (第 359-391 行)。
- **Why it matters**：虽然在此测试中该字段只影响 Tier 分类，不影响 Closed 记录数，但耦合具体的配置数据会增加测试用例在未来的脆弱性。
- **Suggested fix**：
  在测试用例中可通过 pytest fixture 的 monkeypatch 工具显式隔离或 Mock 权重获取函数的返回，使测试更健壮。

---

## 4. Minor / nice-to-have

#### 1. GNU 特性 `xargs -r` 对部分非 Linux 环境的兼容性限制
- **Problem**：Plan 执行总规则中，安全文本扫描使用了 `xargs -r`。
- **Evidence location**：Plan 第 93 行。
- **Why it matters**：虽然项目明确运行在 Linux 系统上，但对使用 macOS（BSD xargs）的协同开发者，手动运行该检查会遇到非法选项报错。可改进为标准兼容写法。

#### 2. 数据层模块未在独立目录隔离
- **Problem**：`data_quality.py` 和 `agent_reviewer.py` 直接创建于项目根目录下。
- **Evidence location**：Plan 第 453、755 行。
- **Why it matters**：根目录扁平结构会随着模块增加而变得杂乱。后续阶段应统一收拢至 `lib/` 文件夹中。

---

## 5. Engineering design points already correct

本 Plan 的工程设计在多个核心要点上完全正确，值得保持：
1. **顺序与阶段关系完备**：严格按 Phase B -> C -> D -> E -> F 的线性依赖演进，坚决贯彻数据源与质量在先、结构调整与接口设计在后的原则。
2. **纯标准库 YAML 文本解析**：在不添加 PyYAML 新依赖的硬约束下，仅通过标准库的正规表达式成功完成对 `data-source-registry.yaml` 的结构化校验，表现出了极高的代码自制力。
3. **零外部副作用边界控制**：对 Telegram 接口、Google Sheets 数据层、Gemini API 以及任何 DB 修改均在全局非范围和 Stop 条件中锁死，完全杜绝了未授权的外部调用。
4. **Agent Reviewer 窄接口只读约束**：`validate_review_output` 极其严格地定义了受限键，使得 reviewer 彻底保持只读属性，无法操纵确定性评分与持仓交易指令。
5. **单元测试与提交演化闭环**：每一个 Phase 都清晰定义了 `Pre-check`、`RED-GREEN 验证`、`Scoped Commit` 与 `Rollback` 动作，保证工程演化的极小步与安全性。

---

## 6. Minimal patch directions

如需落地执行，仅需对 `docs/plans/2026-05-30-data-governance-structure-boundary-execution-plan.md` 文件补丁化修改以下两处代码块：

#### Patch 1. 修复 B2 测试的 field 键匹配 (Line 250)
```diff
def test_registry_blocks_have_required_keys() -> None:
    for field, block in _blocks().items():
        keys = {m.group(1) for m in re.finditer(r"^\s*([a-zA-Z0-9_]+):", block, re.M)}
+       keys.add("field")
        assert REQUIRED_KEYS <= keys, field
```

#### Patch 2. 修复 C3 Smoke 测试的匹配正则 (Line 409)
```diff
- needle = f"Framework A {count} 条已结案记录"
- print({"expected": count, "needle_found": needle in out})
- raise SystemExit(0 if needle in out else 1)
+ import re
+ match = re.search(r"A\s+\d+\s+(\d+)", out)
+ found_count = int(match.group(1)) if match else -1
+ print({"expected": count, "found": found_count})
+ raise SystemExit(0 if count == found_count else 1)
```

通过对以上局部缺陷进行小幅修补，该 Plan 可完全交付 Hermes 进行无干预、全自动、高确定性的下一阶段工程演进。
