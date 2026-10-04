# encoders.py
# Feature encoders for transforming raw circuit graph features into embeddings.
# This module handles encoding of discrete (categorical) and continuous features
# for both nodes and edges in circuit graphs.

import torch
import torch.nn as nn
from torch_geometric.data import Batch

# ============================================
# Encoder Registration System
# ============================================
# Allows dynamic registration of encoder classes by name

node_encoder_dict = {}
edge_encoder_dict = {}


def register_node_encoder(name):
    """Decorator to register a node encoder class.

    Args:
        name: Name identifier for the encoder

    Returns:
        Decorator function
    """
    def decorator(cls):
        node_encoder_dict[name] = cls
        return cls
    return decorator


def register_edge_encoder(name):
    """Decorator to register an edge encoder class.

    Args:
        name: Name identifier for the encoder

    Returns:
        Decorator function
    """
    def decorator(cls):
        edge_encoder_dict[name] = cls
        return cls
    return decorator


# ============================================
# Node Feature Configuration
# ============================================

# Indices of discrete (categorical) features for each node type
# After processing: x columns 0-8 are original features, 9-13 are global features
# Node types: 0=gate, 1=io_pin, 2=net, 3=pin, column 8 is node type
NODE_DISCRETE_FEATURES = {
    0: [2, 3, 5, 8],       # gate: discrete features at columns 2, 3, 5, 8
    1: [2, 3, 8],          # io_pin: discrete features at columns 2, 3, 8
    2: [0, 8],             # net: discrete features at columns 0, 8
    3: [0, 1, 8]           # pin: discrete features at columns 0, 1, 8
}

# Indices of continuous features for each node type (for node-level tasks)
# Includes columns 9-13 (global features)
NODE_CONTINUOUS_FEATURES_NODE = {
    0: [0, 1, 4, 6, 7, 9, 10, 11, 12, 13],  # gate
    1: [0, 1, 4, 5, 6, 7, 9, 10, 11, 12, 13],  # io_pin
    2: [1, 2, 4, 5, 6, 7, 9, 10, 11, 12, 13],  # net
    3: [2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13]   # pin
}

# Indices of continuous features for each node type (for edge-level tasks)
# Excludes columns 9-13 (global features)
NODE_CONTINUOUS_FEATURES_EDGE = {
    0: [0, 1, 4, 6, 7],          # gate (no global features)
    1: [0, 1, 4, 5, 6, 7],       # io_pin (no global features)
    2: [1, 2, 4, 5, 6, 7],       # net (no global features)
    3: [2, 3, 4, 5, 6, 7]        # pin (no global features)
}

# Number of categories for each discrete feature per node type
NODE_DISCRETE_DIMS = {
    0: {2: 96, 3: 8, 5: 2, 8: 4},    # gate: cell_type(96), orientation(8), place_flag(2), node_type(4)
    1: {2: 4, 3: 22, 8: 4},           # io_pin: orientation(4), layer_id(22), node_type(4)
    2: {0: 6, 8: 4},                  # net: net_type(6), node_type(4)
    3: {0: 15, 1: 96, 8: 4}             # pin: pin_type(15), owner_type(96), node_type(4)
}


# ============================================
# Edge Feature Configuration
# ============================================

# Indices of discrete features for each edge type
# After processing: edge_attr columns 0-8 are original features, 9-13 are global features
EDGE_DISCRETE_FEATURES = {
    0: [0, 1, 2, 4, 5, 8],  # gate_gate
    1: [0, 1, 8],           # gate_net
    2: [0, 1, 8],           # gate_pin
    3: [0, 2, 3, 8],        # io_pin_gate
    4: [0, 1, 8],           # io_pin_net
    5: [0, 8],              # io_pin_pin
    6: [0, 1, 8],           # pin_net
    7: [0, 8],              # pin_pin
    8: [2, 3, 5, 8]         # pin_gate_pin
}

