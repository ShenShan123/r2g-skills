# encoders.py
"""
R2G: Multi-View Circuit Graph Benchmark Suite - Feature Encoder Module

Feature encoding module, responsible for encoding raw circuit graph features into unified dimension embedding vectors.

Core Functions:
1. Node encoding: Use Embedding for discrete features, Linear projection for continuous features
2. Edge encoding: Use Embedding for discrete features, Linear projection for continuous features
3. Distinguish feature dimensions and class counts for different node/edge types

Encoding Process:
- Discrete features: index -> Embedding -> fixed dimension embedding
- Continuous features: raw value -> Linear -> fixed dimension projection
- Final output: Add discrete embedding and continuous projection to get unified dimension feature vector

Node Types (4 types):
- gate: Logic gate unit
- io_pin: I/O pin (top-level port)
- net: Network (electrical network connecting multiple nodes)
- pin: Input/output pin of logic gate

Edge Types (8 types):
- gate_gate, gate_net, gate_pin, io_pin_gate, io_pin_net, io_pin_pin, pin_net, pin_pin
"""

import torch
import torch.nn as nn
from torch_geometric.data import Batch

# ============================================================
# Encoder registration mechanism (for easy extension)
# ============================================================
node_encoder_dict = {}
edge_encoder_dict = {}

def register_node_encoder(name):
    """Register node encoder"""
    def decorator(cls):
        node_encoder_dict[name] = cls
        return cls
    return decorator

def register_edge_encoder(name):
    """Register edge encoder"""
    def decorator(cls):
        edge_encoder_dict[name] = cls
        return cls
    return decorator


# ============================================================
# Node feature configuration
# ============================================================

# Node types: 0=gate, 1=io_pin, 2=net, 3=pin
# Discrete feature indices (processed x dimension, 0-8 are original features, 9-13 are global features)
NODE_DISCRETE_FEATURES = {
    0: [2, 3, 5],       # Discrete feature indices for gate node (dimensions 2, 3, 5)
    1: [2, 3],          # Discrete feature indices for io_pin node (dimensions 2, 3)
    2: [0],              # Discrete feature indices for net node (dimension 0)
    3: [0, 1]            # Discrete feature indices for pin node (dimensions 0, 1)
}

# Continuous feature indices
NODE_CONTINUOUS_FEATURES = {
    0: [0, 1, 4, 6, 7, 8, 9, 10, 11, 12, 13],  # gate node
    1: [0, 1, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13],  # io_pin node
    2: [1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13],  # net node
    3: [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13],   # pin node
}

# Class counts for discrete features (set according to actual data distribution)
NODE_DISCRETE_DIMS = {
    0: {2: 96, 3: 8, 5: 2},    # gate: unit type (96 classes), orientation encoding (5 classes), placement status (10 classes)
    1: {2: 4, 3: 22},           # io_pin: orientation mapping (10 classes), placement layer encoding (8 classes)
    2: {0: 6},                    # net: network type encoding (5 classes)
    3: {0: 15, 1: 96}            # pin: pin type encoding (6 classes), component type encoding (4 classes)
}


# ============================================================
# Edge feature configuration
# ============================================================

# Discrete feature indices for edge types (processed edge_attr dimension, 0-8 are original features, 9 is edge_type)
EDGE_DISCRETE_FEATURES = {
    0: [0, 1, 2, 4, 5],     # ('gate', 'connects_to', 'gate')
    1: [0, 1],                # ('gate', 'connects_to', 'net')
    2: [0, 1],                # ('gate', 'has', 'pin')
    3: [0, 2, 3],             # ('io_pin', 'connects_to', 'gate')
    4: [0, 1],                # ('io_pin', 'connects_to', 'net')
    5: [0],                    # ('io_pin', 'connects_to', 'pin')
    6: [0, 1],                # ('pin', 'connects_to', 'net')
    7: [0],                    # ('pin', 'connects_to', 'pin')
    8: [2, 3, 5],             # ('pin', 'gate_connects', 'pin')
}

# Continuous feature indices for edge types
EDGE_CONTINUOUS_FEATURES = {
    0: [3, 6, 7, 8],         # ('gate', 'connects_to', 'gate')
    1: [2, 3, 4, 5, 6, 7, 8], # ('gate', 'connects_to', 'net')
    2: [2, 3, 4, 5, 6, 7, 8], # ('gate', 'has', 'pin')
    3: [1, 4, 5, 6, 7, 8],     # ('io_pin', 'connects_to', 'gate')
    4: [2, 3, 4, 5, 6, 7, 8], # ('io_pin', 'connects_to', 'net')
    5: [1, 2, 3, 4, 5, 6, 7, 8], # ('io_pin', 'connects_to', 'pin')
    6: [2, 3, 4, 5, 6, 7, 8], # ('pin', 'connects_to', 'net')
    7: [1, 2, 3, 4, 5, 6, 7, 8], # ('pin', 'connects_to', 'pin')
    8: [0, 1, 4, 6, 7, 8],   # ('pin', 'gate_connects', 'pin')
}

