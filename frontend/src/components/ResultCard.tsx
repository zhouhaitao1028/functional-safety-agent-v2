import { CheckCircle2, ChevronDown, Download, FileJson, FileOutput, ThumbsUp } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { pretty } from '@/lib/api'
import type { ArtifactRecord, ProjectStatus } from '@/lib/types'

const METRIC_LABELS: Record<string, string> = {
  duration_seconds: '运行耗时（秒）',
  engine: '执行引擎',
  data_rows: '总览数据行',
  module_files: '模块文件',
  parsed_module_files: '成功解析',
  extracted_interface_records: '接口记录',
  cross_application_links: '跨应用链路',
  valid_cross_application_links: '有效链路',
  missing_cross_application_links: '缺失链路',
  validation_errors: '校验错误',
  missing_link_rows: '缺失业务链路',
  exclude_rules: '排除规则',
  participating_groups: '参与链路分组',
  participating_rows: '参与数据行',
  valid_link_rows: '有效链路行',
  non_participating_rows: '非参与数据行',
  serial_errors: '序号错误',
  required_blank_rows: '应留空行',
  aa_ab_mismatches: 'AA/AB 不一致',
  non_participating_aa_ab_mismatches: '非参与 AA/AB 不一致',
  new_rows: '新版行数',
  old_rows: '旧版行数',
  total_diffs: '差异总数',
  new_only: '仅新版',
  old_only: '仅旧版',
  content_diff_cells: '内容差异单元格',
  findings: '发现项',
}

const OPERATION_LABELS: Record<string, string> = {
  generate_overview: '生成接口收发总览表',
  compare_overviews: '新旧接口总览表对比',
  analyze_package: '接口包分析',
}

export const ARTIFACT_TYPE_LABELS: Record<string, string> = {
  software_interface_overview: '接口收发总览表',
  generation_input_manifest: '输入清单',
  generation_validation: '生成校验',
  generated_unit_config_snapshot: '单元配置快照',
  fast_generation_log: '生成日志',
  generation_report: '生成报告',
  comparison_report: '对比报告',
  comparison_workbook: '对比结果工作簿',
}

function fileName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path
}

function metricLabel(key: string): string {
  return METRIC_LABELS[key] ?? key.replaceAll('_', ' ')
}

interface ResultCardProps {
  artifact: ArtifactRecord
  status: ProjectStatus | null
  busy: boolean
  projectId: string
  onApprove: (decision: 'approved' | 'rejected') => void
}

export function ResultCard({ artifact, status, busy, projectId, onApprove }: ResultCardProps) {
  const data = artifact.data ?? {}
  const summary = data.summary ?? {}
  const item = status?.work_items?.find((row) => row.artifact_exists)
  const approval = item?.approval ?? 'missing'
  const approved = approval === 'approved'

  const metrics = Object.entries(summary).filter(([, value]) =>
    ['number', 'string', 'boolean'].includes(typeof value),
  )

  return (
    <Card className="border-l-4 border-l-primary px-5 py-4 animate-fade-up hover:shadow-md">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-[11px] text-muted-foreground">Engineer 结构化结果</div>
          <h3 className="mt-0.5 text-sm font-semibold">
            {OPERATION_LABELS[data.operation ?? ''] ?? data.operation ?? '任务完成'}
          </h3>
        </div>
        {approved ? (
          <Badge variant="ok" className="shrink-0">
            <CheckCircle2 className="h-3 w-3" />
            结果已确认
          </Badge>
        ) : (
          <Badge variant="warn" className="shrink-0">
            待人工确认
          </Badge>
        )}
      </div>
      <p className="mt-1.5 text-sm leading-6 text-muted-foreground">
        Agent 已完成确定性处理。请检查摘要、输出文件和完整证据后确认。
      </p>

      {metrics.length > 0 ? (
        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
          {metrics.map(([key, value]) => (
            <div key={key} className="rounded-lg border bg-muted/40 px-3 py-2">
              <div className="truncate text-[11px] text-muted-foreground">
                {metricLabel(key)}
              </div>
              <div className="mt-0.5 truncate text-sm font-semibold tabular-nums">
                {typeof value === 'number' ? value.toLocaleString() : String(value)}
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {(data.artifacts ?? []).length > 0 ? (
        <div className="mt-3 space-y-1.5">
          {data.artifacts!.map((row) => (
            <div
              key={row.path}
              className="flex items-center gap-2.5 rounded-lg border bg-muted/40 px-3 py-2 text-xs"
              title={row.path}
            >
              <FileOutput className="h-4 w-4 shrink-0 text-primary" />
              <strong className="shrink-0 font-medium">
                {ARTIFACT_TYPE_LABELS[row.type] ?? row.type}
              </strong>
              <code className="ml-auto truncate font-mono text-[11px] text-muted-foreground">
                {fileName(row.path)}
              </code>
              <a
                href={`/api/projects/${encodeURIComponent(projectId)}/download?path=${encodeURIComponent(row.path)}`}
                download
                title="下载文件"
                className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              >
                <Download className="h-3.5 w-3.5" />
              </a>
            </div>
          ))}
        </div>
      ) : null}

      {!approved ? (
        <div className="mt-4 flex flex-wrap gap-2">
          <Button size="sm" disabled={busy} onClick={() => onApprove('approved')}>
            <ThumbsUp />
            确认结果
          </Button>
          <span className="inline-flex items-center text-xs text-muted-foreground">
            确认后写入审计记录；若有问题直接在下方重新描述任务即可
          </span>
        </div>
      ) : null}

      <details className="group mt-3">
        <summary className="inline-flex cursor-pointer select-none items-center gap-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground">
          <FileJson className="h-3.5 w-3.5" />
          查看完整结果 JSON
          <ChevronDown className="h-3.5 w-3.5 transition-transform group-open:rotate-180" />
        </summary>
        <pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-muted/60 p-3 text-[11px] leading-5">
          {pretty(artifact)}
        </pre>
      </details>
    </Card>
  )
}
