"""Build compact, evidence-oriented overlays for retrieval traces."""

from __future__ import annotations

import heapq
import math
from hashlib import md5
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


def _node_label(rag: Any, node_key: str) -> str:
    try:
        if node_key.startswith("entity-"):
            value = str(rag.entity_embedding_store.get_row(node_key)["content"])
            return value if len(value) <= 240 else value[:237] + "..."
        if node_key.startswith("chunk-"):
            value = str(rag.chunk_embedding_store.get_row(node_key)["content"])
            return value if len(value) <= 240 else value[:237] + "..."
    except (KeyError, TypeError):
        pass
    return node_key


def _roles_for_trace(trace: Dict[str, Any]) -> Dict[str, Set[str]]:
    roles: Dict[str, Set[str]] = {}

    def add(items: Iterable[Dict[str, Any]], role: str) -> None:
        for item in items:
            node_key = item.get("node_key")
            if node_key:
                roles.setdefault(str(node_key), set()).add(role)

    for evidence in trace.get("evidence", []):
        node_key = evidence.get("node_key") or evidence.get("chunk_id")
        if node_key:
            roles.setdefault(str(node_key), set()).add("final_evidence")
    add(trace.get("seed_nodes", []), "ppr_seed")
    for fact in trace.get("selected_facts", []):
        triple = fact.get("triple") or []
        for phrase in (triple[:1] + triple[2:3]):
            node_key = "entity-" + md5(str(phrase).lower().encode()).hexdigest()
            roles.setdefault(node_key, set()).add("selected_fact")
    add(trace.get("ppr_nodes", []), "ppr_recall")
    add(trace.get("channel_results", {}).get("graph", []), "graph_recall")
    add(trace.get("fusion_results", []), "fusion_result")
    return roles


def _edge_weight(edge: Any) -> float:
    try:
        value = float(edge["weight"]) if "weight" in edge.attributes() else 1.0
    except (KeyError, TypeError, ValueError):
        value = 1.0
    return value if math.isfinite(value) and value > 0 else 1e-6


def _graph_edges(rag: Any) -> Tuple[Dict[str, List[Tuple[str, float]]], Dict[Tuple[str, str], float]]:
    graph = rag.graph
    adjacency: Dict[str, List[Tuple[str, float]]] = {}
    weights: Dict[Tuple[str, str], float] = {}
    for edge in graph.es:
        source = str(graph.vs[edge.source]["name"])
        target = str(graph.vs[edge.target]["name"])
        weight = _edge_weight(edge)
        adjacency.setdefault(source, []).append((target, weight))
        adjacency.setdefault(target, []).append((source, weight))
        edge_key = tuple(sorted((source, target)))
        weights[edge_key] = max(weight, weights.get(edge_key, 0.0))
    for neighbors in adjacency.values():
        neighbors.sort(key=lambda item: (-item[1], item[0]))
    return adjacency, weights


def _bounded_paths(
    sources: Iterable[str],
    target: str,
    adjacency: Dict[str, List[Tuple[str, float]]],
    *,
    max_hops: int,
    max_paths: int,
    max_expansions: int = 20000,
) -> List[Tuple[float, List[str]]]:
    """Return deterministic high-weight simple paths from any source to target."""
    queue: List[Tuple[float, int, Tuple[str, ...]]] = []
    for source in sorted(set(sources)):
        # A passage may also be a PPR seed; a self path explains no graph relation.
        if source == target:
            continue
        heapq.heappush(queue, (0.0, 0, (source,)))
    found: List[Tuple[float, List[str]]] = []
    expansions = 0
    while queue and len(found) < max_paths and expansions < max_expansions:
        cost, hops, path = heapq.heappop(queue)
        current = path[-1]
        if current == target:
            found.append((cost, list(path)))
            continue
        if hops >= max_hops:
            continue
        expansions += 1
        for neighbor, weight in adjacency.get(current, []):
            if neighbor in path:
                continue
            next_cost = cost + (1.0 / max(weight, 1e-6)) + 0.05
            heapq.heappush(queue, (next_cost, hops + 1, path + (neighbor,)))
    return found