# Indices of continuous features for each edge type (for node-level tasks)
# Includes columns 9-13 (global features)
EDGE_CONTINUOUS_FEATURES = {
    0: [3, 6, 7, 9, 10, 11, 12, 13],    # gate_gate
    1: [2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13],  # gate_net
    2: [2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13],  # gate_pin
    3: [1, 4, 5, 6, 7, 9, 10, 11, 12, 13],     # io_pin_gate
    4: [2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13],  # io_pin_net
    5: [1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13],  # io_pin_pin
    6: [2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13],  # pin_net
    7: [1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13],  # pin_pin
    8: [0, 1, 4, 6, 7, 9, 10, 11, 12, 13]      # pin_gate_pin
}

# For edge-level tasks, edge_attr has 5 additional global features (columns 10-14)
# So we use the same indices (the global features are already included)
EDGE_CONTINUOUS_FEATURES_EDGE = EDGE_CONTINUOUS_FEATURES

# Number of categories for each discrete feature per edge type
EDGE_DISCRETE_DIMS = {
    0: {0: 15, 1: 96, 2: 6, 4: 15, 5: 96, 8: 9},   # gate_gate
    1: {0: 15, 1: 96, 8: 9},                      # gate_net
    2: {0: 96, 1: 15, 8: 9},                      # gate_pin
    3: {0: 6, 2: 15, 3: 96, 8: 9},                 # io_pin_gate
    4: {0: 4, 1: 6, 8: 9},                          # io_pin_net
    5: {0: 6, 8: 9},                                # io_pin_pin
    6: {0: 15, 1: 6, 8: 9},                         # pin_net
    7: {0: 6, 8: 9},                                # pin_pin
    8: {2: 96, 3: 8, 5: 2, 8: 9}                    # pin_gate_pin
}


# ============================================
# Node Encoder
# ============================================

@register_node_encoder('homograph_node')
class HomographNodeEncoder(nn.Module):
    """Encodes node features into embeddings.

    This encoder:
    1. Separates discrete (categorical) and continuous features
    2. Applies embedding lookup for discrete features
    3. Applies linear projection for continuous features
    4. Combines embeddings for each node type separately

    Different node types have different feature schemas, so each type
    is processed independently.
    """

    def __init__(self, emb_dim, num_node_types, task_level: str = 'node'):
        """Initialize node encoder.

        Args:
            emb_dim: Output embedding dimension
            num_node_types: Number of node types
            task_level: 'node' or 'edge', determines which features to include
        """
        super().__init__()
        self.emb_dim = emb_dim
        self.num_node_types = num_node_types
        self.task_level = task_level

        # Select continuous feature indices based on task level
        if self.task_level == 'edge':
            self._node_continuous_features = NODE_CONTINUOUS_FEATURES_EDGE
        else:
            self._node_continuous_features = NODE_CONTINUOUS_FEATURES_NODE

        # Create embedding layers for each discrete feature of each node type
        self.discrete_embeddings = nn.ModuleDict()
        for node_type in NODE_DISCRETE_FEATURES:
            self.discrete_embeddings[str(node_type)] = nn.ModuleDict()

            # Calculate embedding dimension per discrete feature
            num_discrete = len(NODE_DISCRETE_FEATURES[node_type])
            if num_discrete > 0:
                emb_per_feature = emb_dim // num_discrete
                remainder = emb_dim % num_discrete  # Distribute remainder to first features

                for i, feat_idx in enumerate(NODE_DISCRETE_FEATURES[node_type]):
                    # First 'remainder' features get 1 extra dimension
                    dim = emb_per_feature + (1 if i < remainder else 0)
                    self.discrete_embeddings[str(node_type)][str(feat_idx)] = nn.Embedding(
                        NODE_DISCRETE_DIMS[node_type][feat_idx], dim
                    )

        # Create linear projection layers for continuous features of each node type
        self.continuous_projections = nn.ModuleDict()
        for node_type in self._node_continuous_features:
            input_dim = len(self._node_continuous_features[node_type])
            self.continuous_projections[str(node_type)] = nn.Linear(input_dim, emb_dim)

        # Initialize weights with Xavier initialization
        for emb in self.discrete_embeddings.values():
            for e in emb.values():
                nn.init.xavier_uniform_(e.weight.data)
        for proj in self.continuous_projections.values():
            nn.init.xavier_uniform_(proj.weight.data)

    def forward(self, batch: Batch) -> Batch:
        """Encode node features.

        Args:
            batch: PyG Batch containing:
                - x: Node features [num_nodes, feature_dim]
                - node_types: Node type for each node [num_nodes]

        Returns:
            Batch with updated x (encoded embeddings)
        """
        x = batch.x  # Dimensions: node task ~ [num_nodes, 14], edge task ~ [num_nodes, 9]
        node_types = batch.node_types  # [num_nodes]
        num_nodes = x.size(0)
        out = torch.zeros(num_nodes, self.emb_dim, device=x.device)

        # Process each node type separately
        for node_type in torch.unique(node_types):
            mask = (node_types == node_type)
            if not mask.any():
                continue

            node_type = node_type.item()
            node_type_str = str(node_type)
            x_sub = x[mask]

            # Process discrete features: embedding lookup
            discrete_feats = []
            for feat_idx in NODE_DISCRETE_FEATURES.get(node_type, []):
                emb = self.discrete_embeddings[node_type_str][str(feat_idx)]
                discrete_feats.append(emb(x_sub[:, feat_idx].long()))

            # Process continuous features: linear projection
            cont_indices = self._node_continuous_features[node_type]
            cont_feats = x_sub[:, cont_indices].float()
            cont_proj = self.continuous_projections[node_type_str](cont_feats)

            # Combine discrete and continuous features
            if discrete_feats:
                discrete_combined = torch.cat(discrete_feats, dim=1)  # Concatenate discrete embeddings
                node_emb = discrete_combined + cont_proj  # Add to continuous projection
            else:
                node_emb = cont_proj

            out[mask] = node_emb

        batch.x = out
        return batch


