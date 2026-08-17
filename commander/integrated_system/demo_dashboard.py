from __future__ import annotations


def build_demo_dashboard_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Integrated Demo Console</title>
  <style>
    :root {
      --bg: #f6f2e8;
      --panel: rgba(255, 252, 246, 0.96);
      --ink: #1f2b36;
      --muted: #667685;
      --line: rgba(31, 43, 54, 0.12);
      --accent: #b44c35;
      --accent-2: #1d6b76;
      --ok: #2c7a4b;
      --warn: #a86b18;
      --bad: #aa3131;
      --chip: rgba(29, 107, 118, 0.08);
      --shadow: 0 18px 40px rgba(61, 48, 34, 0.12);
      --radius: 20px;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(180, 76, 53, 0.10), transparent 30%),
        radial-gradient(circle at top right, rgba(29, 107, 118, 0.12), transparent 26%),
        linear-gradient(180deg, #f9f4ea 0%, #f0e8da 100%);
    }
    .shell {
      max-width: 1520px;
      margin: 0 auto;
      padding: 28px;
      display: grid;
      gap: 18px;
    }
    .hero, .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }
    .hero {
      padding: 28px;
      display: grid;
      gap: 18px;
    }
    .hero-top {
      display: flex;
      justify-content: space-between;
      gap: 18px;
      flex-wrap: wrap;
      align-items: end;
    }
    .eyebrow {
      color: var(--accent);
      font-size: 12px;
      letter-spacing: 0.2em;
      text-transform: uppercase;
      font-weight: 800;
    }
    .hero h1 {
      margin: 8px 0 0;
      font-size: 34px;
      line-height: 1.1;
    }
    .hero-subtitle {
      margin-top: 10px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.7;
      max-width: 820px;
    }
    .actions {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
    }
    button {
      border: 0;
      border-radius: 999px;
      padding: 11px 18px;
      font-size: 14px;
      font-weight: 700;
      cursor: pointer;
      transition: transform 0.15s ease, opacity 0.15s ease, background 0.15s ease;
    }
    button:hover { transform: translateY(-1px); }
    button:disabled { cursor: wait; opacity: 0.72; }
    .primary { background: var(--accent); color: #fff; }
    .secondary { background: rgba(31, 43, 54, 0.08); color: var(--ink); }
    .ghost { background: rgba(29, 107, 118, 0.12); color: var(--accent-2); }
    .status-strip {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
    }
    .strip-card {
      padding: 16px 18px;
      border-radius: 16px;
      background: rgba(255, 255, 255, 0.68);
      border: 1px solid var(--line);
    }
    .strip-card .label {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .strip-card .value {
      margin-top: 6px;
      font-size: 22px;
      font-weight: 800;
      word-break: break-word;
    }
    .alert {
      min-height: 22px;
      color: var(--muted);
      font-size: 14px;
      padding: 0 4px;
    }
    .grid {
      display: grid;
      grid-template-columns: 350px minmax(0, 1fr);
      gap: 18px;
      align-items: start;
    }
    .sidebar {
      display: grid;
      gap: 18px;
    }
    .panel { padding: 20px; }
    .panel h2 {
      margin: 0 0 14px;
      font-size: 18px;
    }
    .panel .subhead {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.7;
      margin-bottom: 14px;
    }
    .template-list, .mission-list {
      display: grid;
      gap: 10px;
      max-height: 420px;
      overflow: auto;
    }
    .mission-list { max-height: 380px; }
    .template-item, .mission-item {
      padding: 14px;
      border-radius: 16px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.7);
      cursor: pointer;
      transition: border-color 0.15s ease, background 0.15s ease;
    }
    .template-item.active, .mission-item.active {
      border-color: rgba(180, 76, 53, 0.38);
      background: rgba(180, 76, 53, 0.08);
    }
    .mini {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.55;
    }
    .status-pill {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 90px;
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 12px;
      font-weight: 800;
      margin-top: 8px;
      background: rgba(29, 107, 118, 0.12);
      color: var(--accent-2);
    }
    .status-queued, .status-running, .status-paused {
      background: rgba(168, 107, 24, 0.12);
      color: var(--warn);
    }
    .status-completed {
      background: rgba(44, 122, 75, 0.12);
      color: var(--ok);
    }
    .status-failed, .status-aborted {
      background: rgba(170, 49, 49, 0.12);
      color: var(--bad);
    }
    .content {
      display: grid;
      gap: 18px;
    }
    .summary-grid, .brief-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 14px;
    }
    .summary-card, .brief-card, .text-card {
      padding: 18px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.76);
    }
    .big, .brief-value {
      margin-top: 8px;
      font-size: 28px;
      font-weight: 900;
    }
    .chip-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }
    .chip, .brief-line, .algo-line, .section-line {
      padding: 8px 10px;
      border-radius: 12px;
      background: var(--chip);
      font-size: 13px;
      line-height: 1.55;
    }
    .brief-list, .algo-list, .section-list {
      margin-top: 12px;
      display: grid;
      gap: 6px;
    }
    .info-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
      gap: 14px;
    }
    .info-list, .plain-list {
      display: grid;
      gap: 8px;
      margin-top: 10px;
    }
    .info-item, .plain-item {
      padding: 10px 12px;
      border-radius: 12px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.7);
      font-size: 13px;
      line-height: 1.6;
    }
    .workflow-flow {
      display: grid;
      grid-template-columns: repeat(7, minmax(0, 1fr));
      gap: 12px;
    }
    .flow-step {
      padding: 14px;
      border-radius: 16px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.74);
    }
    .flow-step.active {
      border-color: rgba(168, 107, 24, 0.35);
      background: rgba(168, 107, 24, 0.10);
    }
    .flow-step.done {
      border-color: rgba(44, 122, 75, 0.3);
      background: rgba(44, 122, 75, 0.08);
    }
    .flow-index {
      width: 28px;
      height: 28px;
      border-radius: 50%;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: 12px;
      font-weight: 800;
      margin-bottom: 10px;
      background: rgba(31, 43, 54, 0.08);
    }
    details.result-detail {
      border: 1px solid var(--line);
      border-radius: 16px;
      background: rgba(255,255,255,0.76);
      overflow: hidden;
    }
    details.result-detail + details.result-detail {
      margin-top: 12px;
    }
    details.result-detail summary {
      list-style: none;
      cursor: pointer;
      padding: 16px;
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
    }
    details.result-detail summary::-webkit-details-marker { display: none; }
    .detail-body {
      padding: 0 16px 16px;
      display: grid;
      gap: 12px;
    }
    .detail-block {
      padding: 12px 14px;
      border-radius: 14px;
      background: rgba(29, 107, 118, 0.06);
      font-size: 13px;
      line-height: 1.7;
    }
    .detail-card-grid {
      display: grid;
      gap: 10px;
      margin-top: 10px;
    }
    .detail-card {
      padding: 12px 14px;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.72);
    }
    .trace-box, .json-box {
      margin-top: 10px;
      padding: 14px;
      border-radius: 14px;
      background: #202832;
      color: #edf3f8;
      font-size: 12px;
      line-height: 1.55;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      min-height: 80px;
    }
    .hint {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.7;
    }
    .toolbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }
    @media (max-width: 1180px) {
      .grid { grid-template-columns: 1fr; }
      .workflow-flow { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .status-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 760px) {
      .shell { padding: 16px; }
      .status-strip, .workflow-flow { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div class="hero-top">
        <div>
          <div class="eyebrow">Integrated Demo</div>
          <h1>多 Agent 协同演示系统</h1>
          <div class="hero-subtitle">
            这套演示系统以任务库、多帧连续点迹、分支真实算法调用和仿真闭环为主，方便直接做汇报展示。
          </div>
        </div>
        <div class="actions">
          <button id="import-selected-template" class="primary">导入所选示例任务</button>
          <button id="refresh-all" class="secondary">刷新状态</button>
          <button id="pause-selected" class="secondary">暂停</button>
          <button id="resume-selected" class="secondary">恢复</button>
          <button id="abort-selected" class="secondary">终止</button>
          <button id="adjust-selected" class="ghost">注入调整</button>
        </div>
      </div>
      <div class="status-strip">
        <div class="strip-card">
          <div class="label">服务状态</div>
          <div class="value" id="service-status">-</div>
        </div>
        <div class="strip-card">
          <div class="label">任务数量</div>
          <div class="value" id="mission-count">0</div>
        </div>
        <div class="strip-card">
          <div class="label">运行模式</div>
          <div class="value" id="system-mode">-</div>
        </div>
        <div class="strip-card">
          <div class="label">当前任务库选择</div>
          <div class="value" id="selected-template-name">-</div>
        </div>
      </div>
      <div class="alert" id="alert">页面已就绪，先在左侧选择一个示例任务后导入。</div>
    </section>

    <section class="grid">
      <aside class="sidebar">
        <section class="panel">
          <h2>示例任务库</h2>
          <div class="subhead" id="template-summary">请选择一个任务模板。导入后将按多帧连续点迹执行航迹跟踪。</div>
          <div class="template-list" id="template-list"></div>
        </section>

        <section class="panel">
          <h2>任务列表</h2>
          <div class="mission-list" id="mission-list"></div>
        </section>
      </aside>

      <div class="content">
        <section class="panel">
          <h2>任务概览</h2>
          <div id="mission-overview" class="hint">请选择一个任务查看详情。</div>
        </section>

        <section class="summary-grid" id="summary-grid"></section>

        <section class="panel">
          <h2>当前任务态势</h2>
          <div id="task-text" class="info-grid"></div>
        </section>

        <section class="panel">
          <h2>能力流程推进</h2>
          <div id="workflow-flow" class="workflow-flow"></div>
        </section>

        <section class="panel">
          <h2>关键能力结论</h2>
          <div id="brief-grid" class="brief-grid"></div>
        </section>

        <section class="panel">
          <h2>任务对象与资源</h2>
          <div id="mission-elements" class="info-grid"></div>
        </section>

        <section class="panel">
          <div class="toolbar">
            <h2 style="margin:0;">能力链结果</h2>
            <div class="hint">点击展开可查看每一步的中文解释、方案卡片与实际算法实现。</div>
          </div>
          <div id="capability-details" style="margin-top:14px;"></div>
        </section>

        <section class="panel">
          <h2>执行轨迹</h2>
          <div id="trace-box" class="trace-box"></div>
        </section>

        <section class="panel">
          <div class="toolbar">
            <h2 style="margin:0;">原始 JSON</h2>
            <button id="load-raw" class="secondary">加载原始 JSON</button>
          </div>
          <div id="raw-hint" class="hint">点击按钮查看完整任务快照。</div>
          <div id="json-box" class="json-box" style="display:none;"></div>
        </section>
      </div>
    </section>
  </div>

  <script>
    const capabilityOrder = [
      "cognition",
      "tracking",
      "threat_assessment",
      "decision_planning",
      "compliance_authorization",
      "execution_control",
      "effect_evaluation"
    ];

    const capabilityLabels = {
      cognition: "认知识别",
      tracking: "航迹跟踪",
      threat_assessment: "威胁评估",
      decision_planning: "方案规划",
      compliance_authorization: "合规授权",
      execution_control: "执行控制",
      effect_evaluation: "效果评估"
    };

    let selectedTemplateId = null;
    let selectedMissionId = null;
    let selectedMissionSnapshot = null;
    let missionLibrary = [];
    let rawJsonCache = {};
    let lastRenderSignature = "";

    function valueOr(value, fallback) {
      return value === undefined || value === null || value === "" ? fallback : value;
    }

    function showAlert(message, isError) {
      const alert = document.getElementById("alert");
      alert.textContent = message;
      alert.style.color = isError ? "var(--bad)" : "var(--ink)";
    }

    function statusClass(status) {
      return "status-pill status-" + valueOr(status, "running");
    }

    async function api(url, options) {
      const response = await fetch(url, Object.assign({ cache: "no-store" }, options || {}));
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || response.statusText);
      }
      return response.json();
    }

    function selectedTemplate() {
      return missionLibrary.find(function (item) { return item.template_id === selectedTemplateId; }) || null;
    }

    function updateSelectedTemplateInfo() {
      const current = selectedTemplate();
      document.getElementById("selected-template-name").textContent = current ? current.display_name : "-";
      document.getElementById("template-summary").textContent = current
        ? current.display_summary + " 当前包含 " + valueOr(current.frame_count, 0) + " 帧连续点迹。"
        : "请选择一个任务模板。导入后将按多帧连续点迹执行航迹跟踪。";
    }

    function renderTemplateList(library) {
      const root = document.getElementById("template-list");
      root.innerHTML = "";
      if (!library.length) {
        root.innerHTML = '<div class="hint">当前没有可用的示例任务库。</div>';
        return;
      }
      library.forEach(function (item) {
        const node = document.createElement("div");
        node.className = "template-item" + (item.template_id === selectedTemplateId ? " active" : "");
        node.innerHTML = `
          <strong>${valueOr(item.display_name, item.template_id)}</strong>
          <div class="mini" style="margin-top:6px;">${valueOr(item.display_summary, "")}</div>
          <div class="chip-row">
            <div class="chip">${valueOr(item.frame_count, 0)} 帧点迹</div>
            <div class="chip">${valueOr(item.contact_count, 0)} 个目标</div>
            <div class="chip">${valueOr(item.friendly_count, 0)} 个平台</div>
          </div>
        `;
        node.onclick = function () {
          selectedTemplateId = item.template_id;
          renderTemplateList(missionLibrary);
          updateSelectedTemplateInfo();
        };
        root.appendChild(node);
      });
    }

    function renderMissionList(missions) {
      const root = document.getElementById("mission-list");
      const displayMissions = missions.slice();
      if (
        selectedMissionSnapshot &&
        !displayMissions.some(function (mission) { return mission.workflow_id === selectedMissionSnapshot.workflow_id; })
      ) {
        displayMissions.unshift(selectedMissionSnapshot);
      }
      root.innerHTML = "";
      if (!displayMissions.length) {
        root.innerHTML = '<div class="hint">还没有运行任务，先导入一个示例任务。</div>';
        return;
      }
      displayMissions.forEach(function (mission) {
        const node = document.createElement("div");
        node.className = "mission-item" + (mission.workflow_id === selectedMissionId ? " active" : "");
        node.innerHTML = `
          <strong>${valueOr(mission.display_name, mission.workflow_id)}</strong>
          <div class="${statusClass(mission.status)}">${valueOr(mission.status, "-")}</div>
          <div class="mini" style="margin-top:8px;">任务编号: ${mission.workflow_id}</div>
          <div class="mini">提交时间: ${valueOr(mission.submitted_at, "-")}</div>
        `;
        node.onclick = function () { selectMission(mission.workflow_id); };
        root.appendChild(node);
      });
    }

    function renderOverview(report) {
      const overview = report.overview || {};
      document.getElementById("mission-overview").innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;">
          <div>
            <div class="mini">任务名称</div>
            <div style="font-size:22px;font-weight:800;margin-top:6px;">${valueOr(overview.display_name, "未命名任务")}</div>
            <div class="mini" style="margin-top:8px;">任务编号: ${report.workflow_id}</div>
          </div>
          <div class="${statusClass(report.status)}">${valueOr(report.status, "-")}</div>
        </div>
        <div class="chip-row">
          <div class="chip">目标 ${valueOr(overview.contacts_count, 0)} 个</div>
          <div class="chip">平台 ${valueOr(overview.friendly_count, 0)} 个</div>
          <div class="chip">点迹帧数 ${valueOr(overview.frame_count, 0)} 帧</div>
          <div class="chip">成功阈值 ${valueOr(overview.success_threshold, "-")}</div>
          <div class="chip">最大重规划 ${valueOr(overview.max_replans, 0)}</div>
          <div class="chip">规划偏好 ${valueOr(overview.planning_focus, "default")}</div>
        </div>
        <div class="hint" style="margin-top:12px;">${valueOr(overview.display_summary, valueOr(overview.objective, ""))}</div>
      `;
    }

    function renderSummary(report) {
      const performance = report.performance || {};
      document.getElementById("summary-grid").innerHTML = `
        <div class="summary-card">
          <div class="mini">闭环评估分</div>
          <div class="big">${valueOr(performance.overall_score, "-")}</div>
        </div>
        <div class="summary-card">
          <div class="mini">执行完成度</div>
          <div class="big">${valueOr(performance.completion_ratio, "-")}</div>
        </div>
        <div class="summary-card">
          <div class="mini">已完成能力</div>
          <div class="big">${valueOr(performance.completed_capabilities_count, 0)}/${valueOr(performance.total_capabilities, 7)}</div>
        </div>
        <div class="summary-card">
          <div class="mini">已处理点迹帧数</div>
          <div class="big">${valueOr(performance.frames_processed, 0)}</div>
        </div>
      `;
    }

    function renderTaskText(report) {
      const taskText = report.task_text || {};
      document.getElementById("task-text").innerHTML = `
        <div class="text-card">
          <strong>任务概况</strong>
          <div class="plain-list">
            ${(taskText.overview_lines || []).map(function (line) { return `<div class="plain-item">${line}</div>`; }).join("")}
          </div>
        </div>
        <div class="text-card">
          <strong>敌方目标</strong>
          <div class="plain-list">
            ${(taskText.hostile_contacts || []).length
              ? (taskText.hostile_contacts || []).map(function (line) { return `<div class="plain-item">${line}</div>`; }).join("")
              : '<div class="plain-item">暂无敌方目标。</div>'}
          </div>
        </div>
        <div class="text-card">
          <strong>环境与约束</strong>
          <div class="plain-list">
            ${(taskText.environment || []).map(function (line) { return `<div class="plain-item">${line}</div>`; }).join("")}
            ${(taskText.constraints || []).map(function (line) { return `<div class="plain-item">${line}</div>`; }).join("")}
          </div>
        </div>
      `;
    }

    function renderWorkflow(report) {
      const flowMap = {};
      (report.capability_flow || []).forEach(function (item) {
        flowMap[item.capability] = item;
      });
      const current = report.current_capability;
      const root = document.getElementById("workflow-flow");
      root.innerHTML = "";
      capabilityOrder.forEach(function (capability, index) {
        const item = flowMap[capability] || {};
        const done = item.status === "completed";
        const stateClass = done ? "done" : (current === capability ? "active" : "pending");
        const statusText = done ? "已完成" : (current === capability ? "执行中" : "等待执行");
        const node = document.createElement("div");
        node.className = "flow-step " + stateClass;
        node.innerHTML = `
          <div class="flow-index">${index + 1}</div>
          <div style="font-weight:700;">${capabilityLabels[capability]}</div>
          <div class="mini" style="margin-top:8px;">${statusText}</div>
        `;
        root.appendChild(node);
      });
    }

    function renderBriefCards(report) {
      const root = document.getElementById("brief-grid");
      root.innerHTML = "";
      (report.capability_cards || []).forEach(function (card) {
        const node = document.createElement("div");
        node.className = "brief-card";
        node.innerHTML = `
          <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;">
            <h3 style="margin:0;">${card.label}</h3>
            <div class="${statusClass(card.status)}">${valueOr(card.status, "-")}</div>
          </div>
          <div class="brief-value">${valueOr(card.headline, "-")}</div>
          <div class="mini" style="margin-top:8px;line-height:1.7;">${valueOr(card.summary, "")}</div>
          <div class="brief-list">
            ${(card.bullets || []).length
              ? (card.bullets || []).map(function (line) { return `<div class="brief-line">${line}</div>`; }).join("")
              : '<div class="brief-line">暂无更多摘要。</div>'}
          </div>
        `;
        root.appendChild(node);
      });
    }

    function renderMissionElements(report) {
      const taskText = report.task_text || {};
      document.getElementById("mission-elements").innerHTML = `
        <div class="text-card">
          <strong>己方平台</strong>
          <div class="info-list">
            ${(taskText.friendly_platforms || []).length
              ? (taskText.friendly_platforms || []).map(function (line) { return `<div class="info-item">${line}</div>`; }).join("")
              : '<div class="info-item">暂无己方平台。</div>'}
          </div>
        </div>
        <div class="text-card">
          <strong>人工干预状态</strong>
          <div class="info-list">
            ${(taskText.operator || []).map(function (line) { return `<div class="info-item">${line}</div>`; }).join("")}
          </div>
        </div>
      `;
    }

    function renderDetailSection(section) {
      if (section.cards && section.cards.length) {
        return `
          <div class="detail-block">
            <strong>${valueOr(section.title, "详细卡片")}</strong>
            <div class="detail-card-grid">
              ${section.cards.map(function (card) {
                return `
                  <div class="detail-card">
                    <div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start;">
                      <strong>${valueOr(card.title, "未命名")}</strong>
                      <span class="mini">${valueOr(card.subtitle, "")}</span>
                    </div>
                    <div class="mini" style="margin-top:8px;">${valueOr(card.summary, "")}</div>
                    <div class="chip-row" style="margin-top:10px;">
                      <div class="chip">得分 ${valueOr(card.score, "-")}</div>
                    </div>
                    <div class="section-list">
                      ${(card.lines || []).map(function (line) { return `<div class="section-line">${line}</div>`; }).join("")}
                    </div>
                  </div>
                `;
              }).join("")}
            </div>
          </div>
        `;
      }
      if (section.lines && section.lines.length) {
        return `
          <div class="detail-block">
            <strong>${valueOr(section.title, "关键要点")}</strong>
            <div class="section-list">
              ${section.lines.map(function (line) { return `<div class="section-line">${line}</div>`; }).join("")}
            </div>
          </div>
        `;
      }
      return `
        <div class="detail-block">
          <strong>${valueOr(section.title, "说明")}</strong><br />
          ${valueOr(section.text, "暂无说明。")}
        </div>
      `;
    }

    function renderCapabilityDetails(report) {
      const root = document.getElementById("capability-details");
      root.innerHTML = "";
      (report.capability_cards || []).forEach(function (card) {
        const node = document.createElement("details");
        node.className = "result-detail";
        node.innerHTML = `
          <summary>
            <div>
              <strong>${card.label}</strong>
              <div class="mini">${valueOr(card.headline, "-")}</div>
            </div>
            <div class="mini">点击展开</div>
          </summary>
          <div class="detail-body">
            ${(card.detail_sections || []).map(renderDetailSection).join("")}
            <div class="detail-block">
              <strong>实际调用算法实现</strong>
              <div class="algo-list">
                ${(card.algorithms || []).length
                  ? (card.algorithms || []).map(function (line) { return `<div class="algo-line">${line}</div>`; }).join("")
                  : '<div class="algo-line">当前没有单独记录的算法实现。</div>'}
              </div>
            </div>
          </div>
        `;
        root.appendChild(node);
      });
    }

    function renderTrace(report) {
      const trace = report.trace || [];
      const root = document.getElementById("trace-box");
      if (!trace.length) {
        root.textContent = "暂无轨迹。";
        return;
      }
      root.textContent = trace.map(function (item) {
        const detail = valueOr(item.detail, "");
        return "[" + valueOr(item.timestamp, "-") + "] " + valueOr(item.label, item.event) + (detail ? " | " + detail : "");
      }).join("\\n");
    }

    function resetRawJsonSection() {
      document.getElementById("raw-hint").textContent = "点击按钮查看完整任务快照。";
      document.getElementById("json-box").style.display = "none";
      document.getElementById("json-box").textContent = "";
    }

    async function loadRawJson() {
      if (!selectedMissionId) {
        showAlert("请先选中一个任务。", true);
        return;
      }
      if (!rawJsonCache[selectedMissionId]) {
        document.getElementById("raw-hint").textContent = "正在加载完整任务 JSON...";
        rawJsonCache[selectedMissionId] = await api("/missions/" + selectedMissionId);
      }
      document.getElementById("raw-hint").textContent = "下面是完整任务快照。";
      document.getElementById("json-box").style.display = "block";
      document.getElementById("json-box").textContent = JSON.stringify(rawJsonCache[selectedMissionId], null, 2);
    }

    function renderReport(report) {
      renderOverview(report);
      renderSummary(report);
      renderTaskText(report);
      renderWorkflow(report);
      renderBriefCards(report);
      renderMissionElements(report);
      renderCapabilityDetails(report);
      renderTrace(report);
    }

    async function refreshHealth() {
      const health = await api("/health");
      document.getElementById("service-status").textContent = valueOr(health.status, "unknown");
      document.getElementById("mission-count").textContent = String(valueOr(health.mission_count, 0));
      document.getElementById("system-mode").textContent = valueOr(health.mode, "safe");
    }

    async function refreshMissionLibrary() {
      missionLibrary = await api("/demo/mission-library");
      if (!selectedTemplateId && missionLibrary.length) {
        selectedTemplateId = missionLibrary[0].template_id;
      }
      renderTemplateList(missionLibrary);
      updateSelectedTemplateInfo();
    }

    async function refreshMissions() {
      const missions = await api("/missions");
      renderMissionList(missions);
      if (!selectedMissionId && missions.length) {
        selectedMissionId = missions[0].workflow_id;
      }
    }

    async function refreshSelectedMission(force) {
      if (!selectedMissionId) return;
      const report = await api("/missions/" + selectedMissionId + "/report");
      selectedMissionSnapshot = {
        workflow_id: report.workflow_id,
        status: report.status,
        submitted_at: report.submitted_at,
        display_name: valueOr((report.overview || {}).display_name, report.workflow_id)
      };
      const signature = [
        valueOr(report.status, "-"),
        valueOr(report.current_capability, "-"),
        valueOr(report.finished_at, "-"),
        valueOr((report.performance || {}).overall_score, "-"),
        valueOr((report.performance || {}).completion_ratio, "-"),
        valueOr((report.performance || {}).frames_processed, "-"),
        String((report.trace || []).length)
      ].join("|");
      if (!force && signature === lastRenderSignature) {
        return;
      }
      lastRenderSignature = signature;
      renderReport(report);
    }

    async function selectMission(workflowId) {
      selectedMissionId = workflowId;
      lastRenderSignature = "";
      resetRawJsonSection();
      await refreshSelectedMission(true);
      await refreshMissions();
    }

    async function submitSelectedTemplate() {
      if (!selectedTemplateId) {
        showAlert("请先从任务库中选择一个示例任务。", true);
        return;
      }
      try {
        const template = selectedTemplate();
        showAlert("正在导入示例任务并启动多帧演示...", false);
        const sample = await api("/demo/mission-library/" + selectedTemplateId);
        const mission = await api("/missions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(sample)
        });
        selectedMissionId = mission.workflow_id;
        selectedMissionSnapshot = mission;
        lastRenderSignature = "";
        resetRawJsonSection();
        showAlert("已导入示例任务：" + valueOr(template && template.display_name, mission.workflow_id), false);
        await refreshHealth();
        await refreshMissions();
        await refreshSelectedMission(true);
      } catch (error) {
        console.error(error);
        showAlert("导入示例任务失败: " + error.message, true);
      }
    }

    async function missionControl(action) {
      if (!selectedMissionId) {
        showAlert("请先选中一个任务。", true);
        return;
      }
      await api("/missions/" + selectedMissionId + "/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: action })
      });
      showAlert("任务 " + selectedMissionId + " 已执行 " + action + " 操作。", false);
      lastRenderSignature = "";
      await refreshMissions();
      await refreshSelectedMission(true);
    }

    async function adjustSelectedMission() {
      if (!selectedMissionId) {
        showAlert("请先选中一个任务。", true);
        return;
      }
      await api("/missions/" + selectedMissionId + "/adjust", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          note: "Operator switched the plan to containment-oriented behavior.",
          planning_focus: "containment",
          success_threshold: 0.5
        })
      });
      showAlert("已向当前任务注入“偏向围控”的人工调整。若任务仍在运行，会在下一能力前生效。", false);
      lastRenderSignature = "";
      await refreshSelectedMission(true);
    }

    async function boot() {
      document.getElementById("import-selected-template").onclick = submitSelectedTemplate;
      document.getElementById("refresh-all").onclick = async function () {
        await refreshHealth();
        await refreshMissionLibrary();
        await refreshMissions();
        await refreshSelectedMission(true);
      };
      document.getElementById("pause-selected").onclick = function () { missionControl("pause"); };
      document.getElementById("resume-selected").onclick = function () { missionControl("resume"); };
      document.getElementById("abort-selected").onclick = function () { missionControl("abort"); };
      document.getElementById("adjust-selected").onclick = adjustSelectedMission;
      document.getElementById("load-raw").onclick = function () {
        loadRawJson().catch(function (error) {
          console.error(error);
          showAlert("加载原始 JSON 失败: " + error.message, true);
        });
      };

      await refreshHealth();
      await refreshMissionLibrary();
      await refreshMissions();
      await refreshSelectedMission(true);

      window.setInterval(async function () {
        try {
          await refreshHealth();
          await refreshMissions();
          await refreshSelectedMission(false);
        } catch (error) {
          console.error(error);
        }
      }, 3000);
    }

    boot().catch(function (error) {
      console.error(error);
      showAlert("初始化失败: " + error.message, true);
    });
  </script>
</body>
</html>
"""
