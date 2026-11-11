# 大模型自动知识工具调用

## 执行链路

```text
已批准 workplan 与上游产物
          ↓
加载 Agent 配置、Schema 和 Skill
          ↓
调用 Agent 独立模型 API
          ↓
模型返回 tool_calls
          ↓
后端执行 allowlist 只读工具
          ↓
Evidence Pack 作为 tool 消息返回模型
          ↓
模型返回结构化 JSON
          ↓
Schema、确定性计算、保存与人工审批
```

## 当前工具

- `knowledge_retrieve_evidence`：混合 RAG，返回带来源定位的证据包；
- `knowledge_graph`：查询知识、指导手册、模板和业务域关系；
- `artifact_list`：列出当前工作项允许读取的输入引用；
- `artifact_read`：读取一个允许的上游结构化产物；
- `agent_registry_list`：列出业务 Agent 能力，供后续 Manager 运行时使用。

模型不能指定任意文件路径、执行命令或扩大知识标签范围。草稿知识只有在调用方显式允许时才能返回。每次运行最多执行配置的工具轮数，默认 8 轮。

## 配置与运行

以失效分析 Agent 为例：

```powershell
$env:FS_AGENT_MALFUNCTION_BASE_URL = "https://example.com/v1"
$env:FS_AGENT_MALFUNCTION_API_KEY = "..."
$env:FS_AGENT_MALFUNCTION_MODEL = "model-name"

python run.py item-run demo-dynamic W02 --query "分析 AEB 功能失效"
```

真实密钥只放环境变量或密钥服务。模型生成的产物中会记录 Agent、Provider、模型、工具调用摘要和证据引用，但不会记录 API 密钥。
