import sys
import math
import numpy as np

# PyTorch and Gym dependencies
import torch
import torch.nn as nn

from geometry_utils import hole_bbox, keepout_bbox

class ComponentEmbeddingMLP(nn.Module):
    """
    MLP that encodes component features for the RL state.
    Vector: [one_hot(type) [size 6], pin_count, degree, cluster_id, cluster_size, is_connector, width, height] -> Total ~ 14 features
    """
    def __init__(self, input_dim=13, output_dim=32):
        super(ComponentEmbeddingMLP, self).__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, 64),
            nn.ReLU(),
            nn.Linear(64, output_dim),
            nn.ReLU()
        )
        
    def forward(self, features):
        return self.net(features)

class PCBAgentEnv:
    """
    Reinforcement Learning Environment for PCBAgent Layout.
    Formulated as a sequential placement Markov Decision Process.
    """
    def __init__(self, G, sizes, board_w, board_h, clusters, holes=None, keepouts=None,
                 edge_clearance=0.5, comp_clearance=0.25, N=64,
                 component_order=None, region_bbox=None, initial_placement=None,
                 edge_keepout_margin=0.0):
        self.G = G
        self.sizes = sizes
        self.board_w = board_w
        self.board_h = board_h
        self.clusters = clusters
        self.holes = holes or []
        self.keepouts = keepouts or []
        self.N = N # N x N Grid size for spatial masks
        self.edge_clearance = edge_clearance
        self.edge_keepout_margin = max(float(edge_keepout_margin or 0.0), 0.0)
        self.comp_clearance = comp_clearance
        self.hole_clearance = max(comp_clearance, 1.0)

        self.region_bbox = region_bbox
        if region_bbox is not None:
            self.origin_x = float(region_bbox[0])
            self.origin_y = float(region_bbox[1])
            self.place_w = max(float(region_bbox[2]) - self.origin_x, 1e-6)
            self.place_h = max(float(region_bbox[3]) - self.origin_y, 1e-6)
        else:
            self.origin_x = 0.0
            self.origin_y = 0.0
            self.place_w = max(float(board_w), 1e-6)
            self.place_h = max(float(board_h), 1e-6)
        
        # Scaling factors (mm to grid cells)
        self.scale_x = self.N / self.place_w
        self.scale_y = self.N / self.place_h
        
        # Component placement sequence
        order = component_order or list(G.nodes())
        self.components_to_place = [ref for ref in order if ref in G.nodes()]
        self.num_components = len(self.components_to_place)
        self.current_step = 0
        
        # Stateful placement dict map: ref -> (x_mm, y_mm, rotation_deg)
        self.base_placement = {}
        for ref, pose in (initial_placement or {}).items():
            if isinstance(pose, dict):
                self.base_placement[ref] = (
                    float(pose.get("x", 0.0)),
                    float(pose.get("y", 0.0)),
                    float(pose.get("rotation", 0.0)),
                )
            elif isinstance(pose, (list, tuple)):
                if len(pose) >= 3:
                    self.base_placement[ref] = (float(pose[0]), float(pose[1]), float(pose[2]))
                elif len(pose) >= 2:
                    self.base_placement[ref] = (float(pose[0]), float(pose[1]), 0.0)
        self.placement = dict(self.base_placement)
        
        self.mlp = ComponentEmbeddingMLP(input_dim=13, output_dim=32)

    def _to_grid_x(self, x):
        return int((float(x) - self.origin_x) * self.scale_x)

    def _to_grid_y(self, y):
        return int((float(y) - self.origin_y) * self.scale_y)

    def _clip_grid_x(self, x):
        return max(0, min(self.N, self._to_grid_x(x)))

    def _clip_grid_y(self, y):
        return max(0, min(self.N, self._to_grid_y(y)))
        
    def reset(self):
        """Reset MDP for sequential placement."""
        self.current_step = 0
        self.placement = dict(self.base_placement)
        return self.get_state()
        
    def get_state(self):
        """
        Generate State Space S_t.
        Returns the 5 mask matrices (N x N) and the component embedding vector.
        """
        if self.current_step >= self.num_components:
            return None
            
        next_ref = self.components_to_place[self.current_step]
        
        view_mask = self._generate_view_mask()
        position_mask = self._generate_position_mask(next_ref)
        cluster_mask = self._generate_cluster_mask(next_ref)
        constraint_mask = self._generate_constraint_mask(next_ref)
        wire_mask = self._generate_wire_mask(next_ref)
        
        comp_features = self._extract_component_features(next_ref)
        with torch.no_grad():
            comp_embedding = self.mlp(torch.tensor(comp_features, dtype=torch.float32)).numpy()
            
        return {
            "view_mask": view_mask,
            "position_mask": position_mask,
            "wire_mask": wire_mask,
            "cluster_mask": cluster_mask,
            "constraint_mask": constraint_mask,
            "embedding": comp_embedding
        }
        
    def _generate_view_mask(self):
        """1 for occupied grid cells."""
        mask = np.zeros((self.N, self.N), dtype=np.float32)
        for ref, (cx, cy, rot) in self.placement.items():
            w, h = self.sizes.get(ref, (3.0, 3.0))
            if int(rot) % 180 != 0: w, h = h, w
            
            x1 = self._clip_grid_x(cx - w / 2)
            x2 = self._clip_grid_x(cx + w / 2)
            y1 = self._clip_grid_y(cy - h / 2)
            y2 = self._clip_grid_y(cy + h / 2)
            
            mask[y1:y2, x1:x2] = 1.0
            
        # Treat keepouts as firmly occupied visual space
        for ko in self.keepouts:
            ko_box = keepout_bbox(ko)
            if ko_box is None:
                continue
            x1 = self._clip_grid_x(ko_box[0])
            x2 = self._clip_grid_x(ko_box[2])
            y1 = self._clip_grid_y(ko_box[1])
            y2 = self._clip_grid_y(ko_box[3])
            
            mask[y1:y2, x1:x2] = 1.0

        for hole in self.holes:
            x1, y1, x2, y2 = hole_bbox(hole, clearance=self.hole_clearance)
            gx1 = self._clip_grid_x(x1)
            gx2 = self._clip_grid_x(x2)
            gy1 = self._clip_grid_y(y1)
            gy2 = self._clip_grid_y(y2)
            mask[gy1:gy2, gx1:gx2] = 1.0
            
        return mask
        
    def _generate_position_mask(self, next_ref):
        """1 for illegal placement (boundaries, keepouts, existing overlapping components + DRC)."""
        mask = np.zeros((self.N, self.N), dtype=np.float32)
        w, h = self.sizes.get(next_ref, (3.0, 3.0))
        envelope = max(w, h)
        # Account for spacing bounds.
        hw = (envelope / 2 + self.comp_clearance) * self.scale_x
        hh = (envelope / 2 + self.comp_clearance) * self.scale_y
        
        # Boundary masking should be based on the true board outline, not the local cluster box.
        effective_edge_clearance = self.edge_clearance + self.edge_keepout_margin
        min_global_x = effective_edge_clearance + envelope / 2.0
        max_global_x = self.board_w - effective_edge_clearance - envelope / 2.0
        min_global_y = effective_edge_clearance + envelope / 2.0
        max_global_y = self.board_h - effective_edge_clearance - envelope / 2.0

        pad_left = int(max(0.0, (min_global_x - self.origin_x) * self.scale_x))
        pad_right = int(max(0.0, (self.origin_x + self.place_w - max_global_x) * self.scale_x))
        pad_bottom = int(max(0.0, (min_global_y - self.origin_y) * self.scale_y))
        pad_top = int(max(0.0, (self.origin_y + self.place_h - max_global_y) * self.scale_y))

        if pad_left > 0:
            mask[:, :min(self.N, pad_left)] = 1.0
        if pad_right > 0:
            mask[:, max(0, self.N - pad_right):] = 1.0
        if pad_bottom > 0:
            mask[:min(self.N, pad_bottom), :] = 1.0
        if pad_top > 0:
            mask[max(0, self.N - pad_top):, :] = 1.0

        # Mark occupied areas
        for ref, (cx, cy, rot) in self.placement.items():
            ew, eh = self.sizes.get(ref, (3.0, 3.0))
            if int(rot) % 180 != 0: ew, eh = eh, ew
            
            x1 = max(0, self._clip_grid_x(cx - ew / 2) - int(hw))
            x2 = min(self.N, self._clip_grid_x(cx + ew / 2) + int(hw))
            y1 = max(0, self._clip_grid_y(cy - eh / 2) - int(hh))
            y2 = min(self.N, self._clip_grid_y(cy + eh / 2) + int(hh))
            
            mask[y1:y2, x1:x2] = 1.0
            
        # Add individual keepouts and mounting holes to the illegal position mask!
        for ko in self.keepouts:
            ko_box = keepout_bbox(ko)
            if ko_box is None:
                continue
            kx1, ky1, kx2, ky2 = ko_box
            
            # Expand keepout by the component's center clearance (hw, hh)
            x1 = max(0, self._clip_grid_x(kx1) - int(hw))
            x2 = min(self.N, self._clip_grid_x(kx2) + int(hw))
            y1 = max(0, self._clip_grid_y(ky1) - int(hh))
            y2 = min(self.N, self._clip_grid_y(ky2) + int(hh))
            
            mask[y1:y2, x1:x2] = 1.0

        for hole in self.holes:
            hx1, hy1, hx2, hy2 = hole_bbox(hole, clearance=self.hole_clearance)
            x1 = max(0, self._clip_grid_x(hx1) - int(hw))
            x2 = min(self.N, self._clip_grid_x(hx2) + int(hw))
            y1 = max(0, self._clip_grid_y(hy1) - int(hh))
            y2 = min(self.N, self._clip_grid_y(hy2) + int(hh))
            mask[y1:y2, x1:x2] = 1.0
            
        return mask

    def _generate_cluster_mask(self, next_ref):
        """1 for preferred regions belonging to the same cluster."""
        mask = np.zeros((self.N, self.N), dtype=np.float32)
        target_cluster = None
        for cid, refs in self.clusters.items():
            if next_ref in refs:
                target_cluster = cid
                break
                
        if target_cluster is not None:
            # Drop heat locally around already placed items in the same cluster
            for ref in self.clusters[target_cluster]:
                if ref in self.placement:
                    cx, cy, _ = self.placement[ref]
                    grid_x, grid_y = self._to_grid_x(cx), self._to_grid_y(cy)
                    # Simple soft blob logic (fast version: a small square)
                    s = int(5 * self.scale_x) # 5mm blob jump 
                    mask[max(0, grid_y-s):min(self.N, grid_y+s), max(0, grid_x-s):min(self.N, grid_x+s)] = 1.0
        return mask

    def _generate_constraint_mask(self, next_ref):
        """Encodes Keepout zones and Connector edges as a 1."""
        mask = np.zeros((self.N, self.N), dtype=np.float32)
        # Add mechanical holes to keepout
        for ko in self.keepouts:
            ko_box = keepout_bbox(ko)
            if ko_box is None:
                continue
            gx1, gy1 = self._clip_grid_x(ko_box[0]), self._clip_grid_y(ko_box[1])
            gx2, gy2 = self._clip_grid_x(ko_box[2]), self._clip_grid_y(ko_box[3])
            # Keepout areas
            mask[gy1:gy2, gx1:gx2] = 1.0

        for hole in self.holes:
            x1, y1, x2, y2 = hole_bbox(hole, clearance=self.hole_clearance)
            gx1, gy1 = self._clip_grid_x(x1), self._clip_grid_y(y1)
            gx2, gy2 = self._clip_grid_x(x2), self._clip_grid_y(y2)
            mask[gy1:gy2, gx1:gx2] = 1.0
            
        return mask
        
    def _generate_wire_mask(self, next_ref):
        """Represents increase in HPWL. The lower the better!"""
        # (Heuristic simplified HPWL proxy map)
        mask = np.ones((self.N, self.N), dtype=np.float32) * 100.0 # Default high penalty
        # Determine connections to already connected components
        edges = self.G.edges(next_ref, data=True)
        connected_points = []
        for u, v, data in edges:
            other = v if u == next_ref else u
            if other in self.placement:
                connected_points.append(self.placement[other])
                
        if not connected_points:
            return np.zeros((self.N, self.N), dtype=np.float32) # No HPWL penalty
            
        # Draw gradient representing distance from geometric center of connected points
        cx = sum(p[0] for p in connected_points) / len(connected_points)
        cy = sum(p[1] for p in connected_points) / len(connected_points)
        gcx, gcy = self._to_grid_x(cx), self._to_grid_y(cy)
        
        # Super fast numpy meshgrid for manhattan HPWL approximation
        y, x = np.ogrid[0:self.N, 0:self.N]
        dist = np.abs(x - gcx) / self.scale_x + np.abs(y - gcy) / self.scale_y
        mask = dist.astype(np.float32)
        
        return mask

    def _extract_component_features(self, ref):
        """
        f_i = [one_hot(type), pin_count, degree, cluster_id, cluster_size, is_connector, width, height]
        """
        node_data = self.G.nodes.get(ref, {})
        ctype = node_data.get("comp_type", "UNKNOWN")
        types = ["IC", "PASSIVE", "CONNECTOR", "DIODE", "CRYSTAL", "UNKNOWN"]
        one_hot = [1.0 if t == ctype else 0.0 for t in types]
        
        pin_count = float(node_data.get("num_pins", 2))
        degree = float(self.G.degree(ref))
        
        cluster_id = -1
        cluster_size = 0
        for cid, refs in self.clusters.items():
            if ref in refs:
                cluster_id = float(cid)
                cluster_size = float(len(refs))
                break
                
        is_conn = 1.0 if node_data.get("is_connector", False) else 0.0
        w, h = self.sizes.get(ref, (3.0, 3.0))
        
        return one_hot + [pin_count, degree, cluster_id, cluster_size, is_conn, float(w), float(h)]
        
    def step(self, action):
        """
        Action space A_t = (grid_x, grid_y, rot_idx)
        Executes placement mapping into exact coordinate space.
        """
        gx, gy, rot_idx = action
        next_ref = self.components_to_place[self.current_step]
        
        x_mm = self.origin_x + (gx + 0.5) / self.scale_x
        y_mm = self.origin_y + (gy + 0.5) / self.scale_y
        rotations = [0.0, 90.0, 180.0, 270.0]
        rot = rotations[int(rot_idx) % 4]
        
        self.placement[next_ref] = (x_mm, y_mm, rot)
        self.current_step += 1
        
        done = (self.current_step >= self.num_components)
        reward = self._compute_step_reward(next_ref)
        
        return self.get_state(), reward, done, {}
        
    def _compute_step_reward(self, just_placed_ref):
        """
        Reward is calculated as delta-HPWL + bounds checks.
        Positive is better.
        """
        # Stub for HPWL wire computation logic
        # Negative HPWL delta + Overlap Penalties
        return -1.0 # Base penalty
