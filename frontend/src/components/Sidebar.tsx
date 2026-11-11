import { BookOpen, House, Plus, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { ProjectSummary } from '@/lib/types'

function shortDate(value?: string): string {
  if (!value) return ''
  try {
    return new Date(value).toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return value
  }
}

interface SidebarProps {
  projects: ProjectSummary[]
  activeProjectId: string
  healthText: string
  healthy: boolean
  knowledgeActive: boolean
  onSelect: (projectId: string) => void
  onNewChat: () => void
  onOpenKnowledge: () => void
  onHome: () => void
  onDelete: (projectId: string) => void
}

export function Sidebar({
  projects,
  activeProjectId,
  healthText,
  healthy,
  knowledgeActive,
  onSelect,
  onNewChat,
  onOpenKnowledge,
  onHome,
  onDelete,
}: SidebarProps) {
  return (
    <aside className="flex h-full w-72 shrink-0 flex-col border-r bg-card/60 backdrop-blur">
      <div className="flex items-center gap-3 px-5 pt-5">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-primary to-accent-foreground text-sm font-bold text-primary-foreground shadow-md">
          FS
        </div>
        <div className="leading-tight">
          <strong className="block text-sm font-semibold">FuSa Agent</strong>
          <span className="text-xs text-muted-foreground">功能安全工程助手</span>
        </div>
      </div>
      <div className="space-y-2 px-4 pt-5">
        <Button className="w-full justify-start gap-2 shadow-sm" onClick={onNewChat}>
          <Plus />
          新对话
        </Button>
        {activeProjectId && (
          <Button variant="secondary" className="w-full justify-start gap-2" onClick={onHome}>
            <House />
            返回主页
          </Button>
        )}
        <Button
          variant={knowledgeActive ? 'default' : 'secondary'}
          className="w-full justify-start gap-2"
          onClick={onOpenKnowledge}
        >
          <BookOpen />
          功能安全知识库
        </Button>
      </div>
      <div className="px-5 pb-1 pt-5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        历史对话
      </div>
      <div className="flex-1 space-y-0.5 overflow-y-auto px-3 pb-3">
        {projects.length === 0 ? (
          <div className="px-2 py-8 text-center text-xs leading-6 text-muted-foreground">
            还没有历史对话
            <br />
            点击“新对话”开始
          </div>
        ) : (
          projects.map((project) => (
            <div key={project.project_id} className="group relative">
              <button
                className={cn(
                  'w-full rounded-lg px-3 py-2 pr-9 text-left transition-colors hover:bg-accent/70',
                  project.project_id === activeProjectId && 'bg-accent',
                )}
                onClick={() => onSelect(project.project_id)}
              >
                <strong className="block truncate text-sm font-medium">
                  {project.title}
                </strong>
                <span className="text-xs text-muted-foreground">
                  {shortDate(project.created_at)}
                </span>
              </button>
              <button
                aria-label={`移除对话 ${project.title}`}
                title="移到回收站"
                className="absolute right-2 top-2.5 hidden rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive group-hover:block"
                onClick={(event) => {
                  event.stopPropagation()
                  onDelete(project.project_id)
                }}
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))
        )}
      </div>
      <div className="flex items-center gap-2 border-t px-5 py-3 text-xs text-muted-foreground">
        <span
          className={cn(
            'inline-block h-2 w-2 rounded-full',
            healthy ? 'bg-success shadow-[0_0_6px] shadow-success/60' : 'bg-destructive',
          )}
        />
        <span className="truncate">{healthText}</span>
      </div>
    </aside>
  )
}
