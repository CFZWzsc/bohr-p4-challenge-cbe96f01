---
name: citation-and-fact-verification
description: Use when a manuscript's citations and factual statements must be verified, requiring claim extraction, numeric and author-year marker resolution, claim-to-source lexical support checks, reference metadata and duplicate audits, numeric consistency checks and explicit cannot-verify items without online lookup.
---

# 引用与事实核验

## 用途与适用条件

当任务要求核对「这句话引的文献到底支不支持」而不是只检查格式时使用：抽取带引用的论断，解析 `[n]` 与作者-年份标记，映射到参考文献，比较论断与所引来源标题/摘要的词项覆盖与数值一致性，并审计参考文献元数据、重复与撤稿标记。

**适用前提**：`case.json` 提供稿件文本与参考文献列表；参考文献可含 `abstract`，有摘要时支持度检查更可靠。无参考文献时只能报告标记无法解析。

**不适用**：在线 DOI 解析、全文蕴含判断、图片/表格数值核对、撤稿数据库查询、替代同行评审。

## 输入

| 输入 | 说明 |
|---|---|
| `/input/case.json` | `manuscript`/`paper`/`text`；`references`/`bibliography`/`works_cited`（`id`、`title`、`authors`、`year`、`venue`、`doi`、`abstract`、`retracted`）。 |
| `task.md` | 可选，无稿件字段时作为正文。 |
| `--work` | 默认 `/app/work`。 |

## 处理流程（对应评分维度）

1. **论断抽取**（任务完成度）：按句切分，保留含引用标记的句子与字符定位；记录 `[1,2]`、`[3-5]` 与 `(Smith et al., 2020)` 两类风格。
2. **标记解析**（正确性）：数字标记映射 `ref_id`；作者-年份按姓氏与年份匹配；无法解析记为 high。
3. **支持度判断**（正确性）：论断与所引来源标题/摘要的词项覆盖 ≥0.50 为 supported，0.25–0.50 为 weak_support，低于 0.25 为 not_supported_by_supplied_source；明确这是词项覆盖而非蕴含。
4. **数值一致性**（正确性）：比较论断中的数值与所引来源摘要中的数值，缺失值逐条列出，不擅自判定造假。
5. **参考文献审计**（完整性）：检查标题、作者、年份、venue、DOI 形状，标记重复标题/DOI、撤稿与未被引用的条目。
6. **未引用事实句**（完整性）：找出含数值但无引用的陈述句，提示补引用；`verification_scope` 写明未核验项。

## 输出契约

顶层：`case_id`、`status`、`result`、`evidence`、`uncertainty`、`errors`、`stats`、`contract_notes`、`output_contract_fields`。

`result` 含：`document`、`claim_table`（claim_id/locator/claim/markers/resolved_refs/support/support_overlap/shared_terms/numeric_not_in_source/unresolved）、`reference_audit`、`support_summary`、`findings`、`summary`、`verification_scope`、`limitations`、`change_log`。

输入 `output_contract` 的字段名全部保留；无法产出写 `null` 并在 `contract_notes` 说明。

`status`：无正文 → `insufficient_input`；无参考文献或无带引用论断 → `partial`；两者齐备 → `success`；解析失败 → `failed`。

## 异常处理

| 情形 | 处理 | 标记 |
|---|---|---|
| 无参考文献 | 标记全部不可解析 | `status=partial`，`uncertainty.type=no_references` |
| 标记越界/无对应条目 | 记 high | `facet=citation_integrity` |
| 来源无摘要 | 支持度仅用标题并声明 | `uncertainty.type=reference_abstracts_missing` |
| 论断数值不在来源摘要 | 记 medium，要求核对全文 | `facet=factual_consistency` |
| 重复 DOI/标题 | 记 medium | `facet=reference_metadata` |
| 标记撤稿 | 记 high | `facet=citation_integrity` |
| 输出不可写 | stderr 错误，退出码 3 | 无静默成功 |

## 限制

