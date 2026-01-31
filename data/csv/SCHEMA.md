# P2 - CSV Schema (Human-maintainable)
NOTE: UTF-8. 中文字段说明从下一行开始。---------------------------------------------------------

目标：让非程序员可直接填写 CSV，并能完整映射到 RDF（Jena）与 Neo4j（可视化）。

约定：
- 所有 `*_id` 为稳定字符串（建议大写+下划线），跨文件引用。
- 多值字段用 `;` 分隔（例如 `concept_ids`、`evidence_passage_ids`）。
- `source_type` 约定值：`encyclopedia`（中文百科）| `lecture`（高校讲义）| `review`（论文综述）。

## 1) `scholars.csv`

| field (EN) | 中文说明 | 必填 | 示例值（中文） |
|---|---|---:|---|
| scholar_id | 学者唯一 ID | 是 | SCH_LIPPMANN |
| name_zh | 学者中文名 | 是 | 李普曼 |
| name_en | 学者英文名 | 否 | Walter Lippmann |
| description_zh | 简要说明（中文） | 否 | 美国作家、记者、政治评论家，代表作《舆论学》。 |
| school_id | 所属学派 ID（用于 `memberOf`） | 否 | SCHOOL_JOURNALISM |
| active_year | 活跃年份（用于 `activeInYear`） | 否 | 1922 |

## 2) `concepts.csv`

| field (EN) | 中文说明 | 必填 | 示例值（中文） |
|---|---|---:|---|
| concept_id | 概念唯一 ID | 是 | CONCEPT_PUBLIC_SPHERE |
| name_zh | 概念中文名 | 是 | 公共领域 |
| description_zh | 概念解释（中文，尽量可引用） | 否 | 指社会成员就公共事务进行讨论的空间（示例描述） |

## 3) `propositions.csv`

| field (EN) | 中文说明 | 必填 | 示例值（中文） |
|---|---|---:|---|
| proposition_id | 命题唯一 ID | 是 | PROP_LIPPMANN_1922_A |
| scholar_id | 命题提出者（`proposes`） | 是 | SCH_LIPPMANN |
| proposition_text_zh | 命题中文表述（可被引用的断言单元） | 是 | （示例）舆论是人们对于公共事务的看法。 |
| year | 年份（用于 `activeInYear` 或 `publishedInYear`） | 是 | 1922 |
| concept_ids | 关联概念 ID（多值用 `;`，用于"理论路径"） | 否 | CONCEPT_PUBLIC_OPINION |
| evidence_passage_ids | 支撑该命题的段落证据 ID（多值用 `;`） | 是 | PAS_0001 |

## 4) `passages.csv`

| field (EN) | 中文说明 | 必填 | 示例值（中文） |
|---|---|---:|---|
| passage_id | 段落唯一 ID | 是 | PAS_0001 |
| source_id | 来源唯一 ID（同一来源可对应多段落） | 是 | SRC_0001 |
| source_title | 来源标题（公开中文资料） | 是 | 某中文百科词条：舆论（示例） |
| source_url | 来源链接（若公开可填） | 否 | https://example.org/wiki/public_opinion |
| source_type | 来源类型（约定值） | 是 | encyclopedia |
| published_year | 来源发表/发布年份（可选） | 否 | 2019 |
| passage_text | 段落中文文本（必须为中文） | 是 | （示例段落）……（此处填可引用的中文原文段落） |
| locator | 定位信息（页码/章节/段落号/截图编号等） | 否 | 第 3 段 |

## 5) `influences.csv`（仅用于 `influencedBy`，且必须有证据）

| field (EN) | 中文说明 | 必填 | 示例值（中文） |
|---|---|---:|---|
| influence_id | 影响断言唯一 ID | 是 | INF_0001 |
| subject_type | 主体类型（`Scholar`/`Proposition`） | 是 | Scholar |
| subject_id | 主体 ID | 是 | SCH_MCLUHAN |
| object_type | 客体类型（`Scholar`/`Proposition`） | 是 | Scholar |
| object_id | 客体 ID | 是 | SCH_INNIS |
| evidence_passage_id | 证据段落 ID（必须） | 是 | PAS_0002 |
| year | 影响发生/被陈述的年份（可选） | 否 | 1964 |
| note_zh | 备注（中文，可解释该影响为何成立） | 否 | （示例）来源中明确写到"受伊尼斯影响"。 |

## 6) `years.csv`

| field (EN) | 中文说明 | 必填 | 示例值（中文） |
|---|---|---:|---|
| year_id | 年份节点 ID | 是 | YEAR_1922 |
| year_value | 年份值（YYYY） | 是 | 1922 |
| label_zh | 展示标签（可选） | 否 | 1922 年 |

## 7) `books.csv`

| field (EN) | 中文说明 | 必填 | 示例值（中文） |
|---|---|---:|---|
| book_id | 著作唯一 ID | 是 | BOOK_PUBLIC_OPINION |
| title_zh | 著作中文名 | 是 | 舆论学 |
| title_en | 著作英文名 | 否 | Public Opinion |
| scholar_id | 作者 ID | 是 | SCH_LIPPMANN |
| year | 出版年份 | 是 | 1922 |
| description_zh | 著作简介（中文） | 否 | 李普曼的代表作，探讨舆论与民主的关系。 |

## 8) `relations.csv`

| field (EN) | 中文说明 | 必填 | 示例值（中文） |
|---|---|---:|---|
| subject_id | 主体学者 ID | 是 | SCH_MCLUHAN |
| relation | 关系类型 | 是 | 师承 |
| object_id | 客体学者 ID | 是 | SCH_INNIS |
| note_zh | 关系备注（可选） | 否 | 麦克卢汉是伊尼斯的学生 |
