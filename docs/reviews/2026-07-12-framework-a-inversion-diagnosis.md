# Framework A high-score inversion diagnosis

Date: 2026-07-12  
Database: `/home/lin/a-stock-tracker/tracker.db` (opened read-only)  
Cohort: Framework A, `score_date >= '2026-05-15'`, settled 30-day outcomes only

## Executive conclusion

Framework A's post-fix inversion is real in this sample and is not explained by a falling 沪深300 benchmark. Q5 (`total_score >= 50`) averaged **-9.91 percentage points of 30-day alpha** across 206 observations, and remained **-3.81 points below Framework A peers scored on the same date**. The direction persisted in both observed months: -12.02 points in the May cohort and -6.96 points in the June cohort.

The most likely explanation is a combination of:

1. **Cross-sectional scoring/factor miscalibration in this market regime.** Higher total scores selected stocks that underperformed lower-score contemporaneous peers. Neither the quant component nor the qualitative component alone has a monotonic relationship with Q5 alpha, so the available columns cannot identify one offending raw factor.
2. **Severe effective-sample concentration.** The 206 Q5 rows represent only 11 stocks repeatedly scored across dates. Removing the five worst stocks improves Q5 alpha from -9.91 to -5.61 points, but does not remove the inversion.

The evidence does not support a market-wide benchmark decline as the cause, and does not support a narrow one-month anomaly as the sole cause.

## Method and data controls

The diagnostic was run with Python's standard-library `sqlite3` module. The connection used both SQLite URI read-only mode and an additional query-only guard:

```python
uri = "file:/home/lin/a-stock-tracker/tracker.db?mode=ro&immutable=1"
with sqlite3.connect(uri, uri=True) as connection:
    connection.execute("PRAGMA query_only = ON")
```

The script was executed from `/tmp/run_diagnosis.py` because the task's action-safety rule permits only one new project file, this report. No project Python source was modified and no database statement other than `SELECT`/`PRAGMA` was issued.

The supplied context names `lib/scorer.py`; that path does not exist in this checkout. The implementation reviewed was root-level `scorer.py`. It confirms `quant_score` is the sum of non-fixed numeric factor scores, while `total_score` adds fixed/qualitative inputs. `weights.json` gives Framework A's non-fixed factors as ROE, net-profit growth, debt ratio, gross margin, and PB percentile; the added fields are moat, market position, and sentiment. The Phase 4 roadmap explicitly prohibits mixing pre-fix and post-fix results.

The live `qualitative_scores` schema does **not** contain the `quant_score` column stated in the task context. It contains `code`, `scored_date`, `moat`, `market_pos`, and `sentiment`. Quantitative contribution therefore comes from `predictions.quant_score`; qualitative contribution is computed exactly as `total_score - quant_score` and reconciled against the latest qualitative snapshot on or before the prediction date.

All return numbers below are percentage points as stored in the database. Rows require non-null `alpha_30d`; all 665 selected rows also have non-null `benchmark_30d`.

### Cohort validation

```sql
SELECT COUNT(*) AS n,
       MIN(score_date) AS min_date,
       MAX(score_date) AS max_date,
       ROUND(AVG(alpha_30d), 4) AS avg_alpha_pct,
       ROUND(AVG(benchmark_30d), 4) AS avg_benchmark_pct
FROM predictions
WHERE framework = 'A'
  AND score_date >= '2026-05-15'
  AND alpha_30d IS NOT NULL;
```

| n | min_date | max_date | avg alpha | avg benchmark |
| ---: | --- | --- | ---: | ---: |
| 665 | 2026-05-15 | 2026-06-10 | -6.0918% | +0.5034% |

This confirms the established count of 665, but clarifies the range: the strict post-fix cohort begins on 2026-05-15, not 2026-04-21. April is necessarily empty under the required no-mixing rule. The supplied -5.17% overall alpha refers to all settled Framework A records; the strictly post-fix cohort is -6.09%.

## Hypothesis 1 — Time concentration

**Hypothesis.** The score inversion is concentrated in one date window rather than persisting throughout the post-fix outcome window.

### Evidence: alpha by month and score bucket

