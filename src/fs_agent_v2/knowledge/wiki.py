from __future__ import annotations

from hashlib import sha256
from html import escape
from pathlib import Path
import re

from .index import KnowledgeIndex


HOME_FILENAME = "功能安全知识库.html"
KIND_LABELS = {
    "company_guide": "公司指导手册",
    "company_template": "公司交付模板",
    "domain_knowledge": "领域知识",
    "deterministic_rule": "确定性规则",
    "source_catalog": "资料来源目录",
    "knowledge_entry": "知识条目",
}
STATUS_LABELS = {
    "draft": "草稿 · 待审核",
    "approved": "已批准",
    "retired": "已废止",
}
RELATION_LABELS = {
    "has_tag": "带有标签",
    "belongs_to": "属于业务域",
    "guides_template": "指导模板",
    "catalogs": "目录收录",
}

STYLE = """
:root{--blue:#174a8b;--blue2:#0f3568;--ink:#172033;--muted:#617087;--line:#dfe5ef;--bg:#f4f7fb;--card:#fff;--warning:#fff3c9;--ok:#dff5e5}
*{box-sizing:border-box}body{font-family:Segoe UI,Microsoft YaHei,sans-serif;margin:0;background:var(--bg);color:var(--ink);line-height:1.65}
.topbar{background:linear-gradient(120deg,var(--blue2),var(--blue));color:#fff}.topbar-inner{max-width:1240px;margin:0 auto;padding:30px 32px}
.eyebrow{font-size:13px;letter-spacing:.12em;opacity:.78}.topbar h1{margin:4px 0 5px;font-size:30px}.topbar p{margin:0;opacity:.86}
main{max-width:1240px;margin:0 auto;padding:26px 32px 60px}h2{font-size:20px;margin:0 0 12px}a{color:#1557b0;text-decoration:none}a:hover{text-decoration:underline}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px;margin:0 0 18px;box-shadow:0 5px 18px rgba(28,54,89,.04)}
.guide-grid,.stats{display:grid;gap:12px}.guide-grid{grid-template-columns:repeat(3,1fr)}.guide-step{border-left:3px solid #3c78c5;padding:4px 14px}.guide-step b{display:block}.guide-step span{font-size:14px;color:var(--muted)}
.stats{grid-template-columns:repeat(4,1fr);margin-bottom:18px}.stat{background:#edf4ff;border:1px solid #d7e5fb;border-radius:12px;padding:14px}.stat b{display:block;font-size:23px;color:var(--blue)}.stat span{font-size:13px;color:var(--muted)}
.toolbar{display:grid;grid-template-columns:minmax(260px,1fr) 220px 180px auto;gap:10px;align-items:center}.toolbar input,.toolbar select,.toolbar button{height:42px;border:1px solid #cfd8e6;border-radius:9px;background:white;padding:0 12px;font:inherit}.toolbar button{cursor:pointer;color:var(--blue);font-weight:600}.result-count{color:var(--muted);font-size:14px;margin-top:10px}
.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:12px;background:#fff}table{width:100%;border-collapse:collapse;min-width:900px}th,td{padding:12px 13px;border-bottom:1px solid #e8ecf2;text-align:left;vertical-align:top}th{background:#f0f4fa;color:#44536a;font-size:13px;position:sticky;top:0}tr:last-child td{border-bottom:0}.doc-title{font-weight:650}.subtle{color:var(--muted);font-size:13px}
.badge{display:inline-block;background:#e7eefc;border-radius:999px;padding:2px 8px;margin:2px;font-size:12px;white-space:nowrap}.draft{background:var(--warning)}.approved{background:var(--ok)}.retired{background:#eceff3}
.notice{background:#fff8df;border:1px solid #ecd788;border-radius:10px;padding:12px 14px;color:#695719}.breadcrumbs{margin:0 0 16px}.meta-grid{display:grid;grid-template-columns:110px 1fr;gap:8px 16px}.meta-grid b{color:#43516a}.relation-list{margin:0;padding-left:20px}.relation-list li{margin:7px 0}
pre{white-space:pre-wrap;word-break:break-word;background:#f7f8fa;border:1px solid #e4e8ee;padding:18px;border-radius:10px;max-height:none;font-family:Consolas,Microsoft YaHei Mono,monospace;font-size:13px}
.empty{display:none;text-align:center;padding:30px;color:var(--muted)}
@media(max-width:850px){.guide-grid,.stats{grid-template-columns:1fr 1fr}.toolbar{grid-template-columns:1fr 1fr}.toolbar input{grid-column:1/-1}}
@media(max-width:560px){main,.topbar-inner{padding-left:18px;padding-right:18px}.guide-grid,.stats,.toolbar{grid-template-columns:1fr}.toolbar input{grid-column:auto}.topbar h1{font-size:25px}}
"""

