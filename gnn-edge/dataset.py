# dataset.py
# Dataset class for loading and preprocessing R2G circuit graph data.
# This module handles data loading, normalization, and preprocessing for circuit graphs.

import os
from typing_extensions import Self
import torch
import logging
import numpy as np
import matplotlib.pyplot as plt
from torch_geometric.data import InMemoryDataset, Data
from torch_geometric.utils import to_undirected

# Node type constant definitions
# Maps node type names to integer IDs for encoding
NODE_TYPES = {
    'gate': 0,      # Logic gate
    'io_pin': 1,    # I/O pin
    'net': 2,       # Electrical net
    'pin': 3        # Pin
}

# Edge type constant definitions
# Maps edge type names to integer IDs for encoding
EDGE_TYPES = {
    'gate_gate': 0,        # Gate connects to gate
    'gate_net': 1,         # Gate connects to net
    'gate_pin': 2,         # Gate has pin
    'io_pin_gate': 3,      # I/O pin connects to gate
    'io_pin_net': 4,       # I/O pin connects to net
    'io_pin_pin': 5,       # I/O pin connects to pin
    'pin_net': 6,          # Pin connects to net
    'pin_pin': 7,          # Pin connects to pin
    'pin_gate_pin': 8      # Pin gate connects to pin
}


def log_transform(x, epsilon=1e-8):
    """Apply log transformation to handle long-tail distribution.

    Args:
        x: Input array
        epsilon: Small constant to avoid log(0)

    Returns:
        Log-transformed array
    """
    return np.log(x + epsilon)


def standardize(x, mean, std, epsilon=1e-8):
    """Standardize data using mean and standard deviation.

    Args:
        x: Input array
        mean: Mean for standardization
        std: Standard deviation for standardization
        epsilon: Small constant to avoid division by zero

    Returns:
        Standardized array
    """
    return (x - mean) / (std + epsilon)


def inverse_standardize(x, mean, std, epsilon=1e-8):
    """Inverse of standardization.

    Args:
        x: Standardized array
        mean: Mean used for standardization
        std: Standard deviation used for standardization
        epsilon: Small constant

    Returns:
        Original scale array
    """
    return x * (std + epsilon) + mean


def inverse_log_transform(x, epsilon=1e-8):
    """Inverse of log transformation.

    Args:
        x: Log-transformed array
        epsilon: Small constant used in log transform

    Returns:
        Original scale array
    """
    return np.exp(x) - epsilon


