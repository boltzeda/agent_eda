import re
import math
from semantic_rules import *
from semantic_rules import ComponentClass, InterfaceType, PlacementType, BoardType, BoardArchetype, EdgeRole
from geometry_utils import bbox_from_center, bbox_overlap, hole_bbox, keepout_bbox
from pcb import PCB, Component

class SemanticPlacer:
    def __init__(self, pcb_design: PCB):
        self.pcb = pcb_design
        self.components = pcb_design.components
        self.board_type = BoardType.MIXED
        self.archetype = BoardArchetype.CONTROLLER
        self.board_outline = {"width": 100.0, "height": 100.0, "edges": {}}
        self.edge_model_active = False
        self.logs = []

    def log(self, message):
        print(f"[SemanticPlacer] {message}")
        self.logs.append(message)

    def run(self):
        """Execute the 9-stage workflow."""
        self.log("Starting Semantic Placement Workflow...")
        
        # 1. Semantic Component Classification
        self.stage_1_classification()
        
        # 2. Board Type & Archetype Detection
        self.stage_2_board_detection()
        
        # 3. Board Outline Synthesis
        self.stage_3_outline_synthesis()
        
        # 4. Edge Model Activation
        self.stage_4_edge_activation()
        
        if self.edge_model_active:
            # 5. Edge Role Assignment
            self.stage_5_edge_assignment()
            
            # 6. Fixing & Anchoring
            self.stage_6_fixing_anchoring()
            
            # 7. Deterministic Initial Placement
            self.stage_7_initial_placement()
        else:
            self.log("Edge model inactive. Skipping edge logic.")
            
        # 8 & 9 are implicit validation steps or handled by Cypress integration
        self.stage_9_validation()
        
        self.log("Workflow Complete.")
        return self.pcb

    # =================================================================
    # STAGE 1: Semantic Component Classification
    # =================================================================
    def stage_1_classification(self):
        self.log("Stage 1: Classification")
        for refdes, comp in self.components.items():
            # Strict Determinism: Reset existing flags
            comp.fixed = False
            
            # Default
            comp.semantic_class = ComponentClass.UNKNOWN
            comp.interface_type = InterfaceType.NONE
            comp.placement_type = PlacementType.FREE
            
            # Gather info
            fp_name = getattr(comp, 'footprint_name', '') or ''
            # RefDes is comp.name
            value = getattr(comp, 'value', '') or '' # Logic might need to extract value from somewhere if not on comp
            
            full_str = f"{refdes} {fp_name} {value}".upper()
            
            # Prioritized Matching
            matched = False
            for cls, iface, place_type, patterns in CLASSIFICATION_RULES:
                for pat in patterns:
                    if re.search(pat, full_str):
                        comp.semantic_class = cls
                        comp.interface_type = iface
                        comp.placement_type = place_type
                        matched = True
                        break
                if matched:
                    break
            
            if not matched:
                # Fallback for Passives if they look like R/C/L
                if refdes.startswith(('R', 'C', 'L', 'D')):
                    comp.semantic_class = ComponentClass.PASSIVE
                    
            # Set Fixed flag for Cypress
            if comp.placement_type == PlacementType.FIXED:
                comp.fixed = True
                
            self.log(f"  {refdes}: {comp.semantic_class.name}, {comp.placement_type.name} (Fixed={comp.fixed})")

    # =================================================================
    # STAGE 2: Board Type & Archetype Detection
    # =================================================================
    def stage_2_board_detection(self):
        self.log("Stage 2: Board Detection")
        counts = {cls: 0 for cls in ComponentClass}
        for comp in self.components.values():
            counts[comp.semantic_class] += 1
            
        # Board Type Logic
        if counts[ComponentClass.ANTENNA] > 0:
            self.board_type = BoardType.RF
        elif counts[ComponentClass.POWER_ENTRY] >= 2 or counts[ComponentClass.PROTECTION] >= 2:
            self.board_type = BoardType.POWER
        elif counts[ComponentClass.MEMORY] > 0 and counts[ComponentClass.MCU_FPGA] > 0:
            self.board_type = BoardType.HIGH_SPEED
        else:
            self.board_type = BoardType.MIXED
            
        # Archetype Logic (Simplified)
        if counts[ComponentClass.CONNECTOR] > 4:
            self.archetype = BoardArchetype.CARRIER
        elif counts[ComponentClass.MCU_FPGA] > 0:
            self.archetype = BoardArchetype.CONTROLLER
        else:
            self.archetype = BoardArchetype.MODULE
            
        self.log(f"Type: {self.board_type.name}, Archetype: {self.archetype.name}")

    # =================================================================
    # STAGE 3: Board Outline Synthesis
    # =================================================================
    def stage_3_outline_synthesis(self):
        self.log("Stage 3: Outline Synthesis")
        
        # --- FIX: Respect existing Edge.Cuts if they were found ---
        if self.pcb.x_range and self.pcb.x_range[0] != float("inf"):
            width = self.pcb.x_range[1] - self.pcb.x_range[0]
            height = self.pcb.y_range[1] - self.pcb.y_range[0]
            # Extra check: if width/height are reasonable, trust them
            if width > 0 and height > 0 and width < 1000 and height < 1000:
                self.board_outline["width"] = width
                self.board_outline["height"] = height
                self.log(f"Using EXISTING board boundary: {width:.2f} x {height:.2f}")
                return
        # --- END FIX ---

        total_area = 0.0
        for comp in self.components.values():
            total_area += comp.get_x_length() * comp.get_y_length()
            
        density = DENSITY_RULES.get(self.board_type, 0.45)
        board_area = total_area / density
        
        # Aspect Ratio
        connector_count = sum(1 for c in self.components.values() if c.semantic_class == ComponentClass.CONNECTOR)
        if connector_count >= 2:
            ar = 2.0 # 2:1
        elif self.board_type == BoardType.RF:
            ar = 3.0 # Long edge
        else:
            ar = 1.5
            
        if board_area <= 0: board_area = 100.0 # Fallback
            
        height = math.sqrt(board_area / ar)
        width = height * ar
        
        self.board_outline["width"] = width
        self.board_outline["height"] = height
        
        # Update PCB Canvas
        self.pcb.x_range = [0, width]
        self.pcb.y_range = [0, height]
        
        self.log(f"Board Size: {width:.2f} x {height:.2f} (Density: {density})")

    # =================================================================
    # STAGE 4: Edge Model Activation
    # =================================================================
    def stage_4_edge_activation(self):
        has_connector = any(c.semantic_class == ComponentClass.CONNECTOR for c in self.components.values())
        has_antenna = any(c.semantic_class == ComponentClass.ANTENNA for c in self.components.values())
        has_safety = any(c.semantic_class == ComponentClass.SAFETY for c in self.components.values())
        has_power = any(c.semantic_class == ComponentClass.POWER_ENTRY for c in self.components.values())
        has_ui = any(c.semantic_class == ComponentClass.USER_INTERFACE for c in self.components.values())
        
        self.edge_model_active = (has_connector or has_antenna or has_safety or has_power or has_ui)
        self.log(f"Stage 4: Edge Model Active = {self.edge_model_active}")

    # =================================================================
    # STAGE 5: Edge Role Assignment
    # =================================================================
    def stage_5_edge_assignment(self):
        self.log("Stage 5: Edge Role Assignment")
        # Identify required interfaces
        required_interfaces = set()
        for comp in self.components.values():
            if comp.interface_type != InterfaceType.NONE:
                required_interfaces.add(comp.interface_type)

        # Assign Edge Roles (Heuristic: Top/Bottom preference)
        # Priorities: RF > SAFETY > POWER > IO > USER

        assignments = {d: EdgeRole.NONE for d in DIRECTIONS}
        available_edges = list(DIRECTIONS)
        adjacency = {
            "NORTH": ("EAST", "WEST"),
            "EAST": ("NORTH", "SOUTH"),
            "SOUTH": ("EAST", "WEST"),
            "WEST": ("NORTH", "SOUTH"),
        }

        def roles_compatible(role_a, role_b):
            if role_a == EdgeRole.NONE or role_b == EdgeRole.NONE:
                return True
            if role_a in FORBIDDEN_ADJACENCY and role_b in FORBIDDEN_ADJACENCY[role_a]:
                return False
            if role_b in FORBIDDEN_ADJACENCY and role_a in FORBIDDEN_ADJACENCY[role_b]:
                return False
            return True

        def edge_accepts_role(edge, role):
            for neighbor in adjacency[edge]:
                if not roles_compatible(role, assignments[neighbor]):
                    return False
            return True

        def assign(role, preferred_edge=None):
            if role not in required_interfaces:
                return True

            role_edge = convert_interface_to_edge_role(role)
            candidates = []
            if preferred_edge in available_edges:
                candidates.append(preferred_edge)
            for edge in DIRECTIONS:
                if edge in available_edges and edge not in candidates:
                    candidates.append(edge)

            for cand in candidates:
                if edge_accepts_role(cand, role_edge):
                    assignments[cand] = role_edge
                    available_edges.remove(cand)
                    self.log(f"  Assigned {cand} -> {assignments[cand].name}")
                    return True

            # If no legal edge exists, keep higher-priority roles and skip this one.
            self.log(
                f"  Skipped {role_edge.name}: no legal edge available without violating adjacency rules"
            )
            return False

        # 1. RF (Needs isolation, prefer North/South if antenna)
        assign(InterfaceType.RF, "NORTH")

        # 2. Safety (Opposite to User/Low Voltage)
        assign(InterfaceType.SAFETY, "SOUTH" if "NORTH" not in available_edges else "NORTH")

        # 3. Power (Ideally one side)
        assign(InterfaceType.POWER, "WEST")

        # 4. IO
        assign(InterfaceType.IO, "EAST")

        # 5. User
        assign(InterfaceType.USER, "SOUTH")

        self.board_outline["edges"] = assignments

    # =================================================================
    # STAGE 6: Fixing & Anchoring
    # =================================================================
    def stage_6_fixing_anchoring(self):
        self.log("Stage 6: Fixing & Anchoring")
        
        # 6.1 Identify ANCHORED components (Decoupling Caps -> ICs)
        # Use Net connectivity as a "Hard" rule if possible.
        
        # Build Net Map
        net_map = {} # NetName -> List[Comp]
        for net_name, net in self.pcb.nets.items():
            for cname, pname in net.pins:
                if cname in self.components:
                    if net_name not in net_map: net_map[net_name] = []
                    net_map[net_name].append(self.components[cname])
                    
        # Crystal -> MCU
        crystals = [c for c in self.components.values() if "XTAL" in getattr(c, 'footprint_name', '').upper() or c.name.startswith("Y")]
        for xtal in crystals:
            xtal.placement_type = PlacementType.ANCHORED
            parent = None
            for net_name, pins in self.pcb.nets.items():
                if any(p[0] == xtal.name for p in pins.pins):
                    for p in pins.pins:
                        other = self.components.get(p[0])
                        if other and other.semantic_class == ComponentClass.MCU_FPGA:
                            parent = other
                            break
                if parent: break
            
            if parent:
                xtal.anchor_parent = parent
                self.log(f"  Anchored {xtal.name} -> {parent.name}")

    # =================================================================
    # STAGE 7: Deterministic Initial Placement (CORRECTED)
    # =================================================================
    def stage_7_initial_placement(self):
        self.log("Stage 7: Initial Placement")

        # -----------------------------
        # BOARD VALIDATION
        # -----------------------------
        min_x, max_x = self.pcb.x_range
        min_y, max_y = self.pcb.y_range

        if min_x == float("inf") or max_x == float("-inf") or min_x >= max_x:
            self.log("⚠️ Invalid x_range → using default [0,100]")
            min_x, max_x = 0.0, 100.0

        if min_y == float("inf") or max_y == float("-inf") or min_y >= max_y:
            self.log("⚠️ Invalid y_range → using default [0,100]")
            min_y, max_y = 0.0, 100.0

        W = max_x - min_x
        H = max_y - min_y

        margin = 1.0
        # Fixed edge connectors should sit close to the real outline, not an
        # arbitrary large inset. The pipeline can override this per board.
        edge_clearance = max(float(getattr(self.pcb, "connector_edge_gap", 1.0)), 0.25)

        # -----------------------------
        # HELPER FUNCTIONS
        # -----------------------------
        def clamp(cx, cy, w, h):
            cx = max(min_x + w/2 + margin, min(cx, max_x - w/2 - margin))
            cy = max(min_y + h/2 + margin, min(cy, max_y - h/2 - margin))
            return cx, cy

        def is_inside(cx, cy, w, h):
            return (
                cx - w/2 >= min_x and
                cx + w/2 <= max_x and
                cy - h/2 >= min_y and
                cy + h/2 <= max_y
            )

        obstacle_boxes = [
            hole_bbox(hole, clearance=1.0)
            for hole in getattr(self.pcb, "holes", [])
        ]
        obstacle_boxes.extend(
            box for box in (keepout_bbox(ko) for ko in getattr(self.pcb, "keepouts", []))
            if box is not None
        )
        placed_components = set()

        def collides(cx, cy, w, h, ignore_ref=None):
            box = bbox_from_center(cx, cy, w, h, clearance=margin)

            for obstacle_box in obstacle_boxes:
                overlap_x, overlap_y = bbox_overlap(box, obstacle_box)
                if overlap_x > 0 and overlap_y > 0:
                    return True

            for other_ref in placed_components:
                if other_ref == ignore_ref:
                    continue
                other = self.components.get(other_ref)
                if other is None:
                    continue
                other_box = bbox_from_center(
                    other.ctr_x, other.ctr_y,
                    other.get_x_length(), other.get_y_length(),
                    clearance=margin
                )
                overlap_x, overlap_y = bbox_overlap(box, other_box)
                if overlap_x > 0 and overlap_y > 0:
                    return True

            return False

        def find_valid_position(cx, cy, w, h, ignore_ref=None):
            candidate_x, candidate_y = clamp(cx, cy, w, h)
            search_step = max(2.0, min(w, h) / 2.0)
            max_radius = max(W, H)

            radius = 0.0
            while radius <= max_radius:
                offsets = [
                    (0.0, 0.0),
                    (radius, 0.0), (-radius, 0.0),
                    (0.0, radius), (0.0, -radius),
                    (radius, radius), (radius, -radius),
                    (-radius, radius), (-radius, -radius),
                ]
                for dx, dy in offsets:
                    px, py = clamp(candidate_x + dx, candidate_y + dy, w, h)
                    if is_inside(px, py, w, h) and not collides(px, py, w, h, ignore_ref=ignore_ref):
                        return px, py
                radius += search_step

            return candidate_x, candidate_y

        # -----------------------------
        # GROUP CONNECTORS
        # -----------------------------
        edge_comps = {"NORTH": [], "SOUTH": [], "EAST": [], "WEST": []}
        edges = ["NORTH", "SOUTH", "EAST", "WEST"]
        idx = 0

        for comp in self.components.values():

            if comp.semantic_class == ComponentClass.CONNECTOR or comp.interface_type == InterfaceType.POWER:
                comp.fixed = True

                # distribute
                edge = edges[idx % 4]
                edge_comps[edge].append(comp)
                idx += 1

        # -----------------------------
        # PLACE CONNECTORS (SAFE VERSION)
        # -----------------------------
        for edge, comps in edge_comps.items():

            if not comps:
                continue

            n = len(comps)

            for i, comp in enumerate(comps):

                # ---- 1. SET ROTATION CORRECTLY ----
                if edge in ["NORTH", "SOUTH"]:
                    comp.rotation = 90.0
                else:
                    comp.rotation = 0.0

                # ---- 2. GET CORRECT DIMENSIONS AFTER ROTATION ----
                w = comp.get_x_length()
                h = comp.get_y_length()

                # ---- 3. DISTRIBUTION ALONG EDGE ----
                pos = (i + 1) / (n + 1)

                if edge == "NORTH":
                    cx = min_x + W * pos
                    cy = min_y + edge_clearance + h/2

                elif edge == "SOUTH":
                    cx = min_x + W * pos
                    cy = max_y - edge_clearance - h/2

                elif edge == "EAST":
                    cx = max_x - edge_clearance - w/2
                    cy = min_y + H * pos

                elif edge == "WEST":
                    cx = min_x + edge_clearance + w/2
                    cy = min_y + H * pos

                # ---- 4. HARD CLAMP ----
                cx, cy = clamp(cx, cy, w, h)
                cx, cy = find_valid_position(cx, cy, w, h, ignore_ref=comp.name)

                # ---- 5. FINAL VALIDATION ----
                if not is_inside(cx, cy, w, h):
                    self.log(f"❌ {comp.name} still outside → forcing center")
                    cx = min_x + W/2
                    cy = min_y + H/2

                comp.translate_to(cx, cy)
                placed_components.add(comp.name)

                self.log(f"Connector {comp.name} → ({cx:.2f},{cy:.2f}) [{edge}]")

        # -----------------------------
        # PLACE ANCHORED COMPONENTS (FIXED BUG)
        # -----------------------------
        anchored_count = 0

        for comp in self.components.values():

            if not hasattr(comp, "anchor_parent") or comp.anchor_parent is None:
                continue

            parent = comp.anchor_parent

            if parent is None:
                continue
            if parent.name not in placed_components:
                continue

            # offset
            cx = parent.ctr_x + 3.0
            cy = parent.ctr_y + 3.0

            w = comp.get_x_length()
            h = comp.get_y_length()

            cx, cy = clamp(cx, cy, w, h)
            cx, cy = find_valid_position(cx, cy, w, h, ignore_ref=comp.name)

            comp.translate_to(cx, cy)
            comp.fixed = True
            anchored_count += 1
            placed_components.add(comp.name)

            self.log(f"Anchored {comp.name} near {parent.name}")

        self.log(f"Anchored placed: {anchored_count}")

        # -----------------------------
        # PLACE FREE COMPONENTS (GRID)
        # -----------------------------
        free_comps = sorted(
            [c for c in self.components.values() if not c.fixed],
            key=lambda c: c.get_x_length() * c.get_y_length(),
            reverse=True
        )

        if free_comps:

            self.log(f"Placing {len(free_comps)} free components")

            grid_margin = 10.0
            usable_w = W - 2 * grid_margin
            usable_h = H - 2 * grid_margin

            cols = int(math.sqrt(len(free_comps))) + 1
            rows = (len(free_comps) + cols - 1) // cols

            dx = usable_w / max(cols, 1)
            dy = usable_h / max(rows, 1)

            for i, comp in enumerate(free_comps):

                r = i // cols
                c = i % cols

                cx = min_x + grid_margin + c * dx + dx/2
                cy = min_y + grid_margin + r * dy + dy/2

                w = comp.get_x_length()
                h = comp.get_y_length()

                cx, cy = clamp(cx, cy, w, h)
                cx, cy = find_valid_position(cx, cy, w, h, ignore_ref=comp.name)

                comp.translate_to(cx, cy)
                placed_components.add(comp.name)

                self.log(f"Free {comp.name} → ({cx:.2f},{cy:.2f})")

    # =================================================================
    # STAGE 9: Validation
    # =================================================================
    def stage_9_validation(self):
        self.log("Stage 9: Validation")
        # Check Forbidden Adjacencies
        edges = self.board_outline["edges"]
        adj_pairs = [("NORTH", "EAST"), ("EAST", "SOUTH"), ("SOUTH", "WEST"), ("WEST", "NORTH")]
        
        for e1, e2 in adj_pairs:
            r1 = edges.get(e1, EdgeRole.NONE)
            r2 = edges.get(e2, EdgeRole.NONE)
            
            if r1 in FORBIDDEN_ADJACENCY and r2 in FORBIDDEN_ADJACENCY[r1]:
                self.log(f"⚠️ VIOLATION: {e1}({r1.name}) next to {e2}({r2.name}) is forbidden!")

def convert_interface_to_edge_role(iface):
    mapping = {
        InterfaceType.POWER: EdgeRole.POWER_EDGE,
        InterfaceType.RF: EdgeRole.RF_EDGE,
        InterfaceType.IO: EdgeRole.IO_EDGE,
        InterfaceType.USER: EdgeRole.USER_EDGE,
        InterfaceType.SIGNAL: EdgeRole.SIGNAL_EDGE,
        InterfaceType.SAFETY: EdgeRole.SAFETY_EDGE
    }
    return mapping.get(iface, EdgeRole.NONE)