# Class counts for edge discrete features
EDGE_DISCRETE_DIMS = {
    0: {0: 15, 1: 96, 2: 6, 4: 15, 5: 96},     # ('gate', 'connects_to', 'gate')
    1: {0: 15, 1: 96},                       # ('gate', 'connects_to', 'net')
    2: {0: 96, 1: 15},                       # ('gate', 'has', 'pin')
    3: {0: 6, 2: 15, 3: 96},                  # ('io_pin', 'connects_to', 'gate')
    4: {0: 4, 1: 6},                       # ('io_pin', 'connects_to', 'net')
    5: {0: 6},                                # ('io_pin', 'connects_to', 'pin')
    6: {0: 15, 1: 6},                       # ('pin', 'connects_to', 'net')
    7: {0: 6},                                # ('pin', 'connects_to', 'pin')
    8: {2: 96, 3: 8, 5: 2},                 # ('pin', 'gate_connects', 'pin')
}


# ============================================================
# Node encoder
# ============================================================

@register_node_encoder('homograph_node')
class HomographNodeEncoder(nn.Module):
    """
    Homograph node feature encoder

    Processing flow:
    1. Extract nodes of corresponding type based on node_type
    2. Use Embedding for discrete feature encoding
    3. Use Linear projection for continuous features to embedding dimension
    4. Add discrete embedding and continuous projection to get final embedding

    Args:
        emb_dim: Embedding dimension (default 256)
        num_node_types: Number of node types (default 4)
    """

    def __init__(self, emb_dim, num_node_types):
        super().__init__()
        self.emb_dim = emb_dim
        self.num_node_types = num_node_types

        # Create embedding layers for discrete features of each node type
        self.discrete_embeddings = nn.ModuleDict()
        for node_type in NODE_DISCRETE_FEATURES:
            self.discrete_embeddings[str(node_type)] = nn.ModuleDict()
            # Calculate embedding dimension for each discrete feature
            num_discrete = len(NODE_DISCRETE_FEATURES[node_type])
            if num_discrete > 0:
                emb_per_feature = emb_dim // num_discrete
                remainder = emb_dim % num_discrete
                for i, feat_idx in enumerate(NODE_DISCRETE_FEATURES[node_type]):
                    # First remainder features get 1 extra dimension
                    dim = emb_per_feature + (1 if i < remainder else 0)
                    self.discrete_embeddings[str(node_type)][str(feat_idx)] = nn.Embedding(
                        NODE_DISCRETE_DIMS[node_type][feat_idx], dim
                    )

        # Create linear projection layers for continuous features of each node type
        self.continuous_projections = nn.ModuleDict()
        for node_type in NODE_CONTINUOUS_FEATURES:
            input_dim = len(NODE_CONTINUOUS_FEATURES[node_type])
            self.continuous_projections[str(node_type)] = nn.Linear(input_dim, emb_dim)

        # Initialize weights (Xavier uniform)
        for emb in self.discrete_embeddings.values():
            for e in emb.values():
                nn.init.xavier_uniform_(e.weight.data)
        for proj in self.continuous_projections.values():
            nn.init.xavier_uniform_(proj.weight.data)

    def forward(self, batch: Batch) -> Batch:
        """
        Forward pass

        Args:
            batch: PyG Batch object, containing:
                - x: Node features [num_nodes, 14]
                - node_types: Node types [num_nodes]

        Returns:
            batch: Encoded Batch object, x replaced with embedding [num_nodes, emb_dim]
        """
        x = batch.x  # [num_nodes, 14]
        node_types = batch.node_types  # [num_nodes]
        num_nodes = x.size(0)
        out = torch.zeros(num_nodes, self.emb_dim, device=x.device)

        # Process each node type separately
        for node_type in torch.unique(node_types):
            mask = (node_types == node_type)
            if not mask.any():
                continue

            node_type_int = node_type.item()
            node_type_str = str(node_type_int)
            x_sub = x[mask]

            # Process discrete features: Embedding
            discrete_feats = []
            for feat_idx in NODE_DISCRETE_FEATURES.get(node_type_int, []):
                emb = self.discrete_embeddings[node_type_str][str(feat_idx)]
                discrete_feats.append(emb(x_sub[:, feat_idx].long()))

            # Process continuous features: Linear projection
            cont_feats = x_sub[:, NODE_CONTINUOUS_FEATURES[node_type_int]].float()
            cont_proj = self.continuous_projections[node_type_str](cont_feats)

            # Combine: discrete embedding + continuous projection
            if discrete_feats:
                discrete_combined = torch.cat(discrete_feats, dim=1)
                node_emb = discrete_combined + cont_proj
            else:
                node_emb = cont_proj

            out[mask] = node_emb

        batch.x = out
        return batch


