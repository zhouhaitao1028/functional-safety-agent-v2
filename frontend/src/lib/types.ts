export interface HealthInfo {
  status: string
  version: string
  project_root: string
}

export interface ModelOption {
  id: string
  title: string
  description: string
  recommended: boolean
}

export interface QuickTask {
  id: string
  title: string
  description: string
  agent_id: string
  input_label: string
  input_type: string
  plan_approval: string
  result_approval: string
  default_input_path?: string
}

export interface ProjectSummary {
  project_id: string
  title: string
  created_at: string
  deleted_at?: string
}

export interface MessageMetadata {
  channel?: string
  state?: string
  model?: string
  intent?: IntentInfo
  workplan_sha256?: string
  result_summary?: Record<string, unknown>
  result_artifacts?: { type: string; path: string }[]
  [key: string]: unknown
}

export interface IntentInfo {
  operation?: string
  summary?: string
  confidence?: number
  model?: string
  [key: string]: unknown
}

export interface ConversationMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  metadata?: MessageMetadata
}

export interface WorkItemPlan {
  id: string
  title: string
  agent_id: string
  depends_on: string[]
  input_refs: string[]
  parameters: Record<string, unknown>
  output: { id: string; type: string; artifact: string }
  approval_required: boolean
}

export interface WorkplanRecord {
  data: {
    id: string
    goal: string
    work_items: WorkItemPlan[]
    [key: string]: unknown
  }
  provenance: {
    source?: string
    model?: string
    intent?: IntentInfo
    [key: string]: unknown
  }
  sha256?: string
}

export interface WorkItemStatus {
  id: string
  title: string
  agent_id: string
  execution_state?: string
  state?: string
  approval?: string
  ready?: boolean
  artifact_exists?: boolean
  [key: string]: unknown
}

export interface ProjectStatus {
  workplan_approval: string
  work_items: WorkItemStatus[]
  [key: string]: unknown
}

export interface ArtifactRecord {
  data: {
    operation?: string
    summary?: Record<string, unknown>
    artifacts?: { type: string; path: string }[]
    [key: string]: unknown
  }
  [key: string]: unknown
}

export interface QuickTaskResponse {
  state: string
  quick_task?: QuickTask
  interpretation?: IntentInfo
  required_fields?: string[]
  suggested_secondary?: string
  message?: string
  [key: string]: unknown
}

export type TaskMode = { kind: 'general' } | { kind: 'quick'; id: string }

// 流程环节知识图谱（config/stages.json + skills/<skill_id>/SKILL.md）
export interface KnowledgeStage {
  id: string
  name: string
  order: number
  description: string
  agent_id: string
  skill_id: string
  produces: string
  upstream: string[]
  downstream: string[]
  guide_count: number
  guide_ids: string[]
  rag_document_count: number
  skill_status: 'authored' | 'template'
}

// 环节关联的公司模板文档（RAG 库引用层：draft 只展示引用，不注入正文）
export interface RagDocumentRef {
  id: string
  title: string
  status: string
  kind: string
  wiki_url: string
}

export interface StageSkill {
  skill_id: string
  name: string
  description: string
  uses: string[]
  pitfalls: string[]
  jumps: string[]
  body: string
  source_path: string
  is_template: boolean
}

export interface GuideSummary {
  id: string
  title: string
  kind: string
}

export interface KnowledgeStageDetail {
  id: string
  name: string
  order: number
  description: string
  agent_id: string
  skill_id: string
  produces: string
  upstream: string[]
  downstream: string[]
  skill: StageSkill
  guides: GuideSummary[]
  rag_documents: RagDocumentRef[]
}

export interface GuideDetail extends GuideSummary {
  file: string
  stage_id: string
  content: string
}

export interface StageNeighborPack {
  direction: 'upstream' | 'downstream'
  id: string
  name: string
  produces: string
  guide_ids: string[]
}

export interface KnowledgeContextPack {
  stage: Omit<KnowledgeStage, 'guide_count' | 'guide_ids' | 'skill_status'>
  skill: StageSkill
  guides: GuideDetail[]
  upstream: StageNeighborPack[]
  downstream: StageNeighborPack[]
  rag_documents: RagDocumentRef[]
  skill_coverage_note: string
}

// 追溯确认线知识供给：环节链 + 契约 skill + 确定性工具清单
export interface TraceStage {
  id: string
  name: string
  order: number
  description: string
  tools: { id: string; mcp_name: string; description: string }[]
  skill_refs: string[]
}

export interface TraceabilityKnowledge {
  task: string
  skill: {
    name: string
    description: string
    source_path: string
    body: string
  }
  trace_stages: TraceStage[]
  mcp_tools: { name: string; description: string }[]
  algorithms: { id: string; name: string; description: string }[]
  principle: string
}

// Manager 计划成功返回 WorkplanRecord；缺输入线索时返回 needs_input 提示
export type ManagerPlanResponse =
  | WorkplanRecord
  | { state: 'needs_input'; required_fields?: string[]; message?: string }
