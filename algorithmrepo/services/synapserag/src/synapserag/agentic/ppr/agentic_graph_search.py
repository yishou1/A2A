import logging
import time
from typing import Dict, List, Tuple, TYPE_CHECKING, Optional

import numpy as np

from ...utils.misc_utils import (
    compute_mdhash_id,
    masked_softmax,
    min_max_normalize,
    softmax,
)
from .logging_utils import (
    AgenticLogContext,
    AgenticLogOptions,
    AgenticLogFormatter,
    shorten_text,
)

if TYPE_CHECKING:
    from ...SynapseRAG import SynapseRAG
    from ...tracing import RetrievalTraceCollector


logger = logging.getLogger(__name__)


class AgenticGraphSearch:
    """
    Encapsulates the enhanced (agentic) reset logic for HippoRAG's PPR search.
    Keeping this logic outside SynapseRAG.py keeps the upstream file lightweight.
    """

    def __init__(self, rag: "SynapseRAG"):
        self.rag = rag
        self.config = rag.global_config
        self._log_formatter = AgenticLogFormatter(logger)

    def search(self,
               query: str,
               link_top_k: int,
               query_fact_scores: np.ndarray,
               top_k_facts: List[Tuple],
               top_k_fact_indices: List[int],
               passage_node_weight: float = 0.05,
               log_options: Optional[AgenticLogOptions] = None,
               trace_collector: Optional["RetrievalTraceCollector"] = None,
               query_trace: Optional[Dict] = None) -> Tuple[np.ndarray, np.ndarray]:
        resolved_options = self._prepare_log_options(
            query=query,
            link_top_k=link_top_k,
            top_k_facts=top_k_facts,
            log_options=log_options,
        )

        if self._should_log(resolved_options):
            fact_preview = "; ".join(
                [f"{fact[0]} - {fact[1]} - {fact[2]}" for fact in top_k_facts[:3]]
            )
            header_lines = [
                f"top_facts={len(top_k_facts)} | link_top_k={link_top_k}",
            ]
            if fact_preview:
                header_lines.append(f"facts: {fact_preview}")
            self._log_block("Agentic PPR reset", header_lines, resolved_options)

        linking_score_map = {}
        phrase_weights = np.zeros(len(self.rag.graph.vs['name']))
        passage_weights = np.zeros(len(self.rag.graph.vs['name']))
        phrase_scores = {}

        for rank, fact in enumerate(top_k_facts):
            subject_phrase = fact[0].lower()
            predicate_phrase = fact[1].lower()
            object_phrase = fact[2].lower()
            fact_score = query_fact_scores[top_k_fact_indices[rank]] if query_fact_scores.ndim > 0 else query_fact_scores

            for phrase in (subject_phrase, object_phrase):
                phrase_key = compute_mdhash_id(content=phrase, prefix="entity-")
                phrase_id = self.rag.node_name_to_vertex_idx.get(phrase_key)

                if phrase_id is not None:
                    phrase_weights[phrase_id] = fact_score

                    ent_chunks = self.rag.ent_node_to_chunk_ids.get(phrase_key, set())
                    if ent_chunks:
                        phrase_weights[phrase_id] /= len(ent_chunks)

                phrase_scores.setdefault(phrase, []).append(fact_score)

        for phrase, scores in phrase_scores.items():
            linking_score_map[phrase] = float(np.mean(scores))

        self._log_linking_summary(linking_score_map, label="phrase", log_options=resolved_options)

        if link_top_k:
            phrase_weights, linking_score_map = self.rag.get_top_k_weights(
                link_top_k,
                phrase_weights,
                linking_score_map
            )

        dpr_sorted_doc_ids, dpr_sorted_doc_scores = self.rag.dense_passage_retrieval(query)
        normalized_scores = min_max_normalize(dpr_sorted_doc_scores)

        top_p = int(getattr(self.config, 'dpr_topP_for_reset', 200))
        top_p = max(0, min(top_p, len(dpr_sorted_doc_ids)))
        top_p_ids = set(dpr_sorted_doc_ids[:top_p].tolist()) if top_p > 0 else set()

        for i, doc_id in enumerate(dpr_sorted_doc_ids.tolist()):
            if top_p_ids and doc_id not in top_p_ids:
                continue
            passage_node_key = self.rag.passage_node_keys[doc_id]
            passage_node_id = self.rag.node_name_to_vertex_idx[passage_node_key]
            passage_dpr_score = normalized_scores[i]
            passage_weights[passage_node_id] = passage_dpr_score * passage_node_weight
            passage_node_text = self.rag.chunk_embedding_store.get_row(passage_node_key)["content"]
            linking_score_map[passage_node_text] = passage_dpr_score * passage_node_weight

        self._log_ranked_docs(dpr_sorted_doc_ids, normalized_scores, label="DPR", log_options=resolved_options)
        self._log_linking_summary(linking_score_map, label="phrase+passage", log_options=resolved_options)

        phrase_temp = getattr(self.config, 'phrase_temp', 1.0)
        passage_temp = getattr(self.config, 'passage_temp', 1.0)
        lambda_mix = getattr(self.config, 'lambda_mix', 0.5)
        use_softmax_fusion = getattr(self.config, 'use_softmax_fusion', True)
        use_masked_softmax = getattr(self.config, 'use_masked_softmax', True)

        if use_softmax_fusion:
            if use_masked_softmax:
                phrase_mask = (phrase_weights > 0)
                passage_mask = (passage_weights > 0)
                pw = masked_softmax(phrase_weights, mask=phrase_mask, T=phrase_temp)
                dw = masked_softmax(passage_weights, mask=passage_mask, T=passage_temp)
            else:
                pw = softmax(phrase_weights, T=phrase_temp)
                dw = softmax(passage_weights, T=passage_temp)
            node_weights = lambda_mix * pw + (1.0 - lambda_mix) * dw
        else:
            node_weights = phrase_weights + passage_weights

        self._log_reset_nodes(node_weights, resolved_options)

        if len(linking_score_map) > 30:
            linking_score_map = dict(sorted(linking_score_map.items(), key=lambda x: x[1], reverse=True)[:30])

        if np.sum(node_weights) <= 0:
            raise ValueError(f"No valid nodes for agentic reset, facts: {top_k_facts}")

        if trace_collector is not None and query_trace is not None:
            trace_collector.record_seed_nodes(query_trace, [
                {
                    "node_key": str(self.rag.graph.vs[node_id]["name"]),
                    "node_type": "entity" if str(self.rag.graph.vs[node_id]["name"]).startswith("entity-") else "chunk",
                    "reset_score": float(node_weights[node_id]),
                }
                for node_id in np.flatnonzero(node_weights > 0)
            ])

        ppr_started = time.perf_counter()
        ppr_sorted_doc_ids, ppr_sorted_doc_scores = self.rag.run_ppr(
            node_weights,
            damping=self.config.damping,
            trace_collector=trace_collector,
            query_trace=query_trace,
        )
        if trace_collector is not None and query_trace is not None:
            trace_collector.record_timing(
                query_trace, "ppr", time.perf_counter() - ppr_started
            )

        self._log_ranked_docs(ppr_sorted_doc_ids, ppr_sorted_doc_scores, label="PPR", log_options=resolved_options)

        return ppr_sorted_doc_ids, ppr_sorted_doc_scores

    def _log_linking_summary(self, linking_score_map: Dict[str, float], label: str, log_options: AgenticLogOptions) -> None:
        if not linking_score_map or not self._should_log(log_options):
            return
        top_items = sorted(linking_score_map.items(), key=lambda x: x[1], reverse=True)[:3]
        lines = [f"{shorten_text(text)} = {score:.4f}" for text, score in top_items]
        self._log_block(f"Agentic PPR {label} linking", lines, log_options, log_query=False)

    def _describe_node(self, node_idx: int, score: float) -> str:
        node_key = self.rag.graph.vs[node_idx]["name"]
        prefix = "node"
        node_text = node_key
        if node_key.startswith("entity-"):
            prefix = "entity"
            try:
                node_text = self.rag.entity_embedding_store.get_row(node_key)["content"]
            except KeyError:
                node_text = node_key
        elif node_key.startswith("chunk-"):
            prefix = "passage"
            try:
                node_text = self.rag.chunk_embedding_store.get_row(node_key)["content"]
            except KeyError:
                node_text = node_key
        return f"{prefix}: {shorten_text(node_text)} ({score:.4f})"

    def _log_reset_nodes(self, node_weights: np.ndarray, log_options: AgenticLogOptions) -> None:
        if not self._should_log(log_options):
            return
        nonzero = np.nonzero(node_weights)[0]
        if len(nonzero) == 0:
            self._log_block(
                "Agentic PPR reset skipped because no valid nodes had weight > 0.",
                None,
                log_options,
                log_query=False,
            )
            return
        top_k = min(3, len(nonzero))
        top_indices = np.argpartition(node_weights, -top_k)[-top_k:]
        top_indices = top_indices[np.argsort(node_weights[top_indices])[::-1]]
        descriptions = [self._describe_node(idx, node_weights[idx]) for idx in top_indices]
        self._log_block("Agentic PPR reset nodes", descriptions, log_options, log_query=False)

    def _log_ranked_docs(self,
                         doc_ids: np.ndarray,
                         doc_scores: np.ndarray,
                         label: str,
                         log_options: AgenticLogOptions) -> None:
        if not self._should_log(log_options) or doc_ids is None or len(doc_ids) == 0:
            return
        top_k = min(3, len(doc_ids))
        previews = []
        for rank in range(top_k):
            doc_idx = int(doc_ids[rank])
            try:
                chunk_key = self.rag.passage_node_keys[doc_idx]
                chunk_text = self.rag.chunk_embedding_store.get_row(chunk_key)["content"]
            except (IndexError, KeyError):
                chunk_text = f"doc_id={doc_idx}"
            previews.append(
                f"#{rank + 1}: {float(doc_scores[rank]):.4f} | {shorten_text(chunk_text)}"
            )
        self._log_block(f"Agentic PPR {label} top docs", previews, log_options, log_query=False)

    def _prepare_log_options(self,
                             query: str,
                             link_top_k: int,
                             top_k_facts: List[Tuple],
                             log_options: Optional[AgenticLogOptions]) -> AgenticLogOptions:
        base_options = log_options or AgenticLogOptions()
        context = base_options.context or AgenticLogContext()

        if not context.query:
            context = context.with_updates(query=query)

        context = context.with_extras(
            link_top_k=link_top_k,
            top_facts=len(top_k_facts),
        )
        return base_options.derive(context=context)

    def _log_block(self,
                   title: str,
                   lines: Optional[List[str]],
                   log_options: AgenticLogOptions,
                   log_query: Optional[bool] = None,
                   level: Optional[int] = None) -> None:
        options = log_options
        if log_query is not None or level is not None:
            options = options.derive(
                log_query=log_query if log_query is not None else options.log_query,
                level=level if level is not None else options.level
            )
        self._log_formatter.log_block(title, lines, options=options)

    def _should_log(self, log_options: AgenticLogOptions) -> bool:
        return logger.isEnabledFor(log_options.level)
