# FastAPI 本地测试台

## 启动

```powershell
cd "C:\Users\周海韬\Desktop\什么活都有的文件夹\智己实习\functional-safety-agent-v2"
python run.py web
```

- 控制台：`http://127.0.0.1:8000/`
- OpenAPI：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/api/health`

只有明确需要局域网访问时才使用 `--host 0.0.0.0`。当前版本没有用户认证，默认的 `127.0.0.1` 更适合作为开发测试台。

## 前端（React）

控制台前端是 `frontend/` 下的 React 18 + TypeScript + Vite + Tailwind CSS 工程，构建产物提交在 `src/fs_agent_v2/web/static/dist/`，由 FastAPI 直接伺服：

- `GET /` 返回 React 控制台；dist 缺失时自动回退旧版单文件页面
- `GET /legacy` 始终返回旧版单文件页面
- 未知 GET 路径回退到 `dist/index.html`（SPA fallback）

前端开发（需要后端已启动）：

```powershell
cd frontend
npm install
npm run dev   # http://localhost:5173 ，/api 代理到 127.0.0.1:8000
npm run build # 产物输出到 src/fs_agent_v2/web/static/dist/
```

技术栈：TanStack Query（服务端状态）、sonner（toast）、lucide-react（图标）、shadcn 风格组件（手写在 `src/components/ui/`）。执行期间页面通过 SSE 订阅工作流事件并自动刷新。

## 页面流程

1. 左侧以“历史对话”展示已有项目，不再暴露项目 ID 和底层目录。
2. 点击“新对话”，只输入便于识别的项目名称；系统自动生成安全项目 ID。
3. 在输入区填写任务描述，再通过底部工具栏选择任务类型和模型：
   - **通用任务**：可选择 `DeepSeek Flash` 或 `DeepSeek V4 Pro`。Manager 使用所选模型读取 Registry、检查输入、拆解工作计划；页面展示计划，用户点击“批准并运行”后才执行。后续需要大模型的 Engineer 也沿用该选择。
   - **快速任务**：使用后端快速任务注册表固定选择 Engineer Agent，不调用 Manager 做跨 Agent 规划；所选 DeepSeek 模型只把自然语言转换成受约束的业务操作和参数，随后由确定性工具执行。系统自动通过计划门，最后仍由用户确认交付结果。
4. Agent 的计划、状态、结构化摘要、输出文件和审批动作都作为对话消息展示。

当前注册的快速任务是“软件接口任务”，固定路由到 `software_interface_engineer`。模型可识别 `generate_overview`、`compare_overviews`、`analyze_package` 三种意图；文件读取、总览生成、版本对比和独立校验仍由确定性工具完成。对比任务可通过输入路径左侧的“+”补充第二个总览表；缺少第二个文件或用户目标不清楚时，Agent 会先返回一个澄清问题，不执行文件工具。

## API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/health` | 服务状态 |
| `GET` | `/api/agents` | Agent Registry 和自动执行能力 |
| `GET` | `/api/models` | 返回页面允许选择的 DeepSeek 模型元数据，不包含密钥 |
| `GET` | `/api/quick-tasks` | 列出聊天输入区可用的确定性快速任务 |
| `GET/POST` | `/api/projects` | 列出/创建项目 |
| `DELETE` | `/api/projects/{project_id}` | 将项目从历史对话移到回收站，不物理删除文件或 SQLite 记忆 |
| `POST` | `/api/projects/{project_id}/restore` | 恢复一个已移除的历史对话 |
| `GET` | `/api/projects/{project_id}/workplan` | 读取 Manager 计划 |
| `GET` | `/api/projects/{project_id}/messages` | 从 SQLite 读取项目短期对话记忆 |
| `GET` | `/api/projects/{project_id}/memory` | 查看当前工作记忆和已确认长期记忆 |
| `POST` | `/api/projects/{project_id}/manager-plan` | Manager 自动生成并提交候选计划 |
| `POST` | `/api/projects/{project_id}/software-interface-plan` | 创建严格的单软件接口 Agent 测试计划，不调用 Manager 模型 |
| `POST` | `/api/projects/{project_id}/quick-tasks/{task_id}/run` | 模型识别业务操作，固定路由到 Engineer，再执行确定性工具 |
| `POST` | `/api/projects/{project_id}/approvals/workplan` | 批准或驳回计划 |
| `GET` | `/api/projects/{project_id}/status` | 工作项和门禁状态 |
| `POST` | `/api/projects/{project_id}/workflow/run` | 按请求并发度执行就绪 Agent |
| `GET` | `/api/projects/{project_id}/items/{item_id}/artifact` | 读取工作项产物 |
| `POST` | `/api/projects/{project_id}/items/{item_id}/approval` | 批准或驳回工作项产物 |
| `GET` | `/api/projects/{project_id}/events` | 审计事件 |
| `GET` | `/api/projects/{project_id}/execution-events` | Worker 领取、心跳、重试和提交事件 |
| `GET` | `/api/projects/{project_id}/events/stream` | SSE 实时推送工作流事件，按 `sequence` 增量发送，支持 `Last-Event-ID` 断点续传 |

