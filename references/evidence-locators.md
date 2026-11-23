# 证据定位（evidence-locators）

每条 finding 必须能被人按记录回查到原文。定位由 **文件 + 章节/记录 + 原文短引** 三段组成，缺一不可。

## 1. 稿件定位（locator）

```json
{
  "file": "manuscript.md",
  "section": "Results",
  "sentence": "¶2",
  "line": 14,
  "char_start": 201,
  "char_end": 267,
  "format": "manuscript.md §Results ¶2"
}
```

- `file`：输入目录内相对路径；`case.json` 内嵌正文记为 `case.json`。
- `section`：最近的标题（`Abstract` / `Methods` / `Results` / `Discussion` / 中文标题）；无标题记 `正文`。
- `sentence` / `line` / `char_start` / `char_end`：段内序号、行号与字符区间，用于精确回查。
- `format`：人类可读定位串，直接写进报告。

## 2. 来源定位（source_locator）

```json
{
  "file": "sources.json",
  "record": "/sources.json#record[0]",
  "field": "abstract",
  "char_start": 0,
  "format": "sources.json → abstract"
}
```

- `record` 指向 JSON/JSONL/XML/CSV 中的具体记录，或 `case.json#references[i]`。
- `field` 说明短引取自 `title` / `abstract` / `text` 哪一字段。
- 无法定位来源时写 `null`，并在 finding 的 `rationale` 说明原因；**不得伪造定位**。

## 3. 原文短引（quote）

- 必须逐字取自输入文本的**连续片段**，允许空白差异，不允许改写、拼接或概括。
- 稿件短引取整句（上限 400 字符）；来源短引取命中位置附近约 240 字符。
- 引号内文字若作为引文使用，另做逐字比对；比对失败记 `quote_mismatch`。
- 短引为空只允许出现在 `unverifiable` 且来源定位为 `null` 的 finding 中。

## 4. 一条 finding 的最小形状

```json
{
  "finding_id": "F05",
  "claim_id": "C02",
  "citation_id": "CIT02",
  "verdict": "unsupported",
  "severity": "high",
  "facet": "numeric_consistency",
  "locator": {"format": "manuscript.md §Methods ¶1", "line": 9},
  "quote": "The trial enrolled 240 participants across two sites [4].",
  "source_locator": {"format": "sources.json → abstract"},
  "source_quote": "The trial enrolled 120 participants across two sites.",
  "rationale": "词项覆盖 1.00；数值冲突：正文 240 participants vs 来源 120 participants。",
  "suggested_revision": "以来源原文为准更正数值，或更换能支持该数值的来源。",
  "needs_recheck": false
}
```

## 5. 复核者的检查步骤

1. 用 `locator.format` 打开稿件，核对 `quote` 是否逐字存在；
2. 用 `source_locator` 打开来源记录，核对该字段是否真的说了 `source_quote`；
3. 检查 `verdict` 与 `rationale` 中的覆盖/数值证据是否一致；
4. 检查 `severity` 是否与问题性质匹配；
5. 检查 `needs_recheck` 与 `uncertainty` 是否覆盖所有无法确认项。

任一步失败，该 finding 应退回重做，而不是保留“看起来合理”的结论。

## 6. 不可定位时怎么写

- 标记无对应条目：`source_locator = null`，`source_quote = ""`，`verdict = unverifiable`，`rationale` 写明“找不到与 [n] 对应的参考文献条目”。
- PDF 无文本层：`uncertainty` 记 `pdf_no_text_layer`，相关来源不参与支持度判定。
- 来源只有标题：`uncertainty` 记 `source_text_missing`，判定注明“仅基于标题”。
