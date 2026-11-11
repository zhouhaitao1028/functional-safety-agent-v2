import { Bot, Download, FileOutput, RotateCcw, User } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { ARTIFACT_TYPE_LABELS } from '@/components/ResultCard'
import { cn } from '@/lib/utils'
import type { ConversationMessage } from '@/lib/types'

function fileName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path
}

function downloadUrl(projectId: string, path: string): string {
  return `/api/projects/${encodeURIComponent(projectId)}/download?path=${encodeURIComponent(path)}`
}

function headingFor(state?: string): string {
  if (state === 'plan_created') return 'Manager 已生成计划'
  if (state === 'answered') return '接口 Engineer 答复'
  if (state === 'completed') return '任务已完成'
  if (state === 'failed') return '执行失败'
  if (state === 'executing') return '正在执行确定性任务'
  return '需要你补充信息'
}

interface MessageBubbleProps {
  message: ConversationMessage
  projectId: string
  onRetry?: () => void
}

export function MessageBubble({ message, projectId, onRetry }: MessageBubbleProps) {
  const meta = message.metadata ?? {}
  const isManager = meta.channel === 'manager'

  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[78%] animate-fade-up">
          <div className="mb-1 text-right text-[11px] text-muted-foreground">
            {isManager ? '通用任务' : '软件接口任务'}
          </div>
          <div className="rounded-2xl rounded-br-sm bg-primary px-4 py-3 text-sm leading-6 text-primary-foreground shadow-sm">
            {message.content}
          </div>
        </div>
        <div className="ml-3 mt-6 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-secondary text-secondary-foreground">
          <User className="h-4 w-4" />
        </div>
      </div>
    )
  }

  const intent = meta.intent
  const author = isManager
    ? `Manager${meta.model ? ` · ${meta.model}` : ''}`
    : `软件接口 Engineer${intent?.model ? ` · ${intent.model}` : ''}`
  const stateTint: Record<string, string> = {
    plan_created: 'border-l-primary',
    answered: 'border-l-success',
    completed: 'border-l-success',
    failed: 'border-l-destructive',
    executing: 'border-l-warning',
  }

  return (
    <div className="flex">
      <div className="mr-3 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-primary to-accent-foreground text-primary-foreground shadow-sm">
        <Bot className="h-4 w-4" />
      </div>
      <Card
        className={cn(
          'max-w-[78%] border-l-4 px-4 py-3 animate-fade-up',
          stateTint[meta.state ?? ''] ?? 'border-l-primary/30',
        )}
      >
        <div className="text-[11px] text-muted-foreground">{author}</div>
        <h3 className="mt-0.5 text-sm font-semibold">{headingFor(meta.state)}</h3>
        <p className="mt-1 text-sm leading-6">{message.content}</p>
        {intent?.summary ? (
          <div className="mt-3 rounded-lg bg-muted/60 px-3 py-2 text-xs leading-5">
            <strong>当前理解：{intent.operation ?? '对话答复'}</strong>
            <span className="text-muted-foreground">
              {' '}
              · {intent.summary} · 置信度 {Math.round((intent.confidence ?? 0) * 100)}%
            </span>
          </div>
        ) : null}
        {meta.state === 'failed' && onRetry ? (
          <div className="mt-3">
            <Button size="sm" variant="secondary" onClick={onRetry}>
              <RotateCcw />
              重试
            </Button>
          </div>
        ) : null}
        {(meta.result_artifacts ?? []).length > 0 ? (
          <div className="mt-3 space-y-1.5">
            {meta.result_artifacts!.map((row) => (
              <div
                key={row.path}
                title={row.path}
                className="flex items-center gap-2 rounded-lg border bg-muted/40 px-2.5 py-1.5 text-xs"
              >
                <FileOutput className="h-3.5 w-3.5 shrink-0 text-primary" />
                <span className="shrink-0 font-medium">
                  {ARTIFACT_TYPE_LABELS[row.type] ?? row.type}
                </span>
                <code className="ml-auto truncate font-mono text-[11px] text-muted-foreground">
                  {fileName(row.path)}
                </code>
                <a
                  href={downloadUrl(projectId, row.path)}
                  download
                  title="下载文件"
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                >
                  <Download className="h-3 w-3" />
                </a>
              </div>
            ))}
          </div>
        ) : null}
      </Card>
    </div>
  )
}