def _node_payload(
    rag: Any,
    node_key: str,
    roles: Dict[str, Set[str]],
    seed_scores: Dict[str, Any],
    ppr_scores: Dict[str, Any],
    fusion_scores: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "node_key": node_key,
        "label": _node_label(rag, node_key),
        "node_type": "entity" if node_key.startswith("entity-") else "chunk",
        "roles": sorted(roles.get(node_key, {"retrieval_connector"})),
        "reset_score": seed_scores.get(node_key),
        "ppr_score": ppr_scores.get(node_key),
        "final_score": fusion_scores.get(node_key),
    }


def build_graph_overlays(
    rag: Any,
    queries: List[Dict[str, Any]],
    *,
    max_nodes: int,
    max_edges: int,
    max_paths: int = 3,
    max_hops: int = 4,
) -> None:
    """Attach compact evidence paths and a node-only candidate view in place."""
    graph = getattr(rag, "graph", None)
    node_index = getattr(rag, "node_name_to_vertex_idx", {})
    if graph is None or not node_index:
        return

    adjacency, graph_weights = _graph_edges(rag)
    max_paths = max(1, min(int(max_paths), 3))
    max_hops = max(1, min(int(max_hops), 8))

    for trace in queries:
        roles = _roles_for_trace(trace)
        seed_scores = {
            item["node_key"]: item.get("reset_score")
            for item in trace.get("seed_nodes", []) if item.get("node_key")
        }
        ppr_scores = {
            item["node_key"]: item.get("ppr_score")
            for item in trace.get("ppr_nodes", []) if item.get("node_key")
        }
        fusion_scores = {
            item["node_key"]: item.get("final_score")
            for item in trace.get("fusion_results", []) if item.get("node_key")
        }
        source_keys = [
            key for key, value in roles.items()
            if key in node_index and ("ppr_seed" in value or "selected_fact" in value)
        ]
        evidence_keys = [
            str(item.get("node_key") or item.get("chunk_id"))
            for item in trace.get("evidence", [])
            if item.get("node_key") or item.get("chunk_id")
        ]
        evidence_keys = list(dict.fromkeys(key for key in evidence_keys if key in node_index))

        selected_nodes: Set[str] = set()
        selected_edges: Dict[Tuple[str, str], Dict[str, Any]] = {}
        evidence_paths = []
        covered_evidence: Set[str] = set()
        for evidence_key in evidence_keys:
            paths = _bounded_paths(
                source_keys,
                evidence_key,
                adjacency,
                max_hops=max_hops,
                max_paths=max_paths,
            ) if source_keys else []
            if not paths:
                selected_nodes.add(evidence_key)
                continue
            if max_edges <= 0:
                selected_nodes.add(evidence_key)
                continue
            for rank, (cost, path) in enumerate(paths, start=1):
                additions = set(path) - selected_nodes
                new_edge_count = sum(
                    1 for source, target in zip(path, path[1:])
                    if tuple(sorted((source, target))) not in selected_edges
                )
                if len(selected_nodes) + len(additions) > max_nodes:
                    continue
                if max_edges and len(selected_edges) + new_edge_count > max_edges:
                    continue
                selected_nodes.update(path)
                connectors = path[1:-1]
                for connector in connectors:
                    roles.setdefault(connector, set()).add("retrieval_connector")
                path_edges = []
                for source, target in zip(path, path[1:]):
                    edge_key = tuple(sorted((source, target)))
                    payload = {
                        "source_key": source,
                        "target_key": target,
                        "weight": graph_weights.get(edge_key, 1.0),
                        "roles": ["evidence_path"],
                    }
                    selected_edges.setdefault(edge_key, payload)
                    path_edges.append(payload)
                evidence_paths.append({
                    "path_id": f"{evidence_key}-path-{rank}",
                    "evidence_node_key": evidence_key,
                    "rank": rank,
                    "path_score": round(1.0 / (1.0 + cost), 6),
                    "hop_count": max(0, len(path) - 1),
                    "nodes": path,
                    "edges": path_edges,
                    "connector_nodes": connectors,
                })
                covered_evidence.add(evidence_key)

        for source in source_keys:
            if len(selected_nodes) >= max_nodes:
                break
            selected_nodes.add(source)

        candidate_keys = [key for key in roles if key in node_index][:max_nodes]
        node_payloads = {
            node_key: _node_payload(
                rag, node_key, roles, seed_scores, ppr_scores, fusion_scores
            )
            for node_key in set(candidate_keys) | selected_nodes
        }
        trace["evidence_paths"] = evidence_paths
        trace["candidate_overlay"] = {
            "semantics": "retrieval_candidates",
            "truncated": len(roles) > len(candidate_keys),
            "limits": {"max_nodes": max_nodes, "max_edges": 0},
            "nodes": [node_payloads[key] for key in candidate_keys],
            "edges": [],
        }
        trace["graph_overlay"] = {
            "semantics": "evidence_skeleton",
            "truncated": len(covered_evidence) < len(evidence_keys),
            "limits": {
                "max_nodes": max_nodes,
                "max_edges": max_edges,
                "max_paths_per_evidence": max_paths,
                "max_hops": max_hops,
            },
            "nodes": [node_payloads[key] for key in sorted(selected_nodes)],
            "edges": list(selected_edges.values()),
        }
        trace["display_stats"] = {
            "candidate_node_count": len(roles),
            "displayed_node_count": len(selected_nodes),
            "displayed_edge_count": len(selected_edges),
            "evidence_count": len(evidence_keys),
            "path_count": len(evidence_paths),
            "truncated": trace["candidate_overlay"]["truncated"] or trace["graph_overlay"]["truncated"],
        }


