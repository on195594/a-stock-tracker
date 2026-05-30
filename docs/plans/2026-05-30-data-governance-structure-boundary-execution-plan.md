# Data Governance + Structure Boundary 执行计划

创建时间：2026-05-30 09:07
目标项目：`/home/lin/a-stock-tracker`
来源 Spec：`docs/specs/2026-05-29-agent-engineering-governance-spec.md`
执行方式：Hermes 自动执行；每个任务完成后提交一个小 commit；不需要人工干预，除非触发 Stop 条件。

## 0. 改写后的请求

原请求：

> 根据spec帮我写项目执行计划，计划尽可能详细，达到能够让hermes自动完成，无需人工干预。

改写为更可执行的提示：

> 在 `/home/lin/a-stock-tracker` 中，基于 `docs/specs/2026-05-29-agent-engineering-governance-spec.md`，写一份可由 Hermes 自动执行的分阶段 implementation plan。计划必须遵守 Phase 顺序：已完成 Phase A 后，先执行 Phase B/C/D，再执行 Phase E/F；每个任务包含明确文件路径、补丁形状、测试、验证命令、提交边界、回滚方式和 Stop 条件。计划不得授权 DB 历史写入、cron、真实通知、真实 Gemini 调用、credentials 或交易/持仓扩展。

改动说明：把“根据 spec”具体绑定到已存在的 spec 文件，把“自动完成”拆成任务级执行、验证、提交、回滚和 Stop 条件，并显式排除需要人工批准的副作用。

## 1. 当前基线证据

- 仓库：`/home/lin/a-stock-tracker`
- 分支：`master`
- Phase A baseline：已提交，HEAD 曾确认 `9c02b79 chore: 提交 Phase A 基线与治理 spec`
- 当前测试基线：`pytest tests/ -q` → `74 passed, 1 skipped in 16.10s`
- 当前 Spec 结构测试：`tests/test_spec_structure.py` 已存在
- 当前约束：不新增依赖；`requirements.txt` 只有 `akshare`、`pytest`、`ruff`、`mypy`、Google Sheets 依赖；因此 YAML 读取测试不能依赖 PyYAML。

## 2. 总目标

把 Spec 的下一阶段落成可执行变更：

1. 建立 `docs/data-source-registry.yaml`，并用 stdlib-only 测试验证 registry 覆盖 scorer 输入字段与关键报告字段。
2. 固化 `accuracy-report` 与 DB SQL anchor 的一致性，避免 Framework A 被全局样本污染。
3. 建立最小 `data_quality` 结果模型，能表达 required/degradable/derived、missing/fallback/ok 状态。
4. 小步迁移 `_compute_daily_pb_percentile` 到 `scorer.py`，用 golden-master 证明行为一致。
5. 定义 schema-only/fake `lib/agent_reviewer.py`，证明 reviewer 不能覆盖 deterministic score/decision。

## 3. 全局边界

### 3.1 范围

允许修改：

- `docs/data-source-registry.yaml`
- `docs/plans/2026-05-30-data-governance-structure-boundary-execution-plan.md`
- `tests/test_data_source_registry.py`
- `tests/test_accuracy_report_contract.py` 或追加到 `tests/test_pipeline.py`
- `lib/data_quality.py`
- `tests/test_data_quality.py`
- `scorer.py`
- `pipeline.py` 中 `_compute_daily_pb_percentile` 的兼容导入/调用边界
- `tests/test_scorer.py`
- `tests/test_pipeline.py`
- `lib/agent_reviewer.py`
- `tests/test_agent_reviewer.py`
- 必要时小幅更新 `README.md` 或 `docs/impl-plan.md`，仅记录新模块入口，不改产品定位

### 3.2 非范围

禁止修改或执行：

- 不改 `tracker.db` 历史数据，不直接 UPDATE/DELETE `predictions`、`outcome_*d`、`weights_hash`
- 不新增 cron，不改 `cron-setup.sh`
- 不调用真实 Telegram/Google Sheets/Gemini，不新增 API key，不读取或提交 `.env`
- 不新增依赖，不更新 `requirements.txt`
- 不扩展到自动交易、持仓账户管理、多市场投研、Google Sheets 数据层、泛财经内容生成
- 不大规模重写 `pipeline.py` 或 `lib/cache.py`

### 3.3 Stop 条件

任一条件出现即停止执行、记录证据并请求用户确认：

- 需要写真实 DB 历史记录或改 schema 才能继续
- 需要真实外部 API/Telegram/Google Sheets/Gemini 调用才可验证
- 需要新增依赖才可通过测试
- 发现 spec 与现有代码冲突，且无法用小步兼容方式解决
- 测试无法恢复到全量通过
- 计划需要扩大到交易、持仓、账户、自动调权、cron 或凭据

