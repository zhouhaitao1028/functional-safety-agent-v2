import { useEffect, useMemo, useRef, useState } from 'react'
import { Toaster, toast } from 'sonner'
import { Conversation } from '@/components/Conversation'
import { Composer } from '@/components/Composer'
import { Sidebar } from '@/components/Sidebar'
import { TopBar } from '@/components/TopBar'
import { KnowledgePage } from '@/components/KnowledgePage'
import { Button } from '@/components/ui/button'
import { Dialog, Input } from '@/components/ui/dialog'
import { useEventStream } from '@/hooks/use-event-stream'
import { useConsoleMutations, useHealth, useModels, useProjectDetail, useProjects, useQuickTasks } from '@/hooks/use-console-data'
import type { TaskMode } from '@/lib/types'

// Windows“复制文件地址”复制的路径自带引号，发送前剥掉
const cleanPath = (raw: string) =>
  raw
    .trim()
    .replace(/^["']+/, '')
    .replace(/["']+$/, '')
    .trim()

function useTheme() {
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    const stored = localStorage.getItem('fsa-theme')
    return stored === 'dark' ? 'dark' : 'light'
  })
  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    localStorage.setItem('fsa-theme', theme)
  }, [theme])
  return {
    theme,
    toggle: () => setTheme((value) => (value === 'light' ? 'dark' : 'light')),
  }
}

