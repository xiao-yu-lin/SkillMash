from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from skillmash.runtime.app_service import SkillMashService


class SkillMashRequestHandler(BaseHTTPRequestHandler):
    service = SkillMashService()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(INDEX_HTML)
            return
        if parsed.path == "/api/skills":
            self._send_json(self.service.list_skills())
            return
        if parsed.path == "/api/build":
            self._send_json(self.service.build_summary())
            return
        if parsed.path == "/api/graph":
            self._send_json(self.service.graph_summary())
            return
        if parsed.path == "/api/decompose":
            query = parse_qs(parsed.query)
            skill_id = self._first(query, "skill_id")
            self._handle_json_call(lambda: self.service.decompose(skill_id))
            return
        if parsed.path == "/api/match":
            query = parse_qs(parsed.query)
            source = self._first(query, "source")
            target = self._first(query, "target")
            self._handle_json_call(lambda: self.service.match(source, target))
            return
        if parsed.path == "/api/plan":
            query = parse_qs(parsed.query)
            task = self._first(query, "task")
            self._handle_json_call(lambda: self.service.plan(task))
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, format: str, *args: object) -> None:
        return

    def _handle_json_call(self, fn) -> None:
        try:
            self._send_json(fn())
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _first(query: dict[str, list[str]], name: str) -> str:
        value = query.get(name, [""])[0].strip()
        if not value:
            raise ValueError(f"Missing query parameter: {name}")
        return value