## 4. 执行总规则

每个 Phase 按以下固定循环执行：

1. `git status --short`，确认只有预期改动；若有用户未提交改动，停止。
2. 写 RED 测试或结构断言。
3. 最小实现。
4. 运行定向测试。
5. 运行 `pytest tests/ -q`。
6. 运行 `git diff --check`。
7. 运行安全文本扫描：
   ```bash
   files=$(git diff --cached --name-only)
   if [ -n "$files" ]; then
     printf '%s\n' "$files" | xargs grep -nEi 'api[_-]?key|secret|token|password|BEGIN (RSA|OPENSSH)|shell=True|eval\(|pickle\.loads|DROP TABLE|DELETE FROM|UPDATE predictions' || true
   fi
   ```
   注意：不用 GNU-only 空输入扩展参数，保持脚本在 Linux/macOS 上都可读可跑；若 `token/password` 只出现在文档的“禁止项”中，可人工标记为 false positive；若出现在代码或配置中，停止。
8. `git add` 仅 stage 本 Phase 文件。
9. `git diff --cached --check`。
10. `git commit -m "<类型>: <中文简述>"`。

提交建议：

- Phase B：`feat: 新增数据源 registry 与覆盖测试`
- Phase C：`test: 固化 accuracy-report 与 DB 一致性`
- Phase D：`feat: 新增数据质量结果模型`
- Phase E：`refactor: 迁移 PB 分位计算到 scorer`
- Phase F：`feat: 定义只读 agent reviewer schema`

## 5. Phase Ledger

- Phase A — 数据源治理 Spec 落地：`done`
- Phase B — 数据源 registry 与数据质量矩阵：`done`
- Phase C — 报告/DB 一致性测试加固：`done`
- Phase D — 数据质量结果模型：`done`
- Phase E — 代码 seam 小步拆分：`done`
- Phase F — Agent reviewer schema-only：`done`

进入任意 Phase 前，先确认前序 Phase 状态为 `done`。

---

## Phase B — 数据源 registry 与覆盖测试

类型：landing

### B0. 预检

命令：

```bash
cd /home/lin/a-stock-tracker
git status --short
pytest tests/test_spec_structure.py -q
pytest tests/ -q
```

期望：

- `git status --short` 无 tracked dirty 文件；ignored `.env/.venv/cache/tracker.db/logs` 不影响。
- `pytest tests/test_spec_structure.py -q` 通过。
- `pytest tests/ -q` 通过。

失败处理：

- 若 tracked dirty 不是本计划产生，停止。
- 若测试失败，先定位失败是否由环境缺包导致；不能直接跳过测试。

### B1. 新增 registry 文件

新增文件：`docs/data-source-registry.yaml`

格式要求：

- 使用人类可读 YAML 风格，但测试采用 stdlib 文本/正则解析，避免 PyYAML 依赖。
- 所有字段项使用固定块：
  ```yaml
  - field: roe_3y_avg
    owner: scorer_input
    requirement: required
    source_primary: akshare.stock_financial_abstract_ths
    source_fallback: none
    cache: stock_fundamentals.data.roe_3y_avg
    refresh: weekly
    affects_scoring: true
    affects_outcome: false
    failure_behavior: mark_missing; data_quality_degrades
  ```
- 每个条目必须包含这些 key：`field`、`owner`、`requirement`、`source_primary`、`source_fallback`、`cache`、`refresh`、`affects_scoring`、`affects_outcome`、`failure_behavior`。

必须覆盖字段：

scorer 输入字段：

- `roe_3y_avg`
- `net_profit_growth`
- `debt_ratio`
- `gross_margin`
- `pb_percentile_10y`
- `moat_fixed`
- `market_pos_fixed`
- `sentiment_fixed`

派生/支撑字段：

- `bps`
- `pb_hist_monthly`
- `report_period`
- `price_at_score`
- `benchmark_30d`
- `benchmark_60d`
- `benchmark_90d`
- `weights_hash`
- `framework`
- `outcome_30d`
- `outcome_60d`
- `outcome_90d`
- `gemini_qualitative_score`

建议内容要点：

- `roe_3y_avg` / `net_profit_growth` / `debt_ratio`：primary 为 AKShare 财务摘要，cache 为 `stock_fundamentals.data.*`，refresh 为 `weekly`，影响 scoring。
- `gross_margin`：primary 为新浪利润表近 3 年年报均值，fallback 为 `none`，失败时 missing。
- `pb_percentile_10y`：derived，由 `price_at_score + bps + pb_hist_monthly` 计算，失败时 missing。
- `price_at_score`：primary 为腾讯历史日线或 daily 收盘价路径，fallback 为回填路径；影响 outcome 和 daily PB 计算。
- benchmark：primary 为沪深300指数缓存/腾讯指数日线，影响 outcome/report。
- Gemini：本阶段只记录 fallback 常量/缓存行为，不允许真实 LLM 成为必需验证路径。

