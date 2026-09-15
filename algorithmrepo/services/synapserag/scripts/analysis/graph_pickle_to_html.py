#!/usr/bin/env python3
"""
Convert a SynapseRAG igraph pickle into a standalone interactive HTML graph.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import random
import re
from collections import defaultdict
from hashlib import md5
from pathlib import Path
from typing import Any

import igraph as ig


ENTITY_COLOR = "#2F80ED"
CHUNK_COLOR = "#27AE60"
OTHER_COLOR = "#8E44AD"
EDGE_COLOR = "#AAB7C4"


def text_processing(text: Any) -> str:
    """Match the current SynapseRAG entity normalization used for graph ids."""
    if isinstance(text, list):
        return " ".join(text_processing(t) for t in text)
    if not isinstance(text, str):
        text = str(text)
    text = re.sub(r"[^\w\u4e00-\u9fff ]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def compute_mdhash_id(content: str, prefix: str = "") -> str:
    return prefix + md5(content.encode()).hexdigest()


def shorten(text: Any, limit: int = 80) -> str:
    text = "" if text is None else str(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."


def node_type(name: str) -> str:
    if name.startswith("entity-"):
        return "entity"
    if name.startswith("chunk-"):
        return "chunk"
    return "other"


def node_color(kind: str) -> str:
    if kind == "entity":
        return ENTITY_COLOR
    if kind == "chunk":
        return CHUNK_COLOR
    return OTHER_COLOR


def node_label(vertex: ig.Vertex) -> str:
    name = vertex["name"] if "name" in vertex.attributes() else str(vertex.index)
    content = vertex["content"] if "content" in vertex.attributes() else name
    kind = node_type(name)
    if kind == "chunk":
        first_line = str(content).splitlines()[0] if content else name
        return shorten(first_line, 42)
    return shorten(content, 42)


def node_title(vertex: ig.Vertex) -> str:
    name = vertex["name"] if "name" in vertex.attributes() else str(vertex.index)
    content = vertex["content"] if "content" in vertex.attributes() else ""
    kind = node_type(name)
    return f"type: {kind}\nid: {name}\n\n{content}"


def load_relation_labels(openie_path: Path | None) -> dict[tuple[str, str], str]:
    if openie_path is None:
        return {}

    payload = json.loads(openie_path.read_text(encoding="utf-8"))
    relation_map: dict[tuple[str, str], set[str]] = defaultdict(set)

    for doc in payload.get("docs", []):
        for triple in doc.get("extracted_triples", []):
            if not isinstance(triple, list | tuple) or len(triple) != 3:
                continue
            subj, pred, obj = triple
            src = compute_mdhash_id(text_processing(subj), prefix="entity-")
            dst = compute_mdhash_id(text_processing(obj), prefix="entity-")
            pred_text = shorten(pred, 28)
            relation_map[(src, dst)].add(pred_text)
            relation_map[(dst, src)].add(pred_text)

    return {key: " / ".join(sorted(values)[:3]) for key, values in relation_map.items()}


def select_vertices(
    graph: ig.Graph,
    max_nodes: int,
    focus_terms: list[str],
    focus_hops: int,
    include_chunks: bool,
) -> set[int]:
    allowed = set(range(graph.vcount()))

    if not include_chunks:
        allowed = {
            v.index
            for v in graph.vs
            if "name" in v.attributes() and not str(v["name"]).startswith("chunk-")
        }

    if focus_terms:
        lowered_terms = [term.lower() for term in focus_terms]
        seeds = []
        for vertex in graph.vs:
            if vertex.index not in allowed:
                continue
            haystack = f"{node_label(vertex)} {node_title(vertex)}".lower()
            if any(term in haystack for term in lowered_terms):
                seeds.append(vertex.index)

        selected = set(seeds)
        frontier = set(seeds)
        for _ in range(max(0, focus_hops)):
            next_frontier = set()
            for idx in frontier:
                next_frontier.update(graph.neighbors(idx))
            next_frontier &= allowed
            selected.update(next_frontier)
            frontier = next_frontier
        allowed = selected or allowed

    if max_nodes > 0 and len(allowed) > max_nodes:
        degrees = graph.degree(list(allowed))
        ranked = sorted(zip(allowed, degrees), key=lambda item: item[1], reverse=True)
        allowed = {idx for idx, _ in ranked[:max_nodes]}

    return allowed


def compute_structural_layout_3d(
    graph: ig.Graph,
    selected: set[int],
) -> tuple[
    dict[int, tuple[float, float, float]],
    dict[int, int],
    list[dict[str, Any]],
]:
    """Place graph communities in separated, evenly populated 3D regions."""
    selected_vertices = sorted(selected)
    count = len(selected_vertices)
    if not count:
        return {}, {}, []

    subgraph = graph.induced_subgraph(selected_vertices)
    weights = [
        max(0.05, min(5.0, float(edge["weight"])))
        if "weight" in edge.attributes()
        else 1.0
        for edge in subgraph.es
    ]
    ig.set_random_number_generator(random.Random(42))
    if subgraph.ecount():
        membership = subgraph.community_multilevel(weights=weights).membership
    else:
        membership = list(range(count))

    members_by_original_community: dict[int, list[int]] = defaultdict(list)
    for local_index, community_id in enumerate(membership):
        members_by_original_community[community_id].append(local_index)
    ordered_communities = sorted(
        members_by_original_community.values(),
        key=lambda members: (-len(members), min(members)),
    )

    golden_angle = math.pi * (3 - math.sqrt(5))
    community_count = len(ordered_communities)
    community_radii = [
        max(42.0, min(220.0, 31.0 * len(members) ** (1 / 3)))
        for members in ordered_communities
    ]
    center_radius = max(390.0, 135.0 * math.sqrt(community_count)) if community_count > 1 else 0.0
    positions: dict[int, tuple[float, float, float]] = {}
    community_by_vertex: dict[int, int] = {}
    communities: list[dict[str, Any]] = []
    local_degrees = subgraph.degree()

    for community_id, members in enumerate(ordered_communities):
        if community_count == 1:
            center = (0.0, 0.0, 0.0)
        else:
            center_y = 1 - 2 * (community_id + 0.5) / community_count
            center_ring = math.sqrt(max(0.0, 1 - center_y * center_y))
            center_angle = community_id * golden_angle
            center = (
                center_radius * center_ring * math.cos(center_angle),
                center_radius * center_y,
                center_radius * center_ring * math.sin(center_angle),
            )

        members = sorted(members, key=lambda index: (-local_degrees[index], index))
        community_size = len(members)
        local_radius = community_radii[community_id]
        communities.append(
            {
                "id": community_id,
                "label": f"社区 {community_id + 1}",
                "size": community_size,
                "radius": round(local_radius, 4),
                "x": round(center[0], 4),
                "y": round(center[1], 4),
                "z": round(center[2], 4),
            }
        )

        for rank, local_index in enumerate(members):
            if community_size == 1:
                offset = (0.0, 0.0, 0.0)
            else:
                local_y = 1 - 2 * (rank + 0.5) / community_size
                local_ring = math.sqrt(max(0.0, 1 - local_y * local_y))
                local_angle = (rank + community_id * 0.37) * golden_angle
                shell_radius = local_radius * ((rank + 1) / community_size) ** (1 / 3)
                offset = (
                    shell_radius * local_ring * math.cos(local_angle),
                    shell_radius * local_y,
                    shell_radius * local_ring * math.sin(local_angle),
                )
            old_index = selected_vertices[local_index]
            positions[old_index] = tuple(
                round(center[axis] + offset[axis], 4)
                for axis in range(3)
            )
            community_by_vertex[old_index] = community_id

    return positions, community_by_vertex, communities


def build_payload(
    graph: ig.Graph,
    selected: set[int],
    min_edge_weight: float,
    relation_labels: dict[tuple[str, str], str],
) -> dict[str, Any]:
    id_map = {old_idx: new_idx for new_idx, old_idx in enumerate(sorted(selected))}
    degrees = dict(zip(range(graph.vcount()), graph.degree()))
    positions, community_by_vertex, communities = compute_structural_layout_3d(
        graph=graph,
        selected=selected,
    )

    nodes = []
    for old_idx in sorted(selected):
        vertex = graph.vs[old_idx]
        name = vertex["name"] if "name" in vertex.attributes() else str(old_idx)
        kind = node_type(name)
        degree = degrees.get(old_idx, 1)
        x, y, z = positions[old_idx]
        nodes.append(
            {
                "id": id_map[old_idx],
                "name": name,
                "label": node_label(vertex),
                "title": node_title(vertex),
                "type": kind,
                "color": node_color(kind),
                # Keep high-degree nodes distinguishable without letting hubs
                # obscure their neighbors in dense graphs.
                "size": 2.5 + min(6, math.sqrt(max(degree, 1)) * 0.55),
                "degree": degree,
                "community": community_by_vertex[old_idx],
                "x": x,
                "y": y,
                "z": z,
            }
        )

    links = []
    for edge in graph.es:
        src, dst = edge.tuple
        if src not in selected or dst not in selected:
            continue
        weight = float(edge["weight"]) if "weight" in edge.attributes() else 1.0
        if weight < min_edge_weight:
            continue

        src_name = graph.vs[src]["name"] if "name" in graph.vs[src].attributes() else str(src)
        dst_name = graph.vs[dst]["name"] if "name" in graph.vs[dst].attributes() else str(dst)
        label = relation_labels.get((src_name, dst_name), "")
        
        src_kind = node_type(src_name)
        dst_kind = node_type(dst_name)
        is_chunk_edge = (src_kind == "chunk" or dst_kind == "chunk")
        
        if not is_chunk_edge:
            if label:
                label += f" (w:{weight:.3f})"
            else:
                label = f"w:{weight:.3f}"
        
        links.append(
            {
                "source": id_map[src],
                "target": id_map[dst],
                "weight": weight,
                "label": label,
                "is_chunk_edge": is_chunk_edge,
            }
        )

    return {
        "nodes": nodes,
        "links": links,
        "communities": communities,
        "stats": {
            "visible_nodes": len(nodes),
            "visible_edges": len(links),
            "total_nodes": graph.vcount(),
            "total_edges": graph.ecount(),
        },
    }


def render_html(payload: dict[str, Any], title: str) -> str:
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    safe_title = html.escape(title)
    template = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>SynapseRAG 3D · __TITLE__</title>
  <style>
    :root { color-scheme: dark; }
    * { box-sizing: border-box; }
    html, body { margin: 0; height: 100%; overflow: hidden; font-family: Inter, Arial, "Microsoft YaHei", sans-serif; background: #050b14; }
    body::before { content: ""; position: fixed; inset: 0; pointer-events: none; background: radial-gradient(circle at 58% 46%, rgba(25,78,125,.3), transparent 44%), linear-gradient(135deg, #071321, #03070d 72%); }
    body::after { content: ""; position: fixed; inset: 0; pointer-events: none; opacity: .16; background-image: linear-gradient(rgba(83,163,218,.13) 1px, transparent 1px), linear-gradient(90deg, rgba(83,163,218,.13) 1px, transparent 1px); background-size: 42px 42px; mask-image: radial-gradient(circle at 58% 48%, #000 5%, transparent 76%); }
    #toolbar { position: fixed; top: 14px; left: 14px; z-index: 10; width: 382px; max-height: calc(100vh - 28px); overflow: auto; color: #dcecff; background: rgba(8,20,34,.9); border: 1px solid rgba(117,174,222,.24); border-radius: 12px; padding: 13px 14px; box-shadow: 0 14px 40px rgba(0,0,0,.4); backdrop-filter: blur(14px); }
    #toolbar.collapsed { width: auto; overflow: hidden; }
    #toolbar.collapsed > :not(.panel-header) { display: none; }
    .panel-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; }
    .panel-header button { min-width: 30px; padding: 3px 8px !important; font-size: 17px; line-height: 1; }
    #toolbar h1 { font-size: 15px; margin: 0 0 5px; color: #f4f9ff; letter-spacing: .2px; }
    #toolbar .subtitle { margin-bottom: 8px; color: #75bfff; font-size: 11px; text-transform: uppercase; letter-spacing: 1.2px; }
    #toolbar .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 9px; }
    #toolbar .control { display: grid; grid-template-columns: 72px 1fr 42px; gap: 8px; width: 100%; align-items: center; margin-top: 9px; font-size: 12px; color: #aec3d7; }
    #toolbar input[type="search"] { flex: 1; min-width: 220px; padding: 7px 9px; color: #eaf5ff; background: rgba(255,255,255,.06); border: 1px solid rgba(151,190,224,.28); border-radius: 7px; outline: none; }
    #toolbar input[type="search"]:focus { border-color: #4ba8f5; box-shadow: 0 0 0 2px rgba(75,168,245,.14); }
    #toolbar input[type="range"] { width: 100%; accent-color: #4ba8f5; }
    #toolbar button { padding: 6px 10px; border: 1px solid rgba(151,190,224,.3); border-radius: 7px; color: #dcecff; background: rgba(255,255,255,.06); cursor: pointer; }
    #toolbar button:hover { border-color: #58b6ff; background: rgba(88,182,255,.12); }
    #toolbar label { font-size: 12px; color: #b8cadb; }
    #toolbar output { text-align: right; color: #7dc5ff; font-variant-numeric: tabular-nums; }
    #stats { font-size: 12px; line-height: 1.5; color: #91a9bd; }
    #legend { display: flex; gap: 12px; margin-top: 10px; font-size: 11px; color: #aebfd0; }
    .legend-dot { display: inline-block; width: 8px; height: 8px; margin-right: 4px; border-radius: 50%; box-shadow: 0 0 8px currentColor; }
    #hint { margin-top: 10px; padding-top: 9px; border-top: 1px solid rgba(151,190,224,.16); font-size: 11px; line-height: 1.55; color: #728aa0; }
    #edgeCanvas, #graph { position: fixed; inset: 0; width: 100vw; height: 100vh; display: block; }
    #edgeCanvas { z-index: 1; pointer-events: none; }
    #graph { z-index: 2; cursor: grab; touch-action: none; user-select: none; }
    #graph.rotating { cursor: grabbing; }
    .link { stroke: #75a3c7; vector-effect: non-scaling-stroke; }
    .community-region circle { fill: rgba(64,142,201,.025); stroke: rgba(104,181,240,.22); stroke-width: 1px; stroke-dasharray: 5 7; pointer-events: none; }
    .community-region text { fill: #6f9dbf; font-size: 11px; font-weight: 600; text-anchor: middle; letter-spacing: .8px; paint-order: stroke; stroke: #06101a; stroke-width: 3px; pointer-events: none; }
    .edge-label { font-size: 9px; fill: #a9cee9; paint-order: stroke; stroke: #07111c; stroke-width: 3px; stroke-linejoin: round; pointer-events: none; }
    .node { cursor: move; }
    .node circle { stroke: rgba(238,248,255,.9); stroke-width: .8px; filter: drop-shadow(0 0 4px rgba(80,180,255,.42)); }
    .node text { font-size: 10px; fill: #e5f3ff; paint-order: stroke; stroke: #06101a; stroke-width: 3px; stroke-linejoin: round; pointer-events: none; }
    .node.dim { opacity: .1 !important; }
    .node.match circle { stroke: #ffca4b; stroke-width: 2.5px; filter: drop-shadow(0 0 7px #ffb000); }
    .node.selected circle { stroke: #fff176; stroke-width: 3px; filter: drop-shadow(0 0 10px #ffca28); }
    .node.trace-seed circle { stroke: #4aa3c7; stroke-width: 2.4px; }
    .node.trace-recall circle { stroke: #8eb0bf; stroke-width: 2.2px; }
    .node.trace-fusion circle { stroke: #d6a34d; stroke-width: 2.8px; }
    .node.trace-evidence circle { stroke: #e66f61; stroke-width: 3.4px; filter: drop-shadow(0 0 7px rgba(230,111,97,.8)); }
    #tooltip { position: fixed; pointer-events: none; z-index: 20; max-width: 460px; max-height: 45vh; overflow: hidden; white-space: pre-wrap; color: #edf7ff; background: rgba(3,10,18,.96); border: 1px solid rgba(104,181,240,.38); padding: 10px 11px; border-radius: 8px; font-size: 12px; line-height: 1.5; display: none; box-shadow: 0 12px 34px rgba(0,0,0,.48); }
    #selection { color: #ffd369; font-size: 11px; margin-top: 6px; min-height: 16px; }
    #viewportHud { position: fixed; right: 18px; bottom: 16px; z-index: 8; min-width: 240px; padding: 11px 14px; color: #8eabc1; background: linear-gradient(135deg, rgba(7,22,36,.78), rgba(4,12,21,.62)); border: 1px solid rgba(77,172,234,.2); border-radius: 10px; box-shadow: 0 10px 28px rgba(0,0,0,.28); backdrop-filter: blur(9px); pointer-events: none; }
    #viewportHud::before, #viewportHud::after { content: ""; position: absolute; width: 18px; height: 18px; border-color: #42aef4; opacity: .7; }
    #viewportHud::before { left: -1px; top: -1px; border-left: 2px solid; border-top: 2px solid; border-radius: 10px 0 0; }
    #viewportHud::after { right: -1px; bottom: -1px; border-right: 2px solid; border-bottom: 2px solid; border-radius: 0 0 10px; }
    #viewportHud .hud-title { color: #59b9f6; font-size: 9px; letter-spacing: 1.7px; text-transform: uppercase; }
    #viewportHud .hud-values { display: flex; gap: 20px; margin-top: 7px; }
    #viewportHud b { display: block; color: #e8f6ff; font-size: 18px; font-weight: 550; font-variant-numeric: tabular-nums; }
    #viewportHud small { font-size: 9px; letter-spacing: .9px; }
    .status-dot { display: inline-block; width: 6px; height: 6px; margin-right: 6px; border-radius: 50%; background: #35d69b; box-shadow: 0 0 8px #35d69b; }
  </style>
</head>
<body>
  <div id="toolbar">
    <div class="panel-header">
      <div>
        <div class="subtitle">SynapseRAG · Interactive 3D</div>
        <h1>__TITLE__</h1>
      </div>
      <button id="collapsePanel" title="折叠控制面板">−</button>
    </div>
    <div id="stats"></div>
    <div id="selection"></div>
    <div class="row">
      <input id="search" type="search" placeholder="搜索实体或文档内容" />
      <button id="clear">清除</button>
    </div>
    <div class="control">
      <span>节点数量</span>
      <input id="nodeLimit" type="range" min="10" max="300" step="10" value="300" />
      <output id="nodeLimitValue">300</output>
    </div>
    <div class="control">
      <span>边密度</span>
      <input id="edgeDensity" type="range" min="1" max="100" value="12" />
      <output id="edgeDensityValue">12%</output>
    </div>
    <div class="control">
      <span>标签数量</span>
      <input id="labelLimit" type="range" min="0" max="300" value="55" />
      <output id="labelLimitValue">55</output>
    </div>
    <div class="control">
      <span>节点尺寸</span>
      <input id="nodeScale" type="range" min="55" max="180" value="100" />
      <output id="nodeScaleValue">100%</output>
    </div>
    <div class="row">
      <label><input id="toggleLabels" type="checkbox" checked /> 显示节点文字</label>
      <label><input id="toggleEdgeLabels" type="checkbox" /> 显示关系文字</label>
      <label><input id="toggleChunks" type="checkbox" checked /> 显示文档分块</label>
      <label><input id="toggleCommunities" type="checkbox" checked /> 显示社区边界</label>
    </div>
    <div class="row">
      <button id="resetView">重置视角与布局</button>
      <button id="frontView">正视图</button>
      <button id="fullscreen">全屏</button>
    </div>
    <div id="legend">
      <span><i class="legend-dot" style="background:#2F80ED;color:#2F80ED"></i>实体</span>
      <span><i class="legend-dot" style="background:#27AE60;color:#27AE60"></i>文档分块</span>
      <span><i class="legend-dot" style="background:#8E44AD;color:#8E44AD"></i>其他</span>
    </div>
    <div id="hint">拖动空白处旋转 · Shift+拖动平移 · 滚轮缩放<br>拖动节点调整位置 · 单击节点聚焦其关联结构</div>
  </div>
  <canvas id="edgeCanvas"></canvas>
  <svg id="graph"></svg>
  <div id="tooltip"></div>
  <div id="viewportHud">
    <div class="hud-title"><i class="status-dot"></i>Local Knowledge Space</div>
    <div class="hud-values">
      <span><b id="hudNodes">0</b><small>VISIBLE NODES</small></span>
      <span><b id="hudEdges">0</b><small>VISIBLE EDGES</small></span>
      <span><b id="hudCommunities">0</b><small>COMMUNITIES</small></span>
    </div>
  </div>
  <script>
    const payload = __PAYLOAD__;
    const nodes = payload.nodes;
    const links = payload.links;
    const communities = payload.communities || [];
    const edgeCanvas = document.getElementById("edgeCanvas");
    const edgeContext = edgeCanvas.getContext("2d");
    const svg = document.getElementById("graph");
    const tooltip = document.getElementById("tooltip");
    const stats = document.getElementById("stats");
    const selection = document.getElementById("selection");

    const NS = "http://www.w3.org/2000/svg";
    const communityLayer = document.createElementNS(NS, "g");
    const nodeLayer = document.createElementNS(NS, "g");
    svg.append(communityLayer, nodeLayer);

    let width = window.innerWidth;
    let height = window.innerHeight;
    const initialPositions = nodes.map(node => ({ x: node.x, y: node.y, z: node.z }));
    const camera = { rotX: -0.28, rotY: 0.58, zoom: .62, panX: 140, panY: 0, distance: 2200 };
    let draggingNode = null;
    let dragState = null;
    let viewDrag = null;
    let lastDragMoved = false;
    let selectedNodeId = null;
    let searchTerm = "";
    let nodeLimit = Math.min(300, nodes.length);
    let edgeDensity = 12;
    let labelLimit = Math.min(55, nodes.length);
    let nodeScale = 1;
    let showNodeLabels = true;
    let showEdgeLabels = false;
    let showChunks = true;
    let showCommunities = true;
    let cachedState = null;
    let traceOverlayNodes = new Map();
    let traceOverlayEdges = new Map();
    const depthExtent = Math.max(
      360,
      ...nodes.map(node => Math.abs(node.z)),
      ...communities.map(community => Math.abs(community.z) + community.radius),
    );

    const degreeOrder = nodes.map((node, index) => index)
      .sort((a, b) => nodes[b].degree - nodes[a].degree);
    const nodeRank = new Map(degreeOrder.map((index, rank) => [index, rank]));
    const labelRank = new Map(degreeOrder.map((index, rank) => [index, rank]));
    const edgeOrder = links.map((link, index) => index)
      .sort((a, b) => links[b].weight - links[a].weight);
    const nodeIndexByKey = new Map(nodes.map((node, index) => [node.name, index]));

   function graphEdgeKey(sourceKey, targetKey) {
      return `${sourceKey}\\u0000${targetKey}`;
    }

    function overlayEdgeFor(link) {
      const sourceKey = nodes[link.source].name;
      const targetKey = nodes[link.target].name;
      return traceOverlayEdges.get(graphEdgeKey(sourceKey, targetKey)) ||
        traceOverlayEdges.get(graphEdgeKey(targetKey, sourceKey));
    }

    function emitNodeSelection(node) {
      window.dispatchEvent(new CustomEvent("synapserag:node-select", {detail:node}));
      if (window.parent !== window) {
        window.parent.postMessage({type:"synapserag:node-select", node}, window.location.origin);
      }
    }

    const nodeLimitInput = document.getElementById("nodeLimit");
    nodeLimitInput.min = Math.min(10, nodes.length);
    nodeLimitInput.max = nodes.length;
    nodeLimitInput.step = 1;
    nodeLimitInput.value = nodeLimit;
    document.getElementById("nodeLimitValue").textContent = nodeLimit;
    document.getElementById("labelLimit").max = nodes.length;
    document.getElementById("labelLimit").value = labelLimit;
    document.getElementById("labelLimitValue").textContent = labelLimit;

    function make(tag, attrs = {}) {
      const el = document.createElementNS(NS, tag);
      Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, value));
      return el;
    }

    function resizeCanvas() {
      const ratio = Math.min(2, window.devicePixelRatio || 1);
      edgeCanvas.width = Math.round(width * ratio);
      edgeCanvas.height = Math.round(height * ratio);
      edgeContext.setTransform(ratio, 0, 0, ratio, 0, 0);
    }
    resizeCanvas();

    const communityEls = communities.map(community => {
      const group = make("g", { class: "community-region" });
      const circle = make("circle");
      const text = make("text");
      text.textContent = `${community.label} · ${community.size} 节点`;
      group.append(circle, text);
      communityLayer.append(group);
      return group;
    });

    const nodeEls = nodes.map(node => {
      const group = make("g", { class: "node" });
      const circle = make("circle", { r: node.size, fill: node.color });
      const text = make("text", { x: node.size + 4, y: 4 });
      text.textContent = node.label;
      group.append(circle, text);
      nodeLayer.append(group);
      group.addEventListener("mouseenter", event => showTooltip(event, node));
      group.addEventListener("mousemove", event => moveTooltip(event));
      group.addEventListener("mouseleave", hideTooltip);
      group.addEventListener("mousedown", event => {
        draggingNode = node;
        dragState = { clientX: event.clientX, clientY: event.clientY, x: node.x, y: node.y, z: node.z };
        lastDragMoved = false;
        event.preventDefault();
        event.stopPropagation();
      });
      group.addEventListener("click", event => {
        event.stopPropagation();
        if (lastDragMoved) {
          lastDragMoved = false;
          return;
        }
        selectedNodeId = selectedNodeId === node.id ? null : node.id;
        cachedState = null;
        render();
        emitNodeSelection(selectedNodeId === null ? null : nodes[selectedNodeId]);
      });
      return group;
    });

    function showTooltip(event, node) {
      tooltip.style.display = "block";
      tooltip.textContent = `${node.label}\n社区：${node.community + 1} · 连接度：${node.degree}\n\n${node.title}`;
      moveTooltip(event);
    }
    function moveTooltip(event) {
      const x = Math.min(window.innerWidth - 470, event.clientX + 14);
      const y = Math.min(window.innerHeight - 180, event.clientY + 14);
      tooltip.style.left = `${Math.max(8, x)}px`;
      tooltip.style.top = `${Math.max(8, y)}px`;
    }
    function hideTooltip() {
      tooltip.style.display = "none";
    }

    function project(node) {
      const cy = Math.cos(camera.rotY), sy = Math.sin(camera.rotY);
      const cx = Math.cos(camera.rotX), sx = Math.sin(camera.rotX);
      const x1 = cy * node.x + sy * node.z;
      const z1 = -sy * node.x + cy * node.z;
      const y2 = cx * node.y - sx * z1;
      const z2 = sx * node.y + cx * z1;
      const perspective = camera.distance / Math.max(360, camera.distance - z2);
      return {
        x: width / 2 + camera.panX + x1 * camera.zoom * perspective,
        y: height / 2 + camera.panY + y2 * camera.zoom * perspective,
        z: z2,
        scale: perspective,
      };
    }

    function currentState() {
      if (cachedState) return cachedState;
      const matched = new Set();
      if (searchTerm) {
        nodes.forEach((node, index) => {
          if ((node.label + " " + node.title).toLowerCase().includes(searchTerm)) matched.add(index);
        });
      }

      const searchContext = new Set(matched);
      if (matched.size) {
        links.forEach(link => {
          if (matched.has(link.source)) searchContext.add(link.target);
          if (matched.has(link.target)) searchContext.add(link.source);
        });
      }
      const overlayNodeIndexes = new Set(
        Array.from(traceOverlayNodes.keys())
          .map(key => nodeIndexByKey.get(key))
          .filter(index => index !== undefined)
      );
      const visibleNode = index => (
        ((nodeRank.get(index) ?? nodes.length) < nodeLimit || searchContext.has(index) || overlayNodeIndexes.has(index))
        && (showChunks || nodes[index].type !== "chunk" || overlayNodeIndexes.has(index))
      );
      const selectedNeighbors = new Set();
      if (selectedNodeId !== null) {
        selectedNeighbors.add(selectedNodeId);
        links.forEach(link => {
          if (link.source === selectedNodeId) selectedNeighbors.add(link.target);
          if (link.target === selectedNodeId) selectedNeighbors.add(link.source);
        });
      }

      const eligibleEdges = edgeOrder.filter(index => {
        const link = links[index];
        return visibleNode(link.source) && visibleNode(link.target);
      });
      const edgeLimit = Math.max(1, Math.ceil(eligibleEdges.length * edgeDensity / 100));
      const visibleEdges = new Set();
      eligibleEdges.forEach((index, eligibleRank) => {
        const link = links[index];
        let visible = eligibleRank < edgeLimit;
        if (selectedNodeId !== null) {
          visible = link.source === selectedNodeId || link.target === selectedNodeId;
        }
        if (searchTerm) {
          visible = matched.has(link.source) || matched.has(link.target);
        }
        if (overlayEdgeFor(link)) visible = true;
        if (visible) visibleEdges.add(index);
      });
      cachedState = { matched, searchContext, visibleNode, selectedNeighbors, visibleEdges, overlayNodeIndexes };
      return cachedState;
    }

    function render() {
      const projected = nodes.map(project);
      const state = currentState();
      let shownNodes = 0;

      const shownByCommunity = new Array(communities.length).fill(0);
      nodes.forEach((node, index) => {
        if (state.visibleNode(index)) shownByCommunity[node.community] += 1;
      });
      communities.forEach((community, index) => {
        const point = project(community);
        const group = communityEls[index];
        const shown = shownByCommunity[index];
        group.style.display = shown && showCommunities ? "block" : "none";
        if (!shown || !showCommunities) return;
        const occupancyScale = Math.max(.35, Math.cbrt(shown / community.size));
        const radius = community.radius * occupancyScale * camera.zoom * point.scale;
        group.setAttribute("transform", `translate(${point.x},${point.y})`);
        group.querySelector("circle").setAttribute("r", radius);
        group.querySelector("text").textContent = `${community.label} · ${shown}/${community.size} 节点`;
        group.querySelector("text").setAttribute("y", -radius - 8);
        group.style.opacity = Math.max(.35, Math.min(.9, .6 + point.z / 1200));
      });

      edgeContext.clearRect(0, 0, width, height);
      let drawnEdgeLabels = 0;
      state.visibleEdges.forEach(index => {
        const link = links[index];
        const a = projected[link.source], b = projected[link.target];
        const depth = Math.max(0, Math.min(1, ((a.z + b.z) / 2 + depthExtent) / (2 * depthExtent)));
        const focused = selectedNodeId !== null || searchTerm;
        const opacity = focused ? .72 : .07 + depth * .3;
        edgeContext.beginPath();
        edgeContext.setLineDash(link.is_chunk_edge ? [4, 4] : []);
        const traceEdge = overlayEdgeFor(link);
        edgeContext.lineWidth = Math.max(
          .35,
          Math.min(2, Math.sqrt(Math.max(link.weight, .01)) * .55)
        );
        edgeContext.strokeStyle = traceEdge ? "rgba(74,163,199,.9)" : `rgba(117,163,199,${opacity})`;
        edgeContext.moveTo(a.x, a.y);
        edgeContext.lineTo(b.x, b.y);
        edgeContext.stroke();

        if (showEdgeLabels && link.label && drawnEdgeLabels < 120) {
          const x = (a.x + b.x) / 2;
          const y = (a.y + b.y) / 2;
          edgeContext.setLineDash([]);
          edgeContext.font = "9px Arial, Microsoft YaHei, sans-serif";
          edgeContext.lineWidth = 3;
          edgeContext.strokeStyle = "rgba(5,13,22,.9)";
          edgeContext.fillStyle = "rgba(184,220,244,.92)";
          edgeContext.strokeText(link.label, x, y);
          edgeContext.fillText(link.label, x, y);
          drawnEdgeLabels += 1;
        }
      });

      const depthOrder = nodes.map((node, index) => index)
        .sort((a, b) => projected[a].z - projected[b].z);
      depthOrder.forEach(index => nodeLayer.append(nodeEls[index]));

      nodes.forEach((node, index) => {
        const group = nodeEls[index];
        const visible = state.visibleNode(index);
        group.style.display = visible ? "block" : "none";
        if (!visible) return;
        shownNodes += 1;
        const point = projected[index];
        const depth = Math.max(0, Math.min(1, (point.z + depthExtent) / (2 * depthExtent)));
        const radius = node.size * nodeScale * Math.max(.65, Math.min(2.1, point.scale * Math.sqrt(camera.zoom)));
        group.setAttribute("transform", `translate(${point.x},${point.y})`);
        group.style.opacity = 0.42 + depth * 0.58;
        const circle = group.querySelector("circle");
        const text = group.querySelector("text");
        circle.setAttribute("r", radius);
        text.setAttribute("x", radius + 4);
        const important = (labelRank.get(index) ?? nodes.length) < labelLimit;
        const active = index === selectedNodeId || state.selectedNeighbors.has(index) || state.matched.has(index);
        text.style.display = showNodeLabels && (important || active) ? "block" : "none";
        group.classList.toggle("selected", index === selectedNodeId);
        group.classList.toggle("match", state.matched.has(index));
        const traceNode = traceOverlayNodes.get(node.name);
        const roles = traceNode && traceNode.roles || [];
        group.classList.toggle("trace-seed", roles.includes("ppr_seed"));
        group.classList.toggle("trace-recall", roles.includes("ppr_recall") || roles.includes("graph_recall"));
        group.classList.toggle("trace-fusion", roles.includes("fusion_result"));
        group.classList.toggle("trace-evidence", roles.includes("final_evidence"));
        const dimBySelection = selectedNodeId !== null && !state.selectedNeighbors.has(index);
        const dimBySearch = Boolean(searchTerm) && !state.searchContext.has(index);
        const dimByTrace = traceOverlayNodes.size > 0 && selectedNodeId === null && !state.overlayNodeIndexes.has(index);
        group.classList.toggle("dim", dimBySelection || dimBySearch || dimByTrace);
      });

      const selectedNode = selectedNodeId === null ? null : nodes[selectedNodeId];
      const shownCommunityCount = shownByCommunity.filter(Boolean).length;
      selection.textContent = selectedNode ? `已聚焦：${selectedNode.label}（连接度 ${selectedNode.degree}）` : "";
      const traceLabel = traceOverlayNodes.size ? `，${traceOverlayNodes.size} 个轨迹节点` : "";
      stats.textContent = `显示 ${shownNodes} / ${payload.stats.total_nodes} 个节点，${shownCommunityCount} / ${communities.length} 个结构社区，当前 ${state.visibleEdges.size} / ${payload.stats.visible_edges} 条边${traceLabel}`;
      document.getElementById("hudNodes").textContent = shownNodes.toLocaleString();
      document.getElementById("hudEdges").textContent = state.visibleEdges.size.toLocaleString();
      document.getElementById("hudCommunities").textContent = shownCommunityCount;
    }

    svg.addEventListener("wheel", event => {
      event.preventDefault();
      camera.zoom = Math.max(.3, Math.min(4, camera.zoom * (event.deltaY < 0 ? 1.1 : .9)));
      render();
    }, { passive: false });
    svg.addEventListener("contextmenu", event => event.preventDefault());
    svg.addEventListener("mousedown", event => {
      if (event.target.closest && event.target.closest(".node")) return;
      viewDrag = {
        mode: event.shiftKey || event.button === 2 ? "pan" : "rotate",
        clientX: event.clientX,
        clientY: event.clientY,
        rotX: camera.rotX,
        rotY: camera.rotY,
        panX: camera.panX,
        panY: camera.panY,
      };
      svg.classList.add("rotating");
    });
    window.addEventListener("mousemove", event => {
      if (draggingNode && dragState) {
        const dxScreen = event.clientX - dragState.clientX;
        const dyScreen = event.clientY - dragState.clientY;
        lastDragMoved = Math.hypot(dxScreen, dyScreen) > 3;
        const point = project(draggingNode);
        const dx = dxScreen / Math.max(.1, camera.zoom * point.scale);
        const dy = dyScreen / Math.max(.1, camera.zoom * point.scale);
        const cy = Math.cos(camera.rotY), sy = Math.sin(camera.rotY);
        const cx = Math.cos(camera.rotX), sx = Math.sin(camera.rotX);
        draggingNode.x = dragState.x + cy * dx + sx * sy * dy;
        draggingNode.y = dragState.y + cx * dy;
        draggingNode.z = dragState.z + sy * dx - sx * cy * dy;
        render();
      } else if (viewDrag) {
        const dx = event.clientX - viewDrag.clientX;
        const dy = event.clientY - viewDrag.clientY;
        if (viewDrag.mode === "rotate") {
          camera.rotY = viewDrag.rotY + dx * .006;
          camera.rotX = Math.max(-1.45, Math.min(1.45, viewDrag.rotX + dy * .006));
        } else {
          camera.panX = viewDrag.panX + dx;
          camera.panY = viewDrag.panY + dy;
        }
        render();
      }
    });
    window.addEventListener("mouseup", () => {
      draggingNode = null;
      dragState = null;
      viewDrag = null;
      svg.classList.remove("rotating");
    });
    window.addEventListener("resize", () => {
      width = window.innerWidth;
      height = window.innerHeight;
      resizeCanvas();
      render();
    });
    svg.addEventListener("click", event => {
      if (event.target === svg) {
        selectedNodeId = null;
        cachedState = null;
        render();
      }
    });

    document.getElementById("search").addEventListener("input", event => {
      searchTerm = event.target.value.trim().toLowerCase();
      cachedState = null;
      render();
    });
    document.getElementById("clear").addEventListener("click", () => {
      document.getElementById("search").value = "";
      searchTerm = "";
      selectedNodeId = null;
      cachedState = null;
      render();
    });
    nodeLimitInput.addEventListener("input", event => {
      nodeLimit = Number(event.target.value);
      document.getElementById("nodeLimitValue").textContent = nodeLimit;
      if (selectedNodeId !== null && (nodeRank.get(selectedNodeId) ?? nodes.length) >= nodeLimit) {
        selectedNodeId = null;
      }
      cachedState = null;
      render();
    });
    document.getElementById("edgeDensity").addEventListener("input", event => {
      edgeDensity = Number(event.target.value);
      document.getElementById("edgeDensityValue").textContent = `${edgeDensity}%`;
      cachedState = null;
      render();
    });
    document.getElementById("labelLimit").addEventListener("input", event => {
      labelLimit = Number(event.target.value);
      document.getElementById("labelLimitValue").textContent = labelLimit;
      render();
    });
    document.getElementById("nodeScale").addEventListener("input", event => {
      nodeScale = Number(event.target.value) / 100;
      document.getElementById("nodeScaleValue").textContent = `${event.target.value}%`;
      render();
    });
    document.getElementById("toggleLabels").addEventListener("change", event => {
      showNodeLabels = event.target.checked;
      render();
    });
    document.getElementById("toggleEdgeLabels").addEventListener("change", event => {
      showEdgeLabels = event.target.checked;
      render();
    });
    document.getElementById("toggleChunks").addEventListener("change", event => {
      showChunks = event.target.checked;
      if (!showChunks && selectedNodeId !== null && nodes[selectedNodeId].type === "chunk") selectedNodeId = null;
      cachedState = null;
      render();
    });
    document.getElementById("toggleCommunities").addEventListener("change", event => {
      showCommunities = event.target.checked;
      render();
    });
    document.getElementById("resetView").addEventListener("click", () => {
      nodes.forEach((node, index) => Object.assign(node, initialPositions[index]));
      Object.assign(camera, { rotX: -.28, rotY: .58, zoom: .62, panX: 140, panY: 0 });
      selectedNodeId = null;
      cachedState = null;
      render();
    });
    document.getElementById("frontView").addEventListener("click", () => {
      Object.assign(camera, { rotX: 0, rotY: 0, zoom: .62, panX: 140, panY: 0 });
      render();
    });
    document.getElementById("fullscreen").addEventListener("click", () => {
      if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen?.();
      } else {
        document.exitFullscreen?.();
      }
    });
    document.getElementById("collapsePanel").addEventListener("click", event => {
      const collapsed = document.getElementById("toolbar").classList.toggle("collapsed");
      event.currentTarget.textContent = collapsed ? "+" : "−";
      event.currentTarget.title = collapsed ? "展开控制面板" : "折叠控制面板";
    });
    window.addEventListener("keydown", event => {
      if (event.key === "Escape") {
        selectedNodeId = null;
        cachedState = null;
        render();
      }
    });

    window.addEventListener("message", event => {
      if (event.source !== window.parent || event.origin !== window.location.origin || !event.data) return;
      if (event.data.type === "synapserag:apply-overlay") {
        const overlay = event.data.overlay || {};
        traceOverlayNodes = new Map((overlay.nodes || []).map(node => [node.node_key, node]));
        traceOverlayEdges = new Map((overlay.edges || []).map(edge => [graphEdgeKey(edge.source_key, edge.target_key), edge]));
        selectedNodeId = null;
        cachedState = null;
        render();
      }
      if (event.data.type === "synapserag:clear-overlay") {
        traceOverlayNodes = new Map();
        traceOverlayEdges = new Map();
        selectedNodeId = null;
        cachedState = null;
        render();
      }
     if (event.data.type === "synapserag:focus-node") {
       const index = nodeIndexByKey.get(event.data.nodeKey);
       if (index === undefined) return;
       selectedNodeId = index;
        const point = project(nodes[index]);
        camera.panX += width / 2 - point.x;
        camera.panY += height / 2 - point.y;
       cachedState = null;
        render();
        emitNodeSelection(nodes[index]);
      }
    });

    render();
    if (window.parent !== window) {
      window.parent.postMessage({type:"synapserag:ready"}, window.location.origin);
    }
  </script>
</body>
</html>
"""
    return template.replace("__TITLE__", safe_title).replace("__PAYLOAD__", data)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert SynapseRAG graph.pickle to graph.html")
    parser.add_argument("graph_pickle", type=Path, help="Path to graph.pickle")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Output HTML path")
    parser.add_argument("--openie-json", type=Path, default=None, help="Optional openie_results_*.json for relation labels")
    parser.add_argument("--max-nodes", type=int, default=0, help="Limit nodes embedded in HTML; default 0 loads all")
    parser.add_argument("--min-edge-weight", type=float, default=0.0, help="Hide edges below this weight")
    parser.add_argument("--focus", action="append", default=[], help="Keyword/entity to focus on; can be repeated")
    parser.add_argument("--focus-hops", type=int, default=2, help="Neighbor hops to include around focus matches")
    parser.add_argument("--no-chunks", action="store_true", help="Hide chunk/document nodes")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    graph = ig.Graph.Read_Pickle(str(args.graph_pickle))
    output = args.output or args.graph_pickle.with_name("graph.html")

    relation_labels = load_relation_labels(args.openie_json)
    selected = select_vertices(
        graph=graph,
        max_nodes=args.max_nodes,
        focus_terms=args.focus,
        focus_hops=args.focus_hops,
        include_chunks=not args.no_chunks,
    )
    payload = build_payload(
        graph=graph,
        selected=selected,
        min_edge_weight=args.min_edge_weight,
        relation_labels=relation_labels,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_html(payload, title=args.graph_pickle.name), encoding="utf-8")
    print(f"Wrote {output}")
    print(
        "Visible graph: "
        f"{payload['stats']['visible_nodes']} nodes, {payload['stats']['visible_edges']} edges "
        f"(original: {payload['stats']['total_nodes']} nodes, {payload['stats']['total_edges']} edges)"
    )


if __name__ == "__main__":
    main()


"""
conda activate synapserag

python scripts/analysis/graph_pickle_to_html.py \
  outputs/openai_test/Qwen_Qwen2.5-7B-Instruct_BAAI_bge-m3/graph.pickle \
  --openie-json outputs/openai_test/openie_results_ner_Qwen_Qwen2.5-7B-Instruct.json \
  --max-nodes 300
"""
