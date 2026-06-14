import sys
import os
import time
import re
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy

# Ensure imports work from project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from registry.nacos_manager import NacosRegistry
from a2a_protocol.client import A2AClient
from a2a_protocol.messages import is_success_response
from bpel_workflow import BPELActivatity, BPELWorkflowCatalog
from commander_agent.agent_leases import AgentLeaseManager
from local_runtime import LocalAgentRuntime
from workflow_state_store import WorkflowStateStore, new_workflow_id, utc_now_iso
from workflow_payloads import attachment_snapshot, merge_attachments, normalize_attachments
import json

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load_env_file(path=os.path.join(PROJECT_ROOT, ".env")):
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value

class CommanderAgent:
    def __init__(
        self,
        mode: str = None,
        workflow: str = "dynamic",
        workflow_file: str = None,
        workflow_id: str = None,
        state_dir: str = None,
        resume: bool = False,
        mock_eval_score: int = None,
        mock_decision: str = None,
        max_workers: int = 4,
        initial_context: dict = None,
        registry=None,
        lease_manager=None,
    ):
        load_env_file()
        self.mode = (mode or os.environ.get("A2A_COMMANDER_MODE", "remote")).lower()
        if self.mode not in {"remote", "local"}:
            raise ValueError("mode must be either 'remote' or 'local'")

        self.workflow = workflow
        self.workflow_file = workflow_file
        self.workflow_id = workflow_id or os.environ.get("A2A_WORKFLOW_ID") or new_workflow_id()
        self.max_workers = max(1, int(max_workers))
        self.initial_context = deepcopy(initial_context or {})
        self._checkpoint_lock = threading.RLock()
        self.workflow_catalog = BPELWorkflowCatalog(PROJECT_ROOT)
        self.bpel_definition = None
        if self.workflow == "bpel" or self.workflow_file:
            self.bpel_definition = self.workflow_catalog.load(self.workflow_file)
            self.workflow = "bpel"
        default_state_dir = os.path.join(PROJECT_ROOT, ".a2a_state", "workflows")
        self.state_store = WorkflowStateStore(
            state_dir or os.environ.get("A2A_STATE_DIR", default_state_dir)
        )
        self.resume = resume
        self.registry = None if self.mode == "local" else (registry or NacosRegistry())
        self.lease_manager = lease_manager
        if self.mode == "remote" and self.lease_manager is None:
            self.lease_manager = AgentLeaseManager(self.registry)
        self.local_runtime = LocalAgentRuntime() if self.mode == "local" else None
        self._last_agent_responses = {}
        self.mock_eval_score = mock_eval_score
        self.mock_decision = mock_decision
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        self.api_base = os.environ.get("OPENAI_API_BASE", "")
        self.model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self.workflow_state = self._load_or_initialize_workflow_state()
        self.workflow_context = self.workflow_state["context"]
        print("Commander Agent online. Global Orchestration initiated.")
        print(f"Execution mode: {self.mode}")
        print(f"Workflow: {self.workflow} ({self.workflow_id})")
        if self.bpel_definition:
            print(f"BPEL definition: {self.bpel_definition.source_path}")
            print(f"Parallel workers: {self.max_workers}")
        print(f"Workflow state: {self.state_store.state_path(self.workflow_id)}")
        print(f"LLM model: {self.model}")
        if self.api_base:
            print(f"LLM base URL: {self.api_base}")

    def delegate_task(self, role_needed: str, task_payload: dict, stream: bool = False):
        print(f"\n--- STEP: Resolving next unit for role: {role_needed} ---")
        if self.mode == "local":
            return self.delegate_local_task(role_needed, task_payload, stream=stream)
        
        if self.lease_manager:
            return self._delegate_task_with_lease(role_needed, task_payload, stream=stream)

        # Compatibility path for registries without lease support.
        instances = self.registry.discover_service("A2A-Agent", {"role": role_needed, "status": "idle"})
        if not instances:
            print(f"[ERROR] No available agents found for role {role_needed}. Replanning needed!")
            return False

        last_error = None
        for index, target in enumerate(instances, start=1):
            ip = target.get("ip")
            port = target.get("port")
            print(f"[FOUND] Candidate {index}/{len(instances)} for {role_needed} at {ip}:{port}")
            success, error = self._delegate_remote_candidate(
                role_needed,
                target,
                task_payload,
                stream=stream,
            )
            if success:
                return True
            last_error = error
            print(f"[WARN] Candidate {ip}:{port} failed: {error}")

        print(f"[ERROR] A2A communication failed after trying {len(instances)} candidates: {last_error}")
        return False

    def delegate_parallel_task(self, role_needed: str, task_payload: dict, stream: bool = False):
        print(f"\n--- PARALLEL STEP: Resolving all units for role: {role_needed} ---")
        if self.mode == "local":
            return self.delegate_local_task(role_needed, task_payload, stream=stream)

        if self.lease_manager:
            return self._delegate_parallel_task_with_lease(role_needed, task_payload, stream=stream)

        instances = self.registry.discover_service("A2A-Agent", {"role": role_needed, "status": "idle"})
        if not instances:
            print(f"[ERROR] No available agents found for parallel role {role_needed}.")
            return False

        print(f"[PARALLEL] Dispatching {len(instances)} {role_needed} instance(s).")
        with ThreadPoolExecutor(
            max_workers=min(self.max_workers, len(instances)),
            thread_name_prefix=f"a2a-{role_needed}",
        ) as executor:
            futures = {
                executor.submit(
                    self._delegate_remote_candidate,
                    role_needed,
                    target,
                    task_payload,
                    stream,
                ): target
                for target in instances
            }
            results = []
            for future in as_completed(futures):
                target = futures[future]
                success, error = future.result()
                results.append(success)
                if not success:
                    print(f"[WARN] Parallel candidate {target.get('ip')}:{target.get('port')} failed: {error}")

        success_count = sum(results)
        print(f"[PARALLEL] Completed {success_count}/{len(instances)} {role_needed} assignment(s).")
        return bool(results) and all(results)

    def _delegate_task_with_lease(self, role_needed: str, task_payload: dict, stream: bool = False):
        work_item = task_payload.get("work_item", f"{self.workflow_id}:{role_needed}")
        attempted_keys = set()
        last_error = None
        while True:
            lease = self.lease_manager.acquire_one(
                role_needed,
                self.workflow_id,
                work_item,
                exclude_keys=attempted_keys,
            )
            if lease is None:
                if last_error is None:
                    print(f"[ERROR] No available agents found for role {role_needed}. Replanning needed!")
                else:
                    print(f"[ERROR] A2A communication failed after trying {len(attempted_keys)} candidates: {last_error}")
                return False

            target = lease.target
            attempted_keys.add(lease.instance_key)
            label = self._candidate_label(target)
            print(f"[LEASE] {self.workflow_id} acquired {role_needed} at {label}")
            try:
                success, error = self._delegate_remote_candidate(
                    role_needed,
                    target,
                    task_payload,
                    stream=stream,
                )
            finally:
                self._release_agent_lease(lease)

            if success:
                return True
            last_error = error
            print(f"[WARN] Candidate {label} failed: {error}")

    def _delegate_parallel_task_with_lease(self, role_needed: str, task_payload: dict, stream: bool = False):
        work_item = task_payload.get("work_item", f"{self.workflow_id}:{role_needed}")
        leases = self.lease_manager.acquire_all(role_needed, self.workflow_id, work_item)
        if not leases:
            print(f"[ERROR] No available agents found for parallel role {role_needed}.")
            return False

        print(f"[PARALLEL] Dispatching {len(leases)} leased {role_needed} instance(s).")
        with ThreadPoolExecutor(
            max_workers=min(self.max_workers, len(leases)),
            thread_name_prefix=f"a2a-{role_needed}",
        ) as executor:
            futures = {
                executor.submit(
                    self._delegate_leased_candidate,
                    lease,
                    role_needed,
                    task_payload,
                    stream,
                ): lease.target
                for lease in leases
            }
            results = []
            for future in as_completed(futures):
                target = futures[future]
                success, error = future.result()
                results.append(success)
                if not success:
                    print(f"[WARN] Parallel candidate {self._candidate_label(target)} failed: {error}")

        success_count = sum(results)
        print(f"[PARALLEL] Completed {success_count}/{len(leases)} leased {role_needed} assignment(s).")
        return bool(results) and all(results)

    def _delegate_leased_candidate(self, lease, role_needed: str, task_payload: dict, stream: bool = False):
        try:
            return self._delegate_remote_candidate(
                role_needed,
                lease.target,
                task_payload,
                stream=stream,
            )
        finally:
            self._release_agent_lease(lease)

    def _release_agent_lease(self, lease):
        try:
            self.lease_manager.release(lease)
            print(f"[LEASE] Released {lease.role} at {lease.instance_key}")
        except Exception as exc:
            print(f"[WARN] Failed to mirror lease release for {lease.instance_key}: {exc}")

    @staticmethod
    def _candidate_label(target: dict):
        return f"{target.get('ip')}:{target.get('port')}"

    def _delegate_remote_candidate(self, role_needed: str, target: dict, task_payload: dict, stream: bool = False):
        ip = target.get("ip")
        port = target.get("port")
        label = self._candidate_label(target)
        client = A2AClient(ip, port)
        try:
            card = client.discover()
            print(f"[DISCOVERY] {label} Agent Card from '{card.get('name')}'")

            token = client.authenticate()
            print(f"[AUTH] {label} JWT Token: {token[:10]}...")

            if stream:
                print(f"[STREAM] {label} receiving '{role_needed}' updates:")
                for event_data in client.send_message_stream(task_payload):
                    data = json.loads(event_data)
                    print(
                        f"   -> [{label}] [{data.get('status')}] "
                        f"{data.get('progress', '')} {data.get('message', '')}"
                    )
                return True, None

            res = client.send_message(task_payload)
            self._record_agent_response(role_needed, res)
            print(f"[SEND] {label} Task Response: {res}")
            return True, None
        except Exception as exc:
            return False, exc

    def delegate_local_task(self, role_needed: str, task_payload: dict, stream: bool = False):
        try:
            response, events = self.local_runtime.execute(role_needed, task_payload, stream=stream)
            self._record_agent_response(role_needed, response)
            card = response.get("agent_card", {})
            print(f"[LOCAL DISCOVERY] Using local Agent Card from '{card.get('name')}'")
            print(f"[LOCAL AUTH] Obtained local token: {response.get('token')}")

            if stream:
                print(f"[LOCAL STREAM] Receiving task updates from '{role_needed}':")
                for data in events:
                    print(f"   -> [{data.get('status')}] {data.get('progress', '')} {data.get('message', '')}")
            else:
                print(f"[LOCAL SEND] Task Response: {response}")
            return is_success_response(response)
        except Exception as e:
            print(f"[ERROR] Local task execution failed: {e}")
            return False

    def _record_agent_response(self, role: str, response: dict):
        if isinstance(response, dict):
            with self._checkpoint_lock:
                self._last_agent_responses[role] = deepcopy(response)
                work_item = response.get("work_item")
                if work_item:
                    self.workflow_context.setdefault("agent_results", {})[work_item] = deepcopy(response)

    def ask_llm(self, battle_log: list):
        log_str = "\n".join(battle_log)
        if self.mock_decision:
            return f"MOCK_LOCAL_DECISION: {self.mock_decision}"

        if self.mode == "local" or not self.api_key:
            score = self.mock_eval_score if self.mock_eval_score is not None else 40
            if score >= 60:
                return (
                    f"MOCK_LLM_DECISION: Destroy rate is {score}%. "
                    "ASSAULT. Beachhead defenses are sufficiently suppressed. "
                    "(Local/mock decision)"
                )
            return (
                f"MOCK_LLM_DECISION: Destroy rate is {score}%. "
                "The beachhead defenses are too strong. ABORT ASSAULT. "
                "Initiate RE-PLAN and call in bomber support. "
                "(Local/mock decision)"
            )
            
        try:
            from langchain_openai import ChatOpenAI
            from langchain_core.prompts import PromptTemplate

            llm_kwargs = {
                "api_key": self.api_key,
                "model": self.model,
            }
            if self.api_base:
                llm_kwargs["base_url"] = self.api_base

            llm = ChatOpenAI(**llm_kwargs)
            prompt = PromptTemplate.from_template("You are a battlefield commander AI analyzing report:\n{logs}\nAs an AI Commander, briefly decide whether to 'ASSAULT' or 'RE-PLAN'. Reason succinctly in one sentence.")
            chain = prompt | llm
            res = chain.invoke({"logs": log_str})
            return res.content
        except Exception as e:
            return f"LLM_ERROR: {str(e)}"

    def build_llm(self):
        if not self.api_key:
            return None

        from langchain_openai import ChatOpenAI

        llm_kwargs = {
            "api_key": self.api_key,
            "model": self.model,
        }
        if self.api_base:
            llm_kwargs["base_url"] = self.api_base
        return ChatOpenAI(**llm_kwargs)

    def initial_workflow_context(self):
        return {
            "workflow_id": self.workflow_id,
            "workflow_mode": self.mode,
            "workflow_name": self.workflow,
            "workflow_status": "running",
            "workflow_activatity": 0,
            "current_activatity": None,
            "active_activatities": [],
            "last_work_item": None,
            "last_role": None,
            "last_error": None,
            "sector": "Sector_A",
            "coordinates": "120.5E, 35.1N",
            "recon_report": None,
            "strike_result": None,
            "eval_score": None,
            "commander_decision": None,
            "assault_result": None,
            "replan_result": None,
            "risk_assessments": [],
            "scheduled_tasks": [],
            "resources": [],
            "target_histories": [],
            "planning_objectives": [],
            "candidate_plans": [],
            "constraints": [],
            "authorization": {},
            "decision_planning_result": None,
            "compliance_authorization_result": None,
            "compliance_decision": None,
            "agent_outputs": {},
            "agent_results": {},
            "battle_log": [],
            "completed_roles": [],
            "attachments": [],
            "work_list": self._initial_work_list(),
        }

    def _initial_work_list(self):
        if not self.bpel_definition:
            return []
        return self.bpel_definition.initial_work_list(self.workflow_id)

    @staticmethod
    def _migrate_legacy_context(context: dict):
        migrated = dict(context or {})
        legacy_map = {
            "workflow_step": "workflow_activatity",
            "current_step": "current_activatity",
            "last_task_id": "last_work_item",
        }
        for legacy_key, new_key in legacy_map.items():
            if new_key not in migrated and legacy_key in migrated:
                migrated[new_key] = migrated[legacy_key]
            migrated.pop(legacy_key, None)

        current_activatity = migrated.get("current_activatity")
        if isinstance(current_activatity, dict):
            current_activatity = dict(current_activatity)
            if "index" in current_activatity and "activatity_index" not in current_activatity:
                current_activatity["activatity_index"] = current_activatity.pop("index")
            migrated["current_activatity"] = current_activatity
        return migrated

    def _normalize_context(self, context: dict):
        normalized = self.initial_workflow_context()
        normalized.update(self._migrate_legacy_context(context))
        normalized["battle_log"] = list(normalized.get("battle_log", []))
        normalized["completed_roles"] = list(normalized.get("completed_roles", []))
        normalized["attachments"] = normalize_attachments(normalized.get("attachments", []))
        normalized["work_list"] = list(normalized.get("work_list", []))
        normalized["risk_assessments"] = list(normalized.get("risk_assessments", []))
        normalized["scheduled_tasks"] = list(normalized.get("scheduled_tasks", []))
        normalized["resources"] = list(normalized.get("resources", []))
        normalized["target_histories"] = list(normalized.get("target_histories", []))
        normalized["planning_objectives"] = list(
            normalized.get("planning_objectives", [])
        )
        normalized["candidate_plans"] = list(normalized.get("candidate_plans", []))
        normalized["constraints"] = list(normalized.get("constraints", []))
        normalized["authorization"] = dict(normalized.get("authorization", {}) or {})
        normalized["agent_outputs"] = dict(normalized.get("agent_outputs", {}) or {})
        normalized["agent_results"] = dict(normalized.get("agent_results", {}) or {})
        if self.bpel_definition:
            existing_items = {
                item.get("activatity_id"): item
                for item in normalized["work_list"]
                if item.get("activatity_id")
            }
            normalized["work_list"] = [
                {**item, **existing_items.get(item["activatity_id"], {})}
                for item in self._initial_work_list()
            ]
        normalized["workflow_id"] = self.workflow_id
        normalized["workflow_mode"] = self.mode
        normalized["workflow_name"] = self.workflow
        normalized["workflow_status"] = normalized.get("workflow_status", "running")
        normalized["workflow_activatity"] = int(normalized.get("workflow_activatity", 0) or 0)
        normalized["current_activatity"] = normalized.get("current_activatity")
        normalized["active_activatities"] = list(normalized.get("active_activatities", []))
        normalized["last_work_item"] = normalized.get("last_work_item")
        normalized["last_role"] = normalized.get("last_role")
        normalized["last_error"] = normalized.get("last_error")
        return normalized

    def _default_workflow_state(self):
        context = self._normalize_context({**self.initial_workflow_context(), **self.initial_context})
        return {
            "workflow_id": self.workflow_id,
            "workflow": self.workflow,
            "mode": self.mode,
            "status": context["workflow_status"],
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "current_activatity": None,
            "last_error": None,
            "context": context,
        }

    def _load_or_initialize_workflow_state(self):
        if self.resume and self.state_store.exists(self.workflow_id):
            state = self.state_store.load(self.workflow_id)
            context = self._normalize_context(state.get("context", {}))
            if self.initial_context:
                context.update(self.initial_context)
                context = self._normalize_context(context)
            state["workflow_id"] = self.workflow_id
            state["workflow"] = self.workflow
            state["mode"] = self.mode
            state["status"] = state.get("status") or context["workflow_status"]
            state["current_activatity"] = (
                state.get("current_activatity")
                or state.pop("current_step", None)
                or context.get("current_activatity")
            )
            state["last_error"] = state.get("last_error") or context.get("last_error")
            state["context"] = context
            self.state_store.save(self.workflow_id, state)
            print(f"[STATE] Resumed workflow {self.workflow_id}")
            return state

        state = self._default_workflow_state()
        self.state_store.save(self.workflow_id, state)
        if self.resume:
            print(f"[STATE] Resume requested but no checkpoint found; started new workflow {self.workflow_id}")
        else:
            print(f"[STATE] Started new workflow {self.workflow_id}")
        return state

    def _save_workflow_checkpoint(
        self,
        context: dict,
        status: str = None,
        current_activatity: dict = None,
        last_error: str = None,
    ):
        with self._checkpoint_lock:
            normalized = self._normalize_context(context)
            if status is not None:
                normalized["workflow_status"] = status
            if current_activatity is not None:
                normalized["current_activatity"] = current_activatity
            if last_error is not None:
                normalized["last_error"] = last_error

            state = {
                "workflow_id": self.workflow_id,
                "workflow": self.workflow,
                "mode": self.mode,
                "status": normalized["workflow_status"],
                "created_at": self.workflow_state.get("created_at", utc_now_iso()),
                "updated_at": utc_now_iso(),
                "current_activatity": normalized.get("current_activatity"),
                "last_error": normalized.get("last_error"),
                "context": normalized,
            }
            self.workflow_state = state
            self.workflow_context = normalized
            self.state_store.save(self.workflow_id, state)

    def merge_external_attachments(self, attachments: list[dict] | None):
        if not attachments:
            return self.workflow_context["attachments"]

        merged = merge_attachments(self.workflow_context.get("attachments", []), attachments)
        self.workflow_context["attachments"] = merged
        self._save_workflow_checkpoint(
            self.workflow_context,
            status=self.workflow_context.get("workflow_status", "running"),
            current_activatity=self.workflow_context.get("current_activatity"),
            last_error=self.workflow_context.get("last_error"),
        )
        return merged

    def merge_initial_context(self, context_updates: dict | None):
        if not context_updates:
            return self.workflow_context
        self.workflow_context.update(deepcopy(context_updates))
        self._save_workflow_checkpoint(
            self.workflow_context,
            status=self.workflow_context.get("workflow_status", "running"),
            current_activatity=self.workflow_context.get("current_activatity"),
            last_error=self.workflow_context.get("last_error"),
        )
        return self.workflow_context

    def _work_item_for_activatity(self, role: str, activatity_index: int):
        return f"{self.workflow_id}:{activatity_index}:{role}"

    @staticmethod
    def _context_snapshot(context: dict):
        return {
            "workflow_id": context.get("workflow_id"),
            "workflow_mode": context.get("workflow_mode"),
            "workflow_name": context.get("workflow_name"),
            "workflow_status": context.get("workflow_status"),
            "workflow_activatity": context.get("workflow_activatity"),
            "current_activatity": context.get("current_activatity"),
            "active_activatities": list(context.get("active_activatities", [])),
            "sector": context.get("sector"),
            "coordinates": context.get("coordinates"),
            "recon_report": context.get("recon_report"),
            "strike_result": context.get("strike_result"),
            "eval_score": context.get("eval_score"),
            "commander_decision": context.get("commander_decision"),
            "assault_result": context.get("assault_result"),
            "replan_result": context.get("replan_result"),
            "risk_assessments": deepcopy(context.get("risk_assessments", [])),
            "scheduled_tasks": deepcopy(context.get("scheduled_tasks", [])),
            "resources": deepcopy(context.get("resources", [])),
            "target_histories": deepcopy(context.get("target_histories", [])),
            "planning_objectives": deepcopy(context.get("planning_objectives", [])),
            "candidate_plans": deepcopy(context.get("candidate_plans", [])),
            "constraints": deepcopy(context.get("constraints", [])),
            "authorization": deepcopy(context.get("authorization", {})),
            "decision_planning_result": deepcopy(context.get("decision_planning_result")),
            "compliance_authorization_result": deepcopy(
                context.get("compliance_authorization_result")
            ),
            "compliance_decision": context.get("compliance_decision"),
            "agent_outputs": deepcopy(context.get("agent_outputs", {})),
            "agent_results": deepcopy(context.get("agent_results", {})),
            "completed_roles": list(context.get("completed_roles", [])),
            "battle_log": list(context.get("battle_log", [])),
            "last_work_item": context.get("last_work_item"),
            "last_role": context.get("last_role"),
            "last_error": context.get("last_error"),
            "attachments": attachment_snapshot(context.get("attachments", [])),
            "work_list": deepcopy(context.get("work_list", [])),
        }

    def build_task_payload(self, role: str, context: dict, activatity_index: int = None, **legacy_kwargs):
        if activatity_index is None:
            activatity_index = legacy_kwargs.pop("step_index", None)
        if activatity_index is None:
            raise ValueError("activatity_index is required")
        work_item = self._work_item_for_activatity(role, activatity_index)
        context_snapshot = self._context_snapshot(context)

        if role == "recon":
            return {
                "workflow_id": self.workflow_id,
                "workflow": self.workflow,
                "workflow_mode": self.mode,
                "work_item": work_item,
                "parent_work_item": context.get("last_work_item"),
                "activatity_index": activatity_index,
                "activatity_role": role,
                "command": "scan_beach_defenses",
                "input": {
                    "sector": context["sector"],
                },
                "context": context_snapshot,
                "attachments": attachment_snapshot(context.get("attachments", [])),
                "work_list": deepcopy(context.get("work_list", [])),
                "output_hint": "recon_report",
            }, False

        if role == "artillery":
            return {
                "workflow_id": self.workflow_id,
                "workflow": self.workflow,
                "workflow_mode": self.mode,
                "work_item": work_item,
                "parent_work_item": context.get("last_work_item"),
                "activatity_index": activatity_index,
                "activatity_role": role,
                "command": "suppress_beach_sector_A",
                "input": {
                    "coordinates": context["coordinates"],
                    "intensity": "high",
                },
                "context": context_snapshot,
                "attachments": attachment_snapshot(context.get("attachments", [])),
                "work_list": deepcopy(context.get("work_list", [])),
                "output_hint": "strike_result",
            }, True

        if role == "evaluator":
            return {
                "workflow_id": self.workflow_id,
                "workflow": self.workflow,
                "workflow_mode": self.mode,
                "work_item": work_item,
                "parent_work_item": context.get("last_work_item"),
                "activatity_index": activatity_index,
                "activatity_role": role,
                "command": "evaluate_strike",
                "input": {
                    "target_coordinates": context["coordinates"],
                },
                "context": context_snapshot,
                "attachments": attachment_snapshot(context.get("attachments", [])),
                "work_list": deepcopy(context.get("work_list", [])),
                "output_hint": "eval_score",
            }, False

        if role == "assault":
            return {
                "workflow_id": self.workflow_id,
                "workflow": self.workflow,
                "workflow_mode": self.mode,
                "work_item": work_item,
                "parent_work_item": context.get("last_work_item"),
                "activatity_index": activatity_index,
                "activatity_role": role,
                "command": "capture_beachhead",
                "input": {
                    "coordinates": context["coordinates"],
                },
                "context": context_snapshot,
                "attachments": attachment_snapshot(context.get("attachments", [])),
                "work_list": deepcopy(context.get("work_list", [])),
                "output_hint": "assault_result",
            }, False

        raise ValueError(f"Unsupported role: {role}")

    def apply_agent_result(self, role: str, success: bool, context: dict):
        if not success:
            context["battle_log"].append(f"[{role} Error] Task failed or no available agent.")
            return

        if role == "recon":
            context["recon_report"] = "Sector_A is heavily fortified with overlapping machine gun nests."
            context["battle_log"].append(f"[Recon Report] {context['recon_report']}")
        elif role == "artillery":
            context["strike_result"] = "Suppression barrage executed on Sector_A."
            context["battle_log"].append(f"[Artillery Report] {context['strike_result']}")
        elif role == "evaluator":
            context["eval_score"] = self.mock_eval_score if self.mock_eval_score is not None else 40
            context["battle_log"].append(
                f"[Eval Report] Effectiveness matches {context['eval_score']}% destruction rate."
            )
        elif role == "assault":
            context["assault_result"] = "Assault unit captured the beachhead."
            context["battle_log"].append(f"[Assault Report] {context['assault_result']}")
        elif role in {"decision_planning", "compliance_authorization"}:
            self._apply_decision_agent_result(role, context)

        if role not in context["completed_roles"]:
            context["completed_roles"].append(role)

    def _apply_decision_agent_result(self, role: str, context: dict):
        raw_response = deepcopy(self._last_agent_responses.get(role, {}))
        work_item = raw_response.get("work_item") or context.get("last_work_item")
        agent_result = context.get("agent_results", {}).get(work_item, {})
        if isinstance(agent_result, dict) and agent_result:
            raw_response = deepcopy(agent_result)

        output = raw_response.get("output") if isinstance(raw_response, dict) else {}
        if not isinstance(output, dict):
            output = {}
        if work_item and raw_response:
            context.setdefault("agent_results", {})[work_item] = deepcopy(raw_response)

        agent_response = raw_response.get("agent_response")
        if not isinstance(agent_response, dict):
            agent_response = output.get("agent_response")
        if not isinstance(agent_response, dict):
            agent_response = {
                "status": raw_response.get("status"),
                "agent": raw_response.get("agent"),
                "selected_algorithms": raw_response.get("selected_algorithms", output.get("selected_algorithms", [])),
                "result": raw_response.get("result", {}),
                "rag_evidence": raw_response.get("rag_evidence", output.get("rag_evidence", [])),
                "summary": raw_response.get("message", ""),
                "warnings": raw_response.get("warnings", output.get("warnings", [])),
            }
        result = agent_response.get("result") if isinstance(agent_response, dict) else {}
        if not isinstance(result, dict):
            result = {}

        output_result = output.get(f"{role}_result")
        if isinstance(output_result, dict):
            result = output_result
            agent_response["result"] = result

        context.setdefault("agent_outputs", {})[role] = agent_response
        summary = agent_response.get("summary") or raw_response.get("message") or "completed"

        if role == "decision_planning":
            context["decision_planning_result"] = result
            context["candidate_plans"] = result.get(
                "candidate_plans",
                context.get("candidate_plans", []),
            )
            context["battle_log"].append(f"[Decision Planning Report] {summary}")
            return

        if role == "compliance_authorization":
            context["compliance_authorization_result"] = result
            context["compliance_decision"] = result.get("decision")
            context["battle_log"].append(f"[Compliance Authorization Report] {summary}")

    def rule_next_step(self, context: dict):
        """Fast state-machine planner. Returns an action dict or None when rules are unsure."""
        if not context["recon_report"]:
            return {"type": "agent", "role": "recon", "reason": "No recon report is available."}

        if not context["strike_result"]:
            return {"type": "agent", "role": "artillery", "reason": "Recon is done but suppression has not run."}

        if context["eval_score"] is None:
            return {"type": "agent", "role": "evaluator", "reason": "Strike result needs evaluation."}

        if not context["commander_decision"]:
            return {"type": "decision", "reason": "Evaluation is available; commander must decide."}

        decision = context["commander_decision"].upper()
        if "ASSAULT" in decision and "RE-PLAN" not in decision and not context["assault_result"]:
            return {"type": "agent", "role": "assault", "reason": "Commander decision allows assault."}

        if "RE-PLAN" in decision or "ABORT" in decision:
            return {"type": "end", "reason": "Commander selected re-plan or abort."}

        if context["assault_result"]:
            return {"type": "end", "reason": "Assault phase completed."}

        return None

    def llm_next_step(self, context: dict):
        """Fallback planner used only when rule_next_step cannot decide."""
        if not self.api_key:
            print("[PLANNER] No OPENAI_API_KEY. Fallback defaults to end.")
            return {"type": "end", "reason": "LLM fallback unavailable because OPENAI_API_KEY is not set."}

        try:
            from langchain_core.prompts import PromptTemplate

            llm = self.build_llm()
            prompt = PromptTemplate.from_template(
                "You are an A2A workflow planner. Choose the next action from this set only:\n"
                "- recon\n- artillery\n- evaluator\n- assault\n- decision\n- end\n\n"
                "Rules:\n"
                "1. Return only one word from the set.\n"
                "2. Use end if the workflow should stop.\n"
                "3. Use decision if the commander should analyze battle_log.\n\n"
                "Workflow context JSON:\n{context_json}"
            )
            chain = prompt | llm
            response = chain.invoke({"context_json": json.dumps(context, ensure_ascii=False)})
            choice = response.content.strip().lower()
            print(f"[LLM FALLBACK] Suggested next action: {choice}")

            if choice in {"recon", "artillery", "evaluator", "assault"}:
                return {"type": "agent", "role": choice, "reason": "LLM fallback selected an agent role."}
            if choice == "decision":
                return {"type": "decision", "reason": "LLM fallback selected commander decision."}
            if choice == "end":
                return {"type": "end", "reason": "LLM fallback selected end."}
            return {"type": "end", "reason": f"LLM fallback returned invalid action: {choice}"}
        except Exception as e:
            return {"type": "end", "reason": f"LLM fallback failed: {e}"}

    def get_next_step(self, context: dict):
        step = self.rule_next_step(context)
        if step:
            step["planner"] = "rule"
            return step

        step = self.llm_next_step(context)
        step["planner"] = "llm_fallback"
        return step

    def parse_commander_decision(self, decision: str):
        normalized = decision.upper()
        if "RE-PLAN" in normalized or "REPLAN" in normalized or "ABORT" in normalized:
            return "RE-PLAN"
        if re.search(r"\bASSAULT\b", normalized):
            return "ASSAULT"
        return decision

    def _work_list_item(self, context: dict, activatity_id: str):
        for item in context.get("work_list", []):
            if item.get("activatity_id") == activatity_id:
                return item
        raise KeyError(f"Unknown activatity: {activatity_id}")

    def _set_activatity_status(
        self,
        context: dict,
        activatity: BPELActivatity,
        status: str,
        error: str = None,
    ):
        with self._checkpoint_lock:
            item = self._work_list_item(context, activatity.activatity_id)
            item["status"] = status
            item["error"] = error
            item["updated_at"] = utc_now_iso()
            if status == "running":
                item.setdefault("started_at", item["updated_at"])
                if activatity.activatity_id not in context["active_activatities"]:
                    context["active_activatities"].append(activatity.activatity_id)
                context["workflow_activatity"] = int(context.get("workflow_activatity", 0) or 0) + 1
            elif status in {"completed", "failed", "skipped"}:
                item["finished_at"] = item["updated_at"]
                if activatity.activatity_id in context["active_activatities"]:
                    context["active_activatities"].remove(activatity.activatity_id)

            current_activatity = {
                "activatity_id": activatity.activatity_id,
                "activatity_index": item["activatity_index"],
                "work_item": item["work_item"],
                "type": activatity.type,
                "role": activatity.role,
                "status": status,
            }
            context["current_activatity"] = current_activatity
            if activatity.type == "invoke":
                context["last_work_item"] = item["work_item"]
                context["last_role"] = activatity.role
            self._save_workflow_checkpoint(
                context,
                status=context.get("workflow_status", "running"),
                current_activatity=current_activatity,
                last_error=error,
            )

    def _skip_activatity_tree(self, context: dict, activatity: BPELActivatity):
        item = self._work_list_item(context, activatity.activatity_id)
        if item.get("status") == "pending":
            self._set_activatity_status(context, activatity, "skipped")
        for child in activatity.children:
            self._skip_activatity_tree(context, child)

    @staticmethod
    def _context_key_for_bpel_variable(variable_name: str | None):
        return {
            "ReconReport": "recon_report",
            "StrikeCoordinates": "coordinates",
            "StrikeResult": "strike_result",
            "EvalScore": "eval_score",
            "CommanderDecision": "commander_decision",
            "Sector_A": "sector",
            "RiskAssessments": "risk_assessments",
            "ScheduledTasks": "scheduled_tasks",
            "Resources": "resources",
            "TargetHistories": "target_histories",
            "PlanningObjectives": "planning_objectives",
            "CandidatePlans": "candidate_plans",
            "Constraints": "constraints",
            "Authorization": "authorization",
            "PlanningInput": "planning_input",
            "DecisionPlanningResult": "decision_planning_result",
            "ComplianceAuthorizationResult": "compliance_authorization_result",
        }.get(variable_name, variable_name)

    def _build_bpel_task_payload(self, activatity: BPELActivatity, context: dict):
        with self._checkpoint_lock:
            item = self._work_list_item(context, activatity.activatity_id)
            parent_item = (
                self._work_list_item(context, activatity.parent_activatity)
                if activatity.parent_activatity
                else None
            )
            input_key = self._context_key_for_bpel_variable(activatity.input_variable)
            input_payload = {}
            if input_key:
                input_payload[input_key] = context.get(input_key, activatity.input_variable)

            return {
                "workflow_id": self.workflow_id,
                "workflow": self.workflow,
                "workflow_mode": self.mode,
                "work_item": item["work_item"],
                "parent_work_item": parent_item.get("work_item") if parent_item else None,
                "activatity_id": activatity.activatity_id,
                "activatity_index": item["activatity_index"],
                "activatity_role": activatity.role,
                "command": activatity.command,
                "input": input_payload,
                "context": self._context_snapshot(context),
                "attachments": attachment_snapshot(context.get("attachments", [])),
                "work_list": deepcopy(context.get("work_list", [])),
                "output_hint": self._context_key_for_bpel_variable(activatity.output_variable),
            }, activatity.role == "artillery"

    def _evaluate_bpel_condition(self, condition: str | None, context: dict):
        if not condition:
            return False

        match = re.search(
            r"getVariableData\(['\"](?P<variable>[^'\"]+)['\"]\)\s*"
            r"(?P<operator><=|>=|==|!=|<|>)\s*(?P<expected>-?\d+(?:\.\d+)?)",
            condition,
        )
        if not match:
            raise ValueError(f"Unsupported BPEL condition: {condition}")

        context_key = self._context_key_for_bpel_variable(match.group("variable"))
        actual = float(context.get(context_key))
        expected = float(match.group("expected"))
        return {
            "<": actual < expected,
            "<=": actual <= expected,
            ">": actual > expected,
            ">=": actual >= expected,
            "==": actual == expected,
            "!=": actual != expected,
        }[match.group("operator")]

    def _execute_bpel_invoke(self, activatity: BPELActivatity, context: dict):
        if activatity.role == "commander":
            decision = self.ask_llm(context["battle_log"])
            with self._checkpoint_lock:
                context["commander_decision"] = self.parse_commander_decision(decision)
                context["battle_log"].append(f"[Commander Decision] {decision}")
            return True

        if not activatity.role:
            raise ValueError(f"No role mapping for partnerLink={activatity.partner_link}")

        payload, stream = self._build_bpel_task_payload(activatity, context)
        if activatity.dispatch_mode == "parallel":
            success = self.delegate_parallel_task(activatity.role, payload, stream=stream)
        else:
            success = self.delegate_task(activatity.role, payload, stream=stream)
        with self._checkpoint_lock:
            self.apply_agent_result(activatity.role, success, context)
        return success

    def _execute_bpel_activatity(self, activatity: BPELActivatity, context: dict):
        item = self._work_list_item(context, activatity.activatity_id)
        if item.get("status") in {"completed", "skipped"}:
            return True

        self._set_activatity_status(context, activatity, "running")
        try:
            if activatity.type in {"sequence", "case", "otherwise"}:
                success = all(
                    self._execute_bpel_activatity(child, context)
                    for child in activatity.children
                )
            elif activatity.type == "flow":
                child_roles = {
                    child.role
                    for child in activatity.children
                    if child.type == "invoke" and child.role
                }
                if len(child_roles) > 1:
                    raise ValueError(
                        "BPEL flow may only contain concurrent activatities for the same agent role"
                    )
                with ThreadPoolExecutor(
                    max_workers=min(self.max_workers, max(1, len(activatity.children))),
                    thread_name_prefix="a2a-workflow",
                ) as executor:
                    futures = [
                        executor.submit(self._execute_bpel_activatity, child, context)
                        for child in activatity.children
                    ]
                    success = all(future.result() for future in as_completed(futures))
            elif activatity.type == "assign":
                context_key = self._context_key_for_bpel_variable(activatity.assign_to)
                with self._checkpoint_lock:
                    context[context_key] = activatity.assign_from
                success = True
            elif activatity.type == "invoke":
                success = self._execute_bpel_invoke(activatity, context)
            elif activatity.type == "switch":
                selected = None
                for child in activatity.children:
                    if child.type == "case" and self._evaluate_bpel_condition(child.condition, context):
                        selected = child
                        break
                    if child.type == "otherwise":
                        selected = child
                for child in activatity.children:
                    if child is not selected:
                        self._skip_activatity_tree(context, child)
                success = bool(selected) and self._execute_bpel_activatity(selected, context)
            elif activatity.type == "throw":
                raise RuntimeError(activatity.fault_name or "BPEL workflow fault")
            else:
                raise ValueError(f"Unsupported BPEL activatity type: {activatity.type}")
        except Exception as exc:
            with self._checkpoint_lock:
                context["workflow_status"] = "paused"
                context["last_error"] = str(exc)
            self._set_activatity_status(context, activatity, "failed", str(exc))
            return False

        self._set_activatity_status(
            context,
            activatity,
            "completed" if success else "failed",
            None if success else f"Activatity failed: {activatity.activatity_id}",
        )
        return success

    def run_bpel_workflow(self):
        if not self.bpel_definition:
            raise ValueError("No BPEL workflow was loaded")

        context = self.workflow_context
        if context.get("workflow_status") == "completed":
            print(f"[WORKFLOW] Workflow {self.workflow_id} already completed. Nothing to resume.")
            return context

        print(f"\n=== BPEL WORKFLOW: {self.bpel_definition.process_name} ===")
        print(f"[WORKFLOW] Loaded from {self.bpel_definition.source_path}")
        print(f"[WORKFLOW] work_list entries={len(context.get('work_list', []))}")
        with self._checkpoint_lock:
            context["workflow_status"] = "running"
            context["last_error"] = None
            self._save_workflow_checkpoint(context, status="running")

        success = self._execute_bpel_activatity(self.bpel_definition.root_activatity, context)
        with self._checkpoint_lock:
            context["workflow_status"] = "completed" if success else "paused"
            self._save_workflow_checkpoint(
                context,
                status=context["workflow_status"],
                current_activatity=context.get("current_activatity"),
                last_error=context.get("last_error"),
            )

        print("\n================= WORKFLOW CONTEXT =================")
        print(json.dumps(context, ensure_ascii=False, indent=2))
        print("====================================================")
        return context

    def run_dynamic_battle_scenario(self, max_steps: int = 10):
        context = self.workflow_context

        if context.get("workflow_status") == "completed":
            print(f"[WORKFLOW] Workflow {self.workflow_id} already completed. Nothing to resume.")
            print("\n================= WORKFLOW CONTEXT =================")
            print(json.dumps(context, ensure_ascii=False, indent=2))
            print("====================================================")
            return context

        print("\n=== DYNAMIC WORKFLOW: RULE STATE MACHINE + LLM FALLBACK ===")
        print(f"[WORKFLOW] Resuming from workflow_activatity={context.get('workflow_activatity', 0)}")

        start_activatity = int(context.get("workflow_activatity", 0) or 0) + 1
        for activatity_index in range(start_activatity, start_activatity + max_steps):
            step = self.get_next_step(context)
            current_activatity = {
                "activatity_index": activatity_index,
                "planner": step.get("planner"),
                "type": step.get("type"),
                "role": step.get("role"),
                "reason": step.get("reason"),
            }
            context["workflow_activatity"] = activatity_index
            context["current_activatity"] = current_activatity
            context["workflow_status"] = "running"
            self._save_workflow_checkpoint(context, status="running", current_activatity=current_activatity)

            print(
                f"\n=== ACTIVATITY {activatity_index}: planner={step.get('planner')} "
                f"action={step.get('type')} reason={step.get('reason')} ==="
            )

            if step["type"] == "agent":
                role = step["role"]
                payload, stream = self.build_task_payload(role, context, activatity_index)
                context["last_work_item"] = payload["work_item"]
                context["last_role"] = role
                self._save_workflow_checkpoint(context, status="running", current_activatity=current_activatity)

                success = self.delegate_task(role, payload, stream=stream)
                if not success:
                    error_message = f"Agent execution failed for role={role}"
                    context["last_error"] = error_message
                    context["workflow_status"] = "paused"
                    self._save_workflow_checkpoint(
                        context,
                        status="paused",
                        current_activatity=current_activatity,
                        last_error=error_message,
                    )
                    print("[WORKFLOW] Agent execution failed. Stop current workflow.")
                    break

                self.apply_agent_result(role, success, context)
                context["last_error"] = None
                self._save_workflow_checkpoint(context, status="running", current_activatity=current_activatity)
                continue

            if step["type"] == "decision":
                print("[PLANNER] Commander is analyzing workflow context and battle log...")
                context["last_role"] = "commander"
                context["last_work_item"] = f"{self.workflow_id}:{activatity_index}:decision"
                self._save_workflow_checkpoint(context, status="running", current_activatity=current_activatity)

                decision = self.ask_llm(context["battle_log"])
                context["commander_decision"] = self.parse_commander_decision(decision)
                context["battle_log"].append(f"[Commander Decision] {decision}")
                self._save_workflow_checkpoint(context, status="running", current_activatity=current_activatity)
                print("\n================= COMMANDER ORDER =================")
                print(decision)
                print("===================================================")
                continue

            if step["type"] == "end":
                reason = (step.get("reason") or "").lower()
                final_status = "paused" if ("re-plan" in reason or "abort" in reason) else "completed"
                context["workflow_status"] = final_status
                self._save_workflow_checkpoint(context, status=final_status, current_activatity=current_activatity)
                print(f"[WORKFLOW] End: {step.get('reason')}")
                break
        else:
            context["workflow_status"] = "paused"
            self._save_workflow_checkpoint(context, status="paused")
            print(f"[WORKFLOW] Reached max_steps={max_steps}. Stop current workflow.")

        print("\n================= WORKFLOW CONTEXT =================")
        print(json.dumps(context, ensure_ascii=False, indent=2))
        print("====================================================")
        return context

    def run_battle_scenario(self):
        battle_log = []
        
        print("\n=== PHASE 1: RECONNAISSANCE ===")
        recon_task = {
            "command": "scan_beach_defenses",
            "sector": "Sector_A"
        }
        self.delegate_task("recon", recon_task)
        battle_log.append("[Recon Report] Sector_A is heavily fortified with overlapping machine gun nests.")

        print("\n=== PHASE 2: ARTILLERY STRIKE (STREAMING) ===")
        strike_task = {
            "command": "suppress_beach_sector_A",
            "coordinates": "120.5E, 35.1N",
            "intensity": "high"
        }
        success = self.delegate_task("artillery", strike_task, stream=True)
        battle_log.append("[Artillery Report] Suppression barrage executed on Sector_A.")
        
        if success:
            print("\n=== PHASE 3: EVALUATE OUTCOME ===")
            eval_task = {
                "command": "evaluate_strike",
                "target_coordinates": "120.5E, 35.1N"
            }
            eval_success = self.delegate_task("evaluator", eval_task)
            battle_log.append("[Eval Report] Effectiveness matches 40% destruction rate. Defenses still operational.")
            
            print("\n=== PHASE 4: LLM COMMANDER DECISION ===")
            print("[PLANNER] AI Commander is analyzing battle logs to decide next move...")
            decision = self.ask_llm(battle_log)
            print("\n================= LLM COMMANDER ORDER =================")
            print(decision)
            print("========================================================")