### B2. 新增 registry 结构测试

新增文件：`tests/test_data_source_registry.py`

测试形状：

```python
from __future__ import annotations

import re
from pathlib import Path

REGISTRY_PATH = Path(__file__).resolve().parents[1] / "docs" / "data-source-registry.yaml"
WEIGHTS_PATH = Path(__file__).resolve().parents[1] / "weights.json"

REQUIRED_KEYS = {
    "field", "owner", "requirement", "source_primary", "source_fallback",
    "cache", "refresh", "affects_scoring", "affects_outcome", "failure_behavior",
}


def _registry_text() -> str:
    return REGISTRY_PATH.read_text(encoding="utf-8")


def _fields() -> set[str]:
    return set(re.findall(r"^\s*-\s+field:\s*([a-zA-Z0-9_]+)\s*$", _registry_text(), re.M))


def _blocks() -> dict[str, str]:
    text = _registry_text()
    parts = re.split(r"(?m)^\s*-\s+field:\s*", text)
    blocks = {}
    for part in parts[1:]:
        first, _, rest = part.partition("\n")
        blocks[first.strip()] = first + "\n" + rest
    return blocks


def test_registry_file_exists() -> None:
    assert REGISTRY_PATH.exists()


def test_registry_blocks_have_required_keys() -> None:
    for field, block in _blocks().items():
        keys = {m.group(1) for m in re.finditer(r"^\s*([a-zA-Z0-9_]+):", block, re.M)}
        keys.add("field")  # _blocks() uses field as split delimiter; add it back for REQUIRED_KEYS.
        assert REQUIRED_KEYS <= keys, field


def test_registry_covers_framework_a_scored_fields() -> None:
    import json
    weights = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))
    framework_a = weights["frameworks"]["A"]
    expected = set(framework_a["fundamental"]) | set(framework_a["valuation"])
    assert expected <= _fields()


def test_registry_covers_report_contract_fields() -> None:
    expected = {
        "framework", "weights_hash", "report_period", "price_at_score",
        "outcome_30d", "outcome_60d", "outcome_90d",
        "benchmark_30d", "benchmark_60d", "benchmark_90d",
    }
    assert expected <= _fields()


def test_registry_includes_fallback_and_failure_language() -> None:
    text = _registry_text()
    for needle in ["source_fallback:", "failure_behavior:", "fallback", "missing"]:
        assert needle in text
```

如果实际实现发现 `weights.json` 的 Framework B 仍有字段，不要求 Phase B 覆盖 B；Spec 下一阶段以 Framework A 为主。不要扩大范围。

### B3. 运行验证

命令：

```bash
pytest tests/test_data_source_registry.py -q
pytest tests/test_spec_structure.py -q
pytest tests/ -q
git diff --check
```

期望：全部通过。

### B4. Commit

命令：

```bash
git add docs/data-source-registry.yaml tests/test_data_source_registry.py
git diff --cached --check
git commit -m "feat: 新增数据源 registry 与覆盖测试"
```

Rollback：

```bash
git reset --hard HEAD~1
```

Exit Gate：`tests/test_data_source_registry.py` 能证明 registry 覆盖 Framework A scorer 字段和关键报告字段。

---

## Phase C — 报告/DB 一致性测试加固

类型：landing

### C0. 预检

命令：

```bash
git status --short
pytest tests/test_data_source_registry.py -q
pytest tests/ -q
```

期望：Phase B 已提交，工作区干净。

### C1. 新增 report contract helper

优先追加到 `tests/test_pipeline.py`，避免引入过多测试文件重复 fixture。

新增 helper：

```python
def _framework_a_closed_30d_count() -> int:
    db = cache_mod.get_db()
    count = db.execute(
        """SELECT COUNT(*) FROM predictions
           WHERE outcome_30d IS NOT NULL AND framework = 'A'"""
    ).fetchone()[0]
    db.close()
    return int(count)


def assert_report_matches_db(output: str) -> None:
    expected = _framework_a_closed_30d_count()
    assert f"Framework A {expected} 条已结案记录" in output
```

注意：helper 名称必须精确包含 `assert_report_matches_db`，满足 Spec R3 anchor。

### C2. 新增/加固测试

在 `tests/test_pipeline.py` 增加测试：

