import json
import re
from typing import List, Tuple

from pydantic import BaseModel, Field, StrictInt, TypeAdapter

from .utils.logging_utils import get_logger

logger = get_logger(__name__)


class RerankSelection(BaseModel):
    selected_ids: List[StrictInt] = Field(
        default_factory=list,
        description="Candidate IDs selected as relevant to the question",
    )


def _clean_json_response(response: str) -> str:
    response = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL | re.IGNORECASE)
    response = response.strip()
    if response.startswith("```"):
        response = re.sub(r"^```(?:json)?\s*", "", response, flags=re.IGNORECASE)
        response = re.sub(r"\s*```$", "", response)
    start = response.find("{")
    end = response.rfind("}")
    if start >= 0 and end >= start:
        response = response[start:end + 1]
    return response.strip()


class DSPyFilter:
    """Small-model-friendly fact reranker using candidate IDs and strict JSON."""

    def __init__(self, synapserag):
        if synapserag.rerank_llm is None:
            synapserag._ensure_qa_runtime()
        self.llm = synapserag.rerank_llm
        self.model_name = synapserag.global_config.get_llm_endpoint("rerank").model_name
        self.response_adapter = TypeAdapter(RerankSelection)
        self.system_prompt = (
            "你是知识图谱事实重排器。只能从候选事实中选择与问题相关、可能参与推理链的事实。"
            "只返回合法 JSON，格式为 {\"selected_ids\":[0,1]}。不要复制事实，不要输出 Markdown，"
            "不要输出思考过程。"
        )

    def parse_filter(self, response: str, candidate_count: int, limit: int) -> List[int]:
        payload = json.loads(_clean_json_response(response))
        selection = self.response_adapter.validate_python(payload)
        selected = []
        for candidate_id in selection.selected_ids:
            if candidate_id < 0 or candidate_id >= candidate_count:
                raise ValueError(
                    f"Reranker selected out-of-range candidate ID {candidate_id}; "
                    f"candidate_count={candidate_count}")
            if candidate_id not in selected:
                selected.append(candidate_id)
        return selected[:limit]

    def llm_call(self, question: str, candidate_items: List[Tuple]):
        candidates = [
            {"id": index, "fact": list(fact)}
            for index, fact in enumerate(candidate_items)
        ]
        messages = [
            {"role": "system", "content": self.system_prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {"question": question, "candidates": candidates},
                    ensure_ascii=False,
                ),
            },
        ]
        kwargs = {
            "model": self.model_name,
            "temperature": 0.0,
            "max_completion_tokens": 256,
            "response_format": {"type": "json_object"},
        }
        if "qwen3" in self.model_name.lower():
            kwargs["reasoning_effort"] = "none"
        response = self.llm.infer(messages=messages, **kwargs)
        return response.text

    def repair_json(self, response_text: str):
        """One bounded format-only retry for otherwise useful small-model output."""
        messages = [
            {
                "role": "system",
                "content": (
                    "把用户提供的内容修复为合法 JSON，仅保留 selected_ids 整数数组。"
                    "只返回 JSON，不得新增候选编号。"
                ),
            },
            {"role": "user", "content": response_text},
        ]
        response = self.llm.infer(
            messages=messages,
            model=self.model_name,
            temperature=0.0,
            max_completion_tokens=128,
            response_format={"type": "json_object"},
            **({"reasoning_effort": "none"} if "qwen3" in self.model_name.lower() else {}),
        )
        return response.text

    def __call__(self, *args, **kwargs):
        return self.rerank(*args, **kwargs)

    def rerank(
        self,
        query: str,
        candidate_items: List[Tuple],
        candidate_indices: List[int],
        len_after_rerank: int = None,
    ):
        limit = len_after_rerank or len(candidate_items)
        try:
            response = self.llm_call(query, candidate_items)
            format_repaired = False
            try:
                selected_ids = self.parse_filter(response, len(candidate_items), limit)
            except (json.JSONDecodeError, ValueError):
                response = self.repair_json(response)
                selected_ids = self.parse_filter(response, len(candidate_items), limit)
                format_repaired = True
            selected_indices = [candidate_indices[index] for index in selected_ids]
            selected_items = [candidate_items[index] for index in selected_ids]
            return selected_indices, selected_items, {
                "mode": "llm",
                "model": self.model_name,
                "rerank_model": self.model_name,
                "selected_ids": selected_ids,
                "format_repaired": format_repaired,
                "fallback": None,
            }
        except Exception as error:
            logger.exception("Fact rerank call or parsing failed")
            return [], [], {
                "mode": "llm",
                "model": self.model_name,
                "rerank_model": self.model_name,
                "selected_ids": [],
                "error": str(error),
                "fallback": None,
            }
