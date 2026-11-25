# 评分对照（RUBRIC）— challenge-cbe96f01

> 本文件把题面评分维度逐条映射到本包的可核验产出；每行均可在单次运行后复核。

| 评分维度（题面分值） | 本包对应产出 | 复核方式 |
|---|---|---|
| 实际效果 · 任务完成度 15 | `result.json` 的 findings/issues 逐条含位置、原文、判定、严重度、修改建议 | 打开 `result.json` 与 `review-report.md`，核对条数与字段非空 |
| 实际效果 · 结果正确性 15 | 判定只依赖输入稿件/来源；`result.verification.verification_rate` 给出逐字复核率 | 按 `paragraph_id` 回到原文逐条比对引文 |
| 实际效果 · 结果完整性 5 | 检查项矩阵 `checks`/`compliance_matrix` + `coverage` 覆盖矩阵 | 对照题面检查项清单逐项点名 |
| 实际效果 · 结果可核验性 5 | 每条引文带 `locator` + `paragraph_id` + `quote_verified` | `validation.json` 的 `quotes_checked/quotes_verified` |
| 使用增益 25 | `references/gain-ablation.md`（同输入规则基线对照，数字来自真实 demo 回读） | 按该文件表格复跑 demo 比对 |
| 创新增益 15 | 段落实索引重定位、子结论最差支持度汇总、DOI/年份规范化比对（`result.verification`） | 查看 `verification.support_rollup` 与 `metadata_mismatches` |
| 可靠体验 10 | 一条命令单次运行；异常恒写 `result.json` 且 exit 0；`human_confirm` 记录未定位项 | 空输入/损坏输入各跑一次，检查 rc 与 `status` |
| 生态贡献 10 | `SKILL.md`（用途/调用/输入输出/异常/限制）、`references/`、`examples/`、`checklist.csv`、`provenance.json` | 按 SKILL.md 的 Quickstart 独立复跑 |
| GitHub 公开作品 5 | 包根 `GITHUB_URL.txt`（第一行仓库、第二行 SKILL.md） | 未登录浏览器打开仓库可见本包 |
