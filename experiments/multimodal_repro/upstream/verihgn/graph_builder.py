"""
VeriHGN heterogeneous graph construction from preprocessed VLSI placement data.

Builds a PyG HeteroData graph with node types: cell, net, grid_l0, grid_l1
and multiple edge types capturing circuit topology, geometry, and spatial hierarchy.
"""

import os
import time
import numpy as np
import pandas as pd
import torch
from torch_geometric.data import HeteroData


def read_metadata(sample_dir):
    meta = {}
    with open(os.path.join(sample_dir, 'metadata.txt')) as f:
        for line in f:
            if '=' in line:
                k, v = line.strip().split('=', 1)
                meta[k] = v
    return meta


def standardize(x):
    """Per-feature standardization (zero-mean, unit-var)."""
    mu = x.mean(dim=0, keepdim=True)
    std = x.std(dim=0, keepdim=True).clamp(min=1e-6)
    return (x - mu) / std


def build_grid_adjacency(M, N):
    """Build 4-connected grid adjacency (bidirectional)."""
    idx = torch.arange(M * N, dtype=torch.long).reshape(M, N)
    src, dst = [], []
    src.append(idx[:, :-1].reshape(-1))
    dst.append(idx[:, 1:].reshape(-1))
    src.append(idx[:-1, :].reshape(-1))
    dst.append(idx[1:, :].reshape(-1))
    src = torch.cat(src)
    dst = torch.cat(dst)
    edge_index = torch.stack([torch.cat([src, dst]), torch.cat([dst, src])], dim=0)
    return edge_index