```python
def test_accuracy_report_contract_matches_framework_a_sql_anchor(tmp_db, capsys, fake_weights, monkeypatch):
    monkeypatch.setattr(pipeline, "_load_weights", lambda: fake_weights)
    score_date = (date.today() - timedelta(days=30)).isoformat()
    db = cache_mod.get_db()
    # A 框 2 条已结案
    for idx in range(2):
        db.execute(
            """INSERT INTO predictions
               (code, name, framework, score_date, price_at_score,
                quant_score, total_score, weights_hash, report_period,
                outcome_30d, benchmark_30d, created_at)
               VALUES (?, ?, 'A', ?, 10, 40, 50, 'hashA', '2024-09-30', 0.10, 0.02, ?)""",
            (f"60003{idx}", f"A{idx}", score_date, score_date + "T15:00:00"),
        )
    # B 框 3 条已结案：不得污染 A 框样本数
    for idx in range(3):
        db.execute(
            """INSERT INTO predictions
               (code, name, framework, score_date, price_at_score,
                quant_score, total_score, weights_hash, report_period,
                outcome_30d, benchmark_30d, created_at)
               VALUES (?, ?, 'B', ?, 10, 40, 50, 'hashB', '2024-09-30', 0.10, 0.02, ?)""",
            (f"00000{idx}", f"B{idx}", score_date, score_date + "T15:00:00"),
        )
    db.commit()
    db.close()

    pipeline.cmd_accuracy_report()
    out = capsys.readouterr().out

    assert _framework_a_closed_30d_count() == 2
    assert_report_matches_db(out)
    assert "Framework A 2 条已结案记录" in out
```

若当前 `cmd_accuracy_report()` 文案不直接包含该字符串，但已包含等价样本不足提示，则优先小改测试 helper 解析实际输出；不要为了文案大改 renderer。

AGY 审查修正：该测试必须通过 `fake_weights` 和 `monkeypatch.setattr(pipeline, "_load_weights", lambda: fake_weights)` 固定阈值来源，避免未来 `weights.json` 调整让测试和分层输出产生非目标耦合。

### C3. 可选只读 smoke

仅当 `tracker.db` 存在且命令不写 DB 时执行：

```bash
python3 pipeline.py accuracy-report > /tmp/a_stock_accuracy_report.out
python3 - <<'PY'
import sqlite3
import re
from pathlib import Path
import config
out = Path('/tmp/a_stock_accuracy_report.out').read_text(encoding='utf-8')
conn = sqlite3.connect(config.DB_PATH)
count = conn.execute("SELECT COUNT(*) FROM predictions WHERE outcome_30d IS NOT NULL AND framework = 'A'").fetchone()[0]
conn.close()
# 不依赖“样本不足”文案；解析分 Framework 统计中 A 框的 30d 结案数，避免 A>=100 时误失败。
match = re.search(r"^A\s+\d+\s+(\d+)\b", out, re.M)
found_count = int(match.group(1)) if match else -1
print({"expected": count, "found": found_count})
raise SystemExit(0 if count == found_count else 1)
PY
```

如果 smoke 因真实环境缺 AKShare 或 DB 文件不存在失败，但单元测试通过，则记录为环境限制，不阻塞 Phase C。不得写真实 DB 来制造数据。若 smoke 输出格式变化导致正则找不到 `分 Framework 统计` 的 A 行，先补报告解析 helper 或测试 anchor，不要退回到样本不足文案匹配。

### C4. 验证与提交

命令：

```bash
pytest tests/test_pipeline.py -q
pytest tests/ -q
git diff --check
git add tests/test_pipeline.py
git diff --cached --check
git commit -m "test: 固化 accuracy-report 与 DB 一致性"
```

Rollback：

```bash
git reset --hard HEAD~1
```

Exit Gate：测试证明报告输出中的 Framework A 结案数等于 R3 SQL anchor。

---

## Phase D — 数据质量结果模型

类型：landing

### D0. 预检

```bash
git status --short
pytest tests/test_data_source_registry.py tests/test_pipeline.py -q
```

### D1. 新增 `lib/data_quality.py`

新增文件：`lib/data_quality.py`

目标：只定义模型和纯函数，不接入 daily 主流程，不写 DB。

AGY 审查修正：Phase D 必须显式覆盖 Spec R2 的 PB 分位口径状态。`evaluate_data_quality()` 需要区分 `pb_percentile_10y` 是由 `price_at_score + bps + pb_hist_monthly` 支撑的实时/当日计算，还是依赖缓存旧值。最小实现不要求接入 pipeline，但模型中必须能表达：

