from geometry_utils import bbox_overlap, component_bbox, extract_pose, hole_bbox, keepout_bbox
from placement_constraints import component_shape
from shapely.geometry import Point, box as polygon_box


def detect_issues(placement: dict, sizes: dict, board_w: float, board_h: float,
                  holes: list, keepouts: list, edge_clearance: float = 0.5,
                  comp_clearance: float = 0.25,
                  edge_keepout_margin: float = 0.0,
                  sides: dict = None, tht_refs: set = None,
                  footprint_metadata: dict = None):
    """
    Scans the provided placement configuration and returns a list of problematic components (ref)
    that violate hard constraints (overlap, board boundary, keepout zones, hole zones).

    Side-aware mode (sides is not None): two SMD parts on opposite sides may
    legally overlap in XY, so the pair is not an issue. Pairs involving a
    through-hole part are still issues — THT pins pass through the board.
    """
    bad_components = set()
    refs = list(placement.keys())
    tht_refs = tht_refs or set()

    effective_edge_clearance = float(edge_clearance) + max(float(edge_keepout_margin or 0.0), 0.0)

    def exact_shape(ref, clearance=0.0):
        if not footprint_metadata or not (footprint_metadata.get(ref) or {}).get("geometry"):
            return None
        x, y, rotation = extract_pose(placement[ref])
        pose = {"x": x, "y": y, "rotation": rotation,
                "side": (sides or {}).get(ref, "top")}
        shape = component_shape(ref, pose, footprint_metadata, sizes)
        return shape.buffer(max(float(clearance), 0.0), join_style="mitre")

    for i in range(len(refs)):
        for j in range(i + 1, len(refs)):
            r1, r2 = refs[i], refs[j]
            if sides is not None and r1 not in tht_refs and r2 not in tht_refs:
                if sides.get(r1, "top") != sides.get(r2, "top"):
                    continue
            shape1 = exact_shape(r1, comp_clearance / 2.0)
            shape2 = exact_shape(r2, comp_clearance / 2.0)
            if shape1 is not None and shape2 is not None:
                if shape1.intersection(shape2).area > 1e-12:
                    bad_components.add(r1)
                    bad_components.add(r2)
                continue
            x1, y1, rot1 = extract_pose(placement[r1])
            x2, y2, rot2 = extract_pose(placement[r2])
            box1 = component_bbox(
                x1, y1, sizes.get(r1, (3.0, 3.0)),
                rotation=rot1, clearance=comp_clearance / 2.0
            )
            box2 = component_bbox(
                x2, y2, sizes.get(r2, (3.0, 3.0)),
                rotation=rot2, clearance=comp_clearance / 2.0
            )
            overlap_x, overlap_y = bbox_overlap(box1, box2)

            if overlap_x > 0 and overlap_y > 0:
                bad_components.add(r1)
                bad_components.add(r2)

    for ref, pose in placement.items():
        shape = exact_shape(ref)
        if shape is not None:
            left, bottom, right, top = shape.bounds
        else:
            x, y, rot = extract_pose(pose)
            left, bottom, right, top = component_bbox(x, y, sizes.get(ref, (3.0, 3.0)), rotation=rot)

        if left < effective_edge_clearance or right > board_w - effective_edge_clearance or \
           bottom < effective_edge_clearance or top > board_h - effective_edge_clearance:
            bad_components.add(ref)

    hole_clearance = max(comp_clearance, 1.0)
    for ref, pose in placement.items():
        exact = exact_shape(ref)
        if exact is None:
            x, y, rot = extract_pose(pose)
            component_box = component_bbox(x, y, sizes.get(ref, (3.0, 3.0)), rotation=rot)

        for hole in holes:
            if exact is not None:
                radius = float(hole.get("radius", float(hole.get("diameter", 0.0)) / 2.0))
                obstacle = Point(float(hole.get("x", 0.0)), float(hole.get("y", 0.0))).buffer(
                    radius + hole_clearance
                )
                if exact.intersection(obstacle).area > 1e-12:
                    bad_components.add(ref)
            else:
                overlap_x, overlap_y = bbox_overlap(component_box, hole_bbox(hole, clearance=hole_clearance))
                if overlap_x > 0 and overlap_y > 0:
                    bad_components.add(ref)

        for ko in keepouts:
            ko_box = keepout_bbox(ko)
            if ko_box is None:
                continue
            if exact is not None:
                if exact.intersection(polygon_box(*ko_box)).area > 1e-12:
                    bad_components.add(ref)
            else:
                overlap_x, overlap_y = bbox_overlap(component_box, ko_box)
                if overlap_x > 0 and overlap_y > 0:
                    bad_components.add(ref)

    return list(bad_components)
