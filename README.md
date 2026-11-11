# Functional Safety Agent V2

这是按“Manager 动态路由 + 业务 Engineer Agent”重新设计的功能安全智能体内核。系统不强制用户走一条固定流水线；Manager 根据任务目标、输入资料和 Agent 能力注册表生成结构化工作计划，再由人工确认后执行。

## 核心设计

1. 每个 Engineer Agent 拥有明确业务：功能建模、失效分析、HARA、安全目标、FTA、FSR、软件接口追溯、文档生成等。
2. Manager 只做任务理解、Agent 选择和 DAG 工作计划，不直接产出专业结论。
3. Agent 间只传 JSON Schema 约束的结构化数据，不以长篇对话作为下游接口。
4. 知识库、Agent 能力和模型 API 三者相互独立。
5. ASIL 查表、FTA 最小割集、ID 与追溯校验等确定性工作不交给大模型。
6. Manager 工作计划和每个业务工作项都可人工批准；审批绑定 SHA-256，内容变化后自动失效。

## 运行方式

```text
用户任务 + 原始资料
        ↓
Manager Agent 查询 Agent Registry
        ↓
动态 workplan.json（DAG）──人工确认
        ↓
多个业务 Engineer Agent（无依赖项可并行）
        ↓
Schema + 确定性计算/校验
        ↓
结构化候选产物──逐项人工确认
        ↓
Document Engineer 填充公司模板
```

`recipes/concept_to_fsr.example.json` 只是可选示例，适合标准化重复任务和回归测试，不是系统强制业务流程。

## Agent API 配置

每个 Agent 都有独立配置文件，位于 `config/agents/`。例如 HARA Agent 的模型供应商、地址、密钥和模型名称分别由以下环境变量控制：

```text
FS_AGENT_HARA_PROVIDER
FS_AGENT_HARA_BASE_URL
FS_AGENT_HARA_API_KEY
FS_AGENT_HARA_MODEL
```

配置文件只保存环境变量名、温度和超时等非敏感信息。真实密钥不得写入仓库；可参考 `config/api.env.example`。确定性的 `traceability_validator` 不调用模型，因此 `api` 为 `null`。

当前已实现 OpenAI-compatible Chat Completions Adapter 和有界工具调用循环。模型可以自动调用知识检索、知识图谱和当前工作项的只读产物工具；最终结果仍走同一套 Schema、确定性校验、审批和落盘路径。

## 目录

```text
config/agents/       每个 Agent 的业务能力和独立 API 配置
config/knowledge.json  RAG Embedding 与检索配置
src/fs_agent_v2/
├─ agents/           Agent 接口和动态注册表
├─ deterministic/    ASIL、FTA、追溯等确定性算法
├─ knowledge/        带版本、来源和状态的知识读取接口
├─ orchestration/    Manager 工作计划、DAG、Schema 与人工门禁
└─ storage/          项目、产物、审批和事件存储
schemas/             结构化数据契约
knowledge/           与 Agent 代码分离的知识源、规则和领域条目
skills/              Agent 的可复用工作方法，不存放原始业务资料
recipes/             可选的固定流程示例
examples/            演示工作计划和业务候选数据
tests/               无模型依赖的回归测试
```

## 快速演示

```powershell
python run.py agents
python run.py agent-api hara_engineer
python run.py init demo-dynamic --title "AEB 功能安全分析"
python run.py plan-submit demo-dynamic examples/workplan_concept_to_fsr.json
python run.py plan-approve demo-dynamic --reviewer "安全经理" --comment "任务拆解已确认"
python run.py item-submit demo-dynamic W01 examples/function_model.json
python run.py item-approve demo-dynamic W01 --reviewer "功能安全工程师" --comment "功能边界已确认"
python run.py item-submit demo-dynamic W02 examples/malfunction_analysis.json
python run.py plan-status demo-dynamic
```

配置相应 Agent 的 API 环境变量后，可以让模型自动检索知识并生成工作项候选：

```powershell
python run.py item-run demo-dynamic W02 `
  --query "分析 AEB 功能失效与危害事件" `
  --include-draft-knowledge
```

`--include-draft-knowledge` 只适合当前知识审核前的开发演示。正式运行默认仅允许模型检索 `approved` 知识。

构建和查询知识平台：

```powershell
python run.py knowledge-build --reset --include-external
python run.py knowledge-search "FTA 顶事件" --tag company_process --include-draft
python run.py knowledge-graph legacy-8b63f0c0afc0bffa2a7f --depth 2
python run.py wiki-build
```

`knowledge-search` 默认只返回 `approved` 知识；当前旧资料尚未审核，因此演示时需要显式使用 `--include-draft`。

查看可选固定示例：

```powershell
python run.py recipe-show
```

运行测试：

```powershell
python -m unittest discover -s tests -v
```

## FastAPI 测试台