# ============================================
# Edge Encoder
# ============================================

@register_edge_encoder('homograph_edge')
class HomographEdgeEncoder(nn.Module):
    """Encodes edge features into embeddings.

    Similar to node encoder but processes edge features.
    Different edge types have different feature schemas.
    """

    def __init__(self, emb_dim, num_edge_types, task_level: str = 'node'):
        """Initialize edge encoder.

        Args:
            emb_dim: Output embedding dimension
            num_edge_types: Number of edge types
            task_level: 'node' or 'edge', determines which features to include
        """
        super().__init__()
        self.emb_dim = emb_dim
        self.num_edge_types = num_edge_types
        self.task_level = task_level

        # Select continuous feature indices based on task level
        if self.task_level == 'edge':
            self._edge_continuous_features = EDGE_CONTINUOUS_FEATURES_EDGE
        else:
            self._edge_continuous_features = EDGE_CONTINUOUS_FEATURES

        # Create embedding layers for each discrete feature of each edge type
        self.discrete_embeddings = nn.ModuleDict()
        for edge_type in EDGE_DISCRETE_FEATURES:
            self.discrete_embeddings[str(edge_type)] = nn.ModuleDict()

            num_discrete = len(EDGE_DISCRETE_FEATURES[edge_type])
            if num_discrete > 0:
                emb_per_feature = emb_dim // num_discrete
                remainder = emb_dim % num_discrete

                for i, feat_idx in enumerate(EDGE_DISCRETE_FEATURES[edge_type]):
                    dim = emb_per_feature + (1 if i < remainder else 0)
                    self.discrete_embeddings[str(edge_type)][str(feat_idx)] = nn.Embedding(
                        EDGE_DISCRETE_DIMS[edge_type][feat_idx], dim
                    )

        # Create linear projection layers for continuous features of each edge type
        self.continuous_projections = nn.ModuleDict()
        for edge_type in self._edge_continuous_features:
            input_dim = len(self._edge_continuous_features[edge_type])
            self.continuous_projections[str(edge_type)] = nn.Linear(input_dim, emb_dim)

        # Initialize weights
        for emb in self.discrete_embeddings.values():
            for e in emb.values():
                nn.init.xavier_uniform_(e.weight.data)
        for proj in self.continuous_projections.values():
            nn.init.xavier_uniform_(proj.weight.data)

    def forward(self, batch: Batch) -> Batch:
        """Encode edge features.

        Args:
            batch: PyG Batch containing:
                - edge_attr: Edge features [num_edges, feature_dim]
                    - node task: ~ [num_edges, 10]
                    - edge task: ~ [num_edges, 15] (includes global features)

        Returns:
            Batch with updated edge_attr (encoded embeddings)
        """
        edge_attr = batch.edge_attr
        edge_types = edge_attr[:, 8].long()  # Edge type is in column 8
        num_edges = edge_attr.size(0)
        out = torch.zeros(num_edges, self.emb_dim, device=edge_attr.device)

        # Process each edge type separately
        for edge_type in torch.unique(edge_types):
            mask = (edge_types == edge_type)
            if not mask.any():
                continue

            edge_type = edge_type.item()
            edge_type_str = str(edge_type)
            e_sub = edge_attr[mask]

            # Process discrete features
            discrete_feats = []
            for feat_idx in EDGE_DISCRETE_FEATURES.get(edge_type, []):
                emb = self.discrete_embeddings[edge_type_str][str(feat_idx)]
                discrete_feats.append(emb(e_sub[:, feat_idx].long()))

            # Process continuous features (indices selected during init based on task_level)
            cont_indices = self._edge_continuous_features[edge_type]
            cont_feats = e_sub[:, cont_indices].float()
            cont_proj = self.continuous_projections[edge_type_str](cont_feats)

            # Combine discrete and continuous features
            if discrete_feats:
                discrete_combined = torch.cat(discrete_feats, dim=1)
                edge_emb = discrete_combined + cont_proj
            else:
                edge_emb = cont_proj

            out[mask] = edge_emb

        batch.edge_attr = out
        return batch


