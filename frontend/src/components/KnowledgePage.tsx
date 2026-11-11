import { useState } from 'react'
import {
  ArrowRight,
  BookOpen,
  ChevronLeft,
  ExternalLink,
  FileText,
  GitBranch,
  GraduationCap,
  Library,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
  Wrench,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Dialog } from '@/components/ui/dialog'
import { cn } from '@/lib/utils'
import {
  useKnowledgeContextPack,
  useKnowledgeGuide,
  useKnowledgeStage,
  useKnowledgeStages,
  useKnowledgeTraceability,
} from '@/hooks/use-console-data'
import type { KnowledgeStage } from '@/lib/types'

interface KnowledgePageProps {
  onBack: () => void
}

type KnowledgeTab = 'stages' | 'traceability'

export function KnowledgePage({ onBack }: KnowledgePageProps) {
  const [tab, setTab] = useState<KnowledgeTab>('stages')
  const stagesEnabled = tab === 'stages'
  const stages = useKnowledgeStages(stagesEnabled).data ?? []
  const [selectedId, setSelectedId] = useState('')
  const detail = useKnowledgeStage(selectedId, stagesEnabled && Boolean(selectedId)).data ?? null
  const [guideId, setGuideId] = useState<string | null>(null)
  const guide = useKnowledgeGuide(guideId).data ?? null
  const [packOpen, setPackOpen] = useState(false)
  const pack = useKnowledgeContextPack(packOpen ? selectedId : null).data ?? null
  const trace = useKnowledgeTraceability(tab === 'traceability').data ?? null
  const [traceStageId, setTraceStageId] = useState('')
  const traceStage = trace?.trace_stages.find((row) => row.id === traceStageId) ?? null

  const authoredCount = stages.filter((row) => row.skill_status === 'authored').length
  const guideTotal = stages.reduce((sum, row) => sum + row.guide_count, 0)
  const ragDocTotal = stages.reduce((sum, row) => sum + row.rag_document_count, 0)

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* 头部：标题 + 统计 + 返回 */}
      <div className="flex items-center justify-between border-b bg-card/60 px-6 py-4">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <GitBranch className="h-4.5 w-4.5" />
          </div>
          <div>
            <strong className="block text-sm font-semibold">功能安全知识库 · 流程环节图谱</strong>
            <span className="text-xs text-muted-foreground">
              知识图谱串联指南模板上下游 · 每个环节一份工程师 skill · harness 按声明检索注入
            </span>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex rounded-lg border bg-background p-0.5">
            <button
              className={cn(
                'rounded-md px-3 py-1.5 text-xs transition-colors',
                tab === 'stages' ? 'bg-primary/10 font-medium text-primary' : 'text-muted-foreground hover:text-foreground',
              )}
              onClick={() => setTab('stages')}
            >
              报告撰写 · 环节图谱
            </button>
            <button
              className={cn(
                'rounded-md px-3 py-1.5 text-xs transition-colors',
                tab === 'traceability' ? 'bg-primary/10 font-medium text-primary' : 'text-muted-foreground hover:text-foreground',
              )}
              onClick={() => setTab('traceability')}
            >
              追溯确认 · 契约与工具
            </button>
          </div>
          <Button variant="secondary" onClick={onBack}>
            <ChevronLeft />
            返回对话
          </Button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-5">
        {tab === 'stages' && (
        <>
        {/* 环节链图：功能 → HARA → … → 测试 */}
        <div className="mb-3 text-xs text-muted-foreground">
          {stages.length} 个环节 · {guideTotal} 份指南/模板 · {ragDocTotal} 份公司文档引用 · skill 覆盖{' '}
          {authoredCount}/{stages.length}
        </div>
        <div className="flex flex-wrap items-stretch gap-1.5">
          {stages.map((stage, index) => (
            <div key={stage.id} className="flex items-center gap-1.5">
              <button
                className={cn(
                  'group w-36 rounded-xl border bg-card p-3 text-left shadow-sm transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md',
                  stage.id === selectedId && 'border-primary/60 ring-2 ring-primary/20',
                )}
                onClick={() => setSelectedId(stage.id)}
              >
                <div className="flex items-center justify-between">
                  <span className="text-[10px] font-semibold text-muted-foreground">
                    {String(stage.order).padStart(2, '0')}
                  </span>
                  <span
                    className={cn(
                      'rounded-full px-1.5 py-0.5 text-[10px]',
                      stage.skill_status === 'authored'
                        ? 'bg-success/15 text-success'
                        : 'bg-warning/20 text-warning',
                    )}
                  >
                    {stage.skill_status === 'authored' ? 'skill 已沉淀' : '样式模板'}
                  </span>
                </div>
                <strong className="mt-1 block text-sm leading-tight">{stage.name}</strong>
                <span className="mt-0.5 block truncate text-[10px] text-muted-foreground">
                  {stage.guide_count} 份知识资产 · {stage.rag_document_count} 份公司文档
                </span>
              </button>
              {index < stages.length - 1 && (
                <ArrowRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground/60" />
              )}
            </div>
          ))}
        </div>

        {!selectedId && stages.length > 0 && (
          <div className="mt-10 text-center text-sm text-muted-foreground">
            点击上方环节节点，查看该环节的 skill、指南模板与 harness 注入包
          </div>
        )}

        {/* 环节详情 */}
        {selectedId && (
          <div className="mt-5 grid gap-4 lg:grid-cols-2">
            {/* skill 三问 */}
            <Card className="p-5">
              <div className="mb-3 flex items-center gap-2">
                <GraduationCap className="h-4 w-4 text-primary" />
                <strong className="text-sm">工程师 Skill：三问声明</strong>
                {detail?.skill.is_template && (
                  <span className="rounded-full bg-warning/20 px-2 py-0.5 text-[10px] text-warning">
                    样式模板 · 待工程师补全
                  </span>
                )}
              </div>
              {detail ? (
                <div className="space-y-4 text-sm">
                  <div>
                    <div className="mb-1.5 text-xs font-semibold text-muted-foreground">
                      一、用到了哪些指南模板
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {detail.skill.uses.map((id) => {
                        const target = detail.guides.find((row) => row.id === id)
                        return (
                          <button
                            key={id}
                            className="rounded-full border bg-background px-2.5 py-1 text-xs transition-colors hover:border-primary/40 hover:bg-accent/60"
                            onClick={() => setGuideId(id)}
                          >
                            <FileText className="mr-1 inline h-3 w-3 text-muted-foreground" />
                            {target?.title ?? id}
                          </button>
                        )
                      })}
                    </div>
                  </div>
                  <div>
                    <div className="mb-1.5 flex items-center gap-1 text-xs font-semibold text-muted-foreground">
                      <TriangleAlert className="h-3 w-3" />
                      二、模板有哪些容易犯错的注意点
                    </div>
                    <ul className="space-y-1.5">
                      {detail.skill.pitfalls.map((item, index) => (
                        <li key={index} className="flex gap-1.5 text-xs leading-5">
                          <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-warning" />
                          <span>{item}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <div className="mb-1.5 flex items-center gap-1 text-xs font-semibold text-muted-foreground">
                      <Sparkles className="h-3 w-3" />
                      三、什么情况下要参考另一个指南
                    </div>
                    <ul className="space-y-1.5">
                      {detail.skill.jumps.map((item, index) => (
                        <li key={index} className="flex gap-1.5 text-xs leading-5">
                          <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-primary/60" />
                          <span>{item}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div className="border-t pt-2 text-[11px] text-muted-foreground">
                    执行 Agent：{detail.agent_id} · 产物：{detail.produces} · skill 文件：
                    {detail.skill.source_path}
                  </div>
                </div>
              ) : (
                <div className="py-6 text-center text-xs text-muted-foreground">加载中…</div>
              )}
            </Card>

            {/* 指南模板清单 + harness 预览 */}
            <div className="space-y-4">
              <Card className="p-5">
                <div className="mb-3 flex items-center gap-2">
                  <BookOpen className="h-4 w-4 text-primary" />
                  <strong className="text-sm">本环节知识资产</strong>
                </div>
                <div className="space-y-1.5">
                  {detail?.guides.map((row) => (
                    <button
                      key={row.id}
                      className="flex w-full items-center gap-2 rounded-lg border bg-background px-3 py-2 text-left text-xs transition-colors hover:border-primary/40 hover:bg-accent/60"
                      onClick={() => setGuideId(row.id)}
                    >
                      <span
                        className={cn(
                          'rounded px-1.5 py-0.5 text-[10px]',
                          row.kind === 'guide'
                            ? 'bg-primary/10 text-primary'
                            : 'bg-accent text-muted-foreground',
                        )}
                      >
                        {row.kind === 'guide' ? '指南' : '模板'}
                      </span>
                      <span className="flex-1">{row.title}</span>
                      <code className="text-[10px] text-muted-foreground">{row.id}</code>
                    </button>
                  ))}
                </div>
                {detail && (
                  <div className="mt-3 border-t pt-3 text-[11px] leading-5 text-muted-foreground">
                    上游依据：{detail.upstream.length ? detail.upstream.join('、') : '（链首）'}
                    <br />
                    下游去向：{detail.downstream.length ? detail.downstream.join('、') : '（链尾）'}
                  </div>
                )}
              </Card>
              <Card className="p-5">
                <div className="mb-3 flex items-center gap-2">
                  <Library className="h-4 w-4 text-primary" />
                  <strong className="text-sm">关联公司文档</strong>
                  <span className="text-[10px] text-muted-foreground">
                    RAG 知识库引用 · draft 仅作参考，不注入子 Agent
                  </span>
                </div>
                <div className="max-h-72 space-y-1.5 overflow-y-auto">
                  {detail?.rag_documents.map((doc) => (
                    <a
                      key={doc.id}
                      className="flex w-full items-center gap-2 rounded-lg border bg-background px-3 py-2 text-left text-xs transition-colors hover:border-primary/40 hover:bg-accent/60"
                      href={doc.wiki_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <span className="rounded bg-warning/15 px-1.5 py-0.5 text-[10px] text-warning">
                        {doc.status}
                      </span>
                      <span className="flex-1 truncate" title={doc.title}>
                        {doc.title}
                      </span>
                      <ExternalLink className="h-3 w-3 shrink-0 text-muted-foreground" />
                    </a>
                  ))}
                  {detail && detail.rag_documents.length === 0 && (
                    <div className="py-3 text-center text-[11px] text-muted-foreground">
                      本环节暂无关联公司文档
                    </div>
                  )}
                </div>
              </Card>
              <Card className="p-5">
                <strong className="text-sm">Agent harness 检索</strong>
                <p className="mt-1.5 text-xs leading-5 text-muted-foreground">
                  harness 读取本环节 skill 声明，沿知识图谱把指南模板全文、上游环节依据与下游去向一次性拉齐，注入子
                  Agent 上下文——子 Agent 不负责找资料，只负责按模板干活。
                </p>
                <Button
                  className="mt-3 w-full justify-center gap-2"
                  variant="secondary"
                  disabled={!selectedId}
                  onClick={() => setPackOpen(true)}
                >
                  <GitBranch />
                  预览 harness 注入包
                </Button>
              </Card>
            </div>
          </div>
        )}
        </>
        )}

        {tab === 'traceability' && (
          <div className="mx-auto max-w-4xl space-y-4">
            <div className="mb-1 text-xs text-muted-foreground">
              追溯任务线：{trace?.task} · {trace?.trace_stages.length} 个环节 ·{' '}
              {trace?.mcp_tools.length} 个确定性工具
            </div>

            {/* 追溯环节链：包解析 → 生成 → 校验 → 对比 → 人工门 */}
            <div className="flex flex-wrap items-stretch gap-1.5">
              {trace?.trace_stages.map((stage, index) => (
                <div key={stage.id} className="flex items-center gap-1.5">
                  <button
                    className={cn(
                      'group w-40 rounded-xl border bg-card p-3 text-left shadow-sm transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md',
                      stage.id === traceStageId && 'border-primary/60 ring-2 ring-primary/20',
                    )}
                    onClick={() => setTraceStageId(stage.id)}
                  >
                    <span className="text-[10px] font-semibold text-muted-foreground">
                      {String(stage.order).padStart(2, '0')}
                    </span>
                    <strong className="mt-1 block text-sm leading-tight">{stage.name}</strong>
                    <span className="mt-0.5 block truncate text-[10px] text-muted-foreground">
                      {stage.tools.length ? `${stage.tools.length} 个确定性工具` : '人工确认门'}
                    </span>
                  </button>
                  {index < (trace?.trace_stages.length ?? 0) - 1 && (
                    <ArrowRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground/60" />
                  )}
                </div>
              ))}
            </div>

            {/* 选中环节详情：工具 + skill 契约锚点 */}
            {traceStage && (
              <Card className="p-5">
                <div className="mb-2 flex items-center gap-2">
                  <Wrench className="h-4 w-4 text-primary" />
                  <strong className="text-sm">
                    {String(traceStage.order).padStart(2, '0')} · {traceStage.name}
                  </strong>
                </div>
                <p className="text-xs leading-5 text-muted-foreground">{traceStage.description}</p>
                <div className="mt-3 space-y-1.5">
                  {traceStage.tools.map((tool) => (
                    <div key={tool.id} className="rounded-lg border bg-background px-3 py-2">
                      <code className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
                        {tool.mcp_name}
                      </code>
                      <p className="mt-1 text-xs leading-5 text-muted-foreground">
                        {tool.description}
                      </p>
                    </div>
                  ))}
                  {traceStage.tools.length === 0 && (
                    <div className="rounded-lg border border-dashed bg-background px-3 py-2 text-xs text-muted-foreground">
                      本环节无工具参与——人工确认门，模型与工具都无权跳过
                    </div>
                  )}
                </div>
                {traceStage.skill_refs.length > 0 && (
                  <div className="mt-3 border-t pt-3 text-[11px] leading-5 text-muted-foreground">
                    skill 契约锚点：{traceStage.skill_refs.join(' · ')}（见下方 skill 全文对应章节）
                  </div>
                )}
              </Card>
            )}

            <Card className="border-primary/30 bg-primary/5 p-5">
              <div className="flex items-start gap-2">
                <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                <div>
                  <strong className="text-sm">零臆想原则</strong>
                  <p className="mt-1 text-xs leading-5 text-muted-foreground">{trace?.principle}</p>
                </div>
              </div>
            </Card>

            <Card className="p-5">
              <div className="mb-3 flex items-center gap-2">
                <GraduationCap className="h-4 w-4 text-primary" />
                <strong className="text-sm">契约 Skill：{trace?.skill.name}</strong>
                <code className="text-[10px] text-muted-foreground">{trace?.skill.source_path}</code>
              </div>
              {trace?.skill.description && (
                <p className="mb-3 rounded-lg bg-muted/50 p-3 text-xs leading-5 text-muted-foreground">
                  {trace.skill.description}
                </p>
              )}
              <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-lg bg-muted/50 p-4 font-mono text-[11px] leading-5">
                {trace?.skill.body ?? '加载中…'}
              </pre>
            </Card>

            <Card className="p-5">
              <div className="mb-3 flex items-center gap-2">
                <Wrench className="h-4 w-4 text-primary" />
                <strong className="text-sm">确定性工具（{trace?.mcp_tools.length ?? 0} 个 · 均通过 MCP 暴露）</strong>
              </div>
              <div className="space-y-1.5">
                {trace?.mcp_tools.map((tool) => (
                  <div key={tool.name} className="rounded-lg border bg-background px-3 py-2">
                    <div className="flex items-center gap-2">
                      <code className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
                        {tool.name}
                      </code>
                    </div>
                    <p className="mt-1 text-xs leading-5 text-muted-foreground">{tool.description}</p>
                  </div>
                ))}
              </div>
            </Card>

            <Card className="p-5">
              <div className="mb-3 flex items-center gap-2">
                <ShieldCheck className="h-4 w-4 text-primary" />
                <strong className="text-sm">确定性算法（{trace?.algorithms.length ?? 0} 个 · 无模型参与）</strong>
              </div>
              <div className="space-y-1.5">
                {trace?.algorithms.map((algo) => (
                  <div key={algo.id} className="rounded-lg border bg-background px-3 py-2">
                    <strong className="text-xs">{algo.name}</strong>
                    <p className="mt-1 text-xs leading-5 text-muted-foreground">{algo.description}</p>
                  </div>
                ))}
              </div>
            </Card>

            <div className="pb-2 text-center text-[11px] text-muted-foreground">
              对比报告撰写线（指南注入 + 工程师 skill）：追溯线的知识形态是操作契约与确定性不变量——
              规则固化在工具代码与 skill 约束里，模型无权改写计算结果。
            </div>
          </div>
        )}
      </div>
      <Dialog
        open={Boolean(guideId)}
        onClose={() => setGuideId(null)}
        title={guide?.title ?? '知识资产'}
        description={guide ? `${guide.file} · ID: ${guide.id}` : ''}
        footer={
          <Button variant="secondary" onClick={() => setGuideId(null)}>
            关闭
          </Button>
        }
      >
        <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-lg bg-muted/50 p-4 font-mono text-xs leading-5">
          {guide?.content ?? '加载中…'}
        </pre>
      </Dialog>

      {/* harness 注入包预览 */}
      <Dialog
        open={packOpen}
        onClose={() => setPackOpen(false)}
        title="harness 注入包"
        description="执行该环节任务时，以下内容将作为子 Agent 的知识上下文"
        footer={
          <Button variant="secondary" onClick={() => setPackOpen(false)}>
            关闭
          </Button>
        }
      >
        {pack ? (
          <div className="max-h-[60vh] space-y-3 overflow-auto text-xs">
            <div className="rounded-lg border bg-muted/40 p-3">
              <div className="mb-1 font-semibold">环节：{pack.stage.name}</div>
              <div className="text-muted-foreground">{pack.stage.description}</div>
              <div className="mt-1 text-muted-foreground">
                执行 Agent：{pack.stage.agent_id} · 上游：{pack.upstream.map((row) => row.name).join('、') || '（链首）'} · 下游：
                {pack.downstream.map((row) => row.name).join('、') || '（链尾）'}
              </div>
            </div>
            <div className="rounded-lg border p-3">
              <div className="mb-1 font-semibold">
                skill 声明（{pack.skill.name}）
              </div>
              <div className="text-muted-foreground">
                指南 {pack.skill.uses.length} 份 · 易错点 {pack.skill.pitfalls.length} 条 · 跳转{' '}
                {pack.skill.jumps.length} 条 · {pack.skill_coverage_note}
              </div>
            </div>
            {pack.guides.map((row) => (
              <div key={row.id} className="rounded-lg border p-3">
                <div className="mb-1 font-semibold">
                  注入知识资产：{row.title}（{row.kind === 'guide' ? '指南' : '模板'}）
                </div>
                <pre className="max-h-40 overflow-auto whitespace-pre-wrap font-mono text-[11px] leading-5 text-muted-foreground">
                  {row.content}
                </pre>
              </div>
            ))}
            <div className="rounded-lg border p-3">
              <div className="mb-1 font-semibold">
                关联公司文档引用（{pack.rag_documents.length} 份）
              </div>
              <div className="text-muted-foreground">
                引用层：harness 只携带清单与出处链接，draft 正文不注入子 Agent
              </div>
              <div className="mt-1.5 flex flex-wrap gap-1">
                {pack.rag_documents.map((doc) => (
                  <a
                    key={doc.id}
                    className="rounded-full border bg-background px-2 py-0.5 text-[10px] hover:border-primary/40"
                    href={doc.wiki_url}
                    target="_blank"
                    rel="noreferrer"
                    title={doc.title}
                  >
                    {doc.title}
                  </a>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <div className="py-6 text-center text-xs text-muted-foreground">加载中…</div>
        )}
      </Dialog>
    </div>
  )
}

// 供外部按 id 找环节名
export function stageName(stages: KnowledgeStage[], id: string): string {
  return stages.find((row) => row.id === id)?.name ?? id
}
