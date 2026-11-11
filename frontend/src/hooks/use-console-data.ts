import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type {
  ArtifactRecord,
  ConversationMessage,
  GuideDetail,
  HealthInfo,
  KnowledgeContextPack,
  KnowledgeStage,
  KnowledgeStageDetail,
  TraceabilityKnowledge,
  ManagerPlanResponse,
  ModelOption,
  ProjectStatus,
  ProjectSummary,
  QuickTask,
  QuickTaskResponse,
  WorkplanRecord,
} from '@/lib/types'

export interface ProjectDetail {
  messages: ConversationMessage[]
  workplan: WorkplanRecord | null
  status: ProjectStatus | null
  artifact: ArtifactRecord | null
}

async function loadProjectDetail(projectId: string): Promise<ProjectDetail> {
  const encoded = encodeURIComponent(projectId)
  const [messages, workplan, status, artifact] = await Promise.allSettled([
    api<ConversationMessage[]>(`/api/projects/${encoded}/messages?limit=40`),
    api<WorkplanRecord>(`/api/projects/${encoded}/workplan`),
    api<ProjectStatus>(`/api/projects/${encoded}/status`),
    (async () => {
      const statusRow = await api<ProjectStatus>(`/api/projects/${encoded}/status`)
      const item = statusRow.work_items?.find((row) => row.artifact_exists)
      if (!item) return null
      return api<ArtifactRecord>(
        `/api/projects/${encoded}/items/${encodeURIComponent(item.id)}/artifact`,
      )
    })(),
  ])
  return {
    messages: messages.status === 'fulfilled' ? messages.value : [],
    workplan: workplan.status === 'fulfilled' ? workplan.value : null,
    status: status.status === 'fulfilled' ? status.value : null,
    artifact: artifact.status === 'fulfilled' ? artifact.value : null,
  }
}

export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: () => api<HealthInfo>('/api/health'),
    retry: 1,
    refetchInterval: 15000,
  })
}

export function useModels(enabled: boolean) {
  return useQuery({
    queryKey: ['models'],
    queryFn: () => api<ModelOption[]>('/api/models'),
    enabled,
  })
}

export function useQuickTasks(enabled: boolean) {
  return useQuery({
    queryKey: ['quick-tasks'],
    queryFn: () => api<QuickTask[]>('/api/quick-tasks'),
    enabled,
  })
}

export function useProjects() {
  return useQuery({
    queryKey: ['projects'],
    queryFn: () => api<ProjectSummary[]>('/api/projects'),
  })
}

export function useKnowledgeStages(enabled: boolean) {
  return useQuery({
    queryKey: ['knowledge-stages'],
    queryFn: () => api<KnowledgeStage[]>('/api/knowledge/stages'),
    enabled,
  })
}

export function useKnowledgeStage(stageId: string, enabled: boolean) {
  return useQuery({
    queryKey: ['knowledge-stage', stageId],
    queryFn: () =>
      api<KnowledgeStageDetail>(`/api/knowledge/stages/${encodeURIComponent(stageId)}`),
    enabled: enabled && Boolean(stageId),
  })
}

export function useKnowledgeGuide(guideId: string | null) {
  return useQuery({
    queryKey: ['knowledge-guide', guideId],
    queryFn: () => api<GuideDetail>(`/api/knowledge/guides/${encodeURIComponent(guideId!)}`),
    enabled: Boolean(guideId),
  })
}

export function useKnowledgeTraceability(enabled: boolean) {
  return useQuery({
    queryKey: ['knowledge-traceability'],
    queryFn: () => api<TraceabilityKnowledge>('/api/knowledge/traceability'),
    enabled,
  })
}

export function useKnowledgeContextPack(stageId: string | null) {
  return useQuery({
    queryKey: ['knowledge-context-pack', stageId],
    queryFn: () =>
      api<KnowledgeContextPack>(
        `/api/knowledge/stages/${encodeURIComponent(stageId!)}/context-pack`,
      ),
    enabled: Boolean(stageId),
  })
}

export function useProjectDetail(projectId: string, enabled: boolean) {
  return useQuery({
    queryKey: ['project', projectId],
    queryFn: () => loadProjectDetail(projectId),
    enabled: enabled && Boolean(projectId),
  })
}

export function useConsoleMutations(projectId: string) {
  const queryClient = useQueryClient()
  const invalidateProject = async () => {
    await queryClient.invalidateQueries({ queryKey: ['project', projectId] })
  }

  const createProject = useMutation({
    mutationFn: (title: string) =>
      api<ProjectSummary>('/api/projects', {
        method: 'POST',
        body: JSON.stringify({ title }),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['projects'] }),
  })

  const archiveProject = useMutation({
    mutationFn: (id: string) =>
      api<{ state: string }>(`/api/projects/${encodeURIComponent(id)}`, {
        method: 'DELETE',
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['projects'] }),
  })

  const managerPlan = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<ManagerPlanResponse>(`/api/projects/${encodeURIComponent(projectId)}/manager-plan`, {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: invalidateProject,
  })

  const runQuickTask = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<QuickTaskResponse>(
        `/api/projects/${encodeURIComponent(projectId)}/quick-tasks/interface_generation/run`,
        { method: 'POST', body: JSON.stringify(body) },
      ),
    onSuccess: invalidateProject,
  })

  const reviewPlan = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<unknown>(`/api/projects/${encodeURIComponent(projectId)}/approvals/workplan`, {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: invalidateProject,
  })

  const runWorkflow = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<unknown>(`/api/projects/${encodeURIComponent(projectId)}/workflow/run`, {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: invalidateProject,
  })

  const reviewResult = useMutation({
    mutationFn: ({ itemId, body }: { itemId: string; body: Record<string, unknown> }) =>
      api<unknown>(
        `/api/projects/${encodeURIComponent(projectId)}/items/${encodeURIComponent(itemId)}/approval`,
        { method: 'POST', body: JSON.stringify(body) },
      ),
    onSuccess: invalidateProject,
  })

  return {
    createProject,
    archiveProject,
    managerPlan,
    runQuickTask,
    reviewPlan,
    runWorkflow,
    reviewResult,
  }
}
