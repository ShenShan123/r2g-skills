# dataset.py
"""
R2G: Multi-View Circuit Graph Benchmark Suite - Dataset Module

Data loading and preprocessing module, responsible for loading, preprocessing, and splitting circuit graph data converted from DEF files.

Core Functions:
1. Load circuit graph data (nodes, edges, features, labels)
2. Define node and edge type constants
3. Implement data standardization (log transform + z-score standardization)
4. Train/validation/test set split
5. Mark valid nodes (nodes with label != -1)

Data Format:
- Nodes: gate, io_pin, net, pin (4 types)
- Edges: 8 edge types (gate-gate, gate-net, gate-pin, io_pin-gate, etc.)
- Labels: Node-level (placement: HPWL) or edge-level (routing: wire_length, via_count)

Standardization Process (regression task):
1. Offset: label += offset (ensure all values > 0)
2. Log transform: log(label + epsilon) (handle long-tail distribution)
3. Standardization: (log_label - mean) / std (z-score standardization)
4. Inverse transform for evaluation: pred -> denormalize -> exp -> subtract offset

Note: Normalization parameters are only calculated from training set to avoid data leakage.
"""

import os
import torch
import logging
import numpy as np
from torch_geometric.data import InMemoryDataset, Data
from torch_geometric.utils import to_undirected

# ============================================================
# Node type definitions (4 types)
# ============================================================
NODE_TYPES = {
    'gate': 0,      # Logic gate unit
    'io_pin': 1,    # I/O pin (top-level port)
    'net': 2,        # Network (electrical network connecting multiple nodes)
    'pin': 3         # Input/output pin of logic gate
}

# ============================================================
# Edge type definitions (8 types)
# ============================================================
EDGE_TYPES = {
    'gate_gate': 0,       # Gate-gate connection
    'gate_net': 1,        # Gate-network connection
    'gate_pin': 2,        # Gate-pin connection
    'io_pin_gate': 3,     # IO pin-gate connection
    'io_pin_net': 4,      # IO pin-network connection
    'io_pin_pin': 5,      # IO pin-pin connection
    'pin_net': 6,         # Pin-network connection
    'pin_pin': 7,         # Pin-pin connection
    'pin_gate_pin': 8      # Pin-gate pin connection
}


# ============================================================
# Data standardization functions
# ============================================================

def log_transform(x, epsilon=1e-8):
    """
    Log transform: handle long-tail distribution (typical characteristic of circuit metrics)

    Args:
        x: Input data
        epsilon: Small constant to avoid log(0)

    Returns:
        log(x + epsilon)
    """
    return np.log(x + epsilon)


def standardize(x, mean, std, epsilon=1e-8):
    """
    z-score standardization

    Args:
        x: Input data
        mean: Training set mean
        std: Training set standard deviation
        epsilon: Small constant to avoid division by zero

    Returns:
        (x - mean) / (std + epsilon)
    """
    return (x - mean) / (std + epsilon)


def inverse_standardize(x, mean, std, epsilon=1e-8):
    """
    Inverse of standardization

    Args:
        x: Standardized data
        mean: Training set mean
        std: Training set standard deviation
        epsilon: Small constant to avoid division by zero

    Returns:
        x * (std + epsilon) + mean
    """
    return x * (std + epsilon) + mean


def inverse_log_transform(x, epsilon=1e-8):
    """
    Inverse of log transform

    Args:
        x: Log-transformed data
        epsilon: Small constant used during log transform

    Returns:
        exp(x) - epsilon
    """
    return np.exp(x) - epsilon


# ============================================================
# Dataset class
# ============================================================