```sql
WITH months(month) AS (
  VALUES ('2026-04'), ('2026-05'), ('2026-06')
),
buckets(bucket, sort_key) AS (
  VALUES ('Q5 >=50', 5), ('Q4 44-<50', 4), ('Q3 38-<44', 3),
         ('Q2 32-<38', 2), ('Q1 <32', 1)
),
agg AS (
  SELECT substr(score_date, 1, 7) AS month,
         CASE WHEN total_score >= 50 THEN 'Q5 >=50'
              WHEN total_score >= 44 THEN 'Q4 44-<50'
              WHEN total_score >= 38 THEN 'Q3 38-<44'
              WHEN total_score >= 32 THEN 'Q2 32-<38'
              ELSE 'Q1 <32' END AS bucket,
         COUNT(*) AS n,
         ROUND(AVG(alpha_30d), 4) AS avg_alpha_pct
  FROM predictions
  WHERE framework = 'A'
    AND score_date >= '2026-05-15'
    AND alpha_30d IS NOT NULL
  GROUP BY month, bucket
)
SELECT m.month, b.bucket, COALESCE(a.n, 0) AS n, a.avg_alpha_pct
FROM months AS m CROSS JOIN buckets AS b
LEFT JOIN agg AS a ON a.month = m.month AND a.bucket = b.bucket
ORDER BY m.month, b.sort_key DESC;
```

| Month | Q5 n / avg alpha | Q4 n / avg alpha | Q3 n / avg alpha | Q2 n / avg alpha | Q1 n / avg alpha |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2026-04 | 0 / NULL | 0 / NULL | 0 / NULL | 0 / NULL | 0 / NULL |
| 2026-05 | 120 / -12.0192% | 81 / -4.7792% | 63 / -5.9948% | 37 / -1.7432% | 84 / +1.2585% |
| 2026-06 | 86 / -6.9619% | 51 / -13.6695% | 55 / -3.2914% | 31 / -11.1502% | 57 / -1.0980% |

Q5 is negative in both months with settled post-fix observations. May is materially worse, but June still has a large negative Q5 alpha. Other buckets move non-uniformly; the exact bucket ordering varies by month, although Q1 is best in both.

### Evidence: benchmark by month

```sql
WITH months(month) AS (
  VALUES ('2026-04'), ('2026-05'), ('2026-06')
),
agg AS (
  SELECT substr(score_date, 1, 7) AS month,
         COUNT(*) AS n,
         COUNT(benchmark_30d) AS benchmark_n,
         ROUND(AVG(benchmark_30d), 4) AS avg_benchmark_pct,
         ROUND(MIN(benchmark_30d), 4) AS min_benchmark_pct,
         ROUND(MAX(benchmark_30d), 4) AS max_benchmark_pct
  FROM predictions
  WHERE framework = 'A'
    AND score_date >= '2026-05-15'
    AND alpha_30d IS NOT NULL
  GROUP BY month
)
SELECT m.month, COALESCE(a.n, 0) AS n,
       COALESCE(a.benchmark_n, 0) AS benchmark_n,
       a.avg_benchmark_pct, a.min_benchmark_pct, a.max_benchmark_pct
FROM months AS m LEFT JOIN agg AS a USING (month)
ORDER BY m.month;
```

| Month | n | benchmark n | avg benchmark | min | max |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2026-04 | 0 | 0 | NULL | NULL | NULL |
| 2026-05 | 385 | 385 | +0.8061% | -1.6929% | +3.3137% |
| 2026-06 | 280 | 280 | +0.0872% | -2.0808% | +2.3681% |

The benchmark is not uniformly negative; its monthly mean is positive in both observed months. `alpha_30d` is already relative because the schema generates it as `outcome_30d - benchmark_30d`.

**Conclusion: not supported.** Time affects magnitude, especially the May severity, but the Q5 inversion is not confined to one observed month. April cannot be evaluated without violating the post-fix-only rule.

## Hypothesis 2 — Qualitative versus quantitative contribution

**Hypothesis.** Q5 inversion is driven specifically by either high `quant_score` or a high qualitative component (`total_score - quant_score`).

### Evidence: Q5 split into quant-score quartiles

`NTILE(4)` is applied after ordering the 206 Q5 observations by `quant_score`; Q1 is the lowest quant quartile and Q4 the highest.

```sql
WITH q5 AS (
  SELECT quant_score, total_score,
         total_score - quant_score AS qualitative_component,
         alpha_30d,
         NTILE(4) OVER (ORDER BY quant_score) AS quant_quartile
  FROM predictions
  WHERE framework = 'A'
    AND score_date >= '2026-05-15'
    AND alpha_30d IS NOT NULL
    AND total_score >= 50
)
SELECT 'Q' || quant_quartile AS quant_quartile,
       COUNT(*) AS n,
       ROUND(MIN(quant_score), 2) AS min_quant_score,
       ROUND(MAX(quant_score), 2) AS max_quant_score,
       ROUND(AVG(quant_score), 2) AS avg_quant_score,
       ROUND(AVG(qualitative_component), 2) AS avg_qual_component,
       ROUND(AVG(alpha_30d), 4) AS avg_alpha_pct
FROM q5
GROUP BY quant_quartile
ORDER BY quant_quartile;
```