- `pb_percentile_10y` 存在且 `price_at_score`、`bps`、`pb_hist_monthly` 齐全：状态为 `ok`，reason 可写 `daily_computable`。
- `pb_percentile_10y` 存在但任一依赖缺失：状态为 `stale`，reason 写明缺失依赖，例如 `cached_without_daily_inputs:price_at_score`。
- `pb_percentile_10y` 缺失：状态为 `missing`，并进入 `missing_required`。
- fallback 仍优先可见：如果 `fallback_sources` 指定某字段，状态为 `fallback`。

代码形状：

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class FieldRequirement(StrEnum):
    REQUIRED = "required"
    DEGRADABLE = "degradable"
    DERIVED = "derived"
    FIXED = "fixed"


class FieldStatus(StrEnum):
    OK = "ok"
    MISSING = "missing"
    FALLBACK = "fallback"
    STALE = "stale"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class DataQualityField:
    name: str
    requirement: FieldRequirement
    status: FieldStatus
    source: str
    reason: str = ""


@dataclass(frozen=True)
class DataQualityResult:
    code: str
    fields: tuple[DataQualityField, ...]
    missing_required: tuple[str, ...] = field(default_factory=tuple)
    fallback_fields: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_acceptable(self) -> bool:
        return not self.missing_required

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "is_acceptable": self.is_acceptable,
            "missing_required": list(self.missing_required),
            "fallback_fields": list(self.fallback_fields),
            "fields": [field.__dict__ for field in self.fields],
        }


_REQUIRED_FIELDS = ("roe_3y_avg", "net_profit_growth", "debt_ratio", "gross_margin")
_SUPPORT_FIELDS = ("report_period", "price_at_score")
_DERIVED_SUPPORT = ("bps", "pb_hist_monthly")
_PB_DAILY_DEPENDENCIES = ("price_at_score", "bps", "pb_hist_monthly")


def evaluate_data_quality(code: str, data: dict[str, Any], fallback_sources: dict[str, str] | None = None) -> DataQualityResult:
    fallback_sources = fallback_sources or {}
    fields: list[DataQualityField] = []

    def add(name: str, requirement: FieldRequirement) -> None:
        if name in fallback_sources:
            status = FieldStatus.FALLBACK
            reason = fallback_sources[name]
        elif data.get(name) is None:
            status = FieldStatus.MISSING
            reason = "missing"
        else:
            status = FieldStatus.OK
            reason = ""
        fields.append(DataQualityField(name, requirement, status, source=name, reason=reason))

    def add_pb_percentile() -> None:
        name = "pb_percentile_10y"
        if name in fallback_sources:
            fields.append(DataQualityField(name, FieldRequirement.REQUIRED, FieldStatus.FALLBACK, name, fallback_sources[name]))
            return
        if data.get(name) is None:
            fields.append(DataQualityField(name, FieldRequirement.REQUIRED, FieldStatus.MISSING, name, "missing"))
            return
        missing_inputs = [dep for dep in _PB_DAILY_DEPENDENCIES if not data.get(dep)]
        if missing_inputs:
            fields.append(DataQualityField(
                name,
                FieldRequirement.REQUIRED,
                FieldStatus.STALE,
                name,
                "cached_without_daily_inputs:" + ",".join(missing_inputs),
            ))
            return
        fields.append(DataQualityField(name, FieldRequirement.REQUIRED, FieldStatus.OK, name, "daily_computable"))

    for name in _REQUIRED_FIELDS:
        add(name, FieldRequirement.REQUIRED)
    add_pb_percentile()
    for name in _SUPPORT_FIELDS:
        add(name, FieldRequirement.DEGRADABLE)
    for name in _DERIVED_SUPPORT:
        add(name, FieldRequirement.DERIVED)

    missing_required = tuple(
        item.name for item in fields
        if item.requirement == FieldRequirement.REQUIRED and item.status == FieldStatus.MISSING
    )
    fallback_fields = tuple(item.name for item in fields if item.status == FieldStatus.FALLBACK)
    return DataQualityResult(code=code, fields=tuple(fields), missing_required=missing_required, fallback_fields=fallback_fields)
```

实现时可按测试微调，但保持：类型注解、dataclass、纯函数、无外部副作用。

### D2. 新增测试

新增文件：`tests/test_data_quality.py`

必须覆盖：

1. 完整字段 → `is_acceptable is True`
2. 缺失 `price_at_score` → 可降级字段 missing，但不进入 `missing_required`
3. 缺失 `pb_percentile_10y` → required missing，`is_acceptable is False`
4. fallback 使用状态可见：`fallback_sources={"price_at_score": "tencent_hist"}` → `fallback_fields` 包含 `price_at_score`
5. `pb_percentile_10y` 存在但缺少 `price_at_score` / `bps` / `pb_hist_monthly` 之一 → 状态为 `stale`，reason 包含 `cached_without_daily_inputs`
6. `as_dict()` 输出能被 JSON 序列化

测试形状：

```python
import json

