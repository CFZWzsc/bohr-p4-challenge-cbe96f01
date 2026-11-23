# 最小调用示例（示例，非正式案例结果）

本示例使用 `examples/demo-case/` 中的合成稿件与来源，演示完整调用、输出契约与判定形状。

## 1. 调用

在包根目录执行（Windows 下把 `/tmp/...` 换成任意可写目录）：

```bash
python scripts/sk_entry.py \
  --input examples/demo-case \
  --work /tmp/cv-work \
  --out /tmp/cv-out
```

等价写法（`--out` 也可指向具体 JSON 文件）：

```bash
python scripts/sk_entry.py --input examples/demo-case --work /tmp/cv-work --out /tmp/cv-out/result.json
```

Harbor 中的正式调用：

```bash
python /skills/weekly-skill/scripts/sk_entry.py --input /input --work /app/work --out /app/output
```

## 2. 示例输出（示意）

`/tmp/cv-out/result.json` 的关键字段（数值为本次合成示例的确定性结果）：

```json
{
  "case_id": "demo-cv-001",
  "status": "success",
  "claims": [
    {"claim_id": "C01", "verdict": "supported",
     "locator": {"file": "manuscript.md", "section": "Abstract", "format": "manuscript.md §Abstract ¶1"},
     "quote": "Exercise reduced falls by 30% in older adults [1]."}
  ],
  "findings": [
    {"finding_id": "F05", "claim_id": "C04", "verdict": "unsupported", "severity": "high",
     "facet": "numeric_consistency",
     "rationale": "词项覆盖 1.00；数值冲突：正文 240 participants vs 来源 120 participants"},
    {"finding_id": "F04", "claim_id": "C03", "verdict": "unsupported", "severity": "high",
     "facet": "source_reliability", "rationale": "retraction：来源元数据标记为已撤稿"}
  ],
  "unverifiable": [{"claim_id": "C05", "reason": "no_resolvable_source"}],
  "counts": {"claims": 5, "findings": 6,
             "by_verdict": {"supported": 1, "partially_supported": 1, "unsupported": 2, "unverifiable": 1},
             "by_severity": {"high": 3, "medium": 2, "low": 0, "info": 1}}
}
```

同时写出 `/tmp/cv-out/run-review.md`：逐条 findings 的稿件定位、来源定位、原文短引、判定依据与修改建议。

## 3. 这个示例覆盖了什么

| 示例条目 | 演示的判定 |
|---|---|
| C01 `[1]` | supported：30% 与来源一致 |
| C02 `(Kim et al., 2021)` | partially_supported：12% 未被来源报告 |
| C03 `[3]` | unsupported（结论超出来源）+ 撤稿 high 级 finding |
| C04 `[4]` | unsupported：240 vs 120 participants 数值冲突 |
| C05 `见文献5` | unverifiable：无对应来源条目，标记待复核，不补造 |

示例仅用于说明接口与判定形状，**不冒充任何官方固定案例结果**。
