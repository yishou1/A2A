/* Focused AMOS REST client: simulation, active scenario and analysis backend. */

window.PlatformAPI = (function () {
  async function request(url, options) {
    const response = await fetch(url, options);
    var payload = null;
    try { payload = await response.json(); } catch (error) {}
    if (!response.ok) {
      var detail = payload && typeof payload.error === "string" && payload.error ||
        payload && payload.error && payload.error.message ||
        payload && payload.detail && (payload.detail.message || payload.detail) ||
        "HTTP " + response.status;
      var failure = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      failure.status = response.status;
      failure.payload = payload;
      throw failure;
    }
    return payload;
  }

  function get(url) {
    return request(url);
  }

  function post(url, body) {
    return request(url, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body || {}),
    });
  }

  async function data(url) {
    const response = await get(url);
    return response.data;
  }

  return {
    get: get,
    post: post,
    loadSimState: function () { return data("/api/v1/sim/state"); },
    loadScenarios: function () { return data("/api/v1/scenarios"); },
    loadScenario: function (id) {
      return data("/api/v1/scenarios/" + encodeURIComponent(id));
    },
    loadScenarioSupport: function () { return data("/api/v1/scenario-support"); },
    loadEvidenceProducts: function () { return data("/api/v1/evidence-products"); },
    configureDirector: function (options) {
      return post("/api/v1/director/configure", options || {});
    },
    getDirectorState: function () { return data("/api/v1/director/state"); },
    loadRun: function (runId) {
      return data("/api/v1/runs/" + encodeURIComponent(runId));
    },
    runReportUrl: function (runId, format) {
      return "/api/v1/runs/" + encodeURIComponent(runId) +
        "/report?format=" + encodeURIComponent(format);
    },
    runDirectorAction: function (action, options) {
      var body = Object.assign({}, options || {}, {action: action});
      return post("/api/v1/director/action", body);
    },
    issueSimCommand: function (command) {
      return post("/api/v1/sim/commands", command || {});
    },
    getBackendHealth: function () { return data("/api/v1/a2a/backend/health"); },
    loadRuntimeAlgorithms: function () { return data("/api/v1/a2a/algorithms"); },
    submitA2AWorkflow: function (options) {
      return post("/api/v1/a2a/workflows/submit", options || {});
    },
    getWorkflowView: function (workflowId) {
      return data("/api/v1/a2a/workflows/" + encodeURIComponent(workflowId) + "/view");
    },
    resumeWorkflow: function (workflowId, options) {
      return post(
        "/api/v1/a2a/workflows/" + encodeURIComponent(workflowId) + "/resume",
        options || {}
      );
    },
  };
})();