from lib.data_quality import evaluate_data_quality


def _complete_data() -> dict:
    return {
        "roe_3y_avg": 16.5,
        "net_profit_growth": 5.5,
        "debt_ratio": 45.0,
        "gross_margin": 56.8,
        "pb_percentile_10y": 18.0,
        "report_period": "2024-09-30",
        "price_at_score": 35.2,
        "bps": 12.0,
        "pb_hist_monthly": [1.0] * 24,
    }


def test_complete_data_quality_is_acceptable() -> None:
    result = evaluate_data_quality("600036", _complete_data())
    assert result.is_acceptable is True
    assert result.missing_required == ()


def test_missing_price_is_visible_but_degradable() -> None:
    data = _complete_data()
    data["price_at_score"] = None
    result = evaluate_data_quality("600036", data)
    assert result.is_acceptable is True
    assert "price_at_score" not in result.missing_required
    assert any(field.name == "price_at_score" and field.status == "missing" for field in result.fields)


def test_missing_pb_percentile_blocks_acceptability() -> None:
    data = _complete_data()
    data["pb_percentile_10y"] = None
    result = evaluate_data_quality("600036", data)
    assert result.is_acceptable is False
    assert result.missing_required == ("pb_percentile_10y",)


def test_fallback_source_is_visible() -> None:
    result = evaluate_data_quality("600036", _complete_data(), {"price_at_score": "tencent_hist"})
    assert result.fallback_fields == ("price_at_score",)
    assert any(field.reason == "tencent_hist" for field in result.fields)


def test_pb_percentile_marks_cached_value_stale_without_daily_inputs() -> None:
    data = _complete_data()
    data["price_at_score"] = None
    result = evaluate_data_quality("600036", data)
    pb_field = next(field for field in result.fields if field.name == "pb_percentile_10y")
    assert pb_field.status == "stale"
    assert "cached_without_daily_inputs:price_at_score" in pb_field.reason


def test_result_is_json_serializable() -> None:
    json.dumps(evaluate_data_quality("600036", _complete_data()).as_dict(), ensure_ascii=False)
```

### D3. 验证与提交

```bash
pytest tests/test_data_quality.py -q
pytest tests/ -q
git diff --check
git add lib/data_quality.py tests/test_data_quality.py
git diff --cached --check
git commit -m "feat: 新增数据质量结果模型"
```

Rollback：

```bash
git reset --hard HEAD~1
```

Exit Gate：数据质量模型能区分 required/degradable/derived 与 missing/fallback/ok，且未接入真实 DB 或外部 API。

---

## Phase E — `_compute_daily_pb_percentile` seam 迁移到 `scorer.py`

类型：landing

### E0. 预检

```bash
git status --short
pytest tests/test_scorer.py tests/test_pipeline.py -q
```

### E1. 先写 scorer golden-master 测试

修改：`tests/test_scorer.py`

新增测试：

```python
def test_compute_daily_pb_percentile_matches_pipeline_contract() -> None:
    from scorer import compute_daily_pb_percentile

    hist = [0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 2.4, 2.6, 2.8, 3.0]
    data = {"bps": 10.0, "pb_hist_monthly": hist}

    assert compute_daily_pb_percentile(20.0, data) == 50.0
    assert compute_daily_pb_percentile(0, data) is None
    assert compute_daily_pb_percentile(20.0, {"bps": 0, "pb_hist_monthly": hist}) is None
    assert compute_daily_pb_percentile(20.0, {"bps": 10.0, "pb_hist_monthly": hist[:3]}) is None
```

如果现有 `tests/test_scorer.py` 已有相似 fixtures，复用现有风格。

### E2. 在 `scorer.py` 新增函数

新增函数名：`compute_daily_pb_percentile`

代码直接迁移自 `pipeline.py`，保持行为：

```python
def compute_daily_pb_percentile(price: float, data: dict[str, Any]) -> float | None:
    """用当日价格 + 缓存 BPS/PB 历史序列计算实时 PB 历史分位。纯函数，不写 DB。"""
    bps = data.get("bps")
    hist = data.get("pb_hist_monthly")
    if not bps or bps <= 0 or not hist or len(hist) < 12:
        return None
    current_pb = price / bps
    if current_pb <= 0:
        return None
    pct = sum(1 for x in hist if float(x) < current_pb) / len(hist) * 100
    return round(pct, 1)