Manager 和 Engineer 的耗时操作在线程池中执行，不阻塞 FastAPI 事件循环。业务异常返回结构化 JSON，例如：

```json
{
  "error": "GateError",
  "detail": "stage workplan is not approved: missing"
}
```

运行请求可设置 `max_parallel`、`lease_seconds` 和 `max_attempts`。默认 `max_parallel=1` 与旧版串行行为兼容；设置为大于 1 时，未批准的上游产物仍会阻塞下游，但不会阻塞同一层的其他独立节点。

页面只接受后端白名单中的模型标识，当前为 `deepseek-flash` 和 `deepseek-v4-pro`。通用任务把选择写入 Manager 计划；软件接口快速任务把模型输出的结构化意图、模型名和用户原问题写入计划 provenance。模型选择也进入工作项幂等指纹，使同一任务切换模型后不会错误复用旧结果。API 地址和密钥仍由本地运行时配置统一管理，浏览器不会获得密钥。

软件接口对话使用三层 SQLite 记忆：最近对话作为短期上下文，待补信息和当前意图作为工作记忆，确认过的项目规则作为长期记忆。详细边界见 [Agent 记忆架构](MEMORY_ARCHITECTURE.md)。`events/conversation.jsonl` 仅作为可读审计投影和旧数据迁移来源。

历史对话右侧的删除按钮执行软删除：后端只在 `project.json` 写入 `deleted_at`，项目随即从历史列表隐藏，原始输入、交付物、审批、SQLite 记忆和审计事件均保留。需要恢复时调用 `/restore`；真正物理清理应作为单独的管理员操作实现。

## `traceability_validator` 使用边界

它不是每个 Agent 的通用后置步骤。当前实现只对 `tsr_specification` 与 `ssr_specification.parent_tsr_ids` 执行 TSR→SSR 的确定性引用校验，检查覆盖缺口、悬空引用和重复链接。只有同时存在上游/下游结构化产物、稳定 ID 和明确覆盖规则时，Manager 才应安排它。

软件接口任务不调用该 Validator。`software_interface_engineer` 在生成总览时已执行字段、数据行和 AA/AB 一致性独立校验；新旧版本对比也会生成输入哈希及行级差异证据。重复再接通用追溯 Validator 既不匹配输入契约，也不能增加有效验证。

## 安全边界

- 网站不提供任意命令执行接口。
- 项目 ID 继续使用后端安全字符校验。
- 产物读取通过工作计划中的工作项解析，不接受任意文件路径。
- Manager 输入路径只交给已有的只读输入检查和业务 Agent。
- Manager 调用会把任务描述和输入元数据发给配置的模型服务；不要提交密钥或无关敏感数据。
- 审批绑定产物 SHA-256，产物被修改后原审批自动失效。