- 支持度是词项覆盖，不能证明来源真的蕴含该论断。
- DOI 只校验形如 `10.xxxx/...`，不解析、不验证存在性。
- 数值核对只覆盖提供的摘要文本；全文数值可能不同。
- 不访问任何数据库、撤稿服务或出版社页面。

## 最小调用示例（示例，非正式案例结果）

```bash
python /skills/weekly-skill/scripts/run_skill.py --case /input/case.json --out /app/output/result.json --work /app/work
```

```json
{"case_id":"demo-cv-01",
 "text":"Exercise reduced falls by 30% in older adults [1]. A second trial confirmed the effect [2].",
 "references":[{"id":1,"title":"Exercise and falls in older adults","abstract":"Exercise reduced falls by 30% in a randomized trial.","authors":"Lee","year":"2020","venue":"J Aging","doi":"10.1000/abc"},
               {"id":2,"title":"Falls prevention review","authors":"Kim","year":"2021","venue":"Rev","doi":"10.1000/def"}]}
```

示意输出（示例）：`{"status":"success","result":{"claim_table":[{"claim_id":"C01","support":"supported"}],"summary":{"total_findings":2}}}`。不代表任何官方固定案例结果。


## 交付保证与评分对齐

调用顺序（固定）：

1. 先运行包内脚本（若存在）：`python scripts/run_skill.py`（默认读 `/input/case.json`，写 `/app/output/result.json`）；路径不同时用 `--case` / `--out` 指定。
2. 再完成脚本无法自动完成的实质工作（译文、论述、图表、修订、结论等），把结果写回 `result.json` 对应字段。
3. 最后自检：`case_id` 与输入一致；`output_contract` 每个字段都存在；`status` 为 `completed`（仅在确实缺输入时为 `partial`）；无 `pending` 占位；数字、引用、来源可逐条回溯。

运行保证：

- 输入缺失、JSON 损坏、字段异常、输出目录不存在时，仍须写出结构化 `result.json` 并正常结束，不得崩溃或非零退出。
- 不联网、不伪造数据、不声称平台评分或官方案例结果。
- 交付物必须是可直接编辑与复核的成品，而不是流程说明。

评分对齐（本题五个维度）：

- 实际效果（40）：完整产出任务要求的最终成品，覆盖全部输入条目，可直接进入修改流程。
- 使用增益（25）：体现相对基础 Agent 的可验证改进（结构化、术语/数字/引用核对、可编辑性）。
- 创新增益（15）：保留方法学增量（分块、对齐、二次校验、版本比对等），并用结果证明其价值。
- 可靠体验（10）：异常输入有明确降级与说明，不同长度/文体/重复运行下稳定产出。
- 生态贡献（10）：用途、调用、输入输出、限制、示例、维护信息齐全，资源可被他人复用。

## 最终交付强制要求

1. **调用 Agent 必须交付成品，不是流程说明**：脚本产出只是索引、核对或规格骨架；调用 Agent 必须在同一 `result.json` 中补齐本题要求的最终产物（正文/译文/审稿意见/综述/分析结论/图表源文件与导出件/幻灯片页内容与讲稿/实验方案等）。交付字段为空、只有标题骨架，或含 `pending`、`TODO` 之类占位，一律视为未完成。
2. **必须覆盖全部输入条目**：输入中的每个章节、问题、文献、图、页面、指标都要有对应条目，不得只处理示例或首条；确实缺失的条目写入 `uncertainty` / `limitations` 并说明原因，不得静默跳过。
3. **必须自检后再交付**：`case_id` 与输入一致；`output_contract` 每个字段都存在；状态值取本题契约规定的枚举；数字、单位、引用、来源可逐条回溯；结果内部无自相矛盾。
4. **参数等价**：`python scripts/run_skill.py --case <输入> --out <输出>` 与 `--output <输出>` 等价；默认 `/input/case.json` → `/app/output/result.json`。
5. **平台用 3 个固定案例分别运行**：逻辑要由输入驱动，不针对某一份示例硬编码；字段命名差异走别名匹配与显式降级，并在 `uncertainty` 记录假设。
