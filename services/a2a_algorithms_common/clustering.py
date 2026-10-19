"""Deterministic CPU implementations of K-Means and DBSCAN clustering."""
from __future__ import annotations

from collections import deque
import math
from typing import Iterable


Point = tuple[float, ...]


def _load_points(raw_points: object) -> list[Point]:
    if not isinstance(raw_points, list) or len(raw_points) < 2:
        raise ValueError("points must be an array containing at least two points")

    points: list[Point] = []
    dimension: int | None = None
    for point_index, raw_point in enumerate(raw_points):
        if not isinstance(raw_point, list) or not raw_point:
            raise ValueError(f"points[{point_index}] must be a non-empty numeric array")
        point: list[float] = []
        for value_index, value in enumerate(raw_point):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"points[{point_index}][{value_index}] must be a finite number"
                )
            numeric = float(value)
            if not math.isfinite(numeric):
                raise ValueError(
                    f"points[{point_index}][{value_index}] must be a finite number"
                )
            point.append(numeric)
        if dimension is None:
            dimension = len(point)
        elif len(point) != dimension:
            raise ValueError("all points must have the same dimension")
        points.append(tuple(point))
    return points


def _squared_distance(left: Point, right: Point) -> float:
    return sum((a - b) ** 2 for a, b in zip(left, right))


def _mean(points: Iterable[Point], dimension: int) -> Point:
    materialized = list(points)
    return tuple(
        sum(point[axis] for point in materialized) / len(materialized)
        for axis in range(dimension)
    )


def _round_number(value: float) -> float:
    rounded = round(float(value), 12)
    return 0.0 if rounded == -0.0 else rounded


def _round_point(point: Point) -> list[float]:
    return [_round_number(value) for value in point]


def _cluster_payload(
    points: list[Point], labels: list[int], cluster_count: int
) -> list[dict]:
    clusters: list[dict] = []
    for cluster_id in range(cluster_count):
        member_indices = [index for index, label in enumerate(labels) if label == cluster_id]
        members = [points[index] for index in member_indices]
        clusters.append(
            {
                "cluster_id": cluster_id,
                "size": len(member_indices),
                "centroid": _round_point(_mean(members, len(points[0]))),
                "member_indices": member_indices,
            }
        )
    return clusters


def _initial_centroids(points: list[Point], cluster_count: int) -> list[Point]:
    if len(set(points)) < cluster_count:
        raise ValueError("k cannot exceed the number of distinct points")

    # Deterministic farthest-first initialization. This avoids claiming a trained
    # artifact and makes golden cases stable without weakening the real algorithm.
    selected_indices = [0]
    while len(selected_indices) < cluster_count:
        next_index = max(
            (index for index in range(len(points)) if index not in selected_indices),
            key=lambda index: (
                min(
                    _squared_distance(points[index], points[selected])
                    for selected in selected_indices
                ),
                -index,
            ),
        )
        selected_indices.append(next_index)
    return [points[index] for index in selected_indices]


def kmeans(
    points: list[Point],
    *,
    cluster_count: int,
    max_iterations: int,
    tolerance: float,
) -> dict:
    if isinstance(cluster_count, bool) or not isinstance(cluster_count, int):
        raise ValueError("k must be an integer")
    if cluster_count < 1 or cluster_count > len(points):
        raise ValueError("k must be between 1 and the number of points")
    if isinstance(max_iterations, bool) or not isinstance(max_iterations, int):
        raise ValueError("max_iterations must be an integer")
    if max_iterations < 1 or max_iterations > 10000:
        raise ValueError("max_iterations must be between 1 and 10000")
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)):
        raise ValueError("tolerance must be a finite non-negative number")
    tolerance = float(tolerance)
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("tolerance must be a finite non-negative number")

    centroids = _initial_centroids(points, cluster_count)
    labels = [0] * len(points)
    converged = False
    iterations = 0
    tolerance_squared = tolerance**2

    for iterations in range(1, max_iterations + 1):
        labels = [
            min(
                range(cluster_count),
                key=lambda cluster_id: (
                    _squared_distance(point, centroids[cluster_id]),
                    cluster_id,
                ),
            )
            for point in points
        ]
        new_centroids: list[Point] = []
        for cluster_id in range(cluster_count):
            members = [
                point for point, label in zip(points, labels) if label == cluster_id
            ]
            # Retaining the previous centroid is the standard safe behavior for
            # an empty cluster and does not fabricate a point or result.
            new_centroids.append(
                _mean(members, len(points[0])) if members else centroids[cluster_id]
            )

        max_shift = max(
            _squared_distance(old, new)
            for old, new in zip(centroids, new_centroids)
        )
        centroids = new_centroids
        if max_shift <= tolerance_squared:
            converged = True
            break

    labels = [
        min(
            range(cluster_count),
            key=lambda cluster_id: (
                _squared_distance(point, centroids[cluster_id]),
                cluster_id,
            ),
        )
        for point in points
    ]
    inertia = sum(
        _squared_distance(point, centroids[label])
        for point, label in zip(points, labels)
    )
    return {
        "algorithm": "kmeans",
        "labels": labels,
        "clusters": _cluster_payload(points, labels, cluster_count),
        "cluster_count": cluster_count,
        "noise_indices": [],
        "inertia": _round_number(inertia),
        "iterations": iterations,
        "converged": converged,
    }


