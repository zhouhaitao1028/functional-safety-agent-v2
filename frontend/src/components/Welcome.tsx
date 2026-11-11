import { ArrowRight, FileText, Link2, ShieldAlert, Target } from 'lucide-react'

interface WelcomeProps {
  onStarter: (task: 'general' | 'interface_generation', presetRequest?: string) => void
}

// 追溯类子任务的预置请求模板：走通用 Manager 流程，用户补路径后发送
const PRESET_CHANGE_IMPACT =
  '安全目标发生变更，请做变更影响分析：找出受影响的 TSR、FSR 与测试用例，输出带证据的追溯影响清单。'
const PRESET_FSR_MISSING =
  '请做需求覆盖检查：识别哪些安全目标还缺少软件安全需求（FSR）支撑，输出缺失对照表。'

export function Welcome({ onStarter }: WelcomeProps) {
  return (
    <div className="flex flex-1 items-center justify-center p-8">
      <div className="w-full max-w-3xl animate-fade-up">
        <div className="text-center">
          <div className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-primary via-primary/80 to-accent text-2xl font-bold text-primary-foreground shadow-lg shadow-primary/25">
            FS
          </div>
          <h2 className="bg-gradient-to-r from-foreground to-foreground/60 bg-clip-text text-3xl font-bold tracking-tight text-transparent">
            今天要完成什么功能安全任务？
          </h2>
          <p className="mx-auto mt-3 max-w-lg text-sm leading-6 text-muted-foreground">
            按 ISO 26262 功能安全链组织工作：功能 → HARA → 安全目标 → TSR → FSR →
            架构 → 测试。按任务性质选择工作线。
          </p>
        </div>

        <div className="mt-8 grid gap-4 md:grid-cols-2">
          {/* 任务线 A：报告撰写（概率层主导，计划人工批准） */}
          <button
            className="group rounded-xl border bg-card p-5 text-left shadow-sm transition-all hover:-translate-y-1 hover:border-primary/40 hover:shadow-lg hover:shadow-primary/10"
            onClick={() => onStarter('general')}
          >
            <div className="mb-3 flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10 text-primary transition-colors group-hover:bg-primary group-hover:text-primary-foreground">
              <FileText className="h-4.5 w-4.5" />
            </div>
            <div className="text-[11px] font-semibold uppercase tracking-wider text-primary">
              任务线 A · 报告撰写
            </div>
            <strong className="mt-1 block">按指南模板写报告</strong>
            <small className="mt-2 block text-xs leading-5 text-muted-foreground">
              Manager 依据知识库中的指南模板与工程师沉淀的 skill 拆解任务，按模板产出
              HARA、安全需求、测试报告等工作产物，计划执行前等待人工批准。
            </small>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {['HARA 分析', 'TSR/FSR 导出', '测试报告'].map((chip) => (
                <span
                  key={chip}
                  className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground"
                >
                  {chip}
                </span>
              ))}
            </div>
          </button>

          {/* 任务线 B：追溯确认（确定性层主导，无证据不输出） */}
          <div className="rounded-xl border bg-card p-5 shadow-sm transition-all hover:-translate-y-1 hover:border-primary/40 hover:shadow-lg hover:shadow-primary/10">
            <div className="mb-3 flex h-9 w-9 items-center justify-center rounded-lg bg-success/10 text-success">
              <Link2 className="h-4.5 w-4.5" />
            </div>
            <div className="text-[11px] font-semibold uppercase tracking-wider text-success">
              任务线 B · 追溯确认
            </div>
            <strong className="mt-1 block">变更追溯与一致性确认</strong>
            <small className="mt-2 block text-xs leading-5 text-muted-foreground">
              可追溯、强确定性：模型只提名候选，事实判定全部由确定性工具完成，无证据不输出，结果经人工确认后落审计记录。
            </small>
            <div className="mt-3 space-y-1.5">
              <button
                className="flex w-full items-center gap-2 rounded-lg border bg-background px-2.5 py-1.5 text-left text-xs transition-colors hover:border-primary/40 hover:bg-accent/60"
                onClick={() => onStarter('interface_generation')}
              >
                <Link2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <span className="flex-1">软件接口总览表生成 / 对比追溯</span>
                <ArrowRight className="h-3 w-3 shrink-0 text-muted-foreground" />
              </button>
              <button
                className="flex w-full items-center gap-2 rounded-lg border bg-background px-2.5 py-1.5 text-left text-xs transition-colors hover:border-primary/40 hover:bg-accent/60"
                onClick={() => onStarter('general', PRESET_CHANGE_IMPACT)}
              >
                <Target className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <span className="flex-1">安全目标变更影响分析</span>
                <ArrowRight className="h-3 w-3 shrink-0 text-muted-foreground" />
              </button>
              <button
                className="flex w-full items-center gap-2 rounded-lg border bg-background px-2.5 py-1.5 text-left text-xs transition-colors hover:border-primary/40 hover:bg-accent/60"
                onClick={() => onStarter('general', PRESET_FSR_MISSING)}
              >
                <ShieldAlert className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <span className="flex-1">FSR 缺失识别</span>
                <ArrowRight className="h-3 w-3 shrink-0 text-muted-foreground" />
              </button>
            </div>
          </div>
        </div>

        <p className="mt-6 text-center text-xs text-muted-foreground">
          ASIL 查表、接口追溯校验、对比差异均由确定性代码完成，模型只做理解与候选建议
        </p>
      </div>
    </div>
  )
}
