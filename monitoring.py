from __future__ import annotations

import os
import time
from collections import Counter

from prometheus_client import CollectorRegistry, Gauge, generate_latest


class SupervisorMonitor:
    def __init__(self):
        self.registry = CollectorRegistry()
        self.workflow_count = Gauge(
            "a2a_workflows",
            "Commander workflows by status",
            ["status"],
            registry=self.registry,
        )
        self.agent_count = Gauge(
            "a2a_agents",
            "Discovered agents by status",
            ["status"],
            registry=self.registry,
        )
        self.active_leases = Gauge(
            "a2a_active_leases",
            "Currently held agent leases",
            registry=self.registry,
        )
        self.agent_resource = Gauge(
            "a2a_agent_resource_percent",
            "Agent resource utilization",
            ["agent", "resource"],
            registry=self.registry,
        )
        self.alert_count = Gauge(
            "a2a_alerts",
            "Active supervisor alerts by severity",
            ["severity"],
            registry=self.registry,
        )

    def snapshot(self, manager) -> dict:
        workflows = manager.list_workflows()
        agents = manager.list_agents()
        leases = manager.list_agent_leases()
        alerts = evaluate_alerts(workflows, agents)
        self._update_metrics(workflows, agents, leases, alerts)
        return {
            "workflows": workflows,
            "agents": agents,
            "leases": leases,
            "alerts": alerts,
            "summary": {
                "workflow_count": len(workflows),
                "agent_count": len(agents),
                "active_leases": len(leases),
                "alert_count": len(alerts),
            },
        }

    def prometheus(self, manager) -> bytes:
        self.snapshot(manager)
        return generate_latest(self.registry)

    def _update_metrics(self, workflows, agents, leases, alerts) -> None:
        self.workflow_count.clear()
        for status, count in Counter(item.get("status", "unknown") for item in workflows).items():
            self.workflow_count.labels(status=status).set(count)
        self.agent_count.clear()
        self.agent_resource.clear()
        for agent in agents:
            metadata = agent.get("metadata", {}) or {}
            status = metadata.get("status", "unknown")
            self.agent_count.labels(status=status).inc()
            label = f"{agent.get('ip')}:{agent.get('port')}"
            for resource, key in {
                "cpu": "resource_cpu_percent",
                "memory": "resource_memory_percent",
                "gpu": "resource_gpu_percent",
                "gpu_memory": "resource_gpu_memory_percent",
            }.items():
                value = _as_float(metadata.get(key))
                if value is not None:
                    self.agent_resource.labels(agent=label, resource=resource).set(value)
        self.active_leases.set(len(leases))
        self.alert_count.clear()
        for severity, count in Counter(item["severity"] for item in alerts).items():
            self.alert_count.labels(severity=severity).set(count)


def evaluate_alerts(workflows: list[dict], agents: list[dict]) -> list[dict]:
    cpu_limit = float(os.environ.get("A2A_ALERT_CPU_PERCENT", "90"))
    memory_limit = float(os.environ.get("A2A_ALERT_MEMORY_PERCENT", "90"))
    gpu_limit = float(os.environ.get("A2A_ALERT_GPU_PERCENT", "95"))
    heartbeat_limit = float(os.environ.get("A2A_ALERT_HEARTBEAT_SECONDS", "20"))
    alerts = []
    for workflow in workflows:
        if workflow.get("status") in {"failed", "paused"}:
            alerts.append(
                {
                    "severity": "critical" if workflow.get("status") == "failed" else "warning",
                    "type": "workflow_status",
                    "subject": workflow.get("workflow_id"),
                    "message": workflow.get("last_error") or f"workflow is {workflow.get('status')}",
                }
            )
    for agent in agents:
        metadata = agent.get("metadata", {}) or {}
        subject = f"{agent.get('ip')}:{agent.get('port')}"
        heartbeat = _as_float(metadata.get("heartbeat_ts"))
        if heartbeat is not None and time.time() - heartbeat > heartbeat_limit:
            alerts.append(
                {"severity": "critical", "type": "heartbeat", "subject": subject, "message": "heartbeat is stale"}
            )
        for resource, key, limit in (
            ("cpu", "resource_cpu_percent", cpu_limit),
            ("memory", "resource_memory_percent", memory_limit),
            ("gpu", "resource_gpu_percent", gpu_limit),
        ):
            value = _as_float(metadata.get(key))
            if value is not None and value >= limit:
                alerts.append(
                    {
                        "severity": "warning",
                        "type": "resource",
                        "subject": subject,
                        "message": f"{resource} utilization {value:.1f}% >= {limit:.1f}%",
                    }
                )
    return alerts


def _as_float(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


SUPERVISOR_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>A2A Supervisor</title><style>
body{margin:0;font:14px Arial,sans-serif;background:#f4f6f8;color:#17202a}header{background:#17202a;color:#fff;padding:18px 24px}main{padding:20px;max-width:1400px;margin:auto}.summary{display:grid;grid-template-columns:repeat(4,minmax(130px,1fr));gap:12px}.metric{background:#fff;border:1px solid #d9e0e6;padding:14px;border-radius:6px}.metric b{display:block;font-size:24px;margin-top:6px}section{margin-top:22px}table{width:100%;border-collapse:collapse;background:#fff}th,td{text-align:left;padding:10px;border-bottom:1px solid #e5e9ed}th{background:#eef2f5}.critical{color:#b42318}.warning{color:#b54708}@media(max-width:700px){.summary{grid-template-columns:1fr 1fr}table{font-size:12px}}
</style></head><body><header><strong>A2A Supervisor</strong></header><main><div class="summary" id="summary"></div><section><h2>Alerts</h2><table><thead><tr><th>Severity</th><th>Subject</th><th>Message</th></tr></thead><tbody id="alerts"></tbody></table></section><section><h2>Workflows</h2><table><thead><tr><th>ID</th><th>Status</th><th>Workflow</th><th>Error</th></tr></thead><tbody id="workflows"></tbody></table></section><section><h2>Agents</h2><table><thead><tr><th>Endpoint</th><th>Status</th><th>Skills</th><th>CPU</th><th>Memory</th><th>GPU</th></tr></thead><tbody id="agents"></tbody></table></section></main><script>
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function refresh(){const d=await fetch('/supervisor/snapshot').then(r=>r.json());const s=d.summary;document.querySelector('#summary').innerHTML=[['Workflows',s.workflow_count],['Agents',s.agent_count],['Leases',s.active_leases],['Alerts',s.alert_count]].map(x=>`<div class="metric">${x[0]}<b>${x[1]}</b></div>`).join('');document.querySelector('#alerts').innerHTML=d.alerts.map(a=>`<tr><td class="${esc(a.severity)}">${esc(a.severity)}</td><td>${esc(a.subject)}</td><td>${esc(a.message)}</td></tr>`).join('');document.querySelector('#workflows').innerHTML=d.workflows.map(w=>`<tr><td>${esc(w.workflow_id)}</td><td>${esc(w.status)}</td><td>${esc(w.workflow)}</td><td>${esc(w.last_error)}</td></tr>`).join('');document.querySelector('#agents').innerHTML=d.agents.map(a=>{const m=a.metadata||{};return `<tr><td>${esc(a.ip)}:${esc(a.port)}</td><td>${esc(m.status)}</td><td>${esc(m.skill_ids||m.skills)}</td><td>${esc(m.resource_cpu_percent)}</td><td>${esc(m.resource_memory_percent)}</td><td>${esc(m.resource_gpu_percent)}</td></tr>`}).join('')};refresh();setInterval(refresh,5000);
</script></body></html>"""
