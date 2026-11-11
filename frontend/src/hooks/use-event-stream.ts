import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'

/**
 * Subscribe to the project's SSE execution-event stream and invalidate the
 * project query whenever new workflow events arrive, so plan/result views
 * refresh in near real time while agents run.
 */
// 后端 SSE 全部以 `event: <type>` 命名事件发送（onmessage 只收匿名消息，
// 收不到任何一条），必须按类型 addEventListener
const STREAM_EVENT_TYPES = [
  'quick_task_progress',
  'quick_task_message',
  'workplan_registered',
  'item_claimed',
  'item_completed',
  'item_failed',
  'item_reviewed',
] as const

export function useEventStream(projectId: string, enabled: boolean) {
  const queryClient = useQueryClient()
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    if (!enabled || !projectId) {
      setConnected(false)
      return
    }
    const source = new EventSource(
      `/api/projects/${encodeURIComponent(projectId)}/events/stream`,
    )
    let timer: ReturnType<typeof setTimeout> | null = null
    const scheduleInvalidate = () => {
      if (timer) return
      timer = setTimeout(() => {
        timer = null
        void queryClient.invalidateQueries({ queryKey: ['project', projectId] })
      }, 200)
    }
    source.onopen = () => {
      setConnected(true)
      // 连接（含断线重连，服务端按 Last-Event-ID 补发）建立后立即刷新一次
      scheduleInvalidate()
    }
    source.onerror = () => setConnected(false)
    for (const type of STREAM_EVENT_TYPES) {
      source.addEventListener(type, scheduleInvalidate)
    }
    return () => {
      source.close()
      if (timer) {
        clearTimeout(timer)
        timer = null
      }
      setConnected(false)
    }
  }, [projectId, enabled, queryClient])

  return connected
}
