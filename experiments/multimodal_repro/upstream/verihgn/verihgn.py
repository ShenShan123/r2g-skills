"""
VeriHGN: Heterogeneous Graph Neural Network for VLSI Congestion Prediction.

Architecture:
  - Heterogeneous message passing across cell, net, grid node types
  - Hierarchical grid processing (L0 fine-grained, L1 coarse 2x2 pooled)
  - Geometric cell-cell gated aggregation
  - Gradient checkpointing and chunked message passing for memory efficiency
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.checkpoint import checkpoint
from torch_scatter import scatter_mean, scatter_add


class InputProjection(nn.Module):
    def __init__(self, in_dim, hidden_dim):
        super().__init__()
        self.proj = nn.Sequential(
            nn.Linear(in_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
        )

    def forward(self, x):
        return self.proj(x)


class VeriHGNLayer(nn.Module):
    """One round of relation-specific message passing + hierarchical grid update."""

    def __init__(self, hidden_dim, pin_feat_dim=3, geom_feat_dim=4, dropout=0.2):
        super().__init__()
        self.hidden_dim = hidden_dim

        # Op1: Cell -> Net via pin edges
        self.mlp_pin = nn.Sequential(
            nn.Linear(hidden_dim + pin_feat_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
        )

        # Op2: Grid -> Net
        self.W_grid_to_net = nn.Linear(hidden_dim, hidden_dim)

        # Op3: Net embedding update
        self.W_net_update = nn.Linear(hidden_dim * 3, hidden_dim)
        self.net_norm = nn.LayerNorm(hidden_dim)

        # Op4: Cell -> Grid
        self.W_cell_to_grid = nn.Linear(hidden_dim, hidden_dim)
        self.W_grid_update = nn.Linear(hidden_dim * 2, hidden_dim)
        self.grid_norm = nn.LayerNorm(hidden_dim)

        # Op5: Net -> Cell
        self.W_net_to_cell = nn.Linear(hidden_dim, hidden_dim)

        # Op6: Geometric cell-cell gated aggregation
        self.W_gate = nn.Linear(hidden_dim * 2 + geom_feat_dim, 1)
        self.W_geom_msg = nn.Linear(hidden_dim, hidden_dim)

        # Op7: Cell state update
        self.W_cell_fuse = nn.Linear(hidden_dim * 3, hidden_dim)
        self.cell_norm = nn.LayerNorm(hidden_dim)

        # Hierarchical grid
        self.phi_fc = nn.Sequential(nn.Linear(hidden_dim, hidden_dim), nn.ReLU())
        self.W_l1_spatial = nn.Linear(hidden_dim, hidden_dim)
        self.phi_cf = nn.Sequential(nn.Linear(hidden_dim, hidden_dim), nn.ReLU())
        self.W_gate_hier = nn.Linear(hidden_dim * 2, hidden_dim)

        self.dropout = nn.Dropout(dropout)

    def _chunked_grid_net_agg(self, h_grid, gn_ei, num_nets, chunk_size=500_000):
        """Chunked grid->net aggregation to reduce peak memory."""
        gn_net, gn_grid = gn_ei[0], gn_ei[1]
        num_edges = gn_net.size(0)

        if num_edges <= chunk_size:
            grid_msg = self.W_grid_to_net(h_grid[gn_grid]).float()
            return scatter_mean(grid_msg, gn_net, dim=0, dim_size=num_nets)

        result = torch.zeros(num_nets, self.hidden_dim, device=h_grid.device)
        counts = torch.zeros(num_nets, device=h_grid.device)

        for start in range(0, num_edges, chunk_size):
            end = min(start + chunk_size, num_edges)
            chunk_net = gn_net[start:end]
            chunk_grid = gn_grid[start:end]
            msg = self.W_grid_to_net(h_grid[chunk_grid]).float()
            result.scatter_add_(0, chunk_net.unsqueeze(1).expand_as(msg), msg)
            counts.scatter_add_(0, chunk_net, torch.ones(end - start, device=h_grid.device))

        return result / counts.unsqueeze(1).clamp(min=1)

    def forward(self, h_cell, h_net, h_grid_l0, h_grid_l1, data):
        num_cells = h_cell.size(0)
        num_nets = h_net.size(0)
        num_grid_l0 = h_grid_l0.size(0)
        num_grid_l1 = h_grid_l1.size(0)

        # Op1: Cell -> Net
        pin_ei = data['cell', 'pin', 'net'].edge_index
        pin_attr = data['cell', 'pin', 'net'].edge_attr
        pin_src, pin_dst = pin_ei[0], pin_ei[1]
        pin_msg = self.mlp_pin(torch.cat([h_cell[pin_src], pin_attr], dim=-1))
        h_net_cell = scatter_mean(pin_msg, pin_dst, dim=0, dim_size=num_nets)

        # Op2: Grid -> Net (chunked for memory)
        if ('net', 'spans', 'grid_l0') in data.edge_types:
            gn_ei = data['net', 'spans', 'grid_l0'].edge_index
            h_net_grid = self._chunked_grid_net_agg(h_grid_l0, gn_ei, num_nets)
        else:
            h_net_grid = torch.zeros_like(h_net)

        # Op3: Net update with LayerNorm
        h_net_new = self.net_norm(F.relu(self.W_net_update(
            torch.cat([h_net_cell, h_net_grid, h_net], dim=-1)
        )) + h_net)
        h_net_new = self.dropout(h_net_new)

        # Op4: Cell -> Grid with LayerNorm
        cg_ei = data['cell', 'in', 'grid_l0'].edge_index
        cg_cell, cg_grid = cg_ei[0], cg_ei[1]
        cell_to_grid_msg = self.W_cell_to_grid(h_cell[cg_cell])
        h_grid_cell = scatter_mean(cell_to_grid_msg, cg_grid, dim=0, dim_size=num_grid_l0)
        h_grid_l0_new = self.grid_norm(F.relu(self.W_grid_update(
            torch.cat([h_grid_l0, h_grid_cell], dim=-1)
        )) + h_grid_l0)
        h_grid_l0_new = self.dropout(h_grid_l0_new)

        # Op5: Net -> Cell
        rev_ei = data['net', 'pin_rev', 'cell'].edge_index
        rev_net, rev_cell = rev_ei[0], rev_ei[1]
        net_msg = self.W_net_to_cell(h_net_new[rev_net])
        h_cell_net = scatter_mean(net_msg, rev_cell, dim=0, dim_size=num_cells)

        # Op6: Geometric cell-cell gated aggregation (chunked for large graphs)
        geom_ei = data['cell', 'geom', 'cell'].edge_index
        geom_attr = data['cell', 'geom', 'cell'].edge_attr
        geom_src, geom_dst = geom_ei[0], geom_ei[1]
        num_geom_edges = geom_src.size(0)
        chunk_size = 500_000

        h_cell_f = h_cell.float()
        h_cell_geom = torch.zeros(num_cells, self.hidden_dim, device=h_cell.device)
        geom_counts = torch.zeros(num_cells, device=h_cell.device)

        for start in range(0, num_geom_edges, chunk_size):
            end = min(start + chunk_size, num_geom_edges)
            s, d = geom_src[start:end], geom_dst[start:end]
            ga = geom_attr[start:end].float()
            gate_input = torch.cat([h_cell_f[s], h_cell_f[d], ga], dim=-1)
            alpha_chunk = torch.sigmoid(self.W_gate(gate_input.float())).float()
            msg = (alpha_chunk * self.W_geom_msg(h_cell_f[s])).float()
            h_cell_geom.scatter_add_(0, d.unsqueeze(1).expand_as(msg), msg)
            geom_counts.scatter_add_(0, d, torch.ones(end - start, device=h_cell.device))

        h_cell_geom = h_cell_geom / geom_counts.unsqueeze(1).clamp(min=1)

        # Op7: Cell state update with LayerNorm
        h_cell_new = self.cell_norm(F.relu(self.W_cell_fuse(
            torch.cat([h_cell, h_cell_net, h_cell_geom], dim=-1)
        )) + h_cell)
        h_cell_new = self.dropout(h_cell_new)

        # Hierarchical Grid
        fc_ei = data['grid_l0', 'child_of', 'grid_l1'].edge_index
        fc_l0, fc_l1 = fc_ei[0], fc_ei[1]
        h_l1_agg = scatter_mean(h_grid_l0_new[fc_l0], fc_l1, dim=0, dim_size=num_grid_l1)
        h_grid_l1_new = self.phi_fc(h_l1_agg)

        l1_adj = data['grid_l1', 'adj', 'grid_l1'].edge_index
        l1_src, l1_dst = l1_adj[0], l1_adj[1]
        l1_msg = self.W_l1_spatial(h_grid_l1_new[l1_src])
        l1_agg = scatter_mean(l1_msg, l1_dst, dim=0, dim_size=num_grid_l1)
        h_grid_l1_new = h_grid_l1_new + self.dropout(l1_agg)

        cf_ei = data['grid_l1', 'parent_of', 'grid_l0'].edge_index
        cf_l1, cf_l0 = cf_ei[0], cf_ei[1]
        h_l0_coarse = self.phi_cf(h_grid_l1_new[cf_l1])
        h_l0_from_coarse = scatter_mean(h_l0_coarse, cf_l0, dim=0, dim_size=num_grid_l0)

        gamma = torch.sigmoid(self.W_gate_hier(
            torch.cat([h_grid_l0_new, h_l0_from_coarse], dim=-1)
        ))
        h_grid_l0_out = gamma * h_l0_from_coarse + (1 - gamma) * h_grid_l0_new

        return h_cell_new, h_net_new, h_grid_l0_out, h_grid_l1_new


class PredictionHead(nn.Module):
    def __init__(self, hidden_dim):
        super().__init__()
        self.mlp = nn.Sequential(
            nn.Linear(hidden_dim, 64),
            nn.ReLU(),
            nn.Linear(64, 1),
        )

    def forward(self, h):
        return self.mlp(h).squeeze(-1).clamp(min=0)


class VeriHGN(nn.Module):
    def __init__(self, cell_feat_dim=13, net_feat_dim=6, grid_feat_dim=5,
                 pin_feat_dim=3, geom_feat_dim=4,
                 hidden_dim=128, num_layers=3, dropout=0.2,
                 use_checkpoint=True):
        super().__init__()
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.use_checkpoint = use_checkpoint

        self.cell_proj = InputProjection(cell_feat_dim, hidden_dim)
        self.net_proj = InputProjection(net_feat_dim, hidden_dim)
        self.grid_proj = InputProjection(grid_feat_dim, hidden_dim)

        self.layers = nn.ModuleList([
            VeriHGNLayer(hidden_dim, pin_feat_dim, geom_feat_dim, dropout)
            for _ in range(num_layers)
        ])

        self.cell_head = PredictionHead(hidden_dim)
        self.grid_head = PredictionHead(hidden_dim)

    def forward(self, data):
        h_cell = self.cell_proj(data['cell'].x)
        h_net = self.net_proj(data['net'].x)
        h_grid_l0 = self.grid_proj(data['grid_l0'].x)
        h_grid_l1 = self.grid_proj(data['grid_l1'].x)

        for layer in self.layers:
            if self.use_checkpoint and self.training:
                def make_fn(lyr):
                    def fn(hc, hn, hg0, hg1):
                        return lyr(hc, hn, hg0, hg1, data)
                    return fn
                h_cell, h_net, h_grid_l0, h_grid_l1 = checkpoint(
                    make_fn(layer), h_cell, h_net, h_grid_l0, h_grid_l1,
                    use_reentrant=False
                )
            else:
                h_cell, h_net, h_grid_l0, h_grid_l1 = layer(
                    h_cell, h_net, h_grid_l0, h_grid_l1, data
                )

        cell_pred = self.cell_head(h_cell)
        grid_pred = self.grid_head(h_grid_l0)

        return {'cell_pred': cell_pred, 'grid_pred': grid_pred}
