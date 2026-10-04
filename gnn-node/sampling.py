# sampling.py
"""
R2G: Multi-View Circuit Graph Benchmark Suite - Data Sampling Module

Data sampling module, responsible for splitting large circuit graphs into train/validation/test sets and creating data loaders.

Core Functions:
1. Graph-level splitting: Split train/validation/test sets based on graph_id
2. Node sampling: Sample valid nodes from specified subgraphs (label != -1)
3. Neighbor sampling: Use NeighborLoader for mini-batch training
4. Distribution visualization: Plot label distribution histograms

Splitting Strategy:
- Test set: Determined by CLI parameters (fixed IDs or random split)
- Validation set: Fixed ID list or random split by ratio
- Training set: Remaining subgraphs

Neighbor Sampling Parameters:
- num_hops: Number of sampling hops (default 2)
- num_neighbors: Number of neighbors per hop (default 10)
- batch_size: Batch size (default 1024)
"""

import numpy as np
import torch
import matplotlib.pyplot as plt
import os
from torch_geometric.loader import NeighborLoader
from dataset import inverse_standardize, inverse_log_transform, NODE_TYPES


# ============================================================
# Utility functions
# ============================================================

def print_distribution_stats(labels, name, norm_params=None):
    """
    Print label distribution statistics, supports denormalization

    Args:
        labels: Label array
        name: Dataset name
        norm_params: Standardization parameters (for denormalization)

    Returns:
        Denormalized labels
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
    """
    Plot true value distribution of original dataset

    Args:
        dataset: MergedHomographDataset object
        dataset_name: Dataset name
    """
    if not dataset:
        print("Dataset is empty, cannot plot distribution")
        return

    full_graph = dataset[0]
    norm_params = full_graph.norm_params if hasattr(full_graph, 'norm_params') else None

    # Get valid labels (nodes with label != -1)
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

    # Save image
    if not os.path.exists('plots'):
        os.makedirs('plots')
    plt.savefig(os.path.join('plots', f'{dataset_name}_true_values_dist.png'))
    plt.close()


def get_nodes_from_subgraphs(full_graph, graph_indices, sample_ratio, node_type=None):
    """
    Get node indices from specified subgraphs, can sample by ratio

    Args:
        full_graph: Complete graph data
        graph_indices: graph_id list of subgraphs
        sample_ratio: Sampling ratio (1.0 means sample all)
        node_type: Node type filter (currently unused)

    Returns:
        Sampled node index list
    """
    # Get nodes in specified subgraphs
    mask = torch.isin(full_graph.graph_ids, torch.tensor(graph_indices, dtype=torch.long, device=full_graph.graph_ids.device))

    # Only keep valid nodes (label != -1)
    mask = mask & full_graph.valid_mask

    node_indices = torch.where(mask)[0]

    # Sample by ratio
    if sample_ratio < 1.0 and len(node_indices) > 0:
        num_samples = max(1, int(len(node_indices) * sample_ratio))
        node_indices = node_indices[torch.randperm(len(node_indices))[:num_samples]]

    return node_indices


# ============================================================
# Main sampling function
# ============================================================

def dataset_sampling(args, dataset):
    """
    Sample homograph dataset and create train, validation, and test data loaders

    Splitting Strategy:
    - Test set: Provided by dataset.test_graph_ids (already decided in dataset.py based on CLI)
      If args.fixed_test_ids_list is provided, use that fixed list;
      Otherwise, if args.random_test is True, randomly split by args.test_ratio;
      Otherwise, use default fixed list.
    - Validation set:
      If args.fixed_val_ids_list is provided, use that list as validation set in train+val candidate pool;
      Otherwise, randomly split by args.val_ratio in candidate pool.
    - Training set: Remaining subgraphs in candidate pool after removing validation set.

    Args:
        args: Command line arguments
        dataset: MergedHomographDataset object

    Returns:
        train_loader, val_loader, test_loader, max_label
    """
    # Plot true value distribution
    plot_true_values_distribution_before_sampling(dataset, args.dataset)

    # Get full graph data
    full_graph = dataset[0]
    print(f"Full graph statistics - nodes: {full_graph.num_nodes}, edges: {full_graph.num_edges}")

    # Get normalization parameters
    norm_params = full_graph.norm_params if hasattr(full_graph, 'norm_params') else None

    # Get all unique graph_ids
    all_graph_ids = torch.unique(full_graph.graph_ids).numpy()
    print(f"All graph IDs: {all_graph_ids}")

    # Test set provided by dataset.test_graph_ids
    test_graph_ids = list(dataset.test_graph_ids)
    train_val_graph_ids = [gid for gid in all_graph_ids if gid not in test_graph_ids]

    # Validation set split: fixed list or random ratio
    fixed_val_list = getattr(args, 'fixed_val_ids_list', []) or []
    if len(fixed_val_list) > 0:
        # Fixed validation set IDs
        val_graph_ids = [gid for gid in fixed_val_list if gid in train_val_graph_ids]
        if len(val_graph_ids) == 0 and len(train_val_graph_ids) > 0:
            val_graph_ids = [train_val_graph_ids[0]]
        train_graph_ids = [gid for gid in train_val_graph_ids if gid not in val_graph_ids]
    else:
        # Random ratio split, at least 1 subgraph
        rng = np.random.default_rng(args.seed)
        shuffled = train_val_graph_ids.copy()
        rng.shuffle(shuffled)
        val_size = max(1, int(len(shuffled) * args.val_ratio)) if len(shuffled) > 0 else 0
        val_graph_ids = shuffled[:val_size]
        train_graph_ids = shuffled[val_size:]

    print(f"Train subgraph IDs: {train_graph_ids}")
    print(f"Val subgraph IDs: {val_graph_ids}")
    print(f"Test subgraph IDs: {test_graph_ids}")

    # Sample valid nodes (based on valid_mask)
    train_node_ind = get_nodes_from_subgraphs(
        full_graph, train_graph_ids, args.sample_ratio
    )
    val_node_ind = get_nodes_from_subgraphs(
        full_graph, val_graph_ids, args.sample_ratio
    )
    test_node_ind = get_nodes_from_subgraphs(
        full_graph, test_graph_ids, args.sample_ratio
    )

    print(f"Train node count: {len(train_node_ind)}")
    print(f"Val node count: {len(val_node_ind)}")
    print(f"Test node count: {len(test_node_ind)}")

    # Extract label distributions
    train_labels = full_graph.y[train_node_ind].numpy()
    val_labels = full_graph.y[val_node_ind].numpy()
    test_labels = full_graph.y[test_node_ind].numpy()

    # Print distribution statistics (convert back to original space)
    train_labels_raw = print_distribution_stats(train_labels, "Train", norm_params)
    val_labels_raw = print_distribution_stats(val_labels, "Val", norm_params)
    test_labels_raw = print_distribution_stats(test_labels, "Test", norm_params)

    # Plot label distribution histograms
    plt.figure(figsize=(12, 4))
    plt.subplot(131)
    plt.hist(train_labels_raw, bins=20, alpha=0.5, label='Train')
    plt.title('Train Label Distribution')
    plt.xlabel('Label Value')
    plt.ylabel('Count')
    plt.legend()

    plt.subplot(132)
    plt.hist(val_labels_raw, bins=20, alpha=0.5, label='Val', color='orange')
    plt.title('Val Label Distribution')
    plt.xlabel('Label Value')
    plt.ylabel('Count')
    plt.legend()

    plt.subplot(133)
    plt.hist(test_labels_raw, bins=20, alpha=0.5, label='Test', color='green')
    plt.title('Test Label Distribution')
    plt.xlabel('Label Value')
    plt.ylabel('Count')
    plt.legend()

    plt.tight_layout()
    os.makedirs(args.save_dir, exist_ok=True)
    plt.savefig(os.path.join(args.save_dir, 'label_distribution.png'))
    plt.close()

    # Create data loaders (using neighbor sampling)
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

    # Calculate max label value (only consider valid labels)
    max_label = full_graph.y[full_graph.valid_mask].max().item() if full_graph.valid_mask.any() else 0

    return train_loader, val_loader, test_loader, max_label