```

### E3. 保持 pipeline 兼容边界

修改：`pipeline.py`

- import 改为：
  ```python
  from scorer import (
      SUPPORTED_FRAMEWORKS,
      InsufficientDataError,
      UnsupportedFrameworkError,
      compute_daily_pb_percentile,
      score_stock,
  )
  ```
- 把原 `_compute_daily_pb_percentile` 替换为薄 wrapper，避免旧测试或内部调用断裂：
  ```python
  def _compute_daily_pb_percentile(price: float, data: dict) -> float | None:
      return compute_daily_pb_percentile(price, data)
  ```
- 不改 daily 流程其他逻辑。

### E4. 加 pipeline 兼容测试

在 `tests/test_pipeline.py` 增加或保留断言：

```python
def test_pipeline_pb_percentile_wrapper_uses_scorer() -> None:
    hist = [1.0] * 12
    assert pipeline._compute_daily_pb_percentile(20.0, {"bps": 10.0, "pb_hist_monthly": hist}) == 100.0
```

### E5. 验证与提交

```bash
pytest tests/test_scorer.py -q
pytest tests/test_pipeline.py -q
pytest tests/ -q
git diff --check
git diff -- pipeline.py scorer.py tests/test_scorer.py tests/test_pipeline.py
git add scorer.py pipeline.py tests/test_scorer.py tests/test_pipeline.py
git diff --cached --check
git commit -m "refactor: 迁移 PB 分位计算到 scorer"
```

Rollback：

```bash
git reset --hard HEAD~1
```

Exit Gate：`scorer.py` 拥有 PB 分位纯函数，`pipeline.py` 只有兼容 wrapper；全量测试通过；diff 不含 DB schema、cron、通知变更。

---

## Phase F — Agent reviewer schema-only

类型：landing

### F0. 预检

```bash
git status --short
pytest tests/ -q
```

### F1. 新增 `lib/agent_reviewer.py`

目标：只读 schema + fake reviewer，不调用真实 LLM，不读 env，不写 DB。

代码形状：

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


FORBIDDEN_OUTPUT_KEYS = {
    "score", "total_score", "quant_score", "threshold", "thresholds", "weights",
    "weights_hash", "db_write", "trade_action", "position", "data_fetch_instruction",
}


@dataclass(frozen=True)
class ReviewInput:
    code: str
    name: str
    score_result: dict[str, Any]
    data_quality_result: dict[str, Any]
    policy_version: str
    weights_hash: str
    missing_fields: tuple[str, ...] = field(default_factory=tuple)
    report_period: str | None = None
    risk_flags: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ReviewOutput:
    explanation: str
    objections: tuple[str, ...] = field(default_factory=tuple)
    missing_data_comment: str = ""
    human_questions: tuple[str, ...] = field(default_factory=tuple)
    confidence_note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "explanation": self.explanation,
            "objections": list(self.objections),
            "missing_data_comment": self.missing_data_comment,
            "human_questions": list(self.human_questions),
            "confidence_note": self.confidence_note,
        }


def validate_review_output(output: dict[str, Any]) -> None:
    forbidden = FORBIDDEN_OUTPUT_KEYS & set(output)
    if forbidden:
        raise ValueError(f"reviewer output contains forbidden keys: {sorted(forbidden)}")


def fake_review(input_data: ReviewInput) -> ReviewOutput:
    missing = tuple(input_data.missing_fields)
    missing_comment = "无缺失字段" if not missing else "缺失字段：" + ", ".join(missing)
    objections = tuple(f"缺失 {field}" for field in missing)
    return ReviewOutput(
        explanation=f"{input_data.code} {input_data.name} 已完成确定性评分；reviewer 仅提供只读说明。",
        objections=objections,
        missing_data_comment=missing_comment,
        human_questions=(),
        confidence_note="fake reviewer；未调用真实 LLM；不得覆盖 deterministic score/decision。",
    )
```

### F2. 新增测试

新增文件：`tests/test_agent_reviewer.py`

测试：

```python
import pytest

from lib.agent_reviewer import ReviewInput, fake_review, validate_review_output


def _input() -> ReviewInput:
    return ReviewInput(
        code="600036",
        name="招商银行",
        score_result={"total_score": 50.0, "decision": "buy_moderate"},
        data_quality_result={"is_acceptable": True},
        policy_version="phase-f-schema-only",
        weights_hash="abc12345",
        missing_fields=("gross_margin",),
        report_period="2024-09-30",
        risk_flags=("sample_size_low",),
    )


def test_fake_review_is_read_only_commentary() -> None:
    output = fake_review(_input()).as_dict()
    assert "explanation" in output
    assert "gross_margin" in output["missing_data_comment"]
    assert "total_score" not in output
    assert "thresholds" not in output
    assert "trade_action" not in output


def test_validate_review_output_rejects_score_override() -> None:
    with pytest.raises(ValueError):
        validate_review_output({"explanation": "x", "total_score": 99})


def test_validate_review_output_rejects_trade_or_db_instruction() -> None:
    with pytest.raises(ValueError):
        validate_review_output({"explanation": "x", "db_write": "UPDATE predictions"})
    with pytest.raises(ValueError):
        validate_review_output({"explanation": "x", "trade_action": "buy"})
```