| Quant quartile | n | min quant | max quant | avg quant | avg qualitative | avg alpha |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Q1 | 52 | 33.05 | 37.17 | 35.71 | 16.67 | -12.3952% |
| Q2 | 52 | 37.25 | 40.28 | 38.66 | 16.67 | -8.7166% |
| Q3 | 51 | 40.33 | 45.88 | 42.76 | 14.71 | -9.7118% |
| Q4 | 51 | 45.96 | 55.92 | 51.49 | 14.75 | -8.7826% |

The highest-quant quartile is negative, but the lowest-quant quartile is worse. There is no monotonic deterioration as quant score rises.

### Evidence: requested quant-score threshold

```sql
SELECT CASE WHEN quant_score >= 40 THEN 'quant >=40' ELSE 'quant <40' END AS quant_bucket,
       COUNT(*) AS n,
       ROUND(AVG(quant_score), 2) AS avg_quant_score,
       ROUND(AVG(total_score - quant_score), 2) AS avg_qual_component,
       ROUND(AVG(alpha_30d), 4) AS avg_alpha_pct
FROM predictions
WHERE framework = 'A'
  AND score_date >= '2026-05-15'
  AND alpha_30d IS NOT NULL
  AND total_score >= 50
GROUP BY quant_bucket
ORDER BY quant_bucket;
```

| Quant bucket | n | avg quant | avg qualitative | avg alpha |
| --- | ---: | ---: | ---: | ---: |
| quant <40 | 102 | 37.12 | 16.70 | -10.5775% |
| quant >=40 | 104 | 46.99 | 14.74 | -9.2512% |

The two groups are almost equally sized and both invert. High quant score is not uniquely driving Q5 underperformance.

### Evidence: Q5 split into qualitative-component quartiles

```sql
WITH q5 AS (
  SELECT total_score - quant_score AS qualitative_component,
         quant_score, alpha_30d,
         NTILE(4) OVER (ORDER BY total_score - quant_score) AS qual_quartile
  FROM predictions
  WHERE framework = 'A'
    AND score_date >= '2026-05-15'
    AND alpha_30d IS NOT NULL
    AND total_score >= 50
)
SELECT 'Q' || qual_quartile AS qualitative_quartile,
       COUNT(*) AS n,
       ROUND(MIN(qualitative_component), 2) AS min_qual_component,
       ROUND(MAX(qualitative_component), 2) AS max_qual_component,
       ROUND(AVG(qualitative_component), 2) AS avg_qual_component,
       ROUND(AVG(quant_score), 2) AS avg_quant_score,
       ROUND(AVG(alpha_30d), 4) AS avg_alpha_pct
FROM q5
GROUP BY qual_quartile
ORDER BY qual_quartile;
```

| Qualitative quartile | n | min | max | avg qualitative | avg quant | avg alpha |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Q1 | 52 | 14.00 | 14.00 | 14.00 | 47.68 | -11.9297% |
| Q2 | 52 | 14.00 | 16.00 | 15.08 | 45.33 | -8.3703% |
| Q3 | 51 | 16.00 | 17.00 | 16.80 | 37.77 | -8.6872% |
| Q4 | 51 | 17.00 | 17.00 | 17.00 | 37.46 | -10.6350% |

The qualitative component has only four observed values (14–17) and tied values are split by `NTILE`, so the quartile labels should not be treated as precise thresholds. Both extremes perform worse than the middle; there is no monotonic deterioration as qualitative contribution rises.

### Evidence: qualitative snapshot reconciliation

The table has periodic snapshots rather than daily rows. The join therefore uses the latest `scored_date` on or before each prediction date, avoiding look-ahead.

```sql
SELECT COUNT(*) AS q5_n,
       COUNT(q.code) AS latest_prior_join_n,
       SUM(CASE WHEN q.code IS NOT NULL AND
                     ABS((p.total_score - p.quant_score) -
                         (q.moat + q.market_pos + q.sentiment)) < 0.011
                THEN 1 ELSE 0 END) AS reconciled_n,
       ROUND(AVG(q.moat), 2) AS avg_moat,
       ROUND(AVG(q.market_pos), 2) AS avg_market_pos,
       ROUND(AVG(q.sentiment), 2) AS avg_sentiment
FROM predictions AS p
LEFT JOIN qualitative_scores AS q
  ON q.code = p.code
 AND q.scored_date = (
       SELECT MAX(q2.scored_date)
       FROM qualitative_scores AS q2
       WHERE q2.code = p.code AND q2.scored_date <= p.score_date
     )
WHERE p.framework = 'A'
  AND p.score_date >= '2026-05-15'
  AND p.alpha_30d IS NOT NULL
  AND p.total_score >= 50;
```

