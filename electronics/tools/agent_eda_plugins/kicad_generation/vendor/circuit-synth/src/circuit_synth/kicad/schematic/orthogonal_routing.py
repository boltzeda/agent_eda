"""Deterministic obstacle-aware orthogonal routing for generated schematics.

The router is deliberately independent of KiCad's object model.  It consumes pin
endpoints and symbol-body rectangles and returns polylines that the schematic
writer can serialize as wires.  Multi-terminal nets are built incrementally as
rectilinear trees rather than as point-to-point daisy chains.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from itertools import count
from typing import Iterable, Sequence

_EPSILON = 1e-6


@dataclass(frozen=True, order=True)
class RoutePoint:
    """A point in schematic millimetres."""

    x: float
    y: float


@dataclass(frozen=True)
class RouteEndpoint:
    """A component pin and the direction in which its wire should leave."""

    point: RoutePoint
    owner: str
    direction: str


@dataclass(frozen=True)
class RoutingObstacle:
    """An axis-aligned symbol-body rectangle."""

    left: float
    top: float
    right: float
    bottom: float
    owner: str

    def inflated(self, clearance: float) -> "RoutingObstacle":
        return RoutingObstacle(
            self.left - clearance,
            self.top - clearance,
            self.right + clearance,
            self.bottom + clearance,
            self.owner,
        )

    def contains(self, point: RoutePoint) -> bool:
        return (
            self.left - _EPSILON <= point.x <= self.right + _EPSILON
            and self.top - _EPSILON <= point.y <= self.bottom + _EPSILON
        )


@dataclass(frozen=True)
class RoutedPath:
    """One continuous KiCad wire polyline."""

    points: tuple[RoutePoint, ...]


@dataclass
class RoutedNet:
    """Routed geometry and quality measurements for one electrical net."""

    name: str
    paths: list[RoutedPath] = field(default_factory=list)
    junctions: set[RoutePoint] = field(default_factory=set)
    routed: bool = True
    failure: str | None = None
    length: float = 0.0
    bends: int = 0
    crossings: int = 0


@dataclass(frozen=True)
class RouterConfig:
    """Cost and geometry controls for :class:`OrthogonalNetRouter`."""

    grid_size: float = 1.27
    clearance: float = 1.27
    bend_penalty: float = 5.0
    crossing_penalty: float = 25.0
    max_expansions: int = 250_000


class OrthogonalNetRouter:
    """Route orthogonal net trees using deterministic bend-aware A*."""

    _DIRECTIONS = ((1, 0, "right"), (0, 1, "down"), (-1, 0, "left"), (0, -1, "up"))

    def __init__(
        self,
        obstacles: Sequence[RoutingObstacle],
        bounds: tuple[float, float, float, float],
        config: RouterConfig | None = None,
    ) -> None:
        self.config = config or RouterConfig()
        if self.config.grid_size <= 0:
            raise ValueError("routing grid_size must be positive")
        self.obstacles = [
            obstacle.inflated(self.config.clearance) for obstacle in obstacles
        ]
        self.bounds = bounds
        self._occupied_segments: list[tuple[RoutePoint, RoutePoint, str]] = []
        self._blocked_nodes = self._build_blocked_node_index()
        self._occupied_nodes: dict[tuple[int, int], set[str]] = {}
        self._occupied_edges: dict[
            tuple[tuple[int, int], tuple[int, int]], set[str]
        ] = {}
        self._crossable_nodes: dict[tuple[int, int], dict[str, set[str]]] = {}
        self._reserved_nodes: dict[tuple[int, int], set[str]] = {}

    def reserve_endpoints(
        self, net_name: str, endpoints: Sequence[RouteEndpoint]
    ) -> None:
        """Keep every pin escape available before any net consumes routing space."""

        for endpoint in endpoints:
            escape = self._escape_point(endpoint)
            if escape is not None:
                self._reserved_nodes.setdefault(self._to_node(escape), set()).add(
                    net_name
                )

    def reserve_points(self, net_name: str, points: Sequence[RoutePoint]) -> None:
        """Protect electrical attachment points materialized after routing."""

        for point in points:
            self._reserved_nodes.setdefault(self._to_node(point), set()).add(net_name)

    def route_net(self, name: str, endpoints: Sequence[RouteEndpoint]) -> RoutedNet:
        """Route all endpoints as one connected, acyclic orthogonal tree."""

        unique = list(dict.fromkeys(endpoints))
        result = RoutedNet(name=name)
        if len(unique) < 2:
            return result

        escapes: list[tuple[RouteEndpoint, RoutePoint]] = []
        for endpoint in unique:
            escape = self._escape_point(endpoint)
            if escape is None:
                return self._failed(
                    name, f"cannot escape pin owned by {endpoint.owner}"
                )
            if self._occupied_nodes.get(self._to_node(escape), set()) - {name}:
                return self._failed(
                    name, f"pin escape for {endpoint.owner} is occupied by another net"
                )
            escapes.append((endpoint, escape))

        # The first endpoint seeds the tree.  Remaining pins connect to the
        # closest existing tree vertex, producing branch points where useful.
        seed_endpoint, seed_escape = escapes.pop(0)
        paths: list[list[RoutePoint]] = []
        seed_stub = self._orthogonal_stub(seed_endpoint.point, seed_escape)
        if len(seed_stub) > 1:
            paths.append(seed_stub)
        tree_vertices: set[RoutePoint] = {seed_escape}

        while escapes:
            candidates = [
                (
                    index,
                    endpoint,
                    escape,
                    min(
                        tree_vertices,
                        key=lambda point: (
                            self._manhattan(escape, point),
                            point.x,
                            point.y,
                        ),
                    ),
                )
                for index, (endpoint, escape) in enumerate(escapes)
            ]
            candidate_index, endpoint, escape, target = min(
                candidates,
                key=lambda value: (
                    self._manhattan(value[2], value[3]),
                    value[1].owner,
                    value[1].point.x,
                    value[1].point.y,
                ),
            )
            del escapes[candidate_index]

            core = self._find_path(escape, target, name)
            if core is None:
                return self._failed(
                    name,
                    f"no obstacle-free route from {endpoint.owner} to existing tree",
                )
            stub = self._orthogonal_stub(endpoint.point, escape)
            if len(stub) > 1:
                paths.append(stub)
            if len(core) > 1:
                paths.append(core)
            # Only escaped/core vertices are legal branch targets.  Pin stubs
            # intentionally pass through their owner's inflated obstacle; using
            # one as a later A* goal would make that route impossible.
            tree_vertices.update(self._simplify(core))

        simplified = [self._simplify(path) for path in paths]
        simplified = [path for path in simplified if len(path) > 1]
        result.paths = [RoutedPath(tuple(path)) for path in simplified]
        result.junctions = self._branch_points(simplified, unique)
        result.length = sum(self._path_length(path) for path in simplified)
        result.bends = sum(max(0, len(path) - 2) for path in simplified)
        result.crossings = self._count_external_crossings(simplified, name)

        for path in simplified:
            for first, second in zip(path, path[1:]):
                self._occupied_segments.append((first, second, name))
                self._index_wire_segment(first, second, name)
        return result

    @staticmethod
    def _failed(name: str, reason: str) -> RoutedNet:
        return RoutedNet(name=name, routed=False, failure=reason)

    def _escape_point(self, endpoint: RouteEndpoint) -> RoutePoint | None:
        owner_obstacle = next(
            (
                obstacle
                for obstacle in self.obstacles
                if obstacle.owner == endpoint.owner
            ),
            None,
        )
        step = self.config.grid_size
        point = endpoint.point
        direction = endpoint.direction
        if owner_obstacle is None:
            return self._snap_point(point)
        if direction == "left":
            escape = RoutePoint(
                self._floor(owner_obstacle.left - step), self._snap(point.y)
            )
        elif direction == "right":
            escape = RoutePoint(
                self._ceil(owner_obstacle.right + step), self._snap(point.y)
            )
        elif direction == "up":
            escape = RoutePoint(
                self._snap(point.x), self._floor(owner_obstacle.top - step)
            )
        elif direction == "down":
            escape = RoutePoint(
                self._snap(point.x), self._ceil(owner_obstacle.bottom + step)
            )
        else:
            raise ValueError(f"unknown pin escape direction: {direction}")

        if any(
            obstacle.owner != endpoint.owner and obstacle.contains(escape)
            for obstacle in self.obstacles
        ):
            return None
        return escape

    def _orthogonal_stub(self, start: RoutePoint, end: RoutePoint) -> list[RoutePoint]:
        if self._same_point(start, end):
            return [start]
        if self._aligned(start, end):
            return [start, end]
        # Pin coordinates normally lie on the routing grid.  This guarded bend
        # handles imported symbols whose pin coordinate is on a finer grid.
        bend = RoutePoint(end.x, start.y)
        return [start, bend, end]

    def _find_path(
        self, start: RoutePoint, goal: RoutePoint, net_name: str
    ) -> list[RoutePoint] | None:
        if self._same_point(start, goal):
            return [start]
        for direct in self._simple_paths(start, goal):
            if self._path_clear(direct) and self._path_avoids_other_wires(
                direct, net_name
            ):
                return direct

        grid = self.config.grid_size
        start_node = self._to_node(start)
        goal_node = self._to_node(goal)
        bounds = self._node_bounds()
        queue: list[tuple[float, float, int, tuple[int, int], str | None]] = []
        serial = count()
        start_state = (start_node, None)
        heapq.heappush(
            queue,
            (
                self._node_distance(start_node, goal_node),
                0.0,
                next(serial),
                start_node,
                None,
            ),
        )
        costs = {start_state: 0.0}
        previous: dict[
            tuple[tuple[int, int], str | None],
            tuple[tuple[int, int], str | None] | None,
        ] = {start_state: None}
        expansions = 0

        while queue and expansions < self.config.max_expansions:
            _estimate, cost, _serial, node, incoming = heapq.heappop(queue)
            state = (node, incoming)
            if cost > costs.get(state, math.inf) + _EPSILON:
                continue
            if node == goal_node:
                return self._reconstruct(previous, state)
            expansions += 1
            for dx, dy, direction in self._ordered_directions(incoming):
                neighbour = (node[0] + dx, node[1] + dy)
                if not self._inside_node_bounds(neighbour, bounds):
                    continue
                first = self._from_node(node)
                second = self._from_node(neighbour)
                if self._blocked(second):
                    continue
                if (
                    incoming is not None
                    and self._occupied_nodes.get(node, set()) - {net_name}
                    and direction != incoming
                ):
                    # A crossing must leave in the direction it entered; turning
                    # on an existing wire would create an electrical join.
                    continue
                if self._wire_conflict(first, second, net_name):
                    continue
                bend = incoming is not None and incoming != direction
                next_cost = cost + grid
                if bend:
                    next_cost += self.config.bend_penalty
                next_cost += self._wire_penalty(first, second, net_name)
                next_state = (neighbour, direction)
                if next_cost + _EPSILON >= costs.get(next_state, math.inf):
                    continue
                costs[next_state] = next_cost
                previous[next_state] = state
                estimate = next_cost + self._node_distance(neighbour, goal_node) * grid
                heapq.heappush(
                    queue,
                    (estimate, next_cost, next(serial), neighbour, direction),
                )
        return None

    def _simple_paths(
        self, start: RoutePoint, goal: RoutePoint
    ) -> Iterable[list[RoutePoint]]:
        if self._aligned(start, goal):
            yield [start, goal]
            return
        horizontal_first = [start, RoutePoint(goal.x, start.y), goal]
        vertical_first = [start, RoutePoint(start.x, goal.y), goal]
        yield min(
            (horizontal_first, vertical_first),
            key=lambda path: (self._path_wire_penalty(path), tuple(path)),
        )
        yield max(
            (horizontal_first, vertical_first),
            key=lambda path: (self._path_wire_penalty(path), tuple(path)),
        )

    def _path_wire_penalty(self, path: Sequence[RoutePoint]) -> float:
        return sum(
            self._wire_penalty(first, second, "")
            for first, second in zip(path, path[1:])
        )

    def _path_clear(self, path: Sequence[RoutePoint]) -> bool:
        return all(
            self._segment_clear(first, second) for first, second in zip(path, path[1:])
        )

    def _path_avoids_other_wires(
        self, path: Sequence[RoutePoint], net_name: str
    ) -> bool:
        for first, second in zip(path, path[1:]):
            first_node = self._to_node(first)
            second_node = self._to_node(second)
            dx = (
                0
                if first_node[0] == second_node[0]
                else (1 if second_node[0] > first_node[0] else -1)
            )
            dy = (
                0
                if first_node[1] == second_node[1]
                else (1 if second_node[1] > first_node[1] else -1)
            )
            node = first_node
            while node != second_node:
                following = (node[0] + dx, node[1] + dy)
                edge = tuple(sorted((node, following)))
                if self._occupied_edges.get(edge, set()) - {net_name}:
                    return False
                other_nets = self._occupied_nodes.get(following, set()) - {net_name}
                if self._reserved_nodes.get(following, set()) - {net_name}:
                    return False
                if other_nets:
                    orientation = "horizontal" if dy == 0 else "vertical"
                    # A polyline bend/end cannot sit on another net.  Interior
                    # points may cross a straight perpendicular segment.
                    if following == second_node or not self._can_cross_node(
                        following, orientation, other_nets
                    ):
                        return False
                node = following
        return True

    def _blocked(self, point: RoutePoint) -> bool:
        return self._to_node(point) in self._blocked_nodes

    def _segment_clear(self, first: RoutePoint, second: RoutePoint) -> bool:
        return not any(
            self.segment_intersects_obstacle(first, second, obstacle)
            for obstacle in self.obstacles
        )

    @staticmethod
    def segment_intersects_obstacle(
        first: RoutePoint, second: RoutePoint, obstacle: RoutingObstacle
    ) -> bool:
        """Return whether an orthogonal segment touches an obstacle interior."""

        if abs(first.y - second.y) <= _EPSILON:
            y = first.y
            return (
                obstacle.top - _EPSILON <= y <= obstacle.bottom + _EPSILON
                and max(min(first.x, second.x), obstacle.left)
                <= min(max(first.x, second.x), obstacle.right) + _EPSILON
            )
        if abs(first.x - second.x) <= _EPSILON:
            x = first.x
            return (
                obstacle.left - _EPSILON <= x <= obstacle.right + _EPSILON
                and max(min(first.y, second.y), obstacle.top)
                <= min(max(first.y, second.y), obstacle.bottom) + _EPSILON
            )
        return True

    def _wire_penalty(
        self, first: RoutePoint, second: RoutePoint, net_name: str
    ) -> float:
        first_node = self._to_node(first)
        second_node = self._to_node(second)
        edge = tuple(sorted((first_node, second_node)))
        edge_nets = self._occupied_edges.get(edge, set()) - {net_name}
        if edge_nets:
            return self.config.crossing_penalty * 20.0 * len(edge_nets)
        point_nets = self._occupied_nodes.get(second_node, set()) - {net_name}
        return self.config.crossing_penalty * len(point_nets)

    def _wire_conflict(
        self, first: RoutePoint, second: RoutePoint, net_name: str
    ) -> bool:
        first_node = self._to_node(first)
        second_node = self._to_node(second)
        edge = tuple(sorted((first_node, second_node)))
        if self._reserved_nodes.get(second_node, set()) - {net_name}:
            return True
        if self._occupied_edges.get(edge, set()) - {net_name}:
            return True
        other_nets = self._occupied_nodes.get(second_node, set()) - {net_name}
        if not other_nets:
            return False
        orientation = "horizontal" if first_node[1] == second_node[1] else "vertical"
        return not self._can_cross_node(second_node, orientation, other_nets)

    def _can_cross_node(
        self,
        node: tuple[int, int],
        orientation: str,
        other_nets: set[str],
    ) -> bool:
        expected = "vertical" if orientation == "horizontal" else "horizontal"
        crossable = self._crossable_nodes.get(node, {})
        return all(
            expected in crossable.get(net_name, set()) for net_name in other_nets
        )

    def _build_blocked_node_index(self) -> set[tuple[int, int]]:
        """Rasterize small symbol rectangles once instead of per A* expansion."""

        blocked = set()
        grid = self.config.grid_size
        for obstacle in self.obstacles:
            min_x = math.ceil((obstacle.left - _EPSILON) / grid)
            max_x = math.floor((obstacle.right + _EPSILON) / grid)
            min_y = math.ceil((obstacle.top - _EPSILON) / grid)
            max_y = math.floor((obstacle.bottom + _EPSILON) / grid)
            for x in range(min_x, max_x + 1):
                for y in range(min_y, max_y + 1):
                    blocked.add((x, y))
        return blocked

    def _index_wire_segment(
        self, first: RoutePoint, second: RoutePoint, net_name: str
    ) -> None:
        first_node = self._to_node(first)
        second_node = self._to_node(second)
        dx = (
            0
            if first_node[0] == second_node[0]
            else (1 if second_node[0] > first_node[0] else -1)
        )
        dy = (
            0
            if first_node[1] == second_node[1]
            else (1 if second_node[1] > first_node[1] else -1)
        )
        node = first_node
        orientation = "horizontal" if dy == 0 else "vertical"
        segment_nodes = [node]
        self._occupied_nodes.setdefault(node, set()).add(net_name)
        while node != second_node:
            following = (node[0] + dx, node[1] + dy)
            edge = tuple(sorted((node, following)))
            self._occupied_edges.setdefault(edge, set()).add(net_name)
            self._occupied_nodes.setdefault(following, set()).add(net_name)
            node = following
            segment_nodes.append(node)
        for interior in segment_nodes[1:-1]:
            by_net = self._crossable_nodes.setdefault(interior, {})
            by_net.setdefault(net_name, set()).add(orientation)

    def _count_external_crossings(
        self, paths: Sequence[Sequence[RoutePoint]], net_name: str
    ) -> int:
        crossings = 0
        for path in paths:
            for first, second in zip(path, path[1:]):
                for other_first, other_second, other_net in self._occupied_segments:
                    if (
                        other_net != net_name
                        and self._segment_relation(
                            first, second, other_first, other_second
                        )
                        == "cross"
                    ):
                        crossings += 1
        return crossings

    @staticmethod
    def _segment_relation(
        a: RoutePoint, b: RoutePoint, c: RoutePoint, d: RoutePoint
    ) -> str | None:
        a_horizontal = abs(a.y - b.y) <= _EPSILON
        c_horizontal = abs(c.y - d.y) <= _EPSILON
        if a_horizontal == c_horizontal:
            if a_horizontal and abs(a.y - c.y) <= _EPSILON:
                overlap = min(max(a.x, b.x), max(c.x, d.x)) - max(
                    min(a.x, b.x), min(c.x, d.x)
                )
                return "overlap" if overlap > _EPSILON else None
            if not a_horizontal and abs(a.x - c.x) <= _EPSILON:
                overlap = min(max(a.y, b.y), max(c.y, d.y)) - max(
                    min(a.y, b.y), min(c.y, d.y)
                )
                return "overlap" if overlap > _EPSILON else None
            return None
        horizontal_a, horizontal_b = (a, b) if a_horizontal else (c, d)
        vertical_a, vertical_b = (c, d) if a_horizontal else (a, b)
        x = vertical_a.x
        y = horizontal_a.y
        if (
            min(horizontal_a.x, horizontal_b.x) + _EPSILON
            < x
            < max(horizontal_a.x, horizontal_b.x) - _EPSILON
            and min(vertical_a.y, vertical_b.y) + _EPSILON
            < y
            < max(vertical_a.y, vertical_b.y) - _EPSILON
        ):
            return "cross"
        return None

    @staticmethod
    def _branch_points(
        paths: Sequence[Sequence[RoutePoint]], endpoints: Sequence[RouteEndpoint]
    ) -> set[RoutePoint]:
        degree: dict[RoutePoint, int] = {}
        pin_points = {endpoint.point for endpoint in endpoints}
        for path in paths:
            for first, second in zip(path, path[1:]):
                degree[first] = degree.get(first, 0) + 1
                degree[second] = degree.get(second, 0) + 1
        return {
            point
            for point, value in degree.items()
            if value >= 3 and point not in pin_points
        }

    def _reconstruct(self, previous, state) -> list[RoutePoint]:
        nodes = []
        while state is not None:
            nodes.append(state[0])
            state = previous[state]
        nodes.reverse()
        return self._simplify([self._from_node(node) for node in nodes])

    @staticmethod
    def _simplify(points: Sequence[RoutePoint]) -> list[RoutePoint]:
        deduplicated: list[RoutePoint] = []
        for point in points:
            if not deduplicated or point != deduplicated[-1]:
                deduplicated.append(point)
        if len(deduplicated) < 3:
            return deduplicated
        result = [deduplicated[0]]
        for index, point in enumerate(deduplicated[1:-1], start=1):
            previous = result[-1]
            following = deduplicated[index + 1]
            if not (
                abs(previous.x - point.x) <= _EPSILON
                and abs(point.x - following.x) <= _EPSILON
            ) and not (
                abs(previous.y - point.y) <= _EPSILON
                and abs(point.y - following.y) <= _EPSILON
            ):
                result.append(point)
        result.append(deduplicated[-1])
        return result

    def _ordered_directions(self, incoming: str | None):
        if incoming is None:
            return self._DIRECTIONS
        return tuple(sorted(self._DIRECTIONS, key=lambda value: value[2] != incoming))

    def _node_bounds(self) -> tuple[int, int, int, int]:
        left, top, right, bottom = self.bounds
        return (
            math.ceil(left / self.config.grid_size),
            math.ceil(top / self.config.grid_size),
            math.floor(right / self.config.grid_size),
            math.floor(bottom / self.config.grid_size),
        )

    @staticmethod
    def _inside_node_bounds(node, bounds) -> bool:
        return bounds[0] <= node[0] <= bounds[2] and bounds[1] <= node[1] <= bounds[3]

    def _to_node(self, point: RoutePoint) -> tuple[int, int]:
        return (
            round(point.x / self.config.grid_size),
            round(point.y / self.config.grid_size),
        )

    def _from_node(self, node: tuple[int, int]) -> RoutePoint:
        return RoutePoint(
            node[0] * self.config.grid_size, node[1] * self.config.grid_size
        )

    @staticmethod
    def _node_distance(first: tuple[int, int], second: tuple[int, int]) -> int:
        return abs(first[0] - second[0]) + abs(first[1] - second[1])

    @staticmethod
    def _manhattan(first: RoutePoint, second: RoutePoint) -> float:
        return abs(first.x - second.x) + abs(first.y - second.y)

    @staticmethod
    def _path_length(path: Sequence[RoutePoint]) -> float:
        return sum(
            abs(first.x - second.x) + abs(first.y - second.y)
            for first, second in zip(path, path[1:])
        )

    @staticmethod
    def _same_point(first: RoutePoint, second: RoutePoint) -> bool:
        return (
            abs(first.x - second.x) <= _EPSILON and abs(first.y - second.y) <= _EPSILON
        )

    @staticmethod
    def _aligned(first: RoutePoint, second: RoutePoint) -> bool:
        return (
            abs(first.x - second.x) <= _EPSILON or abs(first.y - second.y) <= _EPSILON
        )

    def _snap(self, value: float) -> float:
        return round(value / self.config.grid_size) * self.config.grid_size

    def _floor(self, value: float) -> float:
        return math.floor(value / self.config.grid_size) * self.config.grid_size

    def _ceil(self, value: float) -> float:
        return math.ceil(value / self.config.grid_size) * self.config.grid_size

    def _snap_point(self, point: RoutePoint) -> RoutePoint:
        return RoutePoint(self._snap(point.x), self._snap(point.y))