class MergedHomographDataset(InMemoryDataset):
    """Dataset class for loading processed circuit graph data.

    This class handles:
    1. Loading raw data from disk
    2. Applying normalization (log transform + standardization) for regression tasks
    3. Adding global features to nodes and edges
    4. Creating train/val/test masks
    5. Caching processed data for faster reload

    The dataset supports both node-level and edge-level tasks.
    """

    def __init__(
            self,
            root,
            dataset_name,  # Dataset name (e.g., place_merged_B_homograph)
            args=None,
            to_undirected=True,
            task_level='node',
            task_type='regression',
            transform=None,
            pre_transform=None
    ) -> None:
        """Initialize the dataset.

        Args:
            root: Root directory for the dataset
            dataset_name: Name of the dataset to load
            args: Configuration arguments
            to_undirected: Whether to convert graph to undirected (deprecated)
            task_level: Task level (node/edge/graph)
            task_type: Task type (regression/classification)
            transform: Optional transform to apply to data
            pre_transform: Optional pre-transform to apply to data
        """
        self.args = args
        self.name = dataset_name  # Use the provided dataset name
        self.task_type = task_type
        self.task_level = task_level
        self.to_undirected = to_undirected

        # Test set graph IDs (fixed split for reproducibility)
        self.test_graph_ids = [25, 27, 29, 6, 20, 15, 7, 13]

        super().__init__(root, transform, pre_transform)

        # Load processed data if available, otherwise process it
        if os.path.exists(self.processed_paths[0]):
            self.data, self.slices = torch.load(self.processed_paths[0], weights_only=False)
        else:
            self.process()
            self.data, self.slices = torch.load(self.processed_paths[0], weights_only=False)

    def process(self):
        """Process raw data and save to disk.

        This method:
        1. Loads raw data from disk
        2. Applies normalization (log transform + standardization) using only training data
        3. Concatenates global features to node/edge features
        4. Creates train/val/test masks
        5. Saves processed data for fast reload

        Key: Normalization parameters are computed ONLY from training data to avoid data leakage.
        """
        # Load raw data from data/raw/ directory (not PyG's self.raw_dir)
        raw_data_path = os.path.join(self.root, 'raw', f"{self.name}.pt")
        if not os.path.exists(raw_data_path):
            raise FileNotFoundError(f"Raw data file {raw_data_path} does not exist")

        # Load all data components
        data_components = torch.load(raw_data_path, weights_only=False, map_location='cpu')
        print(f"Raw data components: {data_components}")
        print(f"")

        # Extract components
        x = data_components['x']  # Node features [N, 10]
        if data_components['y'].ndim == 1:
            y = data_components['y']  # Node/edge labels
        elif data_components['y'].ndim == 2:
            y = data_components['y'][:, 0]  # Take first column if 2D

        # IMPORTANT: If raw data already contains norm_params, check if y is already normalized
        # If norm_params exists but is incomplete (missing mean/std), we need to re-normalize
        y_needs_normalization = True
        if 'norm_params' in data_components:
            norm_params = data_components['norm_params']
            if 'mean' in norm_params and 'std' in norm_params:
                # norm_params is complete, y might already be normalized
                print(f"Raw data contains complete norm_params, y may already be normalized")
                y_needs_normalization = False
            else:
                # norm_params is incomplete, need to re-normalize
                print(f"Raw data contains incomplete norm_params, re-normalizing...")

        # Valid label mask BEFORE normalization (keep -1 sentinel semantics)
        # Valid nodes/edges are those with labels != -1
        valid_mask_raw = (y != -1)

        # Valid label mask BEFORE normalization (keep -1 sentinel semantics)
        # Valid nodes/edges are those with labels != -1
        valid_mask_raw = (y != -1)

        edge_index = data_components['edge_index']  # Edge indices [2, E]
        edge_attr = data_components['edge_attr']  # Edge features [E, 10]
        global_features = data_components['global_features']  # Global features [G, 6]
        global_y = data_components['global_y']  # Global labels [G, 4]
        die_coordinates = data_components['die_coordinates']  # Die coordinates [G, 2, 2]

        # Determine train/test masks and valid node masks
        # Graph ID is in column 8 (0-indexed) of x
        graph_ids = x[:, 8].long()
        node_types = x[:, 9].long()  # Node type in column 9
        edge_types = edge_attr[:, 9].long()  # Edge type in column 9

        # Compute graph ID for each edge (use source node's graph_id)
        try:
            src_nodes = edge_index[0]
            dst_nodes = edge_index[1]
            graph_edges_ids = graph_ids[src_nodes]
            # Warn if there are cross-graph edges
            mismatch_edges = (graph_ids[src_nodes] != graph_ids[dst_nodes]).sum().item()
            if mismatch_edges > 0:
                logging.warning(f"Found {mismatch_edges} cross-graph edges; using src graph_id for graph_edges_ids.")
        except Exception as e:
            logging.warning(f"Failed to compute graph_edges_ids: {e}")

        # Remove graph_id column from node features, keep node_type
        x = torch.cat([x[:, :8], x[:, 9:]], dim=1)  # Concatenate columns 0-7 and 9

        # Remove graph_id column from edge features, keep edge_type
        edge_attr = torch.cat([edge_attr[:, :8], edge_attr[:, 9:]], dim=1)

        # Create train/test masks based on task level
        if self.task_level == 'node':
            # Train mask: graphs NOT in test set
            train_mask = torch.tensor([gid not in self.test_graph_ids for gid in graph_ids], dtype=torch.bool)
        elif self.task_level == 'edge':
            # Train mask: edges NOT in test graphs
            train_mask = torch.tensor([gid not in self.test_graph_ids for gid in graph_edges_ids], dtype=torch.bool)

        # Valid node mask: labels != -1
        valid_mask = (y != -1)
        print(f"Valid nodes mask: {valid_mask.sum()}, mask size: {valid_mask.size()}")

        # Valid nodes in training set (only use these to compute normalization parameters)
        train_valid_mask = train_mask & valid_mask

        # Log transform + standardization (for regression tasks only)
        if self.task_type == 'regression' and y_needs_normalization:
            # Extract training set valid labels
            y_train_valid = y[train_valid_mask].numpy()

            # Extract all labels to compute global minimum
            all_y = y.numpy()

            # Handle zero or negative values: compute offset based on global minimum
            global_min = all_y.min()
            epsilon = 1e-8
            if global_min <= 0:
                offset = -global_min + epsilon  # Ensure all values + offset > 0
                y_train_valid = y_train_valid + offset  # Offset training labels
                y_np = all_y + offset  # Offset all data
            else:
                offset = 0
                y_np = all_y

            # Apply log transform to training set to handle long-tail distribution
            y_train_log = log_transform(y_train_valid, epsilon)

            # Check for NaN after log transform
            if np.isnan(y_train_log).any():
                raise ValueError("Log transform on training data resulted in NaN!")

            # Compute mean and std of log-transformed training data
            train_mean = y_train_log.mean()
            train_std = y_train_log.std()

            # Validate normalization parameters
            if train_std == 0:
                raise ValueError("Training std is zero! All training labels are identical.")
            if np.isnan(train_mean) or np.isnan(train_std):
                raise ValueError("Normalization parameters contain NaN!")

            # Apply log transform then standardization to all data
            y_log = log_transform(y_np, epsilon)
            if np.isnan(y_log).any():
                raise ValueError("Log transform on all data resulted in NaN! Check offset calculation.")

            y_normalized = standardize(y_log, train_mean, train_std)

            # Convert back to tensor
            y = torch.tensor(y_normalized, dtype=torch.float32)

            # Check for NaN after normalization
            if torch.isnan(y).any():
                raise ValueError("Normalized y contains NaN values!")

            # Save normalization parameters for inverse transform during evaluation
            self.normalization_params = {
                'offset': offset,
                'epsilon': epsilon,
                'mean': train_mean,
                'std': train_std
            }
        elif self.task_type == 'regression' and not y_needs_normalization:
            # y is already normalized, use existing norm_params from raw data
            print("Using existing normalization from raw data")
            norm_params = data_components.get('norm_params', {})
            self.normalization_params = {
                'offset': norm_params.get('offset', 0),
                'epsilon': norm_params.get('epsilon', 1e-8),
                'mean': norm_params.get('mean', 0),
                'std': norm_params.get('std', 1)
            }
            # Ensure y is a float32 tensor
            if not isinstance(y, torch.Tensor):
                y = torch.tensor(y, dtype=torch.float32)
            elif y.dtype != torch.float32:
                y = y.float()
        else:
            # Classification: keep labels as-is (assumed to be integers)
            self.normalization_params = None

        # Concatenate global features to node features
        global_graph_ids = global_features[:, -1].long()  # Graph ID is last column of global features
        global_features_valid = global_features[:, :-1]  # Valid global features (all except last column, 5 dims)

        # Create mapping from graph_id to global features
        graph_id_to_global = {
            gid.item(): feat for gid, feat in zip(global_graph_ids, global_features_valid)
        }

        # Match each node to its graph's global features
        node_graph_ids = graph_ids
        node_global_features = torch.zeros((x.shape[0], global_features_valid.shape[1]), device=x.device)
        for i, gid in enumerate(node_graph_ids):
            node_global_features[i] = graph_id_to_global[gid.item()]

        # Match each edge to its graph's global features
        edge_global_features = torch.zeros(
            (edge_index.size(1), global_features_valid.shape[1]),
            device=edge_attr.device
        )
        for i, gid in enumerate(graph_edges_ids):
            edge_global_features[i] = graph_id_to_global[gid.item()]

        # Concatenate node features (9 dims) with global features (5 dims) -> 14 dims
        x = torch.cat([x, node_global_features[:, :6]], dim=1)

        # Concatenate edge features (9 dims) with global features (5 dims) -> 14 dims
        edge_attr = torch.cat([edge_attr, edge_global_features[:, :6]], dim=1)

        print(f"x[:, 8].unique(): {x[:, 8].unique()}")
        print(f"edge_attr[:, 8].unique(): {edge_attr[:, 8].unique()}")

        # Create data object
        data = Data(
            x=x,
            y=y,
            edge_index=edge_index,
            edge_attr=edge_attr,
            global_features=global_features,
            global_y=global_y,
            die_coordinates=die_coordinates
        )

        print(f"Processed data: {data}")

        # Recompute edge types and graph_edges_ids for undirected graph (if needed)
        edge_types = data.edge_attr[:, 8].long() if data.edge_attr is not None else edge_types
        data.edge_types = edge_types
        graph_edges_ids = graph_ids[data.edge_index[0]]

        # Create test/train masks
        if self.task_level == 'node':
            # Test mask: graphs in test set
            data.test_mask = torch.tensor([gid in self.test_graph_ids for gid in graph_ids], dtype=torch.bool)
        elif self.task_level == 'edge':
            # Test mask: edges in test graphs
            data.test_mask = torch.tensor([gid in self.test_graph_ids for gid in graph_edges_ids], dtype=torch.bool)

        data.train_mask = ~data.test_mask

        # Save graph IDs
        data.graph_ids = graph_ids
        data.graph_edges_ids = graph_edges_ids

        # Valid label mask (use original mask before normalization)
        valid_mask = valid_mask_raw
        print(f"Valid nodes mask: {valid_mask.sum()}, mask size: {valid_mask.size()}")

        data.valid_mask = valid_mask

        # Save node types and normalization parameters
        data.node_types = node_types
        data.norm_params = self.normalization_params if hasattr(self, 'normalization_params') else None

        # Save processed data
        torch.save(self.collate([data]), self.processed_paths[0])

    @property
    def raw_dir(self) -> str:
        """Directory containing raw data files."""
        return os.path.join(self.root, self.name, 'raw')

    @property
    def processed_dir(self) -> str:
        """Directory containing processed data files."""
        return os.path.join(self.root, self.name, f'processed_{self.task_level}_{self.task_type}')

    @property
    def raw_file_names(self):
        """Raw data file names."""
        return [f'{self.name}.pt']

    @property
    def processed_file_names(self):
        """Processed data file names."""
        return [f'{self.name}_processed.pt']
