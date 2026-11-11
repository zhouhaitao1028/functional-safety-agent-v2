import { Check, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'

interface ActivityBlockProps {
  phase: 'running' | 'done'
  intentSummary?: string
  confidence?: number
  durationSeconds?: number
}

function Step({
  state,
  label,
  detail,
  isLast,
}: {
  state: 'done' | 'active' | 'pending'
  label: string
  detail?: string
  isLast?: boolean
}) {
  return (
    <div className="flex gap-3">
      <div className="flex flex-col items-center">
        <div
          className={cn(
            'flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px]',
            state === 'done' && 'bg-success/15 text-success',
            state === 'active' && 'bg-primary/10 text-primary',
            state === 'pending' && 'bg-muted text-muted-foreground/50',
          )}
        >
          {state === 'done' ? (
            <Check className="h-3 w-3" />
          ) : state === 'active' ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <span className="h-1 w-1 rounded-full bg-current" />
          )}
        </div>
        {!isLast ? <div className="my-0.5 w-px flex-1 bg-border" /> : null}
      </div>
      <div className={cn('pb-2.5 text-sm leading-5', isLast && 'pb-0')}>
        <span
          className={cn(
            state === 'pending' ? 'text-muted-foreground/50' : 'font-medium',
            state === 'active' && 'shimmer-text',
          )}
        >
          {label}
        </span>
        {detail ? (
          <span className="ml-2 text-xs text-muted-foreground">{detail}</span>
        ) : null}
      </div>
    </div>
  )
}

export function ActivityBlock({
  phase,
  intentSummary,
  confidence,
  durationSeconds,
}: ActivityBlockProps) {
  if (phase === 'done') {
    return (
      <div className="flex items-center gap-2.5 pl-1 text-sm text-muted-foreground">
        <span className="flex h-5 w-5 items-center justify-center rounded-full bg-success/15 text-success">
          <Check className="h-3 w-3" />
        </span>
        <span>
          确定性执行完成
          {typeof durationSeconds === 'number'
            ? ` · 用时 ${durationSeconds.toLocaleString(undefined, {
                maximumFractionDigits: 1,
              })} 秒`
            : ''}
          ，结果如下
        </span>
      </div>
    )
  }

  return (
    <div className="flex">
      <div className="mr-3 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-primary to-accent-foreground text-primary-foreground shadow-sm">
        <Loader2 className="h-4 w-4 animate-spin" />
      </div>
      <div className="min-w-0 flex-1 pt-1">
        <p className="shimmer-text text-sm font-medium">正在执行任务…</p>
        <div className="mt-3">
          <Step
            state="done"
            label="理解意图"
            detail={
              intentSummary
                ? `${intentSummary}${typeof confidence === 'number' ? ` · 置信度 ${Math.round(confidence * 100)}%` : ''}`
                : undefined
            }
          />
          <Step state="active" label="执行确定性工具" detail="文件处理与校验由代码完成" />
          <Step state="pending" label="输出结果与证据" isLast />
        </div>
      </div>
    </div>
  )
}
