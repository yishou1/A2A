"""Bounded graph exploration with explicit association semantics."""

from collections import deque

from .graph_overlay import _edge_weight, _node_label


def explore(rag, request, source_map):
    graph = rag.graph
    names = {str(v["name"]): v.index for v in graph.vs}
    start = request.entity_id if request.operation == "neighbors" else request.source_id
    for key in [start] + ([request.target_id] if request.operation == "paths" else []):
        if key not in names:
            raise KeyError(key)
    queue = deque([(names[start],)])
    visited = {names[start]}
    nodes = {names[start]}
    edges = {}
    paths = []
    truncated = False
    examined = 0
    # Bound work as well as returned size on dense graphs.
    budget = 20000
    while queue and examined < budget:
        path = queue.popleft()
        vertex = path[-1]
        if request.operation == "paths" and vertex == names[request.target_id]:
            path_edges = [graph.get_eid(a, b, directed=False) for a, b in zip(path, path[1:])]
            if len(nodes | set(path)) > request.max_nodes or len(set(edges) | set(path_edges)) > request.max_edges:
                truncated = True
                continue
            nodes.update(path)
            for eid in path_edges:
                edges[eid] = graph.es[eid]
            paths.append({"nodes": [str(graph.vs[i]["name"]) for i in path], "hop_count": len(path)-1})
            if len(paths) >= request.max_paths:
                truncated = bool(queue)
                break
            continue
        if len(path) - 1 >= request.max_hops:
            continue
        for eid in graph.incident(vertex, mode="all"):
            examined += 1
            if examined > budget:
                truncated = True
                break
            edge = graph.es[eid]
            other = edge.target if edge.source == vertex else edge.source
            if other in path:
                continue
            if request.operation == "neighbors":
                if len(nodes | {other}) > request.max_nodes or len(set(edges) | {eid}) > request.max_edges:
                    truncated = True
                    continue
                nodes.add(other)
                edges[eid] = edge
                if other in visited:
                    continue
                visited.add(other)
            queue.append((*path, other))
    truncated = truncated or bool(queue)
    payload_nodes = []
    for vertex in sorted(nodes):
        key = str(graph.vs[vertex]["name"])
        item = {"node_key": key, "label": _node_label(rag, key),
                "node_type": "entity" if key.startswith("entity-") else "chunk"}
        if key.startswith("chunk-"):
            text = rag.chunk_embedding_store.get_row(key)["content"]
            item["sources"] = [
                {key: origin[key] for key in ("document_id", "filename", "page_start", "page_end", "heading_path", "chunk_id") if key in origin}
                for origin in source_map.get(text, [])
            ]
        payload_nodes.append(item)
    return {
        "nodes": payload_nodes,
        "edges": [{"source_key": str(graph.vs[e.source]["name"]),
                   "target_key": str(graph.vs[e.target]["name"]),
                   "weight": _edge_weight(e), "relation": "graph_association"}
                  for e in edges.values()],
        "paths": paths, "truncated": truncated,
        "semantics": "undirected_graph_association",
        "examined_edges": examined,
    }