def build_hierarchical_edges(M0, N0, M1, N1):
    """Build parent-child edges between L0 and L1 grids (vectorized)."""
    i = torch.arange(M0)
    j = torch.arange(N0)
    ii, jj = torch.meshgrid(i, j, indexing='ij')
    l0_idx = (ii * N0 + jj).reshape(-1)
    l1_i = (ii // 2).clamp(max=M1 - 1)
    l1_j = (jj // 2).clamp(max=N1 - 1)
    l1_idx = (l1_i * N1 + l1_j).reshape(-1)
    return l0_idx, l1_idx


def build_grid_net_edges(cell_grid_idx, pin_edge_index, M, N, num_nets,
                         max_bbox_tiles=100, max_total_edges=5_000_000):
    """
    Build net-to-grid edges based on net bounding boxes (vectorized).
    Groups nets by bbox size and generates edges in batch per group.
    """
    cell_idx = pin_edge_index[0]
    net_idx = pin_edge_index[1]

    cell_row = cell_grid_idx // N
    cell_col = cell_grid_idx % N
    pin_row = cell_row[cell_idx]
    pin_col = cell_col[cell_idx]

    # Per-net bounding box
    row_min = torch.full((num_nets,), M, dtype=torch.long)
    row_max = torch.full((num_nets,), -1, dtype=torch.long)
    col_min = torch.full((num_nets,), N, dtype=torch.long)
    col_max = torch.full((num_nets,), -1, dtype=torch.long)

    row_min.scatter_reduce_(0, net_idx, pin_row, reduce='amin')
    row_max.scatter_reduce_(0, net_idx, pin_row, reduce='amax')
    col_min.scatter_reduce_(0, net_idx, pin_col, reduce='amin')
    col_max.scatter_reduce_(0, net_idx, pin_col, reduce='amax')

    valid = (row_max >= 0) & (col_max >= 0)
    bbox_h = (row_max - row_min + 1).clamp(min=1)
    bbox_w = (col_max - col_min + 1).clamp(min=1)
    bbox_area = bbox_h * bbox_w
    valid = valid & (bbox_area <= max_bbox_tiles)

    valid_nets = torch.where(valid)[0]
    if valid_nets.numel() == 0:
        return torch.zeros(0, dtype=torch.long), torch.zeros(0, dtype=torch.long)

    # Sort by area (small first) to prioritize local nets under edge budget
    areas = bbox_area[valid_nets]
    order = areas.argsort()
    valid_nets = valid_nets[order]
    sorted_areas = areas[order]

    # Cumulative area to enforce edge budget
    cum_area = sorted_areas.cumsum(0)
    budget_mask = cum_area <= max_total_edges
    valid_nets = valid_nets[budget_mask]

    if valid_nets.numel() == 0:
        return torch.zeros(0, dtype=torch.long), torch.zeros(0, dtype=torch.long)

    # Group by (bbox_h, bbox_w) for vectorized construction
    v_h = bbox_h[valid_nets]
    v_w = bbox_w[valid_nets]
    v_rmin = row_min[valid_nets]
    v_cmin = col_min[valid_nets]

    hw_key = v_h * (N + 1) + v_w
    unique_keys, inverse = hw_key.unique(return_inverse=True)

    all_net_src = []
    all_grid_dst = []

    for ki, key in enumerate(unique_keys):
        mask = inverse == ki
        group_nets = valid_nets[mask]
        h_val = v_h[mask][0].item()
        w_val = v_w[mask][0].item()

        row_off = torch.arange(h_val)
        col_off = torch.arange(w_val)
        rr, cc = torch.meshgrid(row_off, col_off, indexing='ij')
        offsets_r = rr.reshape(-1)
        offsets_c = cc.reshape(-1)

        tiles_per_net = h_val * w_val
        net_ids = group_nets.repeat_interleave(tiles_per_net)

        grp_rmin = v_rmin[mask]
        grp_cmin = v_cmin[mask]
        grid_rows = grp_rmin.unsqueeze(1) + offsets_r.unsqueeze(0)
        grid_cols = grp_cmin.unsqueeze(1) + offsets_c.unsqueeze(0)
        grid_indices = (grid_rows * N + grid_cols).reshape(-1)

        all_net_src.append(net_ids)
        all_grid_dst.append(grid_indices)

    if all_net_src:
        net_src = torch.cat(all_net_src)
        grid_dst = torch.cat(all_grid_dst)
    else:
        net_src = torch.zeros(0, dtype=torch.long)
        grid_dst = torch.zeros(0, dtype=torch.long)

    return net_src, grid_dst


def build_verihgn_graph(sample_dir, congestion_cmax=None):
    """
    Build a VeriHGN heterogeneous graph from a preprocessed sample directory.

    Args:
        sample_dir: Path to sample directory containing .npy features and layout.csv
        congestion_cmax: Global c_max for normalization. If None, use per-sample max.
    """
    t0 = time.time()

    # Load preprocessed data
    cell_feat = torch.from_numpy(np.load(os.path.join(sample_dir, 'cell_features.npy')))
    net_feat = torch.from_numpy(np.load(os.path.join(sample_dir, 'net_features.npy')))
    pin_edge_index = torch.from_numpy(np.load(os.path.join(sample_dir, 'pin_edge_index.npy')))
    pin_feat = torch.from_numpy(np.load(os.path.join(sample_dir, 'pin_features.npy')))
    geom_edge_index = torch.from_numpy(np.load(os.path.join(sample_dir, 'geom_edge_index.npy')))
    geom_edge_feat = torch.from_numpy(np.load(os.path.join(sample_dir, 'geom_edge_features.npy')))

    meta = read_metadata(sample_dir)
    M = int(meta['grid_M'])
    N = int(meta['grid_N'])
    num_cells = cell_feat.shape[0]
    num_nets = net_feat.shape[0]

    layout = pd.read_csv(os.path.join(sample_dir, 'layout.csv'))

    # Node features
    cell_x = standardize(cell_feat.float())
    net_x = standardize(net_feat.float())

    # Grid size check
    expected_grid = M * N
    actual_rows = len(layout)
    if actual_rows != expected_grid:
        if actual_rows > expected_grid:
            layout = layout.iloc[:expected_grid]
        else:
            M = actual_rows
            N = 1

    # Map cells to grid tiles
    cell_raw_x = cell_feat[:, 0].float()
    cell_raw_y = cell_feat[:, 1].float()
    layout_x = layout['x'].values
    layout_y = layout['y'].values
    x_min, x_max = layout_x.min(), layout_x.max()
    y_min, y_max = layout_y.min(), layout_y.max()
    x_step = (x_max - x_min) / M if x_max > x_min else 1.0
    y_step = (y_max - y_min) / N if y_max > y_min else 1.0

    cell_grid_row = ((cell_raw_x - x_min) / x_step).long().clamp(0, M - 1)
    cell_grid_col = ((cell_raw_y - y_min) / y_step).long().clamp(0, N - 1)
    cell_grid_idx = cell_grid_row * N + cell_grid_col

    # Grid L0 features from layout.csv
    layout_feat = torch.zeros(M * N, 5, dtype=torch.float32)
    layout_feat[:, 0] = torch.from_numpy(layout['RUDY'].values[:M * N].astype(np.float32))
    layout_feat[:, 1] = torch.from_numpy(layout['PinRUDY'].values[:M * N].astype(np.float32))
    layout_feat[:, 2] = torch.from_numpy(layout['MacroRegion'].values[:M * N].astype(np.float32))
    layout_feat[:, 3] = torch.from_numpy(layout['H_MacroMargin'].values[:M * N].astype(np.float32))
    layout_feat[:, 4] = torch.from_numpy(layout['V_MacroMargin'].values[:M * N].astype(np.float32))
    grid_l0_feat = layout_feat
    grid_l0_x = standardize(grid_l0_feat)

    # Grid L1: coarser grid (2x2 pooling)
    M1 = (M + 1) // 2
    N1 = (N + 1) // 2

    grid_l0_raw_2d = grid_l0_feat.reshape(M, N, 5)
    padM = M1 * 2 - M
    padN = N1 * 2 - N
    if padM > 0 or padN > 0:
        grid_l0_padded = torch.nn.functional.pad(grid_l0_raw_2d, (0, 0, 0, padN, 0, padM))
    else:
        grid_l0_padded = grid_l0_raw_2d
    grid_l1_feat = grid_l0_padded.reshape(M1, 2, N1, 2, 5).mean(dim=(1, 3))
    grid_l1_x = standardize(grid_l1_feat.reshape(M1 * N1, 5))

    # Labels: log-normalized congestion
    h_cong = layout['H_Congestion'].values[:M * N].astype(np.float32)
    v_cong = layout['V_Congestion'].values[:M * N].astype(np.float32)
    grid_congestion = (h_cong + v_cong) / 2.0
    grid_congestion = np.log1p(grid_congestion)
    local_max = grid_congestion.max()
    if local_max > 0:
        grid_congestion = grid_congestion / local_max

    grid_y = torch.from_numpy(grid_congestion)
    cell_y = grid_y[cell_grid_idx]

    # Build HeteroData
    data = HeteroData()

    data['cell'].x = cell_x
    data['cell'].y = cell_y
    data['cell'].num_nodes = num_cells

    data['net'].x = net_x
    data['net'].num_nodes = num_nets

    data['grid_l0'].x = grid_l0_x
    data['grid_l0'].y = grid_y
    data['grid_l0'].num_nodes = M * N

    data['grid_l1'].x = grid_l1_x
    data['grid_l1'].num_nodes = M1 * N1

    # Edges
    data['cell', 'pin', 'net'].edge_index = pin_edge_index
    data['cell', 'pin', 'net'].edge_attr = pin_feat.float()
    data['net', 'pin_rev', 'cell'].edge_index = pin_edge_index.flip(0)
    data['net', 'pin_rev', 'cell'].edge_attr = pin_feat.float()

    data['cell', 'geom', 'cell'].edge_index = geom_edge_index
    data['cell', 'geom', 'cell'].edge_attr = geom_edge_feat.float()

    grid_l0_adj = build_grid_adjacency(M, N)
    data['grid_l0', 'adj', 'grid_l0'].edge_index = grid_l0_adj

    grid_l1_adj = build_grid_adjacency(M1, N1)
    data['grid_l1', 'adj', 'grid_l1'].edge_index = grid_l1_adj

    l0_idx, l1_idx = build_hierarchical_edges(M, N, M1, N1)
    data['grid_l0', 'child_of', 'grid_l1'].edge_index = torch.stack([l0_idx, l1_idx])
    data['grid_l1', 'parent_of', 'grid_l0'].edge_index = torch.stack([l1_idx, l0_idx])

    cell_indices = torch.arange(num_cells, dtype=torch.long)
    data['cell', 'in', 'grid_l0'].edge_index = torch.stack([cell_indices, cell_grid_idx])
    data['grid_l0', 'contains', 'cell'].edge_index = torch.stack([cell_grid_idx, cell_indices])

    net_src, grid_dst = build_grid_net_edges(
        cell_grid_idx, pin_edge_index, M, N, num_nets,
        max_bbox_tiles=100, max_total_edges=5_000_000
    )
    data['net', 'spans', 'grid_l0'].edge_index = torch.stack([net_src, grid_dst])
    data['grid_l0', 'spans_rev', 'net'].edge_index = torch.stack([grid_dst, net_src])

    data.grid_M = M
    data.grid_N = N
    data.grid_M1 = M1
    data.grid_N1 = N1
    data.build_time = time.time() - t0

    return data


def compute_dataset_cmax(data_dir, designs=None):
    """Compute c_max = max(log(1+x)) across all samples."""
    cmax = 0.0
    if designs is None:
        designs = [d for d in os.listdir(data_dir) if os.path.isdir(os.path.join(data_dir, d))]
    for design in designs:
        design_dir = os.path.join(data_dir, design)
        if not os.path.isdir(design_dir):
            continue
        for sample in os.listdir(design_dir):
            sample_dir = os.path.join(design_dir, sample)
            layout_path = os.path.join(sample_dir, 'layout.csv')
            if os.path.exists(layout_path):
                layout = pd.read_csv(layout_path)
                h = layout['H_Congestion'].values
                v = layout['V_Congestion'].values
                cong = (h + v) / 2.0
                local_max = np.log1p(cong).max()
                cmax = max(cmax, local_max)
    return cmax
