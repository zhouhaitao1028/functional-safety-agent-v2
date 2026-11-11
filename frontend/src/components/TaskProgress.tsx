import { useEffect, useState } from 'react'

// 确定性任务无法精确测量进度，按预估时长做平滑动画：
// 前快后慢（ease-out），最多走到 99%，等真实结果到达后由父级卸载
const ESTIMATED_DURATION_MS = 90000

export function TaskProgress({ active }: { active: boolean }) {
  const [pct, setPct] = useState(0)

  useEffect(() => {
    if (!active) {
      setPct(0)
      return
    }
    const start = Date.now()
    const timer = setInterval(() => {
      const t = Math.min((Date.now() - start) / ESTIMATED_DURATION_MS, 1)
      const eased = 1 - Math.pow(1 - t, 2)
      setPct(Math.min(eased * 100, 99))
    }, 120)
    return () => clearInterval(timer)
  }, [active])

  if (!active) return null

  return (
    <div className="rounded-xl border bg-card px-4 py-3 animate-fade-up">
      <div className="flex items-center justify-between text-[11px] text-muted-foreground">
        <span>任务执行进度</span>
        <span className="font-medium tabular-nums text-foreground/70">
          {Math.round(pct)}%
        </span>
      </div>
      <div className="relative mt-2 h-2 rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-gradient-to-r from-primary/70 to-primary transition-[width] duration-200 ease-linear"
          style={{ width: `${pct}%` }}
        />
        <div
          className="absolute -top-[13px] transition-[left] duration-200 ease-linear"
          style={{ left: `${pct}%`, transform: 'translateX(-50%)' }}
        >
          <span className="block origin-bottom animate-character-bob text-xl leading-none select-none">
            🏃
          </span>
        </div>
      </div>
    </div>
  )
}