FILTER_SCRIPT = """
<script>
const searchInput=document.getElementById('search');
const kindSelect=document.getElementById('kind-filter');
const statusSelect=document.getElementById('status-filter');
const rows=[...document.querySelectorAll('#knowledge-rows tr')];
const count=document.getElementById('result-count');
const empty=document.getElementById('empty');
function applyFilters(){
  const query=searchInput.value.trim().toLowerCase();
  const kind=kindSelect.value; const status=statusSelect.value; let visible=0;
  rows.forEach(row=>{const show=(!query||row.dataset.search.includes(query))&&(!kind||row.dataset.kind===kind)&&(!status||row.dataset.status===status);row.hidden=!show;if(show)visible++;});
  count.textContent=`显示 ${visible} / ${rows.length} 条知识资产`;
  empty.style.display=visible?'none':'block';
}
[searchInput,kindSelect,statusSelect].forEach(el=>el.addEventListener('input',applyFilters));
document.getElementById('reset').addEventListener('click',()=>{searchInput.value='';kindSelect.value='';statusSelect.value='';applyFilters();searchInput.focus();});
applyFilters();
</script>
"""


class WikiBuilder:
    def __init__(self, index: KnowledgeIndex):
        self.index = index

    def build(self, output: Path) -> dict:
        output.mkdir(parents=True, exist_ok=True)
        self._remove_old_generated_pages(output)
        documents = self.index.list_documents()
        rows = []
        for document in documents:
            filename = self._filename(document["id"], document["title"])
            tags = "".join(
                f'<span class="badge">{escape(self._tag_label(tag))}</span>'
                for tag in document["tags"]
            )
            kind_label = KIND_LABELS.get(document["kind"], document["kind"])
            status_label = STATUS_LABELS.get(document["status"], document["status"])
            search_text = " ".join(
                [document["title"], kind_label, status_label, *document["tags"]]
            ).lower()
            rows.append(
                f'<tr data-search="{escape(search_text, quote=True)}" '
                f'data-kind="{escape(document["kind"], quote=True)}" '
                f'data-status="{escape(document["status"], quote=True)}">'
                f'<td><a class="doc-title" href="{escape(filename, quote=True)}">{escape(document["title"])}</a>'
                f'<div class="subtle">{escape(document["id"])}</div></td>'
                f"<td>{escape(kind_label)}</td>"
                f'<td><span class="badge {escape(document["status"])}">{escape(status_label)}</span></td>'
                f"<td>{tags}</td><td>{document['chunk_count']}</td></tr>"
            )
            self._write_document_page(output / filename, document)
        stats = self.index.statistics()
        kind_options = "".join(
            f'<option value="{escape(kind, quote=True)}">{escape(KIND_LABELS.get(kind, kind))}</option>'
            for kind in sorted({document["kind"] for document in documents})
        )
        body = f"""
        <section class="card">
          <h2>这个 Wiki 怎么用？</h2>
          <div class="guide-grid">
            <div class="guide-step"><b>1. 搜索或筛选</b><span>按文件名、业务域、知识类型和审核状态找到资料。</span></div>
            <div class="guide-step"><b>2. 打开知识详情</b><span>查看原文件位置、版本、正文和它关联的指导手册或模板。</span></div>
            <div class="guide-step"><b>3. 判断能否正式使用</b><span>“草稿·待审核”只能探索；只有“已批准”才能支撑正式结论。</span></div>
          </div>
          <p class="notice"><b>注意：</b>这里是给人浏览和审核知识的目录。Agent 的内容检索由 RAG 完成；首页搜索不会替代 RAG，也不会自动修改原始 Word/Excel。</p>
        </section>
        <section class="stats">
          <div class="stat"><b>{stats['documents']}</b><span>知识文档</span></div>
          <div class="stat"><b>{stats['chunks']}</b><span>可检索证据块</span></div>
          <div class="stat"><b>{stats['nodes']}</b><span>图谱节点</span></div>
          <div class="stat"><b>{stats['edges']}</b><span>图谱关系</span></div>
        </section>
        <section class="card">
          <h2>知识目录</h2>
          <div class="toolbar">
            <input id="search" type="search" placeholder="搜索文件名、FTA、需求、架构、测试……" aria-label="搜索知识目录">
            <select id="kind-filter" aria-label="按知识类型筛选"><option value="">全部类型</option>{kind_options}</select>
            <select id="status-filter" aria-label="按审核状态筛选"><option value="">全部状态</option><option value="draft">草稿 · 待审核</option><option value="approved">已批准</option><option value="retired">已废止</option></select>
            <button id="reset" type="button">清空筛选</button>
          </div>
          <div id="result-count" class="result-count"></div>
        </section>
        <div class="table-wrap"><table><thead><tr><th>知识/模板名称</th><th>类型</th><th>审核状态</th><th>业务标签</th><th>证据块</th></tr></thead>
        <tbody id="knowledge-rows">{''.join(rows)}</tbody></table><div id="empty" class="empty">没有找到匹配的知识资产，请调整关键词或筛选条件。</div></div>
        {FILTER_SCRIPT}
        """
        home = self._page(
            "功能安全知识库",
            body,
            subtitle="公司指导手册、交付模板、规则与领域知识的统一浏览入口",
        )
        (output / HOME_FILENAME).write_text(home, encoding="utf-8")
        (output / "index.html").write_text(home, encoding="utf-8")
        (output / ".fs-knowledge-wiki").write_text("generated", encoding="utf-8")
        return {"output": str(output / HOME_FILENAME), "index": str(output / "index.html"), **stats}

    def _write_document_page(self, path: Path, summary: dict) -> None:
        document = self.index.get_document(summary["id"])
        if document is None:
            return
        edges = self.index.graph_neighbors(summary["id"], 1)
        relations = "".join(
            f"<li><b>{escape(edge['source_label'] or edge['source_id'])}</b> "
            f"— {escape(RELATION_LABELS.get(edge['relation'], edge['relation']))} → "
            f"<b>{escape(edge['target_label'] or edge['target_id'])}</b></li>"
            for edge in edges
        ) or "<li>暂无已登记关系</li>"
        tags = "".join(
            f'<span class="badge">{escape(self._tag_label(tag))}</span>'
            for tag in document["tags"]
        )
        kind_label = KIND_LABELS.get(document["kind"], document["kind"])
        status_label = STATUS_LABELS.get(document["status"], document["status"])
        body = f"""
        <p class="breadcrumbs"><a href="{HOME_FILENAME}">← 返回功能安全知识库</a></p>
        <section class="card">
          <div class="meta-grid">
            <b>知识 ID</b><span>{escape(document['id'])}</span>
            <b>类型</b><span>{escape(kind_label)}</span>
            <b>审核状态</b><span><span class="badge {escape(document['status'])}">{escape(status_label)}</span></span>
            <b>版本</b><span>{escape(document['version'])}</span>
            <b>原始文件</b><span>{escape(document['source_path'])}</span>
            <b>标签</b><span>{tags}</span>
          </div>
        </section>
        <p class="notice"><b>使用提示：</b>这页用于核对知识原文和关联关系。当前为草稿的资料不能直接支撑正式工程结论，需要领域负责人审核后晋升为“已批准”。</p>
        <section class="card"><h2>关联知识与模板</h2><ul class="relation-list">{relations}</ul></section>
        <section class="card"><h2>解析出的正文</h2><pre>{escape(document['body'])}</pre></section>
        """
        path.write_text(
            self._page(document["title"], body, subtitle=kind_label), encoding="utf-8"
        )

    @staticmethod
    def _filename(document_id: str, title: str) -> str:
        readable = Path(title).stem.strip()
        readable = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", readable)
        readable = re.sub(r"\s+", "_", readable).strip(" ._")
        readable = readable[:72] or "知识详情"
        suffix = sha256(document_id.encode("utf-8")).hexdigest()[:8]
        return f"{readable}-{suffix}.html"

    @staticmethod
    def _tag_label(tag: str) -> str:
        if tag.startswith("business_area:"):
            return "业务域：" + tag.split(":", 1)[1]
        labels = {
            "company_process": "公司流程",
            "company_template": "公司模板",
            "legacy_source": "旧项目来源",
            "pending_extraction": "待抽取",
        }
        return labels.get(tag, tag)

    @staticmethod
    def _remove_old_generated_pages(output: Path) -> None:
        for path in output.glob("doc-*.html"):
            path.unlink()
        for name in ("index.html", HOME_FILENAME):
            path = output / name
            if path.exists():
                path.unlink()

    @staticmethod
    def _page(title: str, body: str, subtitle: str = "") -> str:
        return (
            '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{escape(title)}</title><style>{STYLE}</style></head><body>"
            '<header class="topbar"><div class="topbar-inner"><div class="eyebrow">FUNCTIONAL SAFETY KNOWLEDGE</div>'
            f"<h1>{escape(title)}</h1><p>{escape(subtitle)}</p></div></header>"
            f"<main>{body}</main></body></html>"
        )