### F3. 可选文档更新

若需要文档入口，只允许在 `README.md` 增加 3-5 行：

```markdown
### Agent reviewer boundary

`lib/agent_reviewer.py` 当前仅提供 schema-only/fake reviewer。它只能输出解释、异议、缺失字段说明和人工问题；不得改分数、阈值、权重、DB、cron、通知或交易行为。真实 LLM reviewer 需要后续单独审批。
```

如 README 当前结构不适合，跳过文档更新，不阻塞。

### F4. 验证与提交

```bash
pytest tests/test_agent_reviewer.py -q
pytest tests/ -q
git diff --check
git add lib/agent_reviewer.py tests/test_agent_reviewer.py README.md
git diff --cached --check
git commit -m "feat: 定义只读 agent reviewer schema"
```

如果 README 未改，`git add README.md` 会失败；实际执行时改为只 add 已变更文件：

```bash
git add lib/agent_reviewer.py tests/test_agent_reviewer.py
```

Rollback：

```bash
git reset --hard HEAD~1
```

Exit Gate：fake reviewer 只能输出只读审查意见；测试证明 forbidden keys 会被拒绝；没有真实 LLM/API/DB/通知副作用。

---

## 6. 终局验证

全部 Phase 完成后运行：

```bash
cd /home/lin/a-stock-tracker
git status --short
pytest tests/test_spec_structure.py tests/test_data_source_registry.py tests/test_data_quality.py tests/test_agent_reviewer.py -q
pytest tests/ -q
git diff --check
python3 - <<'PY'
from pathlib import Path
required = [
    'docs/data-source-registry.yaml',
    'lib/data_quality.py',
    'lib/agent_reviewer.py',
    'tests/test_data_source_registry.py',
    'tests/test_data_quality.py',
    'tests/test_agent_reviewer.py',
]
missing = [p for p in required if not Path(p).exists()]
print({'missing': missing})
raise SystemExit(1 if missing else 0)
PY
```

期望：

- 所有测试通过。
- `git status --short` 只有 ignored 本地文件；tracked 工作区干净。
- 上述 required 文件全部存在。
- commit log 至少包含 Phase B-F 的 5 个 scoped commits。

查看 commit：

```bash
git log --oneline -6
```

## 7. Hermes 自动执行提示词

执行时给 Hermes/子代理使用以下提示：

```text
你在 /home/lin/a-stock-tracker 执行 docs/plans/2026-05-30-data-governance-structure-boundary-execution-plan.md。
严格按 Phase B → C → D → E → F 顺序执行。
每个 Phase 先检查 git status 和前序测试；只修改该 Phase 允许的文件；先写/补测试，再做最小实现；运行定向测试、全量 pytest、git diff --check；每个 Phase 一个 commit。
禁止改真实 tracker.db、cron、credentials、.env、Telegram、Google Sheets、真实 Gemini 调用、交易/持仓/自动调权逻辑。
若触发 Stop 条件，停止并汇报证据，不要自行扩大范围。
最终汇报每个 Phase 的 commit hash、验证命令和输出摘要。
```

## 8. 独立审查门

Phase B-F 全部完成后，运行一次只读审查，审查范围：

- 是否遵守 Phase 顺序
- 是否 registry 覆盖 scorer/report 字段
- 是否 data_quality 没有隐藏 DB/API 副作用
- 是否 PB 分位迁移保持行为一致
- 是否 reviewer 仍是 schema-only，不能覆盖 deterministic decision
- 是否没有引入交易/持仓/cron/通知/真实 LLM 副作用

审查不通过时，只 patch 审查指出的具体问题；不要借机重构邻近代码。

## 9. 回滚策略

单 Phase 回滚：

```bash
git reset --hard HEAD~1
pytest tests/ -q
```

全部回滚到 Phase A baseline：

```bash
git log --oneline --decorate -10
# 找到 Phase A baseline commit，例如 9c02b79
git reset --hard 9c02b79
pytest tests/ -q
```

不要删除 `.env`、`.venv`、`tracker.db`、logs；它们是 ignored 本地文件，不属于本计划。