class MergedHomographDataset(InMemoryDataset):
    """
    Homograph circuit graph dataset

    Functions:
    1. Load preprocessed .pt files from raw/ directory
    2. Calculate and apply standardization (using only training set)
    3. Split train/validation/test sets
    4. Mark valid nodes (label != -1)
    5. Convert directed edges to undirected edges

    Data Layout:
    - raw/<dataset_name>.pt: Preprocessed raw data
    - processed_<task_level>_<task_type>/<dataset_name>_processed.pt: Processed data
    """
    
    def __init__(
            self,
            root,                         # Data root directory
            dataset_name,                  # Dataset name, e.g., 'route_B_homograph'
            args=None,                    # Command line arguments (containing test set split configuration)
            to_undirected=True,           # Whether to convert to undirected graph
            task_level='node',             # Task level: 'node' or 'edge'
            task_type='regression',        # Task type: 'regression' or 'classification'
            transform=None,
            pre_transform=None
    ) -> None:
        self.args = args
        self.name = dataset_name
        self.base_name = os.path.splitext(self.name)[0]  # Remove extension
        self.task_type = task_type
        self.task_level = task_level
        self.to_undirected = to_undirected

        # Use absolute path as root directory
        root_abs = os.path.abspath(root)

        # Test set graph_id list (determined in process())
        self.test_graph_ids = None

        # Call parent class initialization (will automatically call process() or load processed data)
        super().__init__(root_abs, transform, pre_transform)

        # Load processed data
        processed_exists = os.path.exists(self.processed_paths[0])
        if processed_exists:
            data_loaded, slices_loaded = torch.load(self.processed_paths[0], weights_only=False)

            # Check if data split matches current CLI parameters
            all_graph_ids_unique = torch.unique(data_loaded.graph_ids).cpu().numpy().tolist()
            desired_test_ids = self._decide_test_ids(all_graph_ids_unique)
            existing_meta = getattr(data_loaded, 'split_meta', None)
            existing_test_ids = existing_meta.get('test_graph_ids', []) if isinstance(existing_meta, dict) else None

            # If split doesn't match, reprocess
            if existing_test_ids is None or sorted(existing_test_ids) != sorted(desired_test_ids):
                logging.info("Processed dataset split differs from CLI; reprocessing...")
                self.process()
                data_loaded, slices_loaded = torch.load(self.processed_paths[0], weights_only=False)

            self.data, self.slices = data_loaded, slices_loaded
            meta = getattr(self.data, 'split_meta', None)
            self.test_graph_ids = meta.get('test_graph_ids', desired_test_ids) if isinstance(meta, dict) else desired_test_ids
        else:
            # First time processing
            self.process()
            self.data, self.slices = torch.load(self.processed_paths[0], weights_only=False)

    def process(self):
        """
        Process raw data and save

        Processing steps:
        1. Load raw .pt file
        2. Determine train/test split
        3. Calculate standardization parameters (using only training set valid nodes)
        4. Apply standardization (log transform + z-score)
        5. Concatenate global features to node features
        6. Create undirected graph edges
        7. Save processed data
        """
        # Load raw data file
        raw_data_path = os.path.join(self.root, 'raw', f'{self.base_name}.pt')
        if not os.path.exists(raw_data_path):
            raise FileNotFoundError(f"Raw data file not found: {raw_data_path}")
        logging.info(f"Using raw data file: {raw_data_path}")

        # Load data components
        data_components = torch.load(raw_data_path, weights_only=False, map_location='cpu')

        # Extract components
        x = data_components['x']                # Node features [N, 10]
        # y compatible with placement and routing tasks:
        # - Placement dataset: y is 1D [N], y[:, 0] is y itself
        # - Routing dataset: y is 2D [N, 2], y[:, 0] takes first dimension label
        y = data_components['y'][:, 0]         # Node labels [N]
        edge_index = data_components['edge_index']  # Edge indices [2, E]
        edge_attr = data_components['edge_attr']    # Edge features [E, 10]
        global_features = data_components['global_features']    # Global features [G, 6]
        global_y = data_components['global_y']    # Global labels [G, 4]
        die_coordinates = data_components['die_coordinates']    # Die coordinates [G, 2, 2]

        # Extract graph_id and node_type
        graph_ids = x[:, 8].long()      # 9th dimension is graph_id
        node_types = x[:, 9].long()     # 10th dimension is node_type

        # Remove graph_id from x (keep other 9 dimensions: 0-7 and 9)
        x = torch.cat([x[:, :8], x[:, 9:]], dim=1)

        # Determine test set graph_id list
        all_graph_ids_unique = torch.unique(graph_ids).cpu().numpy().tolist()
        self.test_graph_ids = self._decide_test_ids(all_graph_ids_unique)

        # Create masks
        train_mask = torch.tensor([gid.item() not in self.test_graph_ids for gid in graph_ids], dtype=torch.bool)
        valid_mask = (y != -1)   # Valid nodes: label != -1
        train_valid_mask = train_mask & valid_mask  # Valid nodes in training set

        # ============================================================
        # Standardization (only for regression tasks)
        # ============================================================
        if self.task_type == 'regression':
            y_train_valid = y[train_valid_mask].numpy()
            all_y = y.numpy()

            # Calculate offset (ensure all values + offset > 0)
            global_min = all_y.min()
            epsilon = 1e-8
            if global_min <= 0:
                offset = -global_min + epsilon
                y_train_valid = y_train_valid + offset
                y_np = all_y + offset
            else:
                offset = 0
                y_np = all_y

            # Log transform (handle long-tail distribution)
            y_train_log = log_transform(y_train_valid, epsilon)

            # Check for NaN
            if np.isnan(y_train_log).any():
                raise ValueError("NaN appeared after log transform!")

            # Calculate training set statistics
            train_mean = y_train_log.mean()
            train_std = y_train_log.std()

            if train_std == 0:
                raise ValueError("Training set standard deviation is 0! All labels are the same.")
            if np.isnan(train_mean) or np.isnan(train_std):
                raise ValueError("Standardization parameters contain NaN!")

            # Apply log transform and standardization
            y_log = log_transform(y_np, epsilon)
            if np.isnan(y_log).any():
                raise ValueError("NaN appeared after log transform!")

            y_normalized = standardize(y_log, train_mean, train_std)
            y = torch.tensor(y_normalized, dtype=torch.float32)

            if torch.isnan(y).any():
                raise ValueError("Normalized labels contain NaN!")

            # Save standardization parameters
            self.normalization_params = {
                'offset': offset,
                'epsilon': epsilon,
                'mean': train_mean,
                'std': train_std
            }
        else:
            # Classification task: labels remain unchanged
            self.normalization_params = None

        # ============================================================
        # Concatenate global features to node features
        # ============================================================
        global_graph_ids = global_features[:, -1].long()  # graph_id (last dimension)
        global_features_valid = global_features[:, :-1]  # Valid global features (remove last dimension)

        # Create graph_id to global features mapping
        graph_id_to_global = {
            gid.item(): feat for gid, feat in zip(global_graph_ids, global_features_valid)
        }

        # Match global features for each node's belonging graph
        node_global_features = torch.zeros((x.shape[0], global_features_valid.shape[1]), device=x.device)
        for i, gid in enumerate(graph_ids):
            node_global_features[i] = graph_id_to_global[gid.item()]

        # Concatenate node features (9 dims) + global features (5 dims) = 14 dims
        x = torch.cat([x, node_global_features], dim=1)
        
        # ============================================================
        # Create data object and save
        # ============================================================
        data = Data(
            x=x,
            y=y,
            edge_index=edge_index,
            edge_attr=edge_attr,
            global_features=global_features,
            global_y=global_y,
            die_coordinates=die_coordinates
        )

        # Test and training set masks
        data.test_mask = torch.tensor([gid.item() in self.test_graph_ids for gid in graph_ids], dtype=torch.bool)
        data.train_mask = ~data.test_mask
        data.graph_ids = graph_ids
        data.valid_mask = valid_mask
        data.node_types = node_types
        data.norm_params = self.normalization_params if hasattr(self, 'normalization_params') else None

        # Convert to undirected graph
        if self.to_undirected:
            if hasattr(data, 'edge_attr') and data.edge_attr is not None:
                edge_types = edge_attr[:, 9].long()
                new_edge_index = []
                new_edge_attr = []

                for et in torch.unique(edge_types):
                    mask = (edge_types == et)
                    sub_edge_index = edge_index[:, mask]
                    sub_edge_attr = edge_attr[mask]

                    # Choose aggregation method based on edge type
                    if et in [EDGE_TYPES['io_pin_net'], EDGE_TYPES['pin_net']]:
                        undir_edge_index, undir_edge_attr = to_undirected(
                            sub_edge_index, edge_attr=sub_edge_attr,
                            num_nodes=data.num_nodes, reduce='mean'
                        )
                    else:
                        undir_edge_index, undir_edge_attr = to_undirected(
                            sub_edge_index, edge_attr=sub_edge_attr,
                            num_nodes=data.num_nodes, reduce='max'
                        )

                    new_edge_index.append(undir_edge_index)
                    new_edge_attr.append(undir_edge_attr)

                data.edge_index = torch.cat(new_edge_index, dim=1)
                data.edge_attr = torch.cat(new_edge_attr, dim=0)
            else:
                data.edge_index = to_undirected(data.edge_index, num_nodes=data.num_nodes)

        # Save split metadata
        data.split_meta = {
            'test_graph_ids': list(self.test_graph_ids),
            'random_test': bool(getattr(self.args, 'random_test', False)) if self.args else False,
            'test_ratio': float(getattr(self.args, 'test_ratio', 0.2)) if self.args else 0.2,
            'seed': int(getattr(self.args, 'seed', 42)) if self.args else None,
        }

        # Save processed data
        os.makedirs(self.processed_dir, exist_ok=True)
        torch.save(self.collate([data]), self.processed_paths[0])
        logging.info(f"Data processing completed, saved to: {self.processed_paths[0]}")
    
    def _decide_test_ids(self, all_graph_ids_unique):
        """
        Determine test set graph_id list based on command line parameters

        Priority:
        1. Fixed test IDs (--fixed_test_ids)
        2. Random split (--random_test)
        3. Default list

        Args:
            all_graph_ids_unique: All available graph_id list

        Returns:
            Test set graph_id list
        """
        if self.args is not None:
            # Use fixed test set first
            fixed_list = getattr(self.args, 'fixed_test_ids_list', []) or []
            if len(fixed_list) > 0:
                chosen = [gid for gid in fixed_list if gid in all_graph_ids_unique]
                if len(chosen) > 0:
                    return chosen
                logging.warning("Provided fixed_test_ids do not exist in data, falling back to default list")

            # Random split
            if getattr(self.args, 'random_test', False):
                ratio = float(getattr(self.args, 'test_ratio', 0.2))
                rng = np.random.default_rng(self.args.seed if hasattr(self.args, 'seed') else None)
                shuffled = all_graph_ids_unique.copy()
                rng.shuffle(shuffled)
                test_size = max(1, int(len(shuffled) * ratio)) if len(shuffled) > 0 else 0
                return shuffled[:test_size]

        # Default list
        return [2, 9, 21, 27, 28]

    @property
    def raw_dir(self) -> str:
        return os.path.join(self.root, 'raw')

    @property
    def processed_dir(self) -> str:
        return os.path.join(self.root, f'processed_{self.task_level}_{self.task_type}')

    @property
    def raw_file_names(self):
        return [f'{self.base_name}.pt']

    @property
    def processed_file_names(self):
        return [f'{self.base_name}_processed.pt']