def run(host: str = "127.0.0.1", port: int = 8765, index_dir: str | None = None) -> None:
    SkillMashRequestHandler.service = SkillMashService(index_dir=index_dir)
    server = ThreadingHTTPServer((host, port), SkillMashRequestHandler)
    print(f"SkillMash UI running at http://{host}:{port}")
    server.serve_forever()


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>SkillMash 能力编排</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --ink: #1e293b;
      --muted: #64748b;
      --line: #d8dee8;
      --accent: #0f766e;
      --accent-weak: #d9f3ef;
      --warn: #a16207;
      --code: #0f172a;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      letter-spacing: 0;
    }

    header {
      padding: 20px 28px 14px;
      background: #ffffff;
      border-bottom: 1px solid var(--line);
    }

    h1 {
      margin: 0 0 6px;
      font-size: 24px;
      line-height: 1.2;
    }

    header p {
      margin: 0;
      color: var(--muted);
      max-width: 980px;
      line-height: 1.6;
    }

    main {
      display: grid;
      grid-template-columns: minmax(280px, 360px) 1fr;
      gap: 18px;
      padding: 18px 28px 28px;
      min-height: calc(100vh - 92px);
    }

    aside, section {
      min-width: 0;
    }

    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }

    .panel-head {
      padding: 13px 14px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }

    .panel-head h2 {
      margin: 0;
      font-size: 15px;
      line-height: 1.3;
    }

    .content {
      padding: 14px;
    }

    .skill-list {
      display: grid;
      gap: 8px;
      max-height: calc(100vh - 180px);
      overflow: auto;
      padding: 12px;
    }

    .skill-row {
      width: 100%;
      text-align: left;
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 8px;
      padding: 10px;
      cursor: pointer;
      color: var(--ink);
    }

    .skill-row.active {
      border-color: var(--accent);
      background: var(--accent-weak);
    }

    .skill-row strong {
      display: block;
      font-size: 13px;
      margin-bottom: 4px;
    }

    .skill-row span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }

    .toolbar {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
    }

    button, select, input {
      font: inherit;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: #fff;
      color: var(--ink);
      min-height: 34px;
    }

    button {
      padding: 7px 11px;
      cursor: pointer;
    }

    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }

    input {
      padding: 7px 10px;
      min-width: min(480px, 100%);
      flex: 1;
    }

    select {
      padding: 7px 28px 7px 10px;
    }

    .tabs {
      display: flex;
      gap: 6px;
      padding: 10px 10px 0;
      background: #fff;
    }

    .tab {
      border-radius: 7px 7px 0 0;
      border-bottom: 0;
      color: var(--muted);
    }

    .tab.active {
      color: #fff;
      background: var(--accent);
      border-color: var(--accent);
    }

    .view { display: none; }
    .view.active { display: block; }

    dl {
      display: grid;
      grid-template-columns: 120px 1fr;
      gap: 8px 12px;
      margin: 0;
    }

    dt {
      color: var(--muted);
    }

    dd {
      margin: 0;
      min-width: 0;
    }

    .chips {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }

    .chip {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 3px 8px;
      background: #f8fafc;
      color: #334155;
      font-size: 12px;
    }

    .plan-list {
      display: grid;
      gap: 10px;
    }

    .plan {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 12px;
    }

    .plan h3 {
      margin: 0 0 8px;
      font-size: 14px;
    }

    .steps {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      margin: 10px 0;
    }

    .step {
      background: #eef6f6;
      color: #134e4a;
      border: 1px solid #b8dcd8;
      border-radius: 7px;
      padding: 6px 8px;
      font-size: 12px;
    }

    pre {
      margin: 12px 0 0;
      background: var(--code);
      color: #e2e8f0;
      border-radius: 8px;
      padding: 12px;
      overflow: auto;
      max-height: 360px;
      line-height: 1.45;
      font-size: 12px;
    }

    .grid-two {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }

    .empty {
      color: var(--muted);
      line-height: 1.6;
    }

    @media (max-width: 860px) {
      main {
        grid-template-columns: 1fr;
        padding: 14px;
      }
      header {
        padding: 16px 14px 12px;
      }
      .skill-list {
        max-height: 280px;
      }
      .grid-two {
        grid-template-columns: 1fr;
      }
      dl {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <header>
    <h1>SkillMash 能力编排</h1>
    <p>展示技能注册、能力图谱、原子技能拆解、组合匹配和任务规划。界面只调用应用服务 API，核心编排逻辑保留在 Python 包中。</p>
  </header>
  <main>
    <aside class="panel">
      <div class="panel-head">
        <h2>技能库</h2>
        <button id="refreshSkills" title="刷新技能列表">刷新</button>
      </div>
      <div id="skillList" class="skill-list"></div>
    </aside>

    <section class="panel">
      <div class="tabs">
        <button class="tab active" data-view="detail">技能详情</button>
        <button class="tab" data-view="decompose">原子拆解</button>
        <button class="tab" data-view="match">组合匹配</button>
        <button class="tab" data-view="plan">任务规划</button>
        <button class="tab" data-view="build">构建产物</button>
        <button class="tab" data-view="graph">图谱边</button>
      </div>

      <div id="detail" class="view active content"></div>

      <div id="decompose" class="view content">
        <div class="toolbar">
          <select id="decomposeSkill"></select>
          <button class="primary" id="runDecompose">拆解</button>
        </div>
        <pre id="decomposeOutput"></pre>
      </div>

      <div id="match" class="view content">
        <div class="toolbar">
          <select id="matchSource"></select>
          <select id="matchTarget"></select>
          <button class="primary" id="runMatch">匹配</button>
        </div>
        <pre id="matchOutput"></pre>
      </div>

      <div id="plan" class="view content">
        <div class="toolbar">
          <input id="taskInput" value="帮我搜索 AI Agent 最新趋势，并生成 PPT" />
          <button class="primary" id="runPlan">规划</button>
        </div>
        <div id="planOutput" class="plan-list" style="margin-top: 14px;"></div>
      </div>

      <div id="graph" class="view content">
        <div class="toolbar">
          <button class="primary" id="loadGraph">加载图谱</button>
        </div>
        <pre id="graphOutput"></pre>
      </div>

      <div id="build" class="view content">
        <div class="toolbar">
          <button class="primary" id="loadBuild">加载构建产物</button>
        </div>
        <pre id="buildOutput"></pre>
      </div>
    </section>
  </main>

  <script>
    let skills = [];
    let activeSkillId = null;

    const $ = (id) => document.getElementById(id);

    async function api(path) {
      const response = await fetch(path);
      const data = await response.json();
      if (!response.ok || data.error) throw new Error(data.error || response.statusText);
      return data;
    }

    function pretty(data) {
      return JSON.stringify(data, null, 2);
    }

    function chips(values) {
      return `<div class="chips">${values.map(v => `<span class="chip">${escapeHtml(v)}</span>`).join("")}</div>`;
    }

    function escapeHtml(text) {
      return String(text).replace(/[&<>"']/g, ch => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
      }[ch]));
    }

    async function loadSkills() {
      skills = await api("/api/skills");
      if (!activeSkillId && skills.length) activeSkillId = skills[0].id;
      renderSkillList();
      fillSelects();
      renderDetail();
    }

    function renderSkillList() {
      $("skillList").innerHTML = skills.map(skill => `
        <button class="skill-row ${skill.id === activeSkillId ? "active" : ""}" data-skill="${skill.id}">
          <strong>${escapeHtml(skill.name)}</strong>
          <span>${escapeHtml(skill.kind)} · ${escapeHtml(skill.id)}</span>
        </button>
      `).join("");

      document.querySelectorAll(".skill-row").forEach(row => {
        row.addEventListener("click", () => {
          activeSkillId = row.dataset.skill;
          renderSkillList();
          renderDetail();
        });
      });
    }

    function fillSelects() {
      const options = skills.map(skill => `<option value="${skill.id}">${skill.id}</option>`).join("");
      ["decomposeSkill", "matchSource", "matchTarget"].forEach(id => $(id).innerHTML = options);
      $("matchTarget").value = skills.find(s => s.id === "read_webpage")?.id || skills[1]?.id || "";
      $("decomposeSkill").value = "search_and_make_ppt";
      $("matchSource").value = "web_search";
    }

    function renderDetail() {
      const skill = skills.find(item => item.id === activeSkillId);
      if (!skill) {
        $("detail").innerHTML = `<p class="empty">暂无技能。</p>`;
        return;
      }
      $("detail").innerHTML = `
        <dl>
          <dt>ID</dt><dd>${escapeHtml(skill.id)}</dd>
          <dt>名称</dt><dd>${escapeHtml(skill.name)}</dd>
          <dt>类型</dt><dd>${escapeHtml(skill.kind)}</dd>
          <dt>描述</dt><dd>${escapeHtml(skill.description)}</dd>
          <dt>输入</dt><dd>${chips(skill.inputs.map(p => `${p.name}: ${p.type}`))}</dd>
          <dt>输出</dt><dd>${chips(skill.outputs.map(o => `${o.name}: ${o.type}`))}</dd>
          <dt>能力标签</dt><dd>${chips(skill.capability_tags)}</dd>
          <dt>数据标签</dt><dd>${chips(skill.data_tags)}</dd>
          <dt>包含</dt><dd>${skill.contains.length ? chips(skill.contains) : '<span class="empty">无</span>'}</dd>
        </dl>
      `;
    }

    function switchTab(view) {
      document.querySelectorAll(".tab").forEach(tab => tab.classList.toggle("active", tab.dataset.view === view));
      document.querySelectorAll(".view").forEach(panel => panel.classList.toggle("active", panel.id === view));
    }

    async function runDecompose() {
      const skillId = $("decomposeSkill").value;
      $("decomposeOutput").textContent = pretty(await api(`/api/decompose?skill_id=${encodeURIComponent(skillId)}`));
    }

    async function runMatch() {
      const source = $("matchSource").value;
      const target = $("matchTarget").value;
      $("matchOutput").textContent = pretty(await api(`/api/match?source=${encodeURIComponent(source)}&target=${encodeURIComponent(target)}`));
    }

    async function runPlan() {
      const task = $("taskInput").value;
      const data = await api(`/api/plan?task=${encodeURIComponent(task)}`);
      const plans = data.plans || [];
      if (!plans.length) {
        $("planOutput").innerHTML = `<p class="empty">没有找到可行方案。</p><pre>${pretty(data)}</pre>`;
        return;
      }
      $("planOutput").innerHTML = `
        <div class="plan">
          <strong>目标模型</strong>
          <pre>${pretty(data.goal)}</pre>
        </div>
        ${plans.map(plan => `
          <div class="plan">
            <h3>${escapeHtml(plan.id)} · score ${plan.score} · ${escapeHtml(plan.status)}</h3>
            <div class="steps">${plan.steps.map((step, index) => `
              <span class="step">${index + 1}. ${escapeHtml(step.skill_id)}</span>
            `).join("")}</div>
            <p class="empty">${escapeHtml(plan.reason)}</p>
            <pre>${pretty(plan)}</pre>
          </div>
        `).join("")}
      `;
    }

    async function loadGraph() {
      $("graphOutput").textContent = pretty(await api("/api/graph"));
    }

    async function loadBuild() {
      $("buildOutput").textContent = pretty(await api("/api/build"));
    }

    document.querySelectorAll(".tab").forEach(tab => {
      tab.addEventListener("click", () => switchTab(tab.dataset.view));
    });
    $("refreshSkills").addEventListener("click", loadSkills);
    $("runDecompose").addEventListener("click", runDecompose);
    $("runMatch").addEventListener("click", runMatch);
    $("runPlan").addEventListener("click", runPlan);
    $("loadGraph").addEventListener("click", loadGraph);
    $("loadBuild").addEventListener("click", loadBuild);

    loadSkills().then(() => runPlan()).catch(err => {
      $("detail").innerHTML = `<p class="empty">${escapeHtml(err.message)}</p>`;
    });
  </script>
</body>
</html>
"""