| Q5 n | latest-prior joins | reconciled | avg moat | avg market pos | avg sentiment |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 206 | 206 | 206 | 7.72 | 4.63 | 3.35 |

All Q5 score gaps reconcile, confirming the component construction used in this analysis.

**Conclusion: not supported.** Both halves of the quant split and every component quartile have negative alpha, with no monotonic relationship implicating either high quant or high qualitative contribution alone. The result points to total-score selection/composition or interaction among raw factors. Because predictions do not store raw per-stock factor scores, this dataset cannot attribute the inversion to ROE, growth, leverage, margin, or PB individually.

## Hypothesis 3 — Individual-stock concentration

**Hypothesis.** A small set of repeatedly scored stocks accounts for the Q5 inversion.

### Evidence: ten worst Q5 stocks by average alpha

```sql
SELECT code, MAX(name) AS name, COUNT(*) AS observations,
       ROUND(AVG(alpha_30d), 4) AS avg_alpha_pct,
       ROUND(AVG(total_score), 2) AS avg_total_score,
       ROUND(AVG(quant_score), 2) AS avg_quant_score
FROM predictions
WHERE framework = 'A'
  AND score_date >= '2026-05-15'
  AND alpha_30d IS NOT NULL
  AND total_score >= 50
GROUP BY code
ORDER BY AVG(alpha_30d), code
LIMIT 10;
```

| Rank | Code | Name | Observations | Avg alpha | Avg total | Avg quant |
| ---: | --- | --- | ---: | ---: | ---: | ---: |
| 1 | 000786 | 北新建材 | 19 | -20.2572% | 64.25 | 48.25 |
| 2 | 600938 | 中国海油 | 19 | -19.2054% | 53.58 | 36.58 |
| 3 | 603606 | 东方电缆 | 19 | -15.1740% | 62.24 | 48.24 |
| 4 | 002050 | 三花智控 | 19 | -10.1024% | 58.58 | 44.58 |
| 5 | 601899 | 紫金矿业 | 19 | -9.8932% | 54.50 | 37.50 |
| 6 | 002594 | 比亚迪 | 19 | -9.5761% | 56.75 | 39.75 |
| 7 | 600941 | 中国移动 | 19 | -9.2778% | 52.38 | 36.38 |
| 8 | 002475 | 立讯精密 | 16 | -6.9199% | 51.06 | 34.06 |
| 9 | 002648 | 卫星化学 | 19 | -4.5590% | 54.81 | 40.81 |
| 10 | 000963 | 华东医药 | 19 | -2.1994% | 69.87 | 55.87 |

### Evidence: remove all observations from the five worst stocks

```sql
WITH stock_rank AS (
  SELECT code, AVG(alpha_30d) AS stock_avg_alpha,
         ROW_NUMBER() OVER (ORDER BY AVG(alpha_30d), code) AS worst_rank
  FROM predictions
  WHERE framework = 'A'
    AND score_date >= '2026-05-15'
    AND alpha_30d IS NOT NULL
    AND total_score >= 50
  GROUP BY code
),
q5 AS (
  SELECT p.*, r.worst_rank
  FROM predictions AS p JOIN stock_rank AS r USING (code)
  WHERE p.framework = 'A'
    AND p.score_date >= '2026-05-15'
    AND p.alpha_30d IS NOT NULL
    AND p.total_score >= 50
)
SELECT 'all Q5' AS sample, COUNT(*) AS n,
       COUNT(DISTINCT code) AS stocks,
       ROUND(AVG(alpha_30d), 4) AS avg_alpha_pct
FROM q5
UNION ALL
SELECT 'excluding worst 5 stocks', COUNT(*), COUNT(DISTINCT code),
       ROUND(AVG(alpha_30d), 4)
FROM q5 WHERE worst_rank > 5;
```

| Sample | n | Stocks | Avg alpha |
| --- | ---: | ---: | ---: |
| All Q5 | 206 | 11 | -9.9079% |
| Excluding worst 5 stocks | 111 | 6 | -5.6128% |

The worst five account for 95 of 206 Q5 observations. Removing them improves average alpha by 4.30 points, but the remaining six stocks still underperform by 5.61 points.

**Conclusion: supported as a material contributor, not as the sole cause.** Q5 has only 11 independent stock identities and repeated daily observations substantially overstate the effective sample size. Nevertheless, the sign remains negative after excluding the five worst stocks, so idiosyncratic concentration does not fully explain the inversion.