def select_graph_overlay(
    query: Dict[str, Any],
    *,
    view: str = "skeleton",
    evidence_node_key: Optional[str] = None,
    max_paths: int = 1,
) -> Dict[str, Any]:
    """Select a bounded persisted overlay without needing the live graph."""
    if view == "candidates":
        candidate = query.get("candidate_overlay")
        if candidate:
            return candidate
        legacy = query.get("graph_overlay", {"nodes": [], "edges": []})
        return {
            "semantics": "retrieval_candidates",
            "truncated": legacy.get("truncated", False),
            "limits": {"max_nodes": len(legacy.get("nodes", [])), "max_edges": 0},
            "nodes": legacy.get("nodes", []),
            "edges": [],
        }
    overlay = query.get("graph_overlay", {"nodes": [], "edges": []})
    paths = query.get("evidence_paths") or []
    if not paths and overlay.get("semantics") == "high_contribution_neighborhood":
        terminal_roles = {"final_evidence", "ppr_seed", "selected_fact"}
        nodes = [
            node for node in overlay.get("nodes", [])
            if terminal_roles.intersection(node.get("roles", []))
        ]
        node_keys = {node.get("node_key") for node in nodes}
        edges = [
            edge for edge in overlay.get("edges", [])
            if edge.get("source_key") in node_keys and edge.get("target_key") in node_keys
        ]
        return {
            "semantics": "legacy_evidence_terminals",
            "truncated": True,
            "limits": {"legacy_trace": True},
            "nodes": nodes,
            "edges": edges,
        }
    if not evidence_node_key or not paths:
        return overlay

    matching = [path for path in paths if path.get("evidence_node_key") == evidence_node_key]
    selected_paths = matching[:max(1, min(int(max_paths), 3))]
    node_keys = {key for path in selected_paths for key in path.get("nodes", [])}
    edges = [edge for path in selected_paths for edge in path.get("edges", [])]
    edge_by_key = {
        tuple(sorted((str(edge.get("source_key")), str(edge.get("target_key"))))): edge
        for edge in edges
    }
    nodes = [node for node in overlay.get("nodes", []) if node.get("node_key") in node_keys]
    return {
        "semantics": "single_evidence_path",
        "truncated": len(selected_paths) < len(matching),
        "limits": {"max_paths": max_paths},
        "evidence_node_key": evidence_node_key,
        "nodes": nodes,
        "edges": list(edge_by_key.values()),
    }