def dbscan(points: list[Point], *, eps: float, min_samples: int) -> dict:
    if isinstance(eps, bool) or not isinstance(eps, (int, float)):
        raise ValueError("eps must be a finite positive number")
    eps = float(eps)
    if not math.isfinite(eps) or eps <= 0:
        raise ValueError("eps must be a finite positive number")
    if isinstance(min_samples, bool) or not isinstance(min_samples, int):
        raise ValueError("min_samples must be an integer")
    if min_samples < 1:
        raise ValueError("min_samples must be at least 1")

    eps_squared = eps**2

    def neighbors(point_index: int) -> list[int]:
        return [
            candidate
            for candidate in range(len(points))
            if _squared_distance(points[point_index], points[candidate])
            <= eps_squared
        ]

    unassigned = -2
    noise = -1
    labels = [unassigned] * len(points)
    visited = [False] * len(points)
    cluster_id = 0

    for point_index in range(len(points)):
        if visited[point_index]:
            continue
        visited[point_index] = True
        seed_neighbors = neighbors(point_index)
        if len(seed_neighbors) < min_samples:
            labels[point_index] = noise
            continue

        labels[point_index] = cluster_id
        queue = deque(seed_neighbors)
        queued = set(seed_neighbors)
        while queue:
            candidate = queue.popleft()
            if not visited[candidate]:
                visited[candidate] = True
                candidate_neighbors = neighbors(candidate)
                if len(candidate_neighbors) >= min_samples:
                    for neighbor in candidate_neighbors:
                        if neighbor not in queued:
                            queue.append(neighbor)
                            queued.add(neighbor)
            if labels[candidate] in (unassigned, noise):
                labels[candidate] = cluster_id
        cluster_id += 1

    # Every unassigned point is non-density-reachable and therefore noise.
    labels = [noise if label == unassigned else label for label in labels]
    return {
        "algorithm": "dbscan",
        "labels": labels,
        "clusters": _cluster_payload(points, labels, cluster_id),
        "cluster_count": cluster_id,
        "noise_indices": [index for index, label in enumerate(labels) if label == noise],
        "inertia": None,
        "iterations": 1,
        "converged": True,
    }


def cluster_points(inputs: dict, params: dict) -> dict:
    points = _load_points(inputs.get("points"))
    algorithm = str(params.get("algorithm") or "kmeans").strip().lower()
    if algorithm == "kmeans":
        result = kmeans(
            points,
            cluster_count=params.get("k", 2),
            max_iterations=params.get("max_iterations", 100),
            tolerance=params.get("tolerance", 1e-6),
        )
    elif algorithm == "dbscan":
        result = dbscan(
            points,
            eps=params.get("eps", 0.5),
            min_samples=params.get("min_samples", 2),
        )
    else:
        raise ValueError("algorithm must be 'kmeans' or 'dbscan'")

    result["point_count"] = len(points)
    result["dimension"] = len(points[0])
    result["model_runtime"] = {
        "backend": "native_python",
        "used": True,
        "implementation_version": "1.0.0",
    }
    return result
