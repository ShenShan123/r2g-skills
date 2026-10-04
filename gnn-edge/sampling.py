# sampling.py
# Data sampling utilities for creating train/val/test splits and data loaders.
# This module handles neighbor sampling for GNN training on large graphs.

import numpy as np
import torch
import matplotlib.pyplot as plt
import os
from torch_geometric.loader import NeighborLoader, LinkNeighborLoader
from dataset import inverse_standardize, inverse_log_transform, NODE_TYPES


def print_distribution_stats(labels, name, norm_params=None):
    """Print label distribution statistics with optional inverse normalization.

    Args:
        labels: Label array
        name: Name of the split (e.g., 'Train', 'Val', 'Test')
        norm_params: Normalization parameters for inverse transform (optional)

    Returns:
        Labels in original space (if norm_params provided), else as-is
    """
    if norm_params is not None:
        # Convert back to original space
        labels = inverse_standardize(labels, norm_params['mean'], norm_params['std'])
        labels = inverse_log_transform(labels, norm_params['epsilon'])
        labels -= norm_params['offset']

    print(f"{name} stats - mean: {labels.mean():.4f}, std: {labels.std():.4f}, "
          f"min: {labels.min():.4f}, max: {labels.max():.4f}, "
          f"Q1: {np.percentile(labels, 25):.4f}, Q3: {np.percentile(labels, 75):.4f}")
    return labels


def plot_true_values_distribution_before_sampling(dataset, dataset_name):
    """Plot distribution of true values before sampling (for visualization).

    Args:
        dataset: Dataset object
        dataset_name: Name of the dataset
    """
    if not dataset:
        print("Dataset is empty, cannot plot distribution")
        return

    full_graph = dataset[0]
    norm_params = full_graph.norm_params if hasattr(full_graph, 'norm_params') else None

    # Get valid labels (labels != -1)
    valid_labels = full_graph.y[full_graph.valid_mask].numpy()

    # Convert back to original space
    if norm_params is not None:
        valid_labels = inverse_standardize(valid_labels, norm_params['mean'], norm_params['std'])
        valid_labels = inverse_log_transform(valid_labels, norm_params['epsilon'])
        valid_labels -= norm_params['offset']

    # Plot distribution
    plt.figure(figsize=(8, 5))
    plt.hist(valid_labels, bins=50, alpha=0.7)
    plt.title(f'True Values Distribution - {dataset_name}')
    plt.xlabel('Value')
    plt.ylabel('Count')
    plt.grid(alpha=0.3)

    # Save figure
    if not os.path.exists('plots'):
        os.makedirs('plots')
    plt.savefig(os.path.join('plots', f'{dataset_name}_true_values_dist.png'))
    plt.close()


def get_nodes_from_subgraphs(full_graph, graph_indices, sample_ratio, task_level=None, node_type=None):
    """Get node indices from specified subgraphs with optional sampling.

    Key modification: Valid nodes are those with labels != -1 (ignores node_type filtering)

    Args:
        full_graph: Full graph data object
        graph_indices: List of graph IDs to sample from
        sample_ratio: Sampling ratio (0-1), 1.0 means no sampling
        task_level: 'node' or 'edge'
        node_type: Deprecated, not used

    Returns:
        Tensor of node indices
    """
    if task_level == 'node':
        # Get nodes in specified subgraphs
        mask = torch.isin(full_graph.graph_ids, torch.tensor(graph_indices, dtype=torch.long, device=full_graph.graph_ids.device))
    elif task_level == 'edge':
        # For edge tasks, still use node graph_ids
        mask = torch.isin(full_graph.graph_edges_ids, torch.tensor(graph_indices, dtype=torch.long, device=full_graph.graph_edges_ids.device))
    else:
        raise ValueError(f"Unknown or missing task_level: {task_level}. Expected 'node' or 'edge'.")

    # Key modification: valid nodes are those with labels != -1 (no node_type filtering)
    mask = mask & full_graph.valid_mask  # valid_mask is defined as y != -1 in dataset

    node_indices = torch.where(mask)[0]

    # Sample a subset if ratio < 1.0
    if sample_ratio < 1.0 and len(node_indices) > 0:
        num_samples = max(1, int(len(node_indices) * sample_ratio))
        node_indices = node_indices[torch.randperm(len(node_indices))[:num_samples]]

    print(f"Valid nodes in subgraphs: {len(node_indices)}")

    return node_indices