# ============================================
# Feature Encoder (Unified)
# ============================================

class FeatureEncoder(nn.Module):
    """Unified feature encoder for both nodes and edges.

    This class combines the node encoder and edge encoder into a single module
    that can be easily integrated into the GNN model.
    """

    def __init__(self, args, num_node_types, num_edge_types):
        """Initialize feature encoder.

        Args:
            args: Configuration arguments
            num_node_types: Number of node types
            num_edge_types: Number of edge types
        """
        super().__init__()
        self.args = args
        self.emb_dim = args.hid_dim

        # Node encoder
        self.node_encoder = node_encoder_dict[args.node_encoder](
            emb_dim=self.emb_dim,
            num_node_types=num_node_types,
            task_level=args.task_level if hasattr(args, 'task_level') else 'node'
        )
        if args.node_encoder_bn:
            self.node_bn = nn.BatchNorm1d(self.emb_dim)

        # Edge encoder
        self.edge_encoder = edge_encoder_dict[args.edge_encoder](
            emb_dim=self.emb_dim,
            num_edge_types=num_edge_types,
            task_level=args.task_level if hasattr(args, 'task_level') else 'node'
        )
        if args.edge_encoder_bn:
            self.edge_bn = nn.BatchNorm1d(self.emb_dim)

    def forward(self, batch: Batch) -> Batch:
        """Encode node and edge features.

        Args:
            batch: PyG Batch with raw features

        Returns:
            Batch with encoded features
        """
        # Encode node features
        batch = self.node_encoder(batch)
        if self.args.node_encoder_bn:
            batch.x = self.node_bn(batch.x)

        # Encode edge features
        batch = self.edge_encoder(batch)
        if self.args.edge_encoder_bn:
            batch.edge_attr = self.edge_bn(batch.edge_attr)

        return batch
