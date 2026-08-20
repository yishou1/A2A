"""Dense message-passing GNN for pairwise formation-relation inference."""

from __future__ import annotations

import torch
import torch.nn as nn


class DenseGraphRelationGNN(nn.Module):
    """Infer symmetric edge logits after two rounds of graph message passing."""

    def __init__(
        self,
        node_features: int = 7,
        edge_features: int = 6,
        hidden_size: int = 32,
        message_layers: int = 2,
    ) -> None:
        super().__init__()
        self.node_features = node_features
        self.edge_features = edge_features
        self.hidden_size = hidden_size
        self.node_encoder = nn.Sequential(
            nn.Linear(node_features, hidden_size),
            nn.ReLU(),
            nn.Linear(hidden_size, hidden_size),
        )
        self.message_mlps = nn.ModuleList(
            nn.Sequential(
                nn.Linear(hidden_size * 2 + edge_features, hidden_size),
                nn.ReLU(),
                nn.Linear(hidden_size, hidden_size),
            )
            for _ in range(message_layers)
        )
        self.update_mlps = nn.ModuleList(
            nn.Sequential(
                nn.Linear(hidden_size * 2, hidden_size),
                nn.ReLU(),
                nn.Linear(hidden_size, hidden_size),
            )
            for _ in range(message_layers)
        )
        self.norms = nn.ModuleList(nn.LayerNorm(hidden_size) for _ in range(message_layers))
        self.edge_head = nn.Sequential(
            nn.Linear(hidden_size * 2 + edge_features, hidden_size),
            nn.ReLU(),
            nn.Linear(hidden_size, 1),
        )

    def forward(
        self,
        node_inputs: torch.Tensor,
        edge_inputs: torch.Tensor,
        node_mask: torch.Tensor,
    ) -> torch.Tensor:
        """Return symmetric logits with shape ``[batch, nodes, nodes]``."""
        hidden = self.node_encoder(node_inputs)
        mask = node_mask.to(dtype=hidden.dtype)
        hidden = hidden * mask.unsqueeze(-1)
        pair_mask = mask.unsqueeze(2) * mask.unsqueeze(1)

        for message_mlp, update_mlp, norm in zip(
            self.message_mlps, self.update_mlps, self.norms
        ):
            left = hidden.unsqueeze(2).expand(-1, -1, hidden.size(1), -1)
            right = hidden.unsqueeze(1).expand(-1, hidden.size(1), -1, -1)
            messages = message_mlp(torch.cat([left, right, edge_inputs], dim=-1))
            messages = messages * pair_mask.unsqueeze(-1)
            aggregated = messages.sum(dim=2) / pair_mask.sum(dim=2).clamp_min(1.0).unsqueeze(-1)
            hidden = norm(hidden + update_mlp(torch.cat([hidden, aggregated], dim=-1)))
            hidden = hidden * mask.unsqueeze(-1)

        left = hidden.unsqueeze(2).expand(-1, -1, hidden.size(1), -1)
        right = hidden.unsqueeze(1).expand(-1, hidden.size(1), -1, -1)
        logits = self.edge_head(torch.cat([left, right, edge_inputs], dim=-1)).squeeze(-1)
        logits = 0.5 * (logits + logits.transpose(1, 2))
        return logits
