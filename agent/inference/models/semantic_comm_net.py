"""Knowledge-driven Semantic Communication：T5 语义编解码。"""

from __future__ import annotations

import json
from typing import Any

import torch


class KnowledgeSemanticCommNet:
    """使用 Flan-T5 将结构化感知/认知数据编码为语义摘要与向量。"""

    def __init__(self, model_id: str = "google/flan-t5-small"):
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(model_id)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(model_id)
        self.model.eval()

    @property
    def device(self) -> torch.device:
        return next(self.model.parameters()).device

    def to_device(self, device: str) -> KnowledgeSemanticCommNet:
        self.model.to(device)
        return self

    @torch.inference_mode()
    def compress(self, perception: dict[str, Any], cognition: dict[str, Any]) -> dict[str, Any]:
        raw_size = len(json.dumps({"p": perception, "c": cognition}, ensure_ascii=False))
        prompt = self._build_prompt(perception, cognition)
        inputs = self.tokenizer(prompt, return_tensors="pt", truncation=True, max_length=512)
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        summary_ids = self.model.generate(**inputs, max_new_tokens=64, num_beams=2)
        summary = self.tokenizer.decode(summary_ids[0], skip_special_tokens=True).strip()

        enc = self.tokenizer(summary, return_tensors="pt", truncation=True, max_length=128)
        enc = {k: v.to(self.device) for k, v in enc.items()}
        hidden = self.model.encoder(**enc).last_hidden_state.mean(dim=1)[0]
        semantic_vector = hidden.cpu().tolist()

        targets = self._build_targets(perception, cognition)
        compressed = {
            "summary": summary or self._fallback_summary(targets),
            "targets": targets,
            "semantic_vector": semantic_vector,
            "knowledge_graph": {
                "nodes": cognition.get("entities", []),
                "edges": [
                    r for e in cognition.get("entities", []) for r in e.get("relations", [])
                ],
            },
        }
        comp_size = len(json.dumps(compressed, ensure_ascii=False))
        compressed["compression_ratio"] = round(raw_size / max(comp_size, 1), 2)
        return compressed

    @staticmethod
    def _build_prompt(perception: dict, cognition: dict) -> str:
        n_det = len(perception.get("detections", []))
        n_threat = sum(
            1 for t in cognition.get("threats", []) if t.get("threat_level") == "high"
        )
        return (
            "summarize tactical intelligence: "
            f"detections={n_det}, high_threats={n_threat}, "
            f"entities={len(cognition.get('entities', []))}"
        )

    @staticmethod
    def _fallback_summary(targets: list[dict]) -> str:
        high = sum(1 for t in targets if t.get("threat_level") == "high")
        return f"目标数={len(targets)}；高威胁={high}"

    @staticmethod
    def _build_targets(perception: dict, cognition: dict) -> list[dict]:
        class_by_id = {c["target_id"]: c for c in cognition.get("classifications", [])}
        threat_by_id = {t["target_id"]: t for t in cognition.get("threats", [])}
        entity_by_target = {
            e.get("entity_id", "").replace("ENT-", ""): e for e in cognition.get("entities", [])
        }
        targets: list[dict] = []
        for det in perception.get("detections", []):
            tid = det.get("track_id") or "unknown"
            cls = class_by_id.get(tid, {})
            threat = threat_by_id.get(tid, {})
            label = cls.get("label")
            affiliation = cls.get("affiliation")
            if affiliation is None and label:
                affiliation = (
                    "red" if label == "hostile" else "blue" if label == "friendly" else "unknown"
                )
            ent = entity_by_target.get(tid, {})
            targets.append(
                {
                    "track_id": tid,
                    "class": det.get("class_name"),
                    "label": label,
                    "affiliation": affiliation,
                    "threat_level": threat.get("threat_level"),
                    "geo": det.get("geo"),
                    "damage_score": det.get("damage_score"),
                    "confidence": det.get("confidence"),
                    "knowledge_ref": ent.get("entity_id"),
                }
            )
        return targets
