import { useRef, useState } from 'react'
import { ArrowUp, Loader2, Paperclip, Plus } from 'lucide-react'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input, Select, Textarea } from '@/components/ui/dialog'
import type { ModelOption, QuickTask, TaskMode } from '@/lib/types'

interface ComposerProps {
  projectId: string
  mode: TaskMode
  quickTask?: QuickTask
  quickTasks: QuickTask[]
  models: ModelOption[]
  model: string
  busy: boolean
  request: string
  inputPath: string
  secondaryPath: string
  showSecondary: boolean
  onModelChange: (value: string) => void
  onModeChange: (mode: TaskMode) => void
  onToggleSecondary: () => void
  onRequestChange: (value: string) => void
  onInputPathChange: (value: string) => void
  onSecondaryPathChange: (value: string) => void
  onSend: () => void
}

export function Composer({
  projectId,
  mode,
  quickTask,
  quickTasks,
  models,
  model,
  busy,
  request,
  inputPath,
  secondaryPath,
  showSecondary,
  onModelChange,
  onModeChange,
  onToggleSecondary,
  onRequestChange,
  onInputPathChange,
  onSecondaryPathChange,
  onSend,
}: ComposerProps) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const isGeneral = mode.kind === 'general'
  const requestPlaceholder = isGeneral
    ? '描述任务目标，文件路径可以直接写在消息里…'
    : quickTask?.description ?? '补充本次任务要求'
  const inputPlaceholder = isGeneral
    ? '可选：粘贴或选择本地文件路径'
    : `可选：粘贴或选择${quickTask?.input_label ?? '输入文件'}路径`
  const modeNote = isGeneral
    ? 'Manager 规划，执行前需要批准'
    : '模型理解意图，确定性工具执行'

  // 弹框选择本地文件，上传到项目目录后自动回填服务端路径
  const uploadFile = async (file: File) => {
    if (!projectId) return
    setUploading(true)
    try {
      const form = new FormData()
      form.append('file', file)
      const response = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/uploads`,
        { method: 'POST', body: form },
      )
      const payload = (await response.json().catch(() => null)) as
        | { path?: string; name?: string; detail?: string }
        | null
      if (!response.ok || !payload?.path) {
        throw new Error(payload?.detail ?? `上传失败（HTTP ${response.status}）`)
      }
      onInputPathChange(payload.path)
      toast.success(`已添加「${payload.name}」，路径已自动填入`)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '文件上传失败')
    } finally {
      setUploading(false)
    }
  }

  // 拖拽/粘贴文件 → 上传 → 路径回填（与 Paperclip 弹框同一条上传链路）
  const [dragActive, setDragActive] = useState(false)
  const acceptDroppedFile = (fileList: FileList | null | undefined) => {
    const file = fileList?.[0]
    if (file) void uploadFile(file)
  }

  return (
    <div className="border-t bg-background/70 px-6 py-4 backdrop-blur">
      <div
        className={cn(
          'mx-auto max-w-3xl rounded-2xl border bg-card shadow-lg shadow-black/5 transition-all',
          dragActive && 'border-primary/60 bg-primary/5 shadow-primary/10 ring-2 ring-primary/20',
        )}
        onDragOver={(event) => {
          event.preventDefault()
          setDragActive(true)
        }}
        onDragLeave={(event) => {
          if (event.currentTarget.contains(event.relatedTarget as Node)) return
          setDragActive(false)
        }}
        onDrop={(event) => {
          event.preventDefault()
          setDragActive(false)
          acceptDroppedFile(event.dataTransfer.files)
        }}
      >
        <Textarea
          aria-label="任务描述"
          rows={2}
          value={request}
          placeholder={requestPlaceholder}
          className="border-0 bg-transparent px-4 pt-3.5 shadow-none focus-visible:ring-0"
          onChange={(event) => onRequestChange(event.target.value)}
          onPaste={(event) => {
            // 资源管理器里 Ctrl+C 复制文件后粘贴到输入框，自动上传
            if (event.clipboardData.files.length > 0) {
              event.preventDefault()
              acceptDroppedFile(event.clipboardData.files)
            }
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault()
              onSend()
            }
          }}
        />
        <div className="flex items-center gap-2 px-3.5 pb-1.5">
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) void uploadFile(file)
              event.target.value = ''
            }}
          />
          <button
            type="button"
            aria-label="选择本地文件"
            title="弹框选择本地文件，上传后自动填入路径"
            disabled={uploading || !projectId}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
            onClick={() => fileInputRef.current?.click()}
          >
            {uploading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Paperclip className="h-4 w-4" />
            )}
          </button>
          {!isGeneral ? (
            <button
              type="button"
              aria-label="添加第二个输入文件"
              title="对比任务可添加第二个输入文件"
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              onClick={onToggleSecondary}
            >
              <Plus className="h-4 w-4" />
            </button>
          ) : null}
          <Input
            aria-label="输入文件或目录"
            value={inputPath}
            placeholder={inputPlaceholder}
            className="h-8 rounded-full border-0 bg-muted/60 px-3.5 text-xs shadow-none focus-visible:ring-1"
            onFocus={() => {
              if (!isGeneral && quickTask?.default_input_path && !inputPath.trim()) {
                onInputPathChange(quickTask.default_input_path)
              }
            }}
            onChange={(event) => onInputPathChange(event.target.value)}
          />
        </div>
        {showSecondary && !isGeneral ? (
          <div className="flex items-center gap-2 px-3.5 pb-1.5">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold text-muted-foreground">
              2
            </span>
            <Input
              aria-label="第二个输入文件"
              value={secondaryPath}
              placeholder="添加旧版接口总览表路径"
              autoFocus
              className="h-8 rounded-full border-0 bg-muted/60 px-3.5 text-xs shadow-none focus-visible:ring-1"
              onChange={(event) => onSecondaryPathChange(event.target.value)}
            />
          </div>
        ) : null}
        <div className="flex items-center justify-between px-3 pb-2.5 pt-1">
          <div className="flex items-center gap-2">
            <Select
              aria-label="任务类型"
              value={mode.kind === 'general' ? 'general' : `quick:${mode.id}`}
              onChange={(event) => {
                const value = event.target.value
                onModeChange(
                  value === 'general'
                    ? { kind: 'general' }
                    : { kind: 'quick', id: value.replace(/^quick:/, '') },
                )
              }}
            >
              <option value="general">通用任务</option>
              {quickTasks.map((task) => (
                <option key={task.id} value={`quick:${task.id}`}>
                  {task.title}
                </option>
              ))}
            </Select>
            <span className="hidden text-[11px] text-muted-foreground sm:inline">
              {modeNote}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <Select
              aria-label="DeepSeek 模型"
              value={model}
              onChange={(event) => onModelChange(event.target.value)}
            >
              {models.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.title}
                </option>
              ))}
            </Select>
            <Button
              size="icon"
              className="rounded-full"
              aria-label="发送任务"
              title="发送任务"
              disabled={busy}
              onClick={onSend}
            >
              <ArrowUp />
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