## Hypothesis 4 — Systematic market effect

**Hypothesis.** Q5 appears weak primarily because the broad market fell, rather than because high-score stocks underperformed comparable Framework A stocks.

The monthly benchmark query in Hypothesis 1 shows average 沪深300 30-day returns of +0.81% for May and +0.09% for June, already contradicting a broad negative-market explanation.

### Evidence: same-score-date excess alpha

For each settled post-fix Framework A row, this query subtracts the mean alpha of all Framework A stocks with settled outcomes and the same `score_date`. This controls for the exact scoring-date market window. The row itself is included in the daily mean, making this a descriptive peer-centering measure rather than leave-one-out estimation.

```sql
WITH cohort AS (
  SELECT *, AVG(alpha_30d) OVER (PARTITION BY score_date) AS day_avg_alpha
  FROM predictions
  WHERE framework = 'A'
    AND score_date >= '2026-05-15'
    AND alpha_30d IS NOT NULL
)
SELECT CASE WHEN total_score >= 50 THEN 'Q5 >=50'
            WHEN total_score >= 44 THEN 'Q4 44-<50'
            WHEN total_score >= 38 THEN 'Q3 38-<44'
            WHEN total_score >= 32 THEN 'Q2 32-<38'
            ELSE 'Q1 <32' END AS bucket,
       COUNT(*) AS n,
       ROUND(AVG(alpha_30d), 4) AS avg_alpha_pct,
       ROUND(AVG(day_avg_alpha), 4) AS avg_same_day_peer_alpha_pct,
       ROUND(AVG(alpha_30d - day_avg_alpha), 4) AS avg_excess_alpha_pct
FROM cohort
GROUP BY bucket
ORDER BY CASE bucket WHEN 'Q5 >=50' THEN 5 WHEN 'Q4 44-<50' THEN 4
                     WHEN 'Q3 38-<44' THEN 3 WHEN 'Q2 32-<38' THEN 2 ELSE 1 END DESC;
```

| Bucket | n | Avg alpha | Avg same-day peer alpha | Avg excess alpha |
| --- | ---: | ---: | ---: | ---: |
| Q5 >=50 | 206 | -9.9079% | -6.0988% | **-3.8091%** |
| Q4 44-<50 | 132 | -8.2141% | -6.0361% | -2.1780% |
| Q3 38-<44 | 118 | -4.7347% | -6.1418% | +1.4070% |
| Q2 32-<38 | 68 | -6.0317% | -6.1788% | +0.1471% |
| Q1 <32 | 141 | +0.3058% | -6.0498% | **+6.3556%** |

Q5 remains substantially negative relative to stocks scored on the same dates, while Q1 is strongly positive on the same measure. This is cross-sectional inversion after controlling for date-window effects.

**Conclusion: not supported.** Monthly benchmark returns are slightly positive, `alpha_30d` already subtracts the benchmark, and Q5 still trails same-date Framework A peers by 3.81 points.

## Root-cause assessment and recommendation

The strongest diagnosis supported by the available data is **Framework A cross-sectional score miscalibration amplified by stock-level concentration**. A pure time-window cause is inconsistent with negative Q5 alpha in both May and June. A pure market cause is inconsistent with positive average benchmark returns and negative Q5 same-day excess alpha. A single quant-versus-qualitative cause is inconsistent with negative results across all component partitions and the lack of a monotonic component gradient.

The Q5 threshold is instead assembling a small, repeatedly observed basket whose high scores did not translate into relative performance in this regime. This is consistent with the Phase 4 roadmap's warning that strong-signal validity remains unproven. The database cannot establish which raw numeric factor is responsible because predictions retain only aggregate `quant_score`, not per-factor scores. Causal claims about PB, ROE, growth, debt, or gross margin would therefore exceed the evidence.

**Concrete recommendation:** treat Q5/strong as unvalidated and do not tune production weights from these 206 correlated rows. First create an offline, report-only calibration dataset that snapshots every raw factor score at prediction time, evaluates one observation per stock per non-overlapping 30-day window, and uses date-blocked plus leave-one-stock-out validation. Only promote a weight/threshold change if Q5 has non-negative same-date excess alpha out of sample and the result survives exclusion of the five worst stock identities. This directly addresses both the missing factor attribution and the pseudo-replication revealed here.

## Verification

Diagnostic execution:

```text
$ python3 /tmp/run_diagnosis.py
exit code: 0
```

No dependency, type-checker, or project test run was needed: the artifact is a Markdown report, the diagnostic uses only the Python standard library, and the database access was read-only. The relevant smoke test was the successful end-to-end script execution above.