启动本地网站：

```powershell
python run.py web
```

然后打开 `http://127.0.0.1:8000/`。页面支持创建/载入项目、Manager 自动规划、计划审批、并行执行当前就绪 Agent、查看结构化结果、结果审批和审计事件。FastAPI OpenAPI 页面位于 `http://127.0.0.1:8000/docs`。

服务默认只监听本机回环地址。通过页面调用 Manager 时，会按各 Agent 的 API 配置访问模型服务；不要在任务描述中填写密钥或其他不应发送给模型的数据。接口清单和运行说明见 `docs/WEB_CONSOLE.md`。

面向 AI 应用开发岗位的项目问答和追问边界见 `docs/INTERVIEW_QA_AI_APPLICATION.md`。

## 当前知识库边界

当前已经实现 Word/Excel/Markdown/JSON 摄取、结构化分块、词法与向量混合检索、证据包、知识关系图和静态 Wiki。旧项目实际索引了 70 份公司手册/模板，加上 4 个内部知识条目，共形成 74 份文档、591 个证据块和 390 条图关系。

当前使用离线哈希向量用于开发验证；配置 `FS_KNOWLEDGE_EMBEDDING_*` 后可切换到 OpenAI-compatible Embedding API。切换模型后必须用 `knowledge-build --reset` 重建索引。

所有旧资料仍为 `draft`，不能直接宣称为已批准公司知识。迁移和晋升规则见 `docs/KNOWLEDGE_BASE.md`。Agent 的检索和引用规范存放在 `skills/functional-safety-knowledge/`。

## 软件接口追溯与 MCP

`software_interface_engineer` 现在包含两项正式交付能力：基于模块软件接口表生成接口总览表，以及在总览更新后比较新旧版本；同时保留跨应用层链路和 Unit 配置审查。默认生成引擎是从原 VBA 逐条复刻的纯 Python 实现，不启动 Excel/WPS；原宏仍作为兼容性基准和回退引擎保留。相同能力通过 stdio MCP Server 暴露给外部子 Agent：

```powershell
python run.py interface-generate ..\外部接口生成与对比工具.zip --output data\interface-generation
python run.py interface-generate ..\外部接口生成与对比工具.zip --output data\interface-generation-with-diff --compare
python run.py interface-generate ..\外部接口生成与对比工具.zip --output data\interface-generation-legacy --engine legacy
python run.py interface-overview-compare .\new.xlsx .\old.xlsx --output data\interface-comparison
python run.py interface-analyze ..\外部接口生成与对比工具.zip --output data\interface-analysis
python run.py interface-mcp
```

每次生成/对比都会输出输入与产物 SHA-256、JSON 报告、JSONL 行证据和需要人工确认的状态。生成与版本对比默认保持为两个独立能力；只有添加 `--compare` 才在生成后继续执行完整对比。`fast` 生成不要求本机 Excel/WPS；只有显式指定 `--engine legacy` 时才通过 COM 执行固定的原宏。实测样本中，Python 核心生成约 30 秒，原 VBA 同阶段约 160 秒；原结果 12,510 行全部被逐字段覆盖，Python 另外保留了 36 条源表中存在但旧执行结果未保留的有效记录，因此首次切换必须结合差异报告人工确认。

使用方法、安全边界和 MCP 客户端配置见 `docs/SOFTWARE_INTERFACE_AGENT.md`。

让 Manager 自动检查输入、读取 Agent Registry 并生成计划，然后并行执行当前所有就绪 Agent：

```powershell
python run.py init interface-manager-demo --title "软件接口 Manager 演示"
python run.py manager-plan interface-manager-demo `
  --request "审查软件接口链路和配置变更" `
  --input IN-INTERFACE-PACKAGE software_interface_package "..\外部接口生成与对比工具.zip"
python run.py plan-approve interface-manager-demo --reviewer "计划审核人" --comment "范围和 Agent 选择已确认"
python run.py workflow-run interface-manager-demo --max-parallel 4
```

`manager-plan` 生成候选计划但不会批准。`workflow-run` 会从 DAG 中原子领取至多 `--max-parallel` 个无未批准依赖的工作项；每个结果仍单独停在人工确认门，只有批准后的产物才会释放下游节点。默认值 `--max-parallel 1` 保持原有串行行为。租约超时、重试次数和执行事件分别由 `--lease-seconds`、`--max-attempts` 和项目级 SQLite 状态库管理。

`software_interface_engineer` 已接入统一 Workflow，不再只登记工具名称。Manager 会在工作项 `parameters.operation` 中选择 `generate_overview`、`compare_overviews` 或 `analyze_package`；子 Agent 执行后统一产出 `software_interface_task_result`，并携带 Excel、JSON/JSONL 证据、SHA-256 和人工确认状态。可直接使用 `examples/workplan_software_interface_generation.json` 做无模型回归。