def parse_args():
    parser = argparse.ArgumentParser(description="A2A Commander Agent")
    parser.add_argument(
        "--mode",
        choices=["remote", "local"],
        default=os.environ.get("A2A_COMMANDER_MODE", "remote"),
        help="remote uses Nacos + HTTP A2A; local runs an in-process workflow simulation.",
    )
    parser.add_argument(
        "--workflow",
        choices=["bpel", "dynamic", "legacy"],
        default="bpel",
        help="bpel dynamically loads a workflow definition; dynamic uses the rule state-machine; legacy runs the fixed scenario.",
    )
    parser.add_argument(
        "--workflow-file",
        default=None,
        help="BPEL file path, filename, stem, or process name. Defaults to the first discovered .bpel workflow.",
    )
    parser.add_argument(
        "--list-workflows",
        action="store_true",
        help="List discovered BPEL workflow definitions and exit.",
    )
    parser.add_argument(
        "--workflow-id",
        default=None,
        help="Reuse an existing workflow checkpoint id to resume a previous run.",
    )
    parser.add_argument(
        "--input-json",
        default=None,
        help="JSON file merged into the workflow context before execution.",
    )
    parser.add_argument(
        "--state-dir",
        default=None,
        help="Directory used to persist workflow checkpoints.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from an existing workflow checkpoint if it exists.",
    )
    parser.add_argument(
        "--serve-recovery-api",
        action="store_true",
        help="Start the workflow recovery HTTP API instead of executing a workflow immediately.",
    )
    parser.add_argument(
        "--serve-workflow-manager",
        action="store_true",
        help="Start the resident multi-workflow manager HTTP API.",
    )
    parser.add_argument(
        "--recovery-host",
        default="127.0.0.1",
        help="Host used by the recovery HTTP API.",
    )
    parser.add_argument(
        "--recovery-port",
        type=int,
        default=8020,
        help="Port used by the recovery HTTP API.",
    )
    parser.add_argument(
        "--manager-host",
        default="127.0.0.1",
        help="Host used by the resident workflow manager HTTP API.",
    )
    parser.add_argument(
        "--manager-port",
        type=int,
        default=8021,
        help="Port used by the resident workflow manager HTTP API.",
    )
    parser.add_argument(
        "--max-workflows",
        type=int,
        default=4,
        help="Maximum number of workflows executed concurrently by the resident manager.",
    )
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="Maximum number of concurrently dispatched BPEL flow activatities.",
    )
    parser.add_argument(
        "--mock-eval-score",
        type=int,
        default=None,
        help="Local/mock evaluation score used by evaluator and no-key LLM decision.",
    )
    parser.add_argument(
        "--mock-decision",
        choices=["ASSAULT", "RE-PLAN"],
        default=None,
        help="Force commander decision in local/mock runs.",
    )
    return parser.parse_args()


