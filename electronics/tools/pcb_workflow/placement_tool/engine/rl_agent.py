import os
import torch
import torch.nn as nn
import torch.optim as optim
from torch.distributions import Categorical
import numpy as np


class PCBActorCritic(nn.Module):
    """
    Given a 5x128x128 Spatial Mask Tensor and a 32-dim Component Vector,
    Applies CNN feature extraction, merges representations, and outputs:
      1. Spatial Logits (N x N)
      2. Rotational Logits (4)
      3. Value Estimate (1)
    """
    def __init__(self, N=64, embed_dim=32):
        super(PCBActorCritic, self).__init__()
        self.N = N
        
        # Simple stacked CNN for the 5-channel 2D spatial context masks
        self.cnn = nn.Sequential(
            nn.Conv2d(5, 16, kernel_size=3, stride=2, padding=1),
            nn.ReLU(),
            nn.Conv2d(16, 32, kernel_size=3, stride=2, padding=1),
            nn.ReLU(),
            nn.Flatten()
        )
        
        # Calculate flattened CNN output size dynamically
        conv_out_size = 32 * (N // 4) * (N // 4)
        
        # Shared MLP merging CNN and Component Embedding
        self.shared_mlp = nn.Sequential(
            nn.Linear(conv_out_size + embed_dim, 256),
            nn.ReLU(),
            nn.Linear(256, 128),
            nn.ReLU()
        )
        
        # Actor Heads
        # Output N x N grid spatial probabilities
        self.actor_pos = nn.Linear(128, N * N)
        # Output 4 rotational probabilities (0, 90, 180, 270)
        self.actor_rot = nn.Linear(128, 4)
        
        # Critic Head (Value function for PPO)
        self.critic = nn.Linear(128, 1)

    def forward(self, masks, comp_embedding):
        """
        masks: [Batch, 5, N, N]
        comp_embedding: [Batch, 32]
        """
        cnn_features = self.cnn(masks)
        combined = torch.cat([cnn_features, comp_embedding], dim=1)
        shared_features = self.shared_mlp(combined)
        
        pos_logits = self.actor_pos(shared_features)
        rot_logits = self.actor_rot(shared_features)
        value = self.critic(shared_features)
        
        return pos_logits, rot_logits, value


class PPOAgent:
    def __init__(self, N=64):
        self.N = N
        self.device = self._resolve_device()
        self.policy = PCBActorCritic(N=N).to(self.device)
        self.optimizer = optim.Adam(self.policy.parameters(), lr=3e-4)
        self._validate_runtime_device()

    def _resolve_device(self):
        preferred = os.getenv("PCB_RL_DEVICE", "").strip().lower()
        if preferred == "cpu":
            return torch.device("cpu")
        if preferred == "cuda" and torch.cuda.is_available():
            return torch.device("cuda")
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")

    def _is_recoverable_cuda_error(self, error):
        message = str(error).lower()
        return (
            "cuda" in message
            or "device-side assert" in message
            or "out of memory" in message
        )

    def _fallback_to_cpu(self, reason):
        if self.device.type == "cpu":
            return
        print(f"\n[WARN] {reason} Falling back to CPU for RL placement.")
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        self.device = torch.device("cpu")
        self.policy = self.policy.to(self.device)

    def _validate_runtime_device(self):
        if self.device.type != "cuda":
            return

        training = self.policy.training
        try:
            self.policy.eval()
            with torch.inference_mode():
                masks = torch.zeros((1, 5, self.N, self.N), device=self.device)
                emb = torch.zeros((1, 32), device=self.device)
                self.policy(masks, emb)
        except (RuntimeError, torch.cuda.OutOfMemoryError) as error:
            if self._is_recoverable_cuda_error(error):
                self._fallback_to_cpu(
                    f"CUDA self-test failed ({error})."
                )
            else:
                raise
        finally:
            self.policy.train(training)
        
    def select_action(self, state, test=False):
        """
        Given the environment state dict, infer the next (X, Y, Rot).
        """
        # FIX: Handle case when state is None (no components to place)
        if state is None:
            self.last_action = None
            return (0, 0, 0), 0.0, 0.0  # Return dummy action

        # Formulate masks tensor [1, 5, N, N]
        masks = np.stack([
            state["view_mask"],
            state["position_mask"],
            state["wire_mask"],
            state["cluster_mask"],
            state["constraint_mask"]
        ])
        masks_tensor = torch.FloatTensor(masks).unsqueeze(0).to(self.device)
        emb_tensor = torch.FloatTensor(state["embedding"]).unsqueeze(0).to(self.device)
        try:
            pos_logits, rot_logits, value = self.policy(masks_tensor, emb_tensor)
        except (RuntimeError, torch.cuda.OutOfMemoryError) as e:
            if self.device.type == "cuda" and self._is_recoverable_cuda_error(e):
                self._fallback_to_cpu(
                    f"CUDA execution failed during RL rollout ({e})."
                )
                masks_tensor = torch.FloatTensor(masks).unsqueeze(0).to(self.device)
                emb_tensor = torch.FloatTensor(state["embedding"]).unsqueeze(0).to(self.device)
                pos_logits, rot_logits, value = self.policy(masks_tensor, emb_tensor)
            else:
                raise e
        
        # Implement strict illegal mask masking for positioning!
        # Flat position mask [1, N*N]
        flat_pos_mask = torch.FloatTensor(state["position_mask"].flatten()).unsqueeze(0).to(self.device)
        # 1.0 means illegal. We apply a massive negative infinity penalty to those logits.
        pos_logits = pos_logits - (flat_pos_mask * 1e9)
        
        pos_dist = Categorical(logits=pos_logits)
        rot_dist = Categorical(logits=rot_logits)
        
        if test:
            # Deterministic greedy placement
            pos_action = torch.argmax(pos_logits, dim=-1)
            rot_action = torch.argmax(rot_logits, dim=-1)
        else:
            # Stochastic placement for exploration/training
            pos_action = pos_dist.sample()
            rot_action = rot_dist.sample()
            
        pos_idx = pos_action.item()
        grid_y = pos_idx // self.N
        grid_x = pos_idx % self.N
        
        # Action is exactly (gx, gy, rot_idx)
        action = (grid_x, grid_y, rot_action.item())
        
        # Returning log probs and value for PPO memory buffer
        log_prob = pos_dist.log_prob(pos_action) + rot_dist.log_prob(rot_action)
        return action, log_prob.item(), value.item()
        
    def save(self, path):
        torch.save(self.policy.state_dict(), path)
        
    def load(self, path):
        if os.path.exists(path):
            self.policy.load_state_dict(torch.load(path, map_location=self.device))
