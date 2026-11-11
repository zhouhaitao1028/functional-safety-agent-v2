import { useEffect, useRef } from 'react'
import { Check } from 'lucide-react'
import { Card } from '@/components/ui/card'
import { ActivityBlock } from '@/components/ActivityBlock'
import { TaskProgress } from '@/components/TaskProgress'
import { MessageBubble } from '@/components/MessageBubble'
import { PlanCard } from '@/components/PlanCard'
import { ResultCard } from '@/components/ResultCard'
import { Welcome } from '@/components/Welcome'
import type { ProjectDetail } from '@/hooks/use-console-data'
import type { ConversationMessage } from '@/lib/types'

interface ConversationProps {
  hasProject: boolean
  projectId: string
  projectTitle: string
  detail: ProjectDetail | null
  busy: boolean
  optimisticMessage: string
  onStarter: (task: 'general' | 'interface_generation', presetRequest?: string) => void
  onApprovePlan: () => void
  onRunWorkflow: () => void
  onApproveResult: () => void
  onRetryRequest: (text: string) => void
}

// 执行中的过程消息由 ActivityBlock 呈现；completed 仅在它是最新一条时隐藏
// （由结果卡呈现），历史上的 completed 消息保留为气泡，避免旧任务结果消失
function isProcessMessage(message: ConversationMessage, isLatest: boolean): boolean {
  if (message.role !== 'assistant') return false
  const state = message.metadata?.state ?? ''
  if (state === 'executing') return true
  return state === 'completed' && isLatest
}