# ============================================================
# Edge encoder
# ============================================================

@register_edge_encoder('homograph_edge')
class HomographEdgeEncoder(nn.Module):
    """
    Homograph edge feature encoder

    Processing flow:
    1. Extract edges of corresponding type based on edge_type
    2. Use Embedding for discrete feature encoding
    3. Use Linear projection for continuous features to embedding dimension
    4. Add discrete embedding and continuous projection to get final embedding

    Args:
        emb_dim: Embedding dimension (default 256)
        num_edge_types: Number of edge types (default 9)
    """

    def __init__(self, emb_dim, num_edge_types):
        super().__init__()
        self.emb_dim = emb_dim
        self.num_edge_types = num_edge_types

        # Create embedding layers for discrete features of each edge type
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
        for edge_type in EDGE_CONTINUOUS_FEATURES:
            input_dim = len(EDGE_CONTINUOUS_FEATURES[edge_type])
            self.continuous_projections[str(edge_type)] = nn.Linear(input_dim, emb_dim)

        # Initialize weights
        for emb in self.discrete_embeddings.values():
            for e in emb.values():
                nn.init.xavier_uniform_(e.weight.data)
        for proj in self.continuous_projections.values():
            nn.init.xavier_uniform_(proj.weight.data)

    def forward(self, batch: Batch) -> Batch:
        """
        Forward pass

        Args:
            batch: PyG Batch object, containing:
                - edge_attr: Edge features [num_edges, 10]
                - edge_attr[:, 9]: Edge type

        Returns:
            batch: Encoded Batch object, edge_attr replaced with embedding [num_edges, emb_dim]
        """
        edge_attr = batch.edge_attr  # [num_edges, 10]
        edge_types = edge_attr[:, 9].long()  # 10th dimension is edge_type
        num_edges = edge_attr.size(0)
        out = torch.zeros(num_edges, self.emb_dim, device=edge_attr.device)

        # Process each edge type separately
        for edge_type in torch.unique(edge_types):
            mask = (edge_types == edge_type)
            if not mask.any():
                continue

            edge_type_int = edge_type.item()
            edge_type_str = str(edge_type_int)
            e_sub = edge_attr[mask]

            # Process discrete features
            discrete_feats = []
            for feat_idx in EDGE_DISCRETE_FEATURES.get(edge_type_int, []):
                emb = self.discrete_embeddings[edge_type_str][str(feat_idx)]
                discrete_feats.append(emb(e_sub[:, feat_idx].long()))

            # Process continuous features
            cont_feats = e_sub[:, EDGE_CONTINUOUS_FEATURES[edge_type_int]].float()
            cont_proj = self.continuous_projections[edge_type_str](cont_feats)

            # Combine
            if discrete_feats:
                discrete_combined = torch.cat(discrete_feats, dim=1)
                edge_emb = discrete_combined + cont_proj
            else:
                edge_emb = cont_proj

            out[mask] = edge_emb

        batch.edge_attr = out
        return batch


# ============================================================
# Feature encoder integration class
# ============================================================

class FeatureEncoder(nn.Module):
    """
    Integration of node encoder and edge encoder

    Functions:
    1. Call node encoder to process batch.x
    2. Call edge encoder to process batch.edge_attr
    3. Optional batch normalization (BatchNorm)

    Args:
        args: Command line arguments
        num_node_types: Number of node types
        num_edge_types: Number of edge types
    """

    def __init__(self, args, num_node_types, num_edge_types):
        super().__init__()
        self.args = args
        self.emb_dim = args.hid_dim

        # Node encoder
        self.node_encoder = node_encoder_dict[args.node_encoder](
            emb_dim=self.emb_dim,
            num_node_types=num_node_types
        )
        if args.node_encoder_bn:
            self.node_bn = nn.BatchNorm1d(self.emb_dim)

        # Edge encoder
        self.edge_encoder = edge_encoder_dict[args.edge_encoder](
            emb_dim=self.emb_dim,
            num_edge_types=num_edge_types
        )
        if args.edge_encoder_bn:
            self.edge_bn = nn.BatchNorm1d(self.emb_dim)

    def forward(self, batch: Batch) -> Batch:
        """
        Forward pass

        Args:
            batch: PyG Batch object

        Returns:
            batch: Encoded Batch object
        """
        # Node encoding
        batch = self.node_encoder(batch)
        if self.args.node_encoder_bn:
            batch.x = self.node_bn(batch.x)

        # Edge encoding
        batch = self.edge_encoder(batch)
        if self.args.edge_encoder_bn:
            batch.edge_attr = self.edge_bn(batch.edge_attr)

        return batch
