/* SynapseRAG retrieval provenance viewer for the full knowledge graph. */

window.SynapseTraceView = (function () {
  "use strict";

  var API_ROOT = "/synapserag-api";
  var state = {
    opened: false,
    loaded: false,
    graphReady: false,
    traces: [],
    detail: null,
    query: null,
    view: "skeleton",
    pendingOverlay: null,
    requestGeneration: 0,
  };

  var frame = document.getElementById("knowledge-graph-frame");
  var serviceState = document.getElementById("synapse-service-state");
  var traceCount = document.getElementById("synapse-trace-count");
  var traceList = document.getElementById("synapse-trace-list");
  var detailPanel = document.getElementById("synapse-trace-detail");
  var traceTitle = document.getElementById("synapse-trace-title");
  var traceStatus = document.getElementById("synapse-trace-status");
  var traceMeta = document.getElementById("synapse-trace-meta");
  var queryList = document.getElementById("synapse-query-list");
  var stageList = document.getElementById("synapse-stage-list");
  var sourceList = document.getElementById("synapse-source-list");

  function clear(element) {
    while (element.firstChild) element.removeChild(element.firstChild);
  }

  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function errorMessage(payload, fallback) {
    if (!payload) return fallback;
    var detail = payload.detail;
    return payload.message || payload.error || detail && (detail.message || detail.error_code) || fallback;
  }

  async function api(path) {
    var response = await fetch(API_ROOT + path, {headers: {Accept: "application/json"}});
    var payload = null;
    try { payload = await response.json(); } catch (error) {}
    if (!response.ok) throw new Error(errorMessage(payload, "HTTP " + response.status));
    return payload;
  }

  function shortId(value) {
    value = String(value || "");
    return value.length > 28 ? value.slice(0, 13) + "…" + value.slice(-10) : value;
  }

  function formatDuration(value) {
    if (value === null || value === undefined || value === "") return "未记录";
    var milliseconds = Number(value);
    if (!Number.isFinite(milliseconds)) return "未记录";
    if (milliseconds > 0 && milliseconds < 1) return "<1 ms";
    if (milliseconds >= 1000) return (milliseconds / 1000).toFixed(milliseconds >= 10000 ? 1 : 2) + " s";
    return milliseconds.toFixed(milliseconds >= 100 ? 0 : 1) + " ms";
  }

  function formatTime(value) {
    if (!value) return "时间未记录";
    var date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
    }).format(date);
  }

  function setServiceState(text, mode) {
    serviceState.textContent = text;
    serviceState.className = "knowledge-graph-service-state " + (mode || "neutral");
  }

  function empty(container, message) {
    clear(container);
    container.appendChild(element("div", "synapse-empty", message));
  }

  function graphMessage(type, payload) {
    if (!frame || !frame.contentWindow) return;
    frame.contentWindow.postMessage(Object.assign({type: type}, payload || {}), window.location.origin);
  }

  function applyOverlay(overlay) {
    state.pendingOverlay = overlay;
    if (state.graphReady) graphMessage("synapserag:apply-overlay", {overlay: overlay});
  }

  function clearOverlay() {
    state.pendingOverlay = null;
    graphMessage("synapserag:clear-overlay");
  }

  function statusClass(status) {
    if (status === "success") return "status-chip success";
    if (status === "failed") return "status-chip danger";
    return "status-chip warning";
  }

  function renderTraceList() {
    traceCount.textContent = String(state.traces.length);
    if (!state.traces.length) {
      empty(traceList, "暂无可解释检索记录。运行方案生成或规则检查后在此查看。 ");
      return;
    }
    clear(traceList);
    state.traces.forEach(function (trace) {
      var button = element("button", "synapse-trace-item");
      button.type = "button";
      button.dataset.traceId = trace.trace_id;
      if (state.detail && state.detail.trace_id === trace.trace_id) button.classList.add("active");
      var heading = element("span", "synapse-trace-item-heading");
      heading.append(
        element("b", "", trace.task_id || trace.request_id || shortId(trace.trace_id)),
        element("i", trace.status === "success" ? "success" : trace.status === "failed" ? "danger" : "", trace.status || "unknown")
      );
      button.append(
        heading,
        element("span", "synapse-trace-item-id", shortId(trace.trace_id)),
        element("span", "synapse-trace-item-meta", formatTime(trace.started_at) + " · " + formatDuration(trace.duration_ms))
      );
      button.addEventListener("click", function () { selectTrace(trace.trace_id); });
      traceList.appendChild(button);
    });
  }

  function renderQueries() {
    clear(queryList);
    var queries = state.detail && state.detail.queries || [];
    if (!queries.length) {
      empty(queryList, "该任务没有查询明细");
      return;
    }
    queries.forEach(function (query, index) {
      var button = element("button", "synapse-query-item");
      button.type = "button";
      button.classList.toggle("active", state.query === query);
      button.append(
        element("span", "synapse-query-index", String(index + 1).padStart(2, "0")),
        element("span", "synapse-query-copy", query.query_id || "查询 " + (index + 1)),
        element("span", "synapse-query-evidence", (query.evidence || []).length + " 条证据")
      );
      button.title = query.text || query.query_id || "";
      button.addEventListener("click", function () { selectQuery(query); });
      queryList.appendChild(button);
    });
  }

  function renderStages(query) {
    clear(stageList);
    var stages = query && query.stage_summary || [];
    if (!stages.length) {
      empty(stageList, "未记录阶段数据");
      return;
    }
    stages.forEach(function (stage) {
      var row = element("div", "synapse-stage-row");
      var label = element("span", "", stage.label || stage.stage);
      var metrics = element("span", "synapse-stage-metrics");
      metrics.append(
        element("b", "", String(stage.count === undefined ? "—" : stage.count)),
        element("small", "", formatDuration(stage.duration_ms))
      );
      row.append(label, metrics);
      stageList.appendChild(row);
    });
  }

  function evidenceTitle(item) {
    return item.citation || item.title || item.source || item.document_id || item.chunk_id || "未命名来源";
  }

  function focusEvidence(item) {
    if (!state.detail || !state.query || !item.node_key) return;
    loadOverlay(item.node_key).then(function () {
      graphMessage("synapserag:focus-node", {nodeKey: item.node_key});
    }).catch(showError);
  }

  function renderSources(query) {
    clear(sourceList);
    var evidence = query && query.evidence || [];
    if (!evidence.length) {
      empty(sourceList, "该查询没有归因证据");
      return;
    }
    evidence.forEach(function (item, index) {
      var article = element("article", "synapse-source-item");
      if (item.node_key) article.dataset.nodeKey = item.node_key;
      var heading = element("div", "synapse-source-heading");
      heading.append(
        element("span", "synapse-source-rank", String(item.rank || index + 1)),
        element("b", "", evidenceTitle(item))
      );
      var score = Number(item.score);
      if (Number.isFinite(score)) heading.appendChild(element("span", "synapse-source-score", score.toFixed(3)));
      var excerpt = element("p", "", item.text || "没有可显示的原文片段");
      var channels = item.retrieval_reason && item.retrieval_reason.matched_channels || [];
      var channelText = channels.length ? channels.join(" / ") : "来源渠道未记录";
      var actions = element("div", "synapse-source-actions");
      var focus = element("button", "", "定位路径");
      focus.type = "button";
      focus.disabled = !item.node_key;
      focus.addEventListener("click", function () { focusEvidence(item); });
      var preview = element("a", "", "高亮原文");
      preview.target = "_blank";
      preview.rel = "noopener noreferrer";
      preview.href = item.chunk_id ? API_ROOT + "/evidence/" + encodeURIComponent(item.chunk_id) + "/preview" : "#";
      if (!item.chunk_id) preview.setAttribute("aria-disabled", "true");
      actions.append(focus, preview);
      article.append(heading, excerpt, element("div", "synapse-source-channel", channelText), actions);
      sourceList.appendChild(article);
    });
  }

  async function loadOverlay(evidenceNodeKey) {
    if (!state.detail || !state.query) return;
    var generation = ++state.requestGeneration;
    var params = new URLSearchParams({
      query_trace_id: state.query.query_trace_id,
      view: state.view,
      max_paths: evidenceNodeKey ? "3" : "1",
    });
    if (evidenceNodeKey && state.view === "skeleton") params.set("evidence_node_key", evidenceNodeKey);
    var graph = await api(
      "/retrieval-traces/" + encodeURIComponent(state.detail.trace_id) + "/graph?" + params.toString()
    );
    if (generation !== state.requestGeneration) return;
    var query = graph.queries && graph.queries[0];
    applyOverlay(query && query.graph_overlay || {nodes: [], edges: []});
  }

  function selectQuery(query) {
    state.query = query;
    renderQueries();
    renderStages(query);
    renderSources(query);
    loadOverlay().catch(showError);
  }

  async function selectTrace(traceId) {
    var generation = ++state.requestGeneration;
    setServiceState("正在加载轨迹", "loading");
    try {
      var detail = await api("/retrieval-traces/" + encodeURIComponent(traceId));
      if (generation !== state.requestGeneration) return;
      state.detail = detail;
      state.query = detail.queries && detail.queries[0] || null;
      detailPanel.hidden = false;
      traceTitle.textContent = detail.task_id || detail.request_id || shortId(detail.trace_id);
      traceTitle.title = detail.trace_id || "";
      traceStatus.textContent = detail.status || "unknown";
      traceStatus.className = statusClass(detail.status);
      traceMeta.textContent = [
        detail.purpose || "general",
        detail.agent_id || "未标记 Agent",
        detail.index_id || "未标记索引",
        formatDuration(detail.duration_ms),
      ].join(" · ");
      renderTraceList();
      renderQueries();
      renderStages(state.query);
      renderSources(state.query);
      setServiceState("轨迹已加载", "success");
      if (state.query) await loadOverlay();
    } catch (error) {
      if (generation === state.requestGeneration) showError(error);
    }
  }

  function showError(error) {
    setServiceState(error && error.message || "检索服务不可用", "danger");
  }

  async function refresh() {
    setServiceState("正在连接 SynapseRAG", "loading");
    try {
      var results = await Promise.all([api("/health"), api("/retrieval-traces?limit=50")]);
      var health = results[0];
      var listing = results[1];
      state.traces = listing.traces || [];
      state.loaded = true;
      renderTraceList();
      setServiceState(
        health.indexed === false ? "服务在线，知识库尚未激活" : "SynapseRAG 在线",
        health.indexed === false ? "warning" : "success"
      );
      if (state.traces.length && (!state.detail || !state.traces.some(function (item) { return item.trace_id === state.detail.trace_id; }))) {
        await selectTrace(state.traces[0].trace_id);
      }
    } catch (error) {
      state.traces = [];
      renderTraceList();
      showError(error);
    }
  }

  document.getElementById("synapse-refresh-traces").addEventListener("click", refresh);
  document.getElementById("synapse-clear-overlay").addEventListener("click", clearOverlay);
  document.querySelectorAll("[data-synapse-view]").forEach(function (button) {
    button.addEventListener("click", function () {
      state.view = button.dataset.synapseView;
      document.querySelectorAll("[data-synapse-view]").forEach(function (candidate) {
        candidate.classList.toggle("active", candidate === button);
      });
      loadOverlay().catch(showError);
    });
  });
  frame.addEventListener("load", function () {
    state.graphReady = true;
    if (state.pendingOverlay) applyOverlay(state.pendingOverlay);
  });
  window.addEventListener("message", function (event) {
    if (event.origin !== window.location.origin || event.source !== frame.contentWindow || !event.data) return;
    if (event.data.type === "synapserag:ready") {
      state.graphReady = true;
      if (state.pendingOverlay) applyOverlay(state.pendingOverlay);
    }
    if (event.data.type === "synapserag:node-select" && event.data.node) {
      var selected = sourceList.querySelector('[data-node-key="' + CSS.escape(event.data.node.name || "") + '"]');
      sourceList.querySelectorAll(".selected").forEach(function (item) { item.classList.remove("selected"); });
      if (selected) {
        selected.classList.add("selected");
        selected.scrollIntoView({block: "nearest"});
      }
    }
  });

  return {
    open: function () {
      state.opened = true;
      refresh();
    },
    close: function () { state.opened = false; },
    refresh: refresh,
  };
})();