export function Conversation({
  hasProject,
  projectId,
  projectTitle,
  detail,
  busy,
  optimisticMessage,
  onStarter,
  onApprovePlan,
  onRunWorkflow,
  onApproveResult,
  onRetryRequest,
}: ConversationProps) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [detail?.messages.length, detail?.artifact, detail?.workplan, hasProject])

  if (!hasProject) {
    return <Welcome onStarter={onStarter} />
  }

  const status = detail?.status ?? null
  const plan = detail?.workplan ?? null
  const messages = detail?.messages ?? []
  const artifact = detail?.artifact ?? null
  const workItems = status?.work_items ?? []

  const allApproved = Boolean(
    workItems.length &&
      workItems.every((item) => (item.execution_state ?? item.state) === 'approved'),
  )
  const lastMessage = messages[messages.length - 1]
  const lastState =
    lastMessage?.role === 'assistant' ? lastMessage.metadata?.state : undefined
  const waitingInput = ['needs_input', 'needs_clarification'].includes(lastState ?? '')
  const conversationalAnswer = lastState === 'answered'

  const visibleMessages = messages.filter(
    (message, index) => !isProcessMessage(message, index === messages.length - 1),
  )
  const executingMessage = [...messages]
    .reverse()
    .find((message) => message.metadata?.state === 'executing')
  const intent = executingMessage?.metadata?.intent
  const durationRaw = artifact?.data?.summary?.duration_seconds
  const durationSeconds = typeof durationRaw === 'number' ? durationRaw : undefined

  // 执行中：mutation 进行中，或后台任务仍在跑（最新消息为 executing）且尚无结果
  const running = (busy || lastState === 'executing') && !artifact
  const activityDone = Boolean(artifact)

  // 恢复的历史会话可能只有计划没有消息记录，此时显示计划目标
  const historicalGoal = plan ? plan.data.goal : ''

  const planCard = plan ? (
    <PlanCard
      plan={plan}
      status={status}
      busy={busy}
      onApprove={onApprovePlan}
      onRun={onRunWorkflow}
    />
  ) : null
  const resultCard = artifact ? (
    <ResultCard
      artifact={artifact}
      status={status}
      busy={busy}
      projectId={projectId}
      onApprove={onApproveResult}
    />
  ) : null

  // 失败消息的"重试"：取其前面最近一条用户消息原文填回输入框
  const retryTextFor = (index: number): string => {
    for (let i = index - 1; i >= 0; i -= 1) {
      if (visibleMessages[i].role === 'user') return visibleMessages[i].content
    }
    return ''
  }
  const renderMessage = (message: ConversationMessage, index: number) => (
    <MessageBubble
      key={message.id}
      message={message}
      projectId={projectId}
      onRetry={
        message.metadata?.state === 'failed'
          ? () => onRetryRequest(retryTextFor(index))
          : undefined
      }
    />
  )

  return (
    <div className="flex-1 space-y-5 overflow-y-auto px-6 py-6">
      <div className="mx-auto max-w-3xl space-y-5">
        <div className="flex">
          <div className="mr-3 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-secondary text-[11px] font-bold text-secondary-foreground">
            FS
          </div>
          <Card className="px-4 py-3 text-sm">
            <div className="text-[11px] text-muted-foreground">系统</div>
            <p className="mt-0.5">
              对话“{projectTitle}”已就绪。请选择任务类型并提交输入。
            </p>
          </Card>
        </div>

        {messages.length === 0 && historicalGoal ? (
          <div className="flex justify-end">
            <div className="max-w-[78%]">
              <div className="mb-1 text-right text-[11px] text-muted-foreground">
                历史任务
              </div>
              <div className="rounded-2xl rounded-br-sm bg-primary px-4 py-3 text-sm leading-6 text-primary-foreground shadow-sm">
                {historicalGoal}
              </div>
            </div>
          </div>
        ) : null}

        {waitingInput || conversationalAnswer ? (
          <>
            {planCard}
            {running ? (
              <>
                <ActivityBlock
                  phase="running"
                  intentSummary={intent?.summary}
                  confidence={intent?.confidence}
                />
                <TaskProgress active={running} />
              </>
            ) : null}
            {activityDone ? (
              <ActivityBlock phase="done" durationSeconds={durationSeconds} />
            ) : null}
            {resultCard}
            {visibleMessages.map(renderMessage)}
          </>
        ) : (
          <>
            {visibleMessages.map(renderMessage)}
            {planCard}
            {running ? (
              <>
                <ActivityBlock
                  phase="running"
                  intentSummary={intent?.summary}
                  confidence={intent?.confidence}
                />
                <TaskProgress active={running} />
              </>
            ) : null}
            {activityDone ? (
              <ActivityBlock phase="done" durationSeconds={durationSeconds} />
            ) : null}
            {resultCard}
          </>
        )}

        {/* 乐观气泡：发送后立即显示，不等响应 refetch（响应慢时也能即时反馈） */}
        {optimisticMessage ? (
          <div className="flex justify-end">
            <div className="max-w-[78%]">
              <div className="flex items-center justify-end gap-1.5 text-[11px] text-muted-foreground">
                正在发送
                <span className="flex gap-0.5">
                  <span className="h-1 w-1 animate-bounce rounded-full bg-muted-foreground/70 [animation-delay:0ms]" />
                  <span className="h-1 w-1 animate-bounce rounded-full bg-muted-foreground/70 [animation-delay:150ms]" />
                  <span className="h-1 w-1 animate-bounce rounded-full bg-muted-foreground/70 [animation-delay:300ms]" />
                </span>
              </div>
              <div className="mt-1 rounded-2xl rounded-br-sm bg-primary px-4 py-3 text-sm leading-6 text-primary-foreground opacity-80 shadow-sm">
                {optimisticMessage}
              </div>
            </div>
          </div>
        ) : null}

        {allApproved ? (
          <div className="flex items-center gap-2.5 pl-1 text-sm text-muted-foreground">
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-success/15 text-success">
              <Check className="h-3 w-3" />
            </span>
            <span>结果已通过人工确认，完整审计记录保存在当前项目中</span>
          </div>
        ) : null}

        <div ref={bottomRef} />
      </div>
    </div>
  )
}
