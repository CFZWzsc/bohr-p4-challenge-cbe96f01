# 核验协议（verification-protocol）

本文件描述 `scripts/sk_entry.py` 的确定性流程。实现只依赖 Python 标准库，不联网，不使用时间戳或随机数，同一输入重复运行得到逐字节相同的结果。

## 1. 输入发现

1. 读取 `--input` 目录（默认 `/input`）。
2. 发现 `case.json`：取第一个含 `case_id` / `output_contract` / `manuscript` / `references` / `sources` 等字段的 JSON 对象。
3. 稿件来源优先级：
   - `case.json` 中的 `manuscript` / `paper` / `article` / `content` / `full_text`；
   - `case.json` 声明的 `manuscript_file` / `paper_file`；
   - 目录中的 `manuscript.*` / `paper.*` / `article.*` / `draft.*` / `main.*`；
   - 其余 `.md` / `.txt` 中正文最长者。
4. 来源发现：
   - `case.json` 中的 `references` / `bibliography` / `works_cited` / `sources`；
   - 目录内除稿件、`case.json`、`task.md`、`README`、`quickstart`、`policy.json` 之外的 `.json` / `.jsonl` / `.xml` / `.csv` / `.txt` / `.md` / `.pdf`。
5. 不依据文件名或题面标签猜测内容；读不到就记入 `errors` / `uncertainty`。

## 2. 来源解析

| 格式 | 处理 |
|---|---|
| JSON | 递归查找 `sources` / `references` / `records` / `items` / `results` 等容器；单条记录也可 |
| JSONL / NDJSON | 逐行 JSON，坏行跳过并记录 |
| CSV | `csv.DictReader`，空行跳过 |
| XML | `ElementTree`，子元素与属性合并为字段 |
| TXT / MD | 先识别 `[n]` / `n.` 起首的文献行；否则整个文件作为一条来源（标题取首行） |
| PDF | 解压内容流并抽取 `Tj` / `TJ` 字面量字符串；置信度低，记 `pdf_low_confidence` |

字段别名覆盖 `id`、`title`、`authors`、`year`、`venue`、`doi`、`url`、`isbn`、`abstract`、`text`、`retracted` 等中英文写法。归一化键 = 小写 + 仅保留字母、数字、汉字。

## 3. 论断与引用标记

- 按句切分（`.!?。！？；` 及空行），保留字符区间与行号。
- 只审查含引用标记的句子；章节由 Markdown 标题或 `Abstract` / `Methods` / `Results` / `Discussion` 等独立行确定。
- 支持三类标记：
  - 数字：`[1]`、`[1,3]`、`[2-5]`；
  - 作者-年份：`(Smith, 2020)`、`(Kim et al., 2021)`；
  - 显式：`见文献3`、`参见文献3`、`see ref 5`。
- 映射规则：
  - 数字/显式标记 → 来源 `id` 精确相等；
  - 作者-年份 → 作者字段包含姓氏且年份前缀相等；
  - 匹配不到就是匹配不到，不按位置猜、不按“最相近”猜。

## 4. 支持度判定

对每条论断取所解析来源的 `title + abstract + text` 并集，计算：

- `overlap = |共享有效词项| / |论断有效词项|`；
- 有效词项：英文 ≥3 字母（去停用词）与中文二元组；
- 阈值：`supported ≥ 0.55`，`partially_supported ≥ 0.28`，否则 `unsupported`。

随后叠加硬性检查（命中即改写判定）：

| 检查 | 规则 | 结果 |
|---|---|---|
| 数值一致性 | 同单位不同值 | 记 `numeric_mismatch`，判 `unsupported` |
| 数值缺失 | 来源无该数值、无同单位数值 | 记 `numeric_absent`，`supported` 降为 `partially_supported` |
| 否定方向 | 正文与来源否定词奇偶性相反且覆盖达标 | 记 `negation_conflict`，判 `unsupported` |
| 引号片段 | 引号内文字未在来源逐字出现 | 记 `quote_mismatch`，最高只能 `partially_supported` |
| 绝对化措辞 | `all` / `every` / `证明` 等且覆盖 < 0.85 | 判 `unsupported`（结论超出引文范围） |
| 词项过少 | 论断有效词项 < 3 | 判 `unverifiable` |

## 5. 来源可靠性

对每个被引用来源检查：

- 元数据撤稿标记（`retracted` / `withdrawn` / `retraction_status` / 撤稿）→ `high`；
- 预印本特征（arXiv、bioRxiv、medRxiv、SSRN、preprint 等）→ `medium`；
- 缺 DOI/ISBN/URL → `low`；缺 venue → `low`；缺年份 → `low`；
- 期刊/出版社名称特征只作为正向信号记录，不当作已核验。

## 6. 输出与降级

- 先写骨架 `result.json`，再写最终结果，避免超时后磁盘无产物。
- `status`：无正文 → `insufficient_input`；无来源/无带引用论断/全部无法定位 → `partial`；正常完成 → `success`。
- 解析失败、PDF 无文本层、字段缺失、无法联网等一律写入 `uncertainty` / `errors` / `limitations`，不补造。
- 输出目录不可写时，错误写 stderr，进程仍以 0 退出，避免 harness 判为封装失败。
