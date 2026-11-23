---
name: citation-and-fact-verification
description: >-
  引用与事实核验 Skill：逐句检查稿件中的带引用论断，判断所引来源是否支持该论断、
  来源是否可靠、证据能否定位到具体位置，并输出带定位、严重度和修改建议的结构化结果。
  无法核验的条目一律标记待复核，不补造来源或数值。
version: 2.0.0
license: MIT
---

# 引用与事实核验（citation-and-fact-verification）

## 用途

审查学术稿件的**引用是否真正支持其结论**，并回答四个问题：

1. 这条带引用的论断，来源支持吗？（supported / partially_supported / unsupported / unverifiable）
2. 这个来源可靠吗？（同行评审信号、预印本、撤稿、缺失标识符）
3. 证据能不能定位？（文件 + 章节/记录 + 逐字短引）
4. 若有问题，严重度多高、应该怎么改？

最终产出两份文件：

- `/app/output/result.json`：机器可读、契约字段齐全；
- `/app/output/run-review.md`：逐条复核记录，含定位、原文短引、依据与修改建议。

## 适用条件

- 输入中有**稿件正文**（Markdown / 纯文本 / JSON 字段）；
- 正文使用 `[1]`、`(Author, Year)`、`见文献X` 等引用标记；
- 输入中提供了**参考文献列表或来源文件**（JSON / JSONL / XML / CSV / TXT / MD，或可抽取文本的 PDF）；
- 任务是逐条核验“引用—结论”关系，而不是要求改写论文；
- 允许输出“无法核验”的结论，并接受人工复核闭环。

## 不适用场景

- **论文润色 / 语言改写**：本 Skill 不重写句子、不调整语序、不提升文采；
- **综述写作 / 文献综述生成**：不生成新综述、不补充未提供的文献、不做主题归纳；
- **数据分析 / 统计重算**：不重跑模型、不重新估计效应量、不推断缺失数据；
- 需要联网抓取全文、DOI 解析或撤稿库查询的任务（本 Skill 明确离线）；
- 扫描版 PDF 的 OCR 识别（无文本层时只记待复核）；
- 代写审稿意见、自评分或平台评分声明。

## 输入形态

`--input` 目录（默认 `/input`）可包含：

| 文件 | 作用 | 必需 |
|---|---|---|
| `case.json` | `case_id`、`output_contract`、可选内嵌 `manuscript` / `references` / `sources` | 建议 |
| `manuscript.md` / `paper.md` / `article.txt` 等 | 稿件正文 | 是（或用 case 字段） |
| `sources.json` / `references.json` / `*.jsonl` / `*.xml` / `*.csv` | 来源记录 | 是（否则全部 unverifiable） |
| `*.pdf` | 来源全文（低置信抽取） | 可选 |
| `task.md` | 任务说明，仅作上下文，不当作稿件 | 可选 |

来源记录建议字段：`id`、`title`、`authors`、`year`、`venue`、`doi`、`abstract`、`text`、`retracted`、`retraction_note`。字段名支持常见中英文别名。

## 调用方式

Harbor 正式调用（固定命令）：

```bash
python /skills/weekly-skill/scripts/sk_entry.py --input /input --work /app/work --out /app/output
```

本地复现（包根目录）：

```bash
python scripts/sk_entry.py --input examples/demo-case --work /tmp/cv-work --out /tmp/cv-out
```

`run_skill.py` 是同一入口的永不崩溃薄封装，平台若调用它也可：

```bash
python scripts/run_skill.py --input /input --work /app/work --out /app/output
```

参数：

- `--input`：输入目录，默认 `/input`；
- `--work`：工作目录，默认 `/app/work`（写 `run_log.json`）；
- `--out`：输出目录，默认 `/app/output`；也可指向具体 `result.json` 文件；
- 兼容 `--case <case.json>`，此时输入目录取该文件所在目录。

## 核验项

| 核验项 | 检查内容 | 典型 finding facet |
|---|---|---|
| 引用支持度 | 论断与所引来源的词项覆盖；数值、否定方向、引号片段是否一致；结论是否超出引文范围 | `citation_support` |
| 来源可靠性 | 撤稿标记、预印本特征、同行评审名称信号、缺 DOI/venue/年份 | `source_reliability` / `academic_norm` |
| 证据定位 | 每条判定是否有稿件定位与来源定位；短引是否逐字可回查 | 所有 finding 的 `locator` / `source_locator` |
| 数字与统计一致性 | 百分比、样本量、站点数等同单位数值是否与来源一致；来源未报告时不得当作已支持 | `numeric_consistency` |
| 方法描述 | 随机化、测量、样本、统计方法等描述是否有来源依据（缺失时提示人工核对） | `method_description` |

## 判定口径

| 判定 | 含义 | 默认严重度 |
|---|---|---|
| `supported` | 来源明确报告论断核心内容，无数值/否定/引号冲突 | `info` |
| `partially_supported` | 主题相关，但数值、范围或强度未被完整报告 | `medium` |
| `unsupported` | 来源不支持：覆盖过低、数值冲突、否定相反、结论超范围 | `high` |
| `unverifiable` | 无法定位来源、来源无正文、PDF 不可抽取或标识符缺失 | `medium` |

严重度级别：

- `high`：事实错误或高风险，必须修改后才能进入发表流程；
- `medium`：证据不足或需人工确认，须留下处置记录；
- `low`：规范/元数据问题，不改变结论但影响可回查性；
- `info`：通过项或正向记录。

