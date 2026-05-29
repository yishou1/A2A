import sys
import os
import time
import re
import argparse

# Ensure imports work from project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from registry.nacos_manager import NacosRegistry
from a2a_protocol.client import A2AClient
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
        workflow_id: str = None,
        state_dir: str = None,
        resume: bool = False,
        mock_eval_score: int = None,
        mock_decision: str = None,
    ):
        load_env_file()
        self.mode = (mode or os.environ.get("A2A_COMMANDER_MODE", "remote")).lower()
        if self.mode not in {"remote", "local"}:
            raise ValueError("mode must be either 'remote' or 'local'")

        self.workflow = workflow
        self.workflow_id = workflow_id or os.environ.get("A2A_WORKFLOW_ID") or new_workflow_id()
        default_state_dir = os.path.join(PROJECT_ROOT, ".a2a_state", "workflows")
        self.state_store = WorkflowStateStore(
            state_dir or os.environ.get("A2A_STATE_DIR", default_state_dir)
        )
        self.resume = resume
        self.registry = None if self.mode == "local" else NacosRegistry()
        self.local_runtime = LocalAgentRuntime() if self.mode == "local" else None
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
        print(f"Workflow state: {self.state_store.state_path(self.workflow_id)}")
        print(f"LLM model: {self.model}")
        if self.api_base:
            print(f"LLM base URL: {self.api_base}")

    def delegate_task(self, role_needed: str, task_payload: dict, stream: bool = False):
        print(f"\n--- STEP: Resolving next unit for role: {role_needed} ---")
        if self.mode == "local":
            return self.delegate_local_task(role_needed, task_payload, stream=stream)
        
        # 1. Nacos Service Discovery
        instances = self.registry.discover_service("A2A-Agent", {"role": role_needed, "status": "idle"})
        if not instances:
            print(f"[ERROR] No available agents found for role {role_needed}. Replanning needed!")
            return False

        last_error = None
        for index, target in enumerate(instances, start=1):
            ip = target.get("ip")
            port = target.get("port")
            print(f"[FOUND] Candidate {index}/{len(instances)} for {role_needed} at {ip}:{port}")

            # 2. A2A Communication
            client = A2AClient(ip, port)
            try:
                card = client.discover()
                print(f"[DISCOVERY] Retrieved Agent Card from '{card.get('name')}'")

                token = client.authenticate()
                print(f"[AUTH] Obtained JWT Token: {token[:10]}...")

                if stream:
                    print(f"[STREAM] Receiving task updates from '{role_needed}':")
                    for event_data in client.send_message_stream(task_payload):
                        data = json.loads(event_data)
                        print(f"   -> [{data.get('status')}] {data.get('progress', '')} {data.get('message', '')}")
                    return True

                res = client.send_message(task_payload)
                print(f"[SEND] Task Response: {res}")
                return True
            except Exception as e:
                last_error = e
                print(f"[WARN] Candidate {ip}:{port} failed: {e}")

        print(f"[ERROR] A2A communication failed after trying {len(instances)} candidates: {last_error}")
        return False

    def delegate_local_task(self, role_needed: str, task_payload: dict, stream: bool = False):
        try:
            response, events = self.local_runtime.execute(role_needed, task_payload, stream=stream)
            card = response.get("agent_card", {})
            print(f"[LOCAL DISCOVERY] Using local Agent Card from '{card.get('name')}'")
            print(f"[LOCAL AUTH] Obtained local token: {response.get('token')}")

            if stream:
                print(f"[LOCAL STREAM] Receiving task updates from '{role_needed}':")
                for data in events:
                    print(f"   -> [{data.get('status')}] {data.get('progress', '')} {data.get('message', '')}")
            else:
                print(f"[LOCAL SEND] Task Response: {response}")
            return True
        except Exception as e:
            print(f"[ERROR] Local task execution failed: {e}")
            return False

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
            "workflow_step": 0,
            "current_step": None,
            "last_task_id": None,
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
            "battle_log": [],
            "completed_roles": [],
            "attachments": [],
        }

    def _normalize_context(self, context: dict):
        normalized = self.initial_workflow_context()
        normalized.update(context or {})
        normalized["battle_log"] = list(normalized.get("battle_log", []))
        normalized["completed_roles"] = list(normalized.get("completed_roles", []))
        normalized["attachments"] = normalize_attachments(normalized.get("attachments", []))
        normalized["workflow_id"] = self.workflow_id
        normalized["workflow_mode"] = self.mode
        normalized["workflow_name"] = self.workflow
        normalized["workflow_status"] = normalized.get("workflow_status", "running")
        normalized["workflow_step"] = int(normalized.get("workflow_step", 0) or 0)
        normalized["current_step"] = normalized.get("current_step")
        normalized["last_task_id"] = normalized.get("last_task_id")
        normalized["last_role"] = normalized.get("last_role")
        normalized["last_error"] = normalized.get("last_error")
        return normalized

    def _default_workflow_state(self):
        context = self._normalize_context(self.initial_workflow_context())
        return {
            "workflow_id": self.workflow_id,
            "workflow": self.workflow,
            "mode": self.mode,
            "status": context["workflow_status"],
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "current_step": None,
            "last_error": None,
            "context": context,
        }

    def _load_or_initialize_workflow_state(self):
        if self.resume and self.state_store.exists(self.workflow_id):
            state = self.state_store.load(self.workflow_id)
            context = self._normalize_context(state.get("context", {}))
            state["workflow_id"] = self.workflow_id
            state["workflow"] = self.workflow
            state["mode"] = self.mode
            state["status"] = state.get("status") or context["workflow_status"]
            state["current_step"] = state.get("current_step") or context.get("current_step")
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

    def _save_workflow_checkpoint(self, context: dict, status: str = None, current_step: dict = None, last_error: str = None):
        normalized = self._normalize_context(context)
        if status is not None:
            normalized["workflow_status"] = status
        if current_step is not None:
            normalized["current_step"] = current_step
        if last_error is not None:
            normalized["last_error"] = last_error

        state = {
            "workflow_id": self.workflow_id,
            "workflow": self.workflow,
            "mode": self.mode,
            "status": normalized["workflow_status"],
            "created_at": self.workflow_state.get("created_at", utc_now_iso()),
            "updated_at": utc_now_iso(),
            "current_step": normalized.get("current_step"),
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
            current_step=self.workflow_context.get("current_step"),
            last_error=self.workflow_context.get("last_error"),
        )
        return merged

    def _task_id_for_step(self, role: str, step_index: int):
        return f"{self.workflow_id}:{step_index}:{role}"

    @staticmethod
    def _context_snapshot(context: dict):
        return {
            "workflow_id": context.get("workflow_id"),
            "workflow_mode": context.get("workflow_mode"),
            "workflow_name": context.get("workflow_name"),
            "workflow_status": context.get("workflow_status"),
            "workflow_step": context.get("workflow_step"),
            "current_step": context.get("current_step"),
            "sector": context.get("sector"),
            "coordinates": context.get("coordinates"),
            "recon_report": context.get("recon_report"),
            "strike_result": context.get("strike_result"),
            "eval_score": context.get("eval_score"),
            "commander_decision": context.get("commander_decision"),
            "assault_result": context.get("assault_result"),
            "replan_result": context.get("replan_result"),
            "completed_roles": list(context.get("completed_roles", [])),
            "battle_log": list(context.get("battle_log", [])),
            "last_task_id": context.get("last_task_id"),
            "last_role": context.get("last_role"),
            "last_error": context.get("last_error"),
            "attachments": attachment_snapshot(context.get("attachments", [])),
        }

    def build_task_payload(self, role: str, context: dict, step_index: int):
        task_id = self._task_id_for_step(role, step_index)
        context_snapshot = self._context_snapshot(context)

        if role == "recon":
            return {
                "workflow_id": self.workflow_id,
                "workflow": self.workflow,
                "workflow_mode": self.mode,
                "task_id": task_id,
                "parent_task_id": context.get("last_task_id"),
                "step_index": step_index,
                "step_role": role,
                "command": "scan_beach_defenses",
                "input": {
                    "sector": context["sector"],
                },
                "context": context_snapshot,
                "attachments": attachment_snapshot(context.get("attachments", [])),
                "output_hint": "recon_report",
            }, False

        if role == "artillery":
            return {
                "workflow_id": self.workflow_id,
                "workflow": self.workflow,
                "workflow_mode": self.mode,
                "task_id": task_id,
                "parent_task_id": context.get("last_task_id"),
                "step_index": step_index,
                "step_role": role,
                "command": "suppress_beach_sector_A",
                "input": {
                    "coordinates": context["coordinates"],
                    "intensity": "high",
                },
                "context": context_snapshot,
                "attachments": attachment_snapshot(context.get("attachments", [])),
                "output_hint": "strike_result",
            }, True

        if role == "evaluator":
            return {
                "workflow_id": self.workflow_id,
                "workflow": self.workflow,
                "workflow_mode": self.mode,
                "task_id": task_id,
                "parent_task_id": context.get("last_task_id"),
                "step_index": step_index,
                "step_role": role,
                "command": "evaluate_strike",
                "input": {
                    "target_coordinates": context["coordinates"],
                },
                "context": context_snapshot,
                "attachments": attachment_snapshot(context.get("attachments", [])),
                "output_hint": "eval_score",
            }, False

        if role == "assault":
            return {
                "workflow_id": self.workflow_id,
                "workflow": self.workflow,
                "workflow_mode": self.mode,
                "task_id": task_id,
                "parent_task_id": context.get("last_task_id"),
                "step_index": step_index,
                "step_role": role,
                "command": "capture_beachhead",
                "input": {
                    "coordinates": context["coordinates"],
                },
                "context": context_snapshot,
                "attachments": attachment_snapshot(context.get("attachments", [])),
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

        if role not in context["completed_roles"]:
            context["completed_roles"].append(role)

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

    def run_dynamic_battle_scenario(self, max_steps: int = 10):
        context = self.workflow_context

        if context.get("workflow_status") == "completed":
            print(f"[WORKFLOW] Workflow {self.workflow_id} already completed. Nothing to resume.")
            print("\n================= WORKFLOW CONTEXT =================")
            print(json.dumps(context, ensure_ascii=False, indent=2))
            print("====================================================")
            return context

        print("\n=== DYNAMIC WORKFLOW: RULE STATE MACHINE + LLM FALLBACK ===")
        print(f"[WORKFLOW] Resuming from workflow_step={context.get('workflow_step', 0)}")

        start_step = int(context.get("workflow_step", 0) or 0) + 1
        for step_index in range(start_step, start_step + max_steps):
            step = self.get_next_step(context)
            current_step = {
                "index": step_index,
                "planner": step.get("planner"),
                "type": step.get("type"),
                "role": step.get("role"),
                "reason": step.get("reason"),
            }
            context["workflow_step"] = step_index
            context["current_step"] = current_step
            context["workflow_status"] = "running"
            self._save_workflow_checkpoint(context, status="running", current_step=current_step)

            print(
                f"\n=== STEP {step_index}: planner={step.get('planner')} "
                f"action={step.get('type')} reason={step.get('reason')} ==="
            )

            if step["type"] == "agent":
                role = step["role"]
                payload, stream = self.build_task_payload(role, context, step_index)
                context["last_task_id"] = payload["task_id"]
                context["last_role"] = role
                self._save_workflow_checkpoint(context, status="running", current_step=current_step)

                success = self.delegate_task(role, payload, stream=stream)
                if not success:
                    error_message = f"Agent execution failed for role={role}"
                    context["last_error"] = error_message
                    context["workflow_status"] = "paused"
                    self._save_workflow_checkpoint(
                        context,
                        status="paused",
                        current_step=current_step,
                        last_error=error_message,
                    )
                    print("[WORKFLOW] Agent execution failed. Stop current workflow.")
                    break

                self.apply_agent_result(role, success, context)
                context["last_error"] = None
                self._save_workflow_checkpoint(context, status="running", current_step=current_step)
                continue

            if step["type"] == "decision":
                print("[PLANNER] Commander is analyzing workflow context and battle log...")
                context["last_role"] = "commander"
                context["last_task_id"] = f"{self.workflow_id}:{step_index}:decision"
                self._save_workflow_checkpoint(context, status="running", current_step=current_step)

                decision = self.ask_llm(context["battle_log"])
                context["commander_decision"] = self.parse_commander_decision(decision)
                context["battle_log"].append(f"[Commander Decision] {decision}")
                self._save_workflow_checkpoint(context, status="running", current_step=current_step)
                print("\n================= COMMANDER ORDER =================")
                print(decision)
                print("===================================================")
                continue

            if step["type"] == "end":
                reason = (step.get("reason") or "").lower()
                final_status = "paused" if ("re-plan" in reason or "abort" in reason) else "completed"
                context["workflow_status"] = final_status
                self._save_workflow_checkpoint(context, status=final_status, current_step=current_step)
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
        choices=["dynamic", "legacy"],
        default="dynamic",
        help="dynamic uses the rule state-machine workflow; legacy runs the older fixed scenario.",
    )
    parser.add_argument(
        "--workflow-id",
        default=None,
        help="Reuse an existing workflow checkpoint id to resume a previous run.",
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
    parser.add_argument("--max-steps", type=int, default=10)
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


if __name__ == "__main__":
    args = parse_args()

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

    cmd = CommanderAgent(
        mode=args.mode,
        workflow=args.workflow,
        workflow_id=args.workflow_id,
        state_dir=args.state_dir,
        resume=args.resume,
        mock_eval_score=args.mock_eval_score,
        mock_decision=args.mock_decision,
    )

    if args.workflow == "legacy":
        cmd.run_battle_scenario()
    else:
        cmd.run_dynamic_battle_scenario(max_steps=args.max_steps)
