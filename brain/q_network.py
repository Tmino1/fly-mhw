"""
Q2RL (Dodeja et al., RSS 2026 -- q2rl.rai-inst.com, MIT-licensed reference
at github.com/rai-opensource/q2rl) support: a small Q(s,a)/V(s) critic
pair, trained offline from BC-derived pseudo-targets by
training/q_estimation.py. See that script and docs/architecture.md's
"IL -> RL fine-tuning technique (Phase 4): Q2RL" entry for the full
algorithm and why it fits ConnectomeBrain's discrete softmax
motor_readout unusually well (the reference implementation was built for
continuous Gaussian-mixture robot policies).

NOT part of the fixed connectome -- Design Principle 1 only constrains
ConnectomeBrain's own trainable surface (edge_gain, neuron_bias/gain/tau,
sensory_encoder, motor_readout). These are separate, ordinary MLPs, the
same way Q2RL's own critic/value networks are separate modules from its
BC policy, not a modification to it.
"""

from __future__ import annotations

import torch
import torch.nn as nn


class QNetwork(nn.Module):
    """Q(s, a) for a DISCRETE action space -- one output head per action,
    read out at the chosen action's index. This is a standard DQN-style
    critic head (state in, one Q-value per action out), not Q2RL's own
    continuous (state, action)-concatenated input -- that convention is
    specific to continuous action spaces and doesn't apply to this
    project's 8-way discrete action set."""

    def __init__(self, input_dim: int, n_actions: int, hidden_dim: int = 256):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, n_actions),
        )

    def forward(self, state_features: torch.Tensor) -> torch.Tensor:
        """state_features: (batch, input_dim) -> (batch, n_actions)."""
        return self.net(state_features)


class ValueNetwork(nn.Module):
    """V(s) -- same input as QNetwork, single scalar output."""

    def __init__(self, input_dim: int, hidden_dim: int = 256):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, 1),
        )

    def forward(self, state_features: torch.Tensor) -> torch.Tensor:
        """state_features: (batch, input_dim) -> (batch,)."""
        return self.net(state_features).squeeze(-1)