判定顺序与阈值见 `references/verification-protocol.md`，完整决策表见 `references/verdicts-and-severity.md`。阈值来自 `assets/policy.json`：`supported_min=0.55`、`partial_min=0.28`、`min_claim_terms=3`。

## 证据定位格式

统一使用 **文件 + 章节/记录 + 原文短引**：

- 稿件：`manuscript.md §Results ¶2`，并给出 `line`、`char_start`、`char_end`；
- 来源：`sources.json → abstract`，并给出记录指针与字段名；
- 短引：必须逐字取自输入文本的连续片段，不概括、不改写、不拼接；
- 无法定位时 `source_locator` 为 `null`，`verdict` 必须是 `unverifiable`，不得伪造定位。

字段形状见 `references/evidence-locators.md`。

## 待复核规则

以下情况**必须**判 `unverifiable` 并写入 `unverifiable` / `uncertainty`：

1. 引用标记在提供的参考文献/来源中找不到对应条目；
2. 来源只有标题或元数据，没有摘要/正文可比较；
3. PDF 无文本层或抽取失败（可能为扫描件）；
4. 论断有效词项过少，覆盖比例不可靠；
5. 只有“应该有一篇文献”的线索，没有实际条目。

待复核条目必须带 `reason`、`resolution`、`needs_recheck=true`，并且**绝不补写**来源标题、作者、年份、DOI、页码或数值。

## 异常处理

- 空输入、缺失稿件、损坏 `case.json`、不支持的格式：进程以 0 退出，`status` 诚实记为 `insufficient_input` 或 `partial`，在 `errors` / `uncertainty` 说明原因；
- 单个文件解析失败：跳过该文件并记录，不影响其余文件；
- 输出目录不可写：错误写 stderr，进程仍以 0 退出，避免 harness 误判；
- 任何未捕获异常：`run_skill.py` 补写结构合法、契约键齐全的空结果，绝不崩溃；
- 结果先写骨架再写终稿，避免超时后磁盘无产物。

## 自检清单

运行后逐项检查（`result.json` 与 `run-review.md` 均会列出）：

- [ ] `case_id`、`status` 存在，且 `status` 与实际输入相符；
- [ ] `output_contract` 的每个键都存在且非空（或说明为何为 null）；
- [ ] 每条 claim 都有 `verdict`、`locator`（文件+章节）、`quote`；
- [ ] 每条 finding 都有 `claim_id`、`citation_id`、`verdict`、`severity`、`locator`、`quote`、`rationale`、`suggested_revision`；
- [ ] `counts.by_verdict` 之和 = claims 数；`counts.by_severity` 之和 = findings 数；
- [ ] 数值冲突、撤稿、缺失标识符分别有独立 finding；
- [ ] 无法定位的引用全部出现在 `unverifiable`，且没有编造来源；
- [ ] `uncertainty` 覆盖 PDF 低置信、来源缺正文、离线限制；
- [ ] `run-review.md` 已写到 `/app/output/run-review.md`。

## Agent 执行指令

1. **运行**：执行
   `python /skills/weekly-skill/scripts/sk_entry.py --input /input --work /app/work --out /app/output`
   （本地用 `python scripts/sk_entry.py --input <dir> --work <dir> --out <dir>`）。
2. **读取** `/app/output/result.json`，确认 `status` 不是意外降级；若为 `insufficient_input` / `partial`，先检查 `errors` 与 `uncertainty`，不要自行补造输入。
3. **逐条复核**：对每个 finding，按 `locator` 打开稿件核对 `quote`，按 `source_locator` 打开来源核对 `source_quote`；定位对不上就标记该条为需重做，不能沿用。
4. **核对计数**：`counts.by_verdict` 与 claims、`counts.by_severity` 与 findings 必须一致；不一致时以实际列表为准并说明。
5. **核对待复核**：`unverifiable` 中的每条都应有明确 `reason` 与 `resolution`；确认没有把待复核写成已确认。
6. **写入报告**：把上述复核结论整理进 `/app/output/run-review.md`（脚本已生成初稿，可在其后追加“人工复核记录”小节；不得删除原始定位证据）。
7. **汇报**：只报告实际运行结果与定位核对情况，不声称已在线核验 DOI、撤稿库或全文。

## 输出结构

`result.json` 顶层字段：

| 字段 | 说明 |
|---|---|
| `case_id` / `status` / `status_reason` | 案例标识与运行状态 |
| `claims` | 带引用论断：文本、定位、短引、逐条判定 |
| `citations` | 标记 → 来源解析记录（含 `resolved`、`source_keys`） |
| `findings` | 问题/检查记录：判定、严重度、facet、定位、短引、依据、修改建议 |
| `counts` / `summary_counts` | 按 verdict / severity 的计数与总数 |
| `summary` | 一段话摘要（字符串） |
| `suggestions` | `finding_id → suggested_revision` |
| `unverifiable` | 待复核清单及处置 |
| `conflicts` | 数值冲突、否定冲突、撤稿冲突 |
| `evidence` | 定位索引（claim / source / source_record） |
| `uncertainty` / `limitations` | 不确定性与方法边界 |
| `run_log` / `errors` / `stats` / `inputs` | 运行痕迹与输入清单 |
| `output_contract_fields` / `contract_map` / `contract_extras` | 契约键覆盖、别名映射与缺口说明 |

`output_contract` 别名映射：`issues` / `checks` → `findings`；`results` → `claims`；`refs` / `references` → `citations`；`severity` → `counts`；`suggestions` → 修改建议映射；`notes` → `uncertainty`；`summary` → 摘要字符串。未知键保留 `null` 并写入 `contract_extras`。