def sample_edges_by_graph_ids(full_graph, graph_indices, sample_ratio, balanced=True):
    """Sample edges by graph IDs with optional per-graph balancing.

    Args:
        full_graph: Full graph data object
        graph_indices: List of graph IDs to sample from
        sample_ratio: Sampling ratio (0-1), 1.0 means no sampling
        balanced: If True, sample evenly from each graph; otherwise sample globally

    Returns:
        Tensor of edge indices
    """
    device = full_graph.graph_edges_ids.device

    if balanced:
        # Sample evenly from each subgraph to avoid large graphs dominating
        selected = []
        for gid in graph_indices:
            mask = (full_graph.graph_edges_ids == gid) & full_graph.valid_mask
            edge_indices = torch.where(mask)[0]

            if sample_ratio < 1.0 and edge_indices.numel() > 0:
                n = max(1, int(edge_indices.numel() * sample_ratio))
                perm = torch.randperm(edge_indices.numel(), device=device)
                edge_indices = edge_indices[perm[:n]]

            if edge_indices.numel() > 0:
                selected.append(edge_indices)

        if len(selected) == 0:
            return torch.empty(0, dtype=torch.long, device=device)

        return torch.cat(selected, dim=0)
    else:
        # Global sampling: create mask for all specified graphs and sample
        mask = torch.isin(
            full_graph.graph_edges_ids,
            torch.tensor(graph_indices, dtype=torch.long, device=device)
        ) & full_graph.valid_mask

        edge_indices = torch.where(mask)[0]

        if sample_ratio < 1.0 and edge_indices.numel() > 0:
            n = max(1, int(edge_indices.numel() * sample_ratio))
            perm = torch.randperm(edge_indices.numel(), device=device)
            edge_indices = edge_indices[perm[:n]]

        return edge_indices


