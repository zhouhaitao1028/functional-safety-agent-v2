import {
  Check,
  CircleAlert,
  CircleDashed,
  FileText,
  ListTodo,
  Loader2,
  Play,
  ShieldCheck,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { ProjectStatus, WorkplanRecord } from '@/lib/types'

const AGENT_LABELS: Record<string, string> = {
  software_interface_engineer: '软件接口工程师',
  manager_agent: 'Manager',
}

const OPERATION_LABELS: Record<string, string> = {
  generate_overview: '生成接口总览',
  compare_overviews: '对比新旧总览',
  analyze_package: '接口包分析',
}

interface PlanInput {
  id: string
  type: string
  location: string
}

function fileName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path
}

interface PlanCardProps {
  plan: WorkplanRecord
  status: ProjectStatus | null
  busy: boolean
  onApprove: () => void
  onRun: () => void
}

export function PlanCard({ plan, status, busy, onApprove, onRun }: PlanCardProps) {
  const data = plan.data
  const approval = status?.workplan_approval ?? 'missing'
  const quick = plan.provenance?.source === 'quick_task_router'
  const intent = plan.provenance?.intent
  const hasReadyItems = Boolean(status?.work_items?.some((item) => item.ready))
  const statusById = new Map(
    (status?.work_items ?? []).map((item) => [item.id, item]),
  )
  const inputs = (data.inputs as PlanInput[] | undefined) ?? []

  return (
    <Card className="border-l-4 border-l-primary px-5 py-4 animate-fade-up hover:shadow-md">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 pt-0.5">
          <ListTodo className="h-4 w-4 shrink-0 text-primary" />
          <span className="text-xs font-medium text-foreground/80">
            {quick ? '意图识别 · 确定性执行' : '执行计划'}
          </span>
        </div>
        <Badge
          variant={
            approval === 'approved' ? 'ok' : approval === 'rejected' ? 'bad' : 'warn'
          }
          className="shrink-0"
        >
          {approval === 'approved'
            ? '计划已批准'
            : approval === 'rejected'
              ? '计划已驳回'
              : '待人工批准'}
        </Badge>
      </div>

      <h3 className="mt-2 text-sm font-semibold leading-6">{data.goal}</h3>

      {approval !== 'approved' && approval !== 'rejected' && !quick ? (
        <div className="mt-2 flex items-center gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs font-medium text-amber-700 dark:text-amber-400">
          <CircleAlert className="h-3.5 w-3.5 shrink-0" />
          等待你的确认——点击下方「批准并运行」后 Agent 才会开始执行
        </div>
      ) : null}

      {intent?.summary ? (
        <p className="mt-2 rounded-lg border-l-2 border-l-primary/60 bg-muted/60 px-3 py-2 text-xs leading-5 text-foreground/90">
          理解结果：{intent.summary}
          {typeof intent.confidence === 'number'
            ? `（置信度 ${Math.round(intent.confidence * 100)}%）`
            : ''}
        </p>
      ) : null}

      {inputs.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {inputs.map((input) => (
            <span
              key={input.id}
              title={input.location}
              className="inline-flex max-w-full items-center gap-1.5 rounded-full border bg-muted/40 px-2.5 py-1 text-[11px] text-foreground/80"
            >
              <FileText className="h-3 w-3 shrink-0 text-primary" />
              <span className="truncate">{fileName(input.location)}</span>
            </span>
          ))}
        </div>
      ) : null}

      <div className="mt-4">
        {data.work_items.map((item, index) => {
          const itemStatus = statusById.get(item.id)
          const execution =
            itemStatus?.execution_state ?? itemStatus?.state ?? 'pending'
          const done = ['approved', 'completed'].includes(execution)
          const running = ['executing', 'running', 'in_progress'].includes(execution)
          const failed = ['failed', 'retry_wait'].includes(execution)
          const waitApproval = execution === 'waiting_for_approval'
          const operation = String(item.parameters?.operation ?? '')
          return (
            <div key={item.id} className="relative flex gap-3 pb-4 last:pb-0">
              {index < data.work_items.length - 1 ? (
                <span className="absolute left-[11px] top-7 h-[calc(100%-28px)] w-px bg-border" />
              ) : null}
              <span
                className={cn(
                  'z-10 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[11px] font-semibold',
                  done && 'border-success/40 bg-success/15 text-success',
                  running && 'border-primary/40 bg-primary/10 text-primary',
                  failed && 'border-destructive/40 bg-destructive/10 text-destructive',
                  !done && !running && !failed &&
                    'bg-muted text-muted-foreground',
                )}
              >
                {done ? (
                  <Check className="h-3.5 w-3.5" />
                ) : running ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : failed ? (
                  <CircleAlert className="h-3.5 w-3.5" />
                ) : (
                  index + 1
                )}
              </span>
              <div className="min-w-0 pt-0.5">
                <div className="text-sm font-medium leading-5">{item.title}</div>
                <div className="mt-0.5 text-xs text-muted-foreground">
                  {AGENT_LABELS[item.agent_id] ?? item.agent_id}
                  {operation && OPERATION_LABELS[operation]
                    ? ` · ${OPERATION_LABELS[operation]}`
                    : ''}
                  {done
                    ? ' · 已完成'
                    : running
                      ? ' · 执行中'
                      : failed
                        ? ' · 执行失败'
                        : waitApproval
                          ? ' · 等待结果确认'
                          : ''}
                </div>
              </div>
            </div>
          )
        })}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {/* missing/pending 都代表计划尚未批准，必须始终提供批准入口 */}
        {approval !== 'approved' && approval !== 'rejected' && !quick ? (
          <Button size="sm" disabled={busy} onClick={onApprove}>
            <ShieldCheck />
            批准并运行
          </Button>
        ) : null}
        {approval === 'approved' && hasReadyItems ? (
          <Button size="sm" disabled={busy} onClick={onRun} title="运行计划中已就绪的任务项">
            <Play />
            执行计划
          </Button>
        ) : null}
        {quick && approval !== 'approved' ? (
          <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
            <CircleDashed className="h-3.5 w-3.5" />
            意图已确认，确定性工具将自动执行
          </span>
        ) : null}
      </div>
    </Card>
  )
}
