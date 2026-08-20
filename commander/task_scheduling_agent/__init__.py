"""独立任务调度智能体（AMOS JSON → 资源/任务分配）。"""

from importlib import import_module

__all__ = [
    "TaskSchedulingAgent",
    "TaskSchedulingA2AAgent",
    "situation_from_amos",
    "run_schedule",
]


_EXPORTS = {
    "TaskSchedulingAgent": ("task_scheduling_agent.agent", "TaskSchedulingAgent"),
    "TaskSchedulingA2AAgent": ("task_scheduling_agent.main", "TaskSchedulingA2AAgent"),
    "situation_from_amos": ("task_scheduling_agent.amos_adapter", "situation_from_amos"),
    "run_schedule": ("task_scheduling_agent.engine", "run_schedule"),
}


def __getattr__(name):
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value