export default function App() {
  const { theme, toggle } = useTheme()
  const [activeProjectId, setActiveProjectId] = useState('')
  // 顶层视图：对话工作区 / 功能安全知识库（流程环节图谱）
  const [view, setView] = useState<'chat' | 'knowledge'>('chat')
  const [mode, setMode] = useState<TaskMode>({ kind: 'general' })
  const [model, setModel] = useState('deepseek-flash')
  const [request, setRequest] = useState('')
  const [inputPath, setInputPath] = useState('')
  const [secondaryPath, setSecondaryPath] = useState('')
  const [showSecondary, setShowSecondary] = useState(false)
  const [newChatOpen, setNewChatOpen] = useState(false)
  const [newProjectTitle, setNewProjectTitle] = useState('')
  const [deleteTargetId, setDeleteTargetId] = useState('')
  // 乐观气泡：发送瞬间即显示，待响应+refetch 返回后由真实消息接班
  const [optimisticRequest, setOptimisticRequest] = useState('')
  // 主页追溯类入口选择的预置请求模板，项目创建后回填输入框
  const [presetRequest, setPresetRequest] = useState('')

  const health = useHealth()
  const healthy = health.isSuccess
  const models = useModels(healthy).data ?? []
  const quickTasks = useQuickTasks(healthy).data ?? []
  const projects = useProjects().data ?? []
  const activeProject = useMemo(
    () => projects.find((row) => row.project_id === activeProjectId),
    [projects, activeProjectId],
  )
  const detail = useProjectDetail(activeProjectId, Boolean(activeProjectId)).data ?? null
  const streamLive = useEventStream(activeProjectId, Boolean(activeProjectId))

  const quickTask = mode.kind === 'quick' ? quickTasks.find((row) => row.id === mode.id) : undefined

  const mutations = useConsoleMutations(activeProjectId)
  const busy =
    mutations.managerPlan.isPending ||
    mutations.runQuickTask.isPending ||
    mutations.reviewPlan.isPending ||
    mutations.runWorkflow.isPending ||
    mutations.reviewResult.isPending ||
    mutations.createProject.isPending ||
    mutations.archiveProject.isPending

  // 执行状态绑定到发起任务的项目：切换到其他对话时不显示进度条/执行中
  const [busyProjectId, setBusyProjectId] = useState<string | null>(null)
  const activeProjectIdRef = useRef('')
  activeProjectIdRef.current = activeProjectId
  useEffect(() => {
    if (busy) {
      setBusyProjectId((prev) => prev ?? activeProjectIdRef.current)
    } else {
      setBusyProjectId(null)
    }
  }, [busy])
  const projectBusy = busy && busyProjectId === activeProjectId

  // quick task 提供默认输入路径且当前为空时自动填充
  useEffect(() => {
    if (mode.kind === 'quick' && quickTask?.default_input_path && !inputPath.trim()) {
      setInputPath(quickTask.default_input_path)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode.kind, quickTask?.id])

  // 选中历史项目后沿用计划使用的模型
  useEffect(() => {
    const plannedModel = detail?.workplan?.provenance?.model
    if (plannedModel && models.some((item) => item.id === plannedModel)) {
      setModel(plannedModel)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeProjectId, detail?.workplan?.provenance?.model])

  // 切换项目时恢复任务类型：快速任务项目回到对应子 Agent，避免串台成通用任务
  const loadedProjectRef = useRef('')
  useEffect(() => {
    if (!activeProjectId || !detail) return
    if (loadedProjectRef.current === activeProjectId) return
    loadedProjectRef.current = activeProjectId
    // 尚未生成计划的新项目不做恢复，保留入口（主页任务卡片）选择的任务类型
    if (!detail.workplan) return
    const source = detail.workplan.provenance?.source
    setMode(
      source === 'quick_task_router'
        ? { kind: 'quick', id: 'interface_generation' }
        : { kind: 'general' },
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeProjectId, detail])

  // 对比任务缺旧版时后端在 needs_input 消息里给出候选，经 SSE 到达后自动填入
  const messageCount = detail?.messages?.length ?? 0
  useEffect(() => {
    const last = detail?.messages?.[messageCount - 1]
    const suggestion = last?.metadata?.suggested_secondary as string | undefined
    if (
      last?.role === 'assistant' &&
      last.metadata?.state === 'needs_input' &&
      suggestion &&
      !secondaryPath.trim()
    ) {
      setSecondaryPath(suggestion)
      setShowSecondary(true)
      toast.info('已根据上一步产物填入候选旧版总览表，点击发送即可确认')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messageCount])

  // 失败消息一键重试：把触发任务的原话填回输入框
  const handleRetryRequest = (text: string) => {
    setRequest(text)
    toast('已填回输入框，可补充或修改后重新发送')
  }

  const statePill = useMemo(() => {
    const workItems = detail?.status?.work_items ?? []
    const lastMessage = detail?.messages?.[detail.messages.length - 1]
    const lastState =
      lastMessage?.role === 'assistant' ? lastMessage.metadata?.state : undefined
    // 后台异步执行：mutation 已返回，但最新消息仍是 executing 时任务仍在跑
    if (projectBusy || lastState === 'executing') {
      return { label: '任务执行中…', tone: 'busy' as const }
    }
    const allApproved = Boolean(
      workItems.length &&
        workItems.every(
          (item) => (item.execution_state ?? item.state) === 'approved',
        ),
    )
    const waitingResult = workItems.some((item) => item.execution_state === 'waiting_for_approval')
    const failed = workItems.some((item) =>
      ['failed', 'retry_wait'].includes(item.execution_state ?? ''),
    )
    const waitingInput = ['needs_input', 'needs_clarification'].includes(lastState ?? '')
    if (waitingInput) return { label: '等待补充信息', tone: 'warn' as const }
    if (failed) return { label: '执行失败', tone: 'bad' as const }
    if (allApproved) return { label: '已完成', tone: 'ok' as const }
    if (waitingResult) return { label: '等待结果确认', tone: 'busy' as const }
    // 有计划但任务项全部未开始：在等人工批准，不是在执行
    const planWaitingApproval = Boolean(
      detail?.workplan &&
        workItems.length &&
        workItems.every((item) => (item.execution_state ?? item.state) === 'pending'),
    )
    if (planWaitingApproval) return { label: '等待计划批准', tone: 'warn' as const }
    if (detail?.workplan) return { label: '处理中', tone: 'busy' as const }
    return { label: '未开始', tone: 'idle' as const }
  }, [projectBusy, detail])

  const resetComposer = () => {
    setRequest('')
    setSecondaryPath('')
    setShowSecondary(false)
  }

  const handleSend = () => {
    if (!activeProjectId) {
      setNewChatOpen(true)
      return
    }
    if (!request.trim()) {
      toast.error('请描述任务')
      return
    }
    // 通用任务与快速任务一致：只写一句话即可，路径可由后端从消息文本智能提取
    if (mode.kind === 'general') {
      const path = cleanPath(inputPath)
      const sentRequest = request.trim()
      setOptimisticRequest(sentRequest)
      mutations.managerPlan.mutate(
        {
          request: sentRequest,
          inputs: path
            ? [{ id: 'IN-USER-SOURCE', type: '', location: path }]
            : [],
          include_draft_knowledge: false,
          max_tool_rounds: 8,
          model,
        },
        {
          onSuccess: (response) => {
            setRequest('')
            setOptimisticRequest('')
            if ('state' in response && response.state === 'needs_input') {
              toast.info(response.message ?? '请在对话中粘贴输入文件或目录的路径')
              return
            }
            toast.success('Manager 已生成计划，请确认后运行')
          },
          onError: (error) => {
            setOptimisticRequest('')
            toast.error(error.message)
          },
        },
      )
      return
    }
    const sentRequest = request.trim()
    setOptimisticRequest(sentRequest)
    mutations.runQuickTask.mutate(
      {
        request: sentRequest,
        input_path: cleanPath(inputPath),
        secondary_input_path: cleanPath(secondaryPath),
        max_evidence: 1000,
        model,
      },
      {
        onSuccess: (response) => {
          // 到这里时 mutation 级 refetch 已完成，真实用户消息已入库显示
          setOptimisticRequest('')
          // 异步执行：请求立即返回，进度/结果经 SSE 消息推送
          if (response.state === 'executing') {
            resetComposer()
            toast.success('任务已开始执行，执行期间可以继续对话')
            return
          }
          if (response.state === 'busy') {
            toast.warning(response.message ?? '上一个任务仍在执行中')
            return
          }
          if (response.state === 'needs_input' || response.state === 'needs_clarification') {
            resetComposer()
            setShowSecondary(response.required_fields?.includes('secondary_input_path') ?? false)
            toast.info(response.message ?? '需要补充信息，请查看对话提示')
            return
          }
          if (response.state === 'answered') {
            resetComposer()
            return
          }
          if (response.state === 'failed') {
            resetComposer()
            toast.error(response.message ?? '确定性工具执行失败')
            return
          }
          resetComposer()
          toast.success('意图已识别，确定性任务执行完成，等待结果确认')
        },
        onError: (error) => {
          setOptimisticRequest('')
          toast.error(error.message)
        },
      },
    )
  }

  const handleApprovePlan = () => {
    mutations.reviewPlan.mutate(
      {
        reviewer: '页面用户',
        decision: 'approved',
        comment: '已确认 Agent 选择和工作计划',
      },
      {
        onSuccess: () => {
          const plannedModel =
            (detail?.workplan?.provenance?.model as string | undefined) ?? model
          mutations.runWorkflow.mutate(
            {
              max_findings: 1000,
              max_parallel: 4,
              lease_seconds: 120,
              max_attempts: 3,
              max_tool_rounds: 8,
              model: plannedModel,
            },
            {
              onSuccess: () => toast.success('Agent 执行完成，请检查结果'),
              onError: (error) => toast.error(error.message),
            },
          )
        },
        onError: (error) => toast.error(error.message),
      },
    )
  }

  const handleApproveResult = () => {
    const workItems = detail?.status?.work_items ?? []
    const item =
      workItems.find((row) => row.artifact_exists && row.approval !== 'approved') ??
      workItems.find((row) => row.artifact_exists)
    if (!item) {
      toast.error('没有可确认的结果')
      return
    }
    mutations.reviewResult.mutate(
      {
        itemId: item.id,
        body: {
          reviewer: '页面用户',
          decision: 'approved',
          comment: '已检查结构化结果和证据',
        },
      },
      {
        onSuccess: () => toast.success('结果已确认'),
        onError: (error) => toast.error(error.message),
      },
    )
  }

  const handleCreateProject = () => {
    const title = newProjectTitle.trim()
    if (!title) {
      toast.error('请输入项目名称')
      return
    }
    mutations.createProject.mutate(title, {
      onSuccess: (created) => {
        setActiveProjectId(created.project_id)
        setNewChatOpen(false)
        setNewProjectTitle('')
        resetComposer()
        setInputPath('')
        if (presetRequest.trim()) {
          setRequest(presetRequest.trim())
          setPresetRequest('')
          toast.info('已填入任务模板，补充文件路径后发送即可')
        } else {
          toast.success('新对话已创建')
        }
      },
      onError: (error) => toast.error(error.message),
    })
  }

  const handleArchive = () => {
    if (!deleteTargetId) return
    const removedId = deleteTargetId
    mutations.archiveProject.mutate(removedId, {
      onSuccess: () => {
        setDeleteTargetId('')
        if (activeProjectId === removedId) {
          setActiveProjectId('')
          resetComposer()
          setInputPath('')
        }
        toast.success('对话已移到回收站')
      },
      onError: (error) => toast.error(error.message),
    })
  }

  const deleteTarget = projects.find((row) => row.project_id === deleteTargetId)

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar
        projects={projects}
        activeProjectId={activeProjectId}
        healthText={
          healthy
            ? `FastAPI ${health.data?.version ?? ''} · 服务正常`
            : health.isError
              ? '服务连接失败 · 正在重连'
              : '正在连接服务…'
        }
        healthy={healthy}
        knowledgeActive={view === 'knowledge'}
        onSelect={(projectId) => {
          setView('chat')
          setActiveProjectId(projectId)
        }}
        onNewChat={() => setNewChatOpen(true)}
        onOpenKnowledge={() => setView('knowledge')}
        onHome={() => {
          setView('chat')
          setActiveProjectId('')
          setPresetRequest('')
        }}
        onDelete={setDeleteTargetId}
      />
      <div className="flex min-w-0 flex-1 flex-col">
        {view === 'knowledge' ? (
          <KnowledgePage onBack={() => setView('chat')} />
        ) : (
          <>
        <TopBar
          title={activeProject?.title ?? '新对话'}
          subtitle={
            activeProject
              ? '项目和执行状态会作为对话内容保存'
              : '选择任务类型，然后描述你希望 Agent 完成的工作'
          }
          stateLabel={statePill.label}
          stateTone={statePill.tone}
          busy={projectBusy}
          streamLive={streamLive}
          theme={theme}
          onToggleTheme={toggle}
        />
        <Conversation
          hasProject={Boolean(activeProjectId)}
          projectId={activeProjectId}
          projectTitle={activeProject?.title ?? ''}
          detail={detail}
          busy={projectBusy}
          optimisticMessage={optimisticRequest}
          onStarter={(task, preset) => {
            setMode(task === 'general' ? { kind: 'general' } : { kind: 'quick', id: task })
            // 追溯类子入口可能携带预置请求模板，项目创建后回填输入框
            setPresetRequest(preset ?? '')
            setNewChatOpen(true)
          }}
          onApprovePlan={handleApprovePlan}
          onRunWorkflow={handleApprovePlan}
          onApproveResult={handleApproveResult}
          onRetryRequest={handleRetryRequest}
        />
        <Composer
          projectId={activeProjectId}
          mode={mode}
          quickTask={quickTask}
          quickTasks={quickTasks}
          models={models}
          model={model}
          busy={projectBusy}
          request={request}
          inputPath={inputPath}
          secondaryPath={secondaryPath}
          showSecondary={showSecondary}
          onModelChange={setModel}
          onModeChange={setMode}
          onToggleSecondary={() => setShowSecondary((value) => !value)}
          onRequestChange={setRequest}
          onInputPathChange={setInputPath}
          onSecondaryPathChange={setSecondaryPath}
          onSend={handleSend}
        />
          </>
        )}
      </div>

      <Dialog
        open={newChatOpen}
        onClose={() => {
          setNewChatOpen(false)
          setPresetRequest('')
        }}
        title="新建对话"
        description="项目文件夹将使用你输入的项目名称，同名项目会自动添加序号。"
        footer={
          <>
            <Button variant="secondary" onClick={() => setNewChatOpen(false)}>
              取消
            </Button>
            <Button
              disabled={mutations.createProject.isPending}
              onClick={handleCreateProject}
            >
              创建
            </Button>
          </>
        }
      >
        <label className="mb-1.5 block text-xs font-medium" htmlFor="new-project-title">
          项目名称
        </label>
        <Input
          id="new-project-title"
          maxLength={200}
          autoFocus
          placeholder="例如：空调控制器接口总览"
          value={newProjectTitle}
          onChange={(event) => setNewProjectTitle(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.nativeEvent.isComposing) {
              event.preventDefault()
              handleCreateProject()
            }
          }}
        />
      </Dialog>

      <Dialog
        open={Boolean(deleteTargetId)}
        onClose={() => setDeleteTargetId('')}
        title="删除对话及全部材料"
        description={`将永久删除“${deleteTarget?.title ?? ''}”的对话记录、计划、结果产物、上传文件和审计事件，不可恢复。`}
        footer={
          <>
            <Button variant="secondary" onClick={() => setDeleteTargetId('')}>
              取消
            </Button>
            <Button
              variant="destructive"
              disabled={mutations.archiveProject.isPending}
              onClick={handleArchive}
            >
              永久删除
            </Button>
          </>
        }
      />

      <Toaster position="top-center" richColors closeButton />
    </div>
  )
}
