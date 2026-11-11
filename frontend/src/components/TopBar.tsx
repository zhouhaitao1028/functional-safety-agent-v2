import { Moon, Sun, Wifi, WifiOff } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface TopBarProps {
  title: string
  subtitle: string
  stateLabel: string
  stateTone: 'idle' | 'busy' | 'ok' | 'warn' | 'bad'
  busy: boolean
  streamLive: boolean
  theme: 'light' | 'dark'
  onToggleTheme: () => void
}

const toneClass: Record<TopBarProps['stateTone'], string> = {
  idle: 'secondary',
  busy: 'warn',
  ok: 'ok',
  warn: 'warn',
  bad: 'bad',
}

export function TopBar({
  title,
  subtitle,
  stateLabel,
  stateTone,
  busy,
  streamLive,
  theme,
  onToggleTheme,
}: TopBarProps) {
  return (
    <header className="relative flex items-center justify-between gap-4 border-b bg-background/70 px-6 py-4 backdrop-blur">
      {busy ? (
        <div className="absolute inset-x-0 bottom-0 h-0.5 overflow-hidden">
          <div className="h-full w-1/3 animate-[busy-slide_1.2s_ease-in-out_infinite] rounded-full bg-primary" />
        </div>
      ) : null}
      <div className="min-w-0">
        <h1 className="truncate text-base font-semibold">{title}</h1>
        <p className="truncate text-xs text-muted-foreground">{subtitle}</p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Badge
          variant={toneClass[stateTone] as 'secondary' | 'ok' | 'warn' | 'bad'}
          className="h-6"
        >
          <span
            className={cn(
              'inline-block h-1.5 w-1.5 rounded-full',
              stateTone === 'ok' && 'bg-success',
              stateTone === 'bad' && 'bg-destructive',
              (stateTone === 'busy' || stateTone === 'warn') && 'animate-pulse bg-warning',
              stateTone === 'idle' && 'bg-muted-foreground/50',
            )}
          />
          {stateLabel}
        </Badge>
        <span
          title={streamLive ? '实时事件流已连接' : '实时事件流未连接'}
          className={cn(
            'flex h-6 items-center gap-1 rounded-full px-2 text-[11px]',
            streamLive ? 'text-success' : 'text-muted-foreground/60',
          )}
        >
          {streamLive ? <Wifi className="h-3.5 w-3.5" /> : <WifiOff className="h-3.5 w-3.5" />}
          SSE
        </span>
        <Button variant="ghost" size="icon" onClick={onToggleTheme} aria-label="切换主题">
          {theme === 'light' ? <Moon /> : <Sun />}
        </Button>
      </div>
    </header>
  )
}
