# React Web 控制台迁移方案

## Context

当前 Web 控制台是 419 行单文件原生 HTML/JS（[index.html](../../../src/fs_agent_v2/web/static/index.html)）：内嵌 CSS + 模板字符串 innerHTML 渲染，无构建、无自动刷新，视觉呆板。用户要求迁移到现代前端栈并提升视觉体验。

**已确认的选型**：React 18 + TypeScript + Vite + Tailwind CSS 3.4 + shadcn/ui + TanStack Query + sonner + lucide-react；构建产物由 FastAPI 伺服（保持 `python run.py web` 一键启动）；新增 SSE 实时推送执行事件。

**关键事实**（已核实）：
- API 全 JSON、无鉴权、仅回环地址，前端可直连；开发期用 Vite 代理，无需 CORS。
- `workflow_events` 表有全局自增 `sequence` + `(project_id, sequence)` 索引（execution_store.py L156-170），SSE 用"按 sequence 增量轮询"实现，Windows 下无需 aiosqlite/更新钩子。
- `tests/test_web.py` L64-95 有对旧 HTML 字符串的脆弱断言，必须重写。
- 仓库无任何 node 文件，.gitignore 未忽略 node_modules。

## 1. 前端工程（frontend/）

- `npm create vite@latest frontend -- --template react-ts`
- 依赖仅 4 个：`@tanstack/react-query`、`tailwindcss@3.4`、`sonner`、`lucide-react`。不装 react-router（单页对话式，`activeProjectId` 即路由语义）、不装 redux/zustand。
- shadcn/ui（PowerShell）：`npx shadcn@latest init -d` 跳过交互；`npx shadcn@latest add button card dialog select textarea badge scroll-area separator`。组件源码在 `frontend/src/components/ui/` 可直接改。
- `vite.config.ts`：`base: "/static/dist/"`、`build.outDir: "../src/fs_agent_v2/web/static/dist"`、`emptyOutDir: true`、`server.proxy: { "/api": "http://127.0.0.1:8000" }`。资产走现有 `/static` mount，不加新挂载。
- 脚本：`dev` / `build`（`tsc -b && vite build`）/ `preview`。
- Tailwind 3.4（shadcn 兼容最稳、Windows 零原生依赖）；把现有 design token（`--primary: #2559d6` 等）映射进 tailwind.config + index.css，深浅双主题用 shadcn CSS 变量。

## 2. 后端改造（src/fs_agent_v2/web/app.py）

- **dist 缺席回退**：`create_app` 检测 `static/dist/index.html`，不存在则 `/` 仍走旧页面（纯 Python 环境可用）。
- **SPA fallback**：所有路由之后注册 catch-all `GET /{full_path:path}`（include_in_schema=False）：`api/`、`static/` 前缀 404；命中 dist 内真实文件走 FileResponse，否则回 `dist/index.html`（`Cache-Control: no-store`）。另加 `GET /legacy` 直出旧页面。
- **SSE 端点** `GET /api/projects/{project_id}/events/stream`：`StreamingResponse(text/event-stream)`，异步生成器循环——`run_in_threadpool` 调新增 `ExecutionStore.events_since(project_id, after_sequence)`（`SELECT ... WHERE project_id=? AND sequence>? ORDER BY sequence`），每行 yield `id:/event:/data:` 帧；无事件 `await asyncio.sleep(1)`；每 15 次空转发 `: ping` 心跳；支持 `Last-Event-ID` 头断点续传；`request.is_disconnected()` 时退出。

## 3. 其他文件

- `ExecutionStore.events_since()`：新增于 src/fs_agent_v2/storage/execution_store.py（参考现有 `events()` L1130）。
- `pyproject.toml` L28-29 package-data：加 `static/dist/*`、`static/dist/assets/*`。
- `.gitignore`：加 `node_modules/`、`frontend/dist`（若在 frontend 下产生）。
- 旧 `index.html` 改名 `static/legacy.html` 保留，PR4 验收后删除。
- dist 构建产物提交入库，保证 clone 后 `python run.py web` 即可用。

## 4. 组件拆分与状态管理

- **TanStack Query**：服务端状态全入 Query——`projects`、`models`、`quick-tasks`、`["project", id]`（聚合 messages+workplan+status+artifact）；提交用 `useMutation` + `invalidateQueries`。本地 UI 态（composer 输入、dialog 开关）用 useState。
- 组件树：`App` → `Sidebar`（品牌/新对话/HistoryList/健康灯）、`TopBar`（标题/StatePill）、`Conversation`（`Welcome`+`StarterCard` 或 `MessageBubble[]`+`PlanCard`+`ResultCard`）、`Composer`（输入行/高级设置折叠）、`NewChatDialog`/`DeleteChatDialog`、`Toaster`。对应旧版函数：`renderHistory→HistoryList`、`conversationMessages→MessageBubble`、`planMessage→PlanCard`、`resultMessage→ResultCard`。
- **视觉升级**（解决"太呆"）：渐变 hero 欢迎页、卡片 hover 位移阴影、非对称气泡圆角、语义色状态 pill、深浅双主题。

## 5. SSE 前端接入

`frontend/src/hooks/use-event-stream.ts`：`new EventSource(/api/projects/${id}/events/stream)`，onmessage 内 200ms 节流后 `invalidateQueries(["project", id])`（不区分事件类型，统一失效）；onerror 交给浏览器自动重连（自动带 Last-Event-ID）；切换项目/卸载时 close。健康灯复用连接状态 + 低频 `/api/health` 探测。

## 6. 测试策略

- 重写 tests/test_web.py L64-95：删 HTML 字符串断言，改为 (a) 路由注册断言含 `/events/stream` 与 `/legacy`；(b) TestClient 验证 SSE 端点返回 `text/event-stream` 且能收到注入事件；(c) 保留"dist 缺席时 / 可用"回退断言。其余 8 个用例不动。
- **暂不引入 vitest**：前端是薄胶水层，tsc 类型检查 + `npm run build` 作为提交门槛即可。
- E2E：`npm run build` → `python run.py web` → 浏览器走通"新建对话→发起任务→批准计划→并行执行（观察 SSE 实时刷新）→结果确认"。

## 7. 实施步骤

1. **PR1 后端**：`events_since()` + SSE 端点 + catch-all fallback + `/legacy` + 重写 test_web.py（旧页不受影响）
2. **PR2 前端脚手架**：frontend/ 初始化、Tailwind + shadcn + 主题 token、vite.config、可构建
3. **PR3 组件迁移**：lib/api.ts、types.ts、hooks、全部组件，功能对齐旧版
4. **PR4 收尾**：SSE 接入 + 视觉打磨 + 端到端验证 + 删 legacy.html

## 验证

- `cd frontend && npm run build`（tsc + vite 通过）
- `python -m unittest tests.test_web -v`
- `python run.py web` → 浏览器完整走通审批流，执行期间观察 SSE 实时刷新
- 删除 dist 后启动，确认回退到 legacy 页面可用
