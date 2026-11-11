# 知识库现状与迁移规则

## 当前已有内容

当前知识平台已经可运行，但还不是可直接用于生产的批准知识库：

- `knowledge/domains/vehicle_domain_seed.json`：车辆领域与功能上下文草案；
- `knowledge/domains/hara_scenario_seed.json`：HARA 场景维度和失效类别草案；
- `knowledge/rules/asil_matrix.json`：确定性 ASIL 查表草案；
- `knowledge/sources/legacy_company_assets.json`：旧项目公司手册和模板的外部来源登记。

系统已经从来源登记指向的旧工程中解析 70 份 Word/Excel 公司手册和模板，并生成本地混合检索索引、证据块、图关系和 Wiki。来源登记和被解析资料目前均为 `draft`；“已被索引”不等于“已被批准”。

## 已实现的知识能力

- Word、Excel、Markdown、文本和 JSON 解析；
- 保留段落、表格行和工作表单元格定位；
- 词法检索与向量检索混合排序；
- RAG 结果自动携带知识 ID、版本、状态、来源和定位；
- 指导手册、模板、业务域和标签之间的图关系；
- 基于检索结果进行一跳或多跳图扩展；
- 从同一索引生成静态 Wiki；
- `approved` 默认过滤和显式 `draft` 探索开关；
- 可切换 OpenAI-compatible Embedding API。

默认离线 Embedding 是开发用哈希向量，只用于本地验证流程。生产语义检索应在 `config/knowledge.json` 中选择正式 Embedding 服务并重建索引。

## 旧项目资料如何进入知识库

旧项目中的目录不是一个整体 Skill，也不能原样全部塞进向量库。应按语义拆分：

| 旧目录 | V2 中的归属 | 处理方式 |
| --- | --- | --- |
| `01_指导手册` | 规则与流程知识 | 抽取章节、规则、适用条件和来源页码，人工审核后发布 |
| `02_模板` | 模板资产库 | 保留原文件；解析工作表、表格、字段和必填约束 |
| `03_输入物` | 项目输入 | 只属于具体任务，不进入全局知识库 |
| `04_过程产物` | 项目证据 | 用于审计与复现；优秀样例需脱敏和审批后才能晋升 |
| `05_输出物` | 正式交付件 | 默认是项目结果，不自动变成通用知识 |

## 知识分层

```text
knowledge/
├─ sources/       原始文件登记、哈希、版本和权限
├─ templates/     Word/Excel 的字段与版式结构
├─ rules/         可执行规则和确定性表格
├─ domains/       领域术语、失效模式、场景和安全机制
└─ examples/      经脱敏、评审通过的黄金样例
```

Agent 只通过 `knowledge_scopes` 检索需要的知识。模板由 `document_engineer` 使用，业务 Engineer 不直接修改模板文件。

`skills/functional-safety-knowledge` 存放的是“如何检索、引用和判断证据状态”的工作方法，不存放 Word/Excel 原件。所有需要知识的 LLM Agent 已在自己的配置中引用该 Skill。

## 晋升门槛

每个知识条目必须保留 `id`、`version`、`status`、`source` 和 `tags`。只有完成内容抽取、来源定位、版本确认和领域负责人审核后，状态才能从 `draft` 改为 `approved`。

## 当前解析告警

旧资料中有 5 个以 `.docx` 命名但不是有效 Office ZIP 的文件，索引器已跳过并报告告警；另有 2 个空文本说明文件。需要回到源资料确认这些文件是占位符、损坏文件还是旧格式伪装文件。