def load_initial_context(path: str | None) -> dict:
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as input_file:
        payload = json.load(input_file)
    if not isinstance(payload, dict):
        raise ValueError("--input-json must contain a JSON object")
    return payload


if __name__ == "__main__":
    args = parse_args()
    initial_context = load_initial_context(args.input_json)

    if args.list_workflows:
        catalog = BPELWorkflowCatalog(PROJECT_ROOT)
        definitions = [catalog.load(str(path)) for path in catalog.discover()]
        print("Available BPEL workflows:")
        for definition in definitions:
            print(f"- {definition.process_name}: {definition.source_path}")
        raise SystemExit(0)

    if args.serve_recovery_api:
        import uvicorn

        from commander_agent.recovery_api import build_recovery_app

        app = build_recovery_app(
            default_mode=args.mode,
            default_workflow=args.workflow,
            default_state_dir=args.state_dir,
        )
        uvicorn.run(app, host=args.recovery_host, port=args.recovery_port)
        raise SystemExit(0)

    if args.serve_workflow_manager:
        import uvicorn

        from commander_agent.manager_api import build_workflow_manager_app

        app = build_workflow_manager_app(
            mode=args.mode,
            state_dir=args.state_dir,
            max_workflows=args.max_workflows,
        )
        uvicorn.run(app, host=args.manager_host, port=args.manager_port)
        raise SystemExit(0)

    cmd = CommanderAgent(
        mode=args.mode,
        workflow=args.workflow,
        workflow_file=args.workflow_file,
        workflow_id=args.workflow_id,
        state_dir=args.state_dir,
        resume=args.resume,
        mock_eval_score=args.mock_eval_score,
        mock_decision=args.mock_decision,
        max_workers=args.max_workers,
        initial_context=initial_context,
    )

    if args.workflow == "legacy":
        cmd.run_battle_scenario()
    elif args.workflow == "bpel":
        cmd.run_bpel_workflow()
    else:
        cmd.run_dynamic_battle_scenario(max_steps=args.max_steps)