def dataset_sampling(args, dataset):
    """Sample dataset and create train/val/test data loaders.

    This function:
    1. Splits graphs into train/val/test sets
    2. Samples nodes or edges based on task_level
    3. Creates NeighborLoader for node tasks or LinkNeighborLoader for edge tasks
    4. Computes and prints label distribution statistics

    Args:
        args: Configuration arguments
        dataset: MergedHomographDataset object

    Returns:
        Tuple of (train_loader, val_loader, test_loader, max_label)
    """
    # Plot true value distribution (disabled by default)
    # plot_true_values_distribution_before_sampling(dataset, args.dataset)
    print(f"============================={os.getpid()}=============================")
    print(f"g: {dataset[0]}")

    # Get full graph
    full_graph = dataset[0]
    print(f"Full graph statistics - nodes: {full_graph.num_nodes}, edges: {full_graph.num_edges}")

    # Get normalization parameters (for visualization only)
    norm_params = full_graph.norm_params if hasattr(full_graph, 'norm_params') else None

    # Select graph_ids based on task_level
    if args.task_level == 'node':
        all_graph_ids = torch.unique(full_graph.graph_ids).numpy()
    elif args.task_level == 'edge':
        all_graph_ids = torch.unique(full_graph.graph_edges_ids).numpy()
    else:
        raise ValueError(f"Unknown task_level: {args.task_level}")

    print(f"All graph IDs: {all_graph_ids}")

    # Split into train/val/test graph IDs
    if args.random_test:
        # Random split for testing
        test_ratio = 0.2
        all_graph_ids_shuffled = all_graph_ids.copy()
        np.random.shuffle(all_graph_ids_shuffled)
        test_size = int(len(all_graph_ids_shuffled) * test_ratio)
        train_val_graph_ids = all_graph_ids_shuffled[test_size:]
        test_graph_ids = all_graph_ids_shuffled[:test_size]
    else:
        # Fixed split for reproducibility (as in paper)
        test_graph_ids = dataset.test_graph_ids
        train_val_graph_ids = [gid for gid in all_graph_ids if gid not in test_graph_ids]

    # Split train/val (fixed validation set as in paper)
    val_ratio = 0.2
    val_size = int(len(train_val_graph_ids) * val_ratio)
    np.random.shuffle(train_val_graph_ids)

    # Fixed validation set IDs (as used in paper)
    val_graph_ids = [5, 14, 21, 24, 26]
    train_graph_ids = [gid for gid in train_val_graph_ids if gid not in val_graph_ids]

    print(f"Train graph IDs: {train_graph_ids}")
    print(f"Val graph IDs: {val_graph_ids}")
    print(f"Test graph IDs: {test_graph_ids}")

    if args.task_level == 'node':
        # ===== NODE-LEVEL TASKS =====
        # Sample valid node indices
        train_node_ind = get_nodes_from_subgraphs(
            full_graph, train_graph_ids, args.sample_ratio, task_level=args.task_level
        )
        val_node_ind = get_nodes_from_subgraphs(
            full_graph, val_graph_ids, args.sample_ratio, task_level=args.task_level
        )
        test_node_ind = get_nodes_from_subgraphs(
            full_graph, test_graph_ids, args.sample_ratio, task_level=args.task_level
        )

        print(f"Train nodes: {len(train_node_ind)}")
        print(f"Val nodes: {len(val_node_ind)}")
        print(f"Test nodes: {len(test_node_ind)}")

        # Print label distribution (node-level)
        train_labels = full_graph.y[train_node_ind].numpy()
        val_labels = full_graph.y[val_node_ind].numpy()
        test_labels = full_graph.y[test_node_ind].numpy()

        train_labels_raw = print_distribution_stats(train_labels, "Train", norm_params)
        val_labels_raw = print_distribution_stats(val_labels, "Val", norm_params)
        test_labels_raw = print_distribution_stats(test_labels, "Test", norm_params)

        # Create NeighborLoader for node-level tasks
        train_loader = NeighborLoader(
            full_graph,
            num_neighbors=args.num_hops * [args.num_neighbors],
            input_nodes=train_node_ind,
            batch_size=args.batch_size,
            shuffle=True,
            num_workers=args.num_workers,
        )

        val_loader = NeighborLoader(
            full_graph,
            num_neighbors=args.num_hops * [args.num_neighbors],
            input_nodes=val_node_ind,
            batch_size=args.batch_size,
            shuffle=False,
            num_workers=args.num_workers,
        )

        test_loader = NeighborLoader(
            full_graph,
            num_neighbors=args.num_hops * [args.num_neighbors],
            input_nodes=test_node_ind,
            batch_size=args.batch_size,
            shuffle=False,
            num_workers=args.num_workers,
        )

    else:
        # ===== EDGE-LEVEL TASKS =====
        # Sample edge indices (filtered by graph_ids and valid_mask)
        train_edge_ind = sample_edges_by_graph_ids(full_graph, train_graph_ids, args.sample_ratio)
        val_edge_ind = sample_edges_by_graph_ids(full_graph, val_graph_ids, args.sample_ratio)
        test_edge_ind = sample_edges_by_graph_ids(full_graph, test_graph_ids, args.sample_ratio)

        print(f"train_graph_ids: {torch.unique(torch.as_tensor(train_graph_ids)).tolist()}")
        print(f"val_graph_ids: {torch.unique(torch.as_tensor(val_graph_ids)).tolist()}")
        print(f"test_graph_ids: {torch.unique(torch.as_tensor(test_graph_ids)).tolist()}")
        print(f"Valid nodes mask: {full_graph.valid_mask.sum()}")

        # Supervision edges (keep directed)
        train_edge_label_index = full_graph.edge_index[:, train_edge_ind]
        val_edge_label_index = full_graph.edge_index[:, val_edge_ind]
        test_edge_label_index = full_graph.edge_index[:, test_edge_ind]

        # Extract edge labels
        if args.task == 'regression':
            train_edge_label = full_graph.y[train_edge_ind].to(torch.float)
            val_edge_label = full_graph.y[val_edge_ind].to(torch.float)
            test_edge_label = full_graph.y[test_edge_ind].to(torch.float)
        else:
            train_edge_label = full_graph.y[train_edge_ind].to(torch.long)
            val_edge_label = full_graph.y[val_edge_ind].to(torch.long)
            test_edge_label = full_graph.y[test_edge_ind].to(torch.long)

        # Create undirected base graph for GNN (mean aggregation to get unique edges)
        from torch_geometric.utils import to_undirected
        full_graph_undir = full_graph.clone()

        # Option 1: Aggregate by mean to get unique undirected edges
        ei_u, ea_u = to_undirected(
            full_graph.edge_index,
            edge_attr=full_graph.edge_attr,
            num_nodes=full_graph.num_nodes,
            reduce='mean'
        )

        # Option 2: Keep both directions by duplication (currently used)
        full_graph_undir.edge_index = torch.cat([full_graph.edge_index, full_graph.edge_index.flip(0)], dim=1)
        full_graph_undir.edge_attr = torch.cat([full_graph.edge_attr, full_graph.edge_attr], dim=0)

        print(f"[debug] base_edges_before={full_graph.edge_index.size(1)}, base_edges_after={full_graph_undir.edge_index.size(1)}")
        print(f"Train edges: {train_edge_label.numel()}")
        print(f"Val edges: {val_edge_label.numel()}")
        print(f"Test edges: {test_edge_label.numel()}")

        # Print label distribution (edge-level)
        train_labels_raw = print_distribution_stats(train_edge_label.cpu().numpy(), "Train", norm_params)
        val_labels_raw = print_distribution_stats(val_edge_label.cpu().numpy(), "Val", norm_params)
        test_labels_raw = print_distribution_stats(test_edge_label.cpu().numpy(), "Test", norm_params)

        # Create LinkNeighborLoader for edge-level tasks
        # Base graph is undirected, supervision edges are directed
        train_loader = LinkNeighborLoader(
            data=full_graph_undir,
            edge_label_index=train_edge_label_index,
            edge_label=train_edge_label,
            num_neighbors=args.num_hops * [args.num_neighbors],
            batch_size=args.batch_size,
            neg_sampling_ratio=0.0,
            shuffle=True,
            num_workers=args.num_workers,
        )

        val_loader = LinkNeighborLoader(
            data=full_graph_undir,
            edge_label_index=val_edge_label_index,
            edge_label=val_edge_label,
            num_neighbors=args.num_hops * [args.num_neighbors],
            batch_size=args.batch_size,
            neg_sampling_ratio=0.0,
            shuffle=False,
            num_workers=args.num_workers,
        )

        test_loader = LinkNeighborLoader(
            data=full_graph_undir,
            edge_label_index=test_edge_label_index,
            edge_label=test_edge_label,
            num_neighbors=args.num_hops * [args.num_neighbors],
            batch_size=args.batch_size,
            neg_sampling_ratio=0.0,
            shuffle=False,
            num_workers=args.num_workers,
        )

    # Compute max label (only considering valid labels)
    max_label = full_graph.y[full_graph.valid_mask].max().item() if full_graph.valid_mask.any() else 0

    return (train_loader, val_loader, test_loader, max_label)
