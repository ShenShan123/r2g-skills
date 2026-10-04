# model.py
# Graph Neural Network model for circuit graph prediction tasks.
# This module implements the GNN backbone with feature encoders and decoder head.

import torch
from torch import nn
from torch_geometric.nn import GCNConv, SAGEConv, GATConv, GINEConv, ResGatedGraphConv, global_add_pool, global_mean_pool
from torch_geometric.nn.models.mlp import MLP
from dataset import EDGE_TYPES, NODE_TYPES
from encoders import FeatureEncoder


class GraphHead(nn.Module):
    """GNN model for graph-level or node-level prediction on homogeneous circuit graphs.

    This model consists of:
    1. Feature encoder: Encodes raw node and edge features into embeddings
    2. GNN layers: Message passing layers (GCN/SAGE/GAT/GINE/GatedGCN)
    3. Decoder head: MLP that produces final predictions

    The model supports:
    - Node-level tasks: predict labels for individual nodes
    - Edge-level tasks: predict labels for edges
    - Graph-level tasks: predict labels for entire graphs
    """

    def __init__(self, args):
        """Initialize the GraphHead model.

        Args:
            args: Configuration object containing:
                - hid_dim: Hidden dimension
                - model: GNN type (gcn/sage/gat/gine/resgatedgcn)
                - num_gnn_layers: Number of GNN layers
                - num_head_layers: Number of MLP layers in decoder head
                - task: Task type (regression/classification)
                - task_level: Task level (node/edge/graph)
                - src_dst_agg: Aggregation method for edge tasks
                - act_fn: Activation function
                - use_bn: Whether to use batch normalization
                - dropout: Dropout rate
        """
        super().__init__()
        self.args = args
        self.hidden_dim = args.hid_dim
        self.task = args.task
        self.task_level = args.task_level
        self.model = args.model

        # Number of node and edge types for encoding
        self.num_node_types = len(NODE_TYPES)
        self.num_edge_types = max(EDGE_TYPES.values()) + 1

        # Feature encoder: transforms raw features to embeddings
        self.feature_encoder = FeatureEncoder(args, num_node_types=self.num_node_types, num_edge_types=self.num_edge_types)

        # GNN layers: message passing
        self.layers = nn.ModuleList()
        for _ in range(args.num_gnn_layers):
            if args.model == 'gcn':
                self.layers.append(GCNConv(self.hidden_dim, self.hidden_dim))
            elif args.model == 'sage':
                self.layers.append(SAGEConv(self.hidden_dim, self.hidden_dim))
            elif args.model == 'gat':
                # GAT with edge features
                self.layers.append(GATConv(self.hidden_dim, self.hidden_dim, heads=1, edge_dim=self.hidden_dim))
            elif args.model == 'resgatedgcn':
                # ResGatedGCN with edge features
                self.layers.append(ResGatedGraphConv(self.hidden_dim, self.hidden_dim, edge_dim=self.hidden_dim))
            elif args.model == 'gine':
                mlp = MLP(
                    in_channels=self.hidden_dim,
                    hidden_channels=self.hidden_dim,
                    out_channels=self.hidden_dim,
                    num_layers=2,
                    norm=None,
                )
                self.layers.append(GINEConv(mlp, train_eps=True, edge_dim=self.hidden_dim))
            else:
                raise ValueError(f'Unsupported GNN model: {args.model}')

        # Pooling function for graph-level tasks
        self.src_dst_agg = args.src_dst_agg
        if self.src_dst_agg == 'pooladd':
            self.pooling_fun = global_add_pool
        elif self.src_dst_agg == 'poolmean':
            self.pooling_fun = global_mean_pool

        # Output layer configuration
        # Input dimension depends on task level and aggregation method
        head_input_dim = self.hidden_dim
        if self.task_level == 'edge':
            # Edge-level tasks:
            # - concat: [src_emb, dst_emb, edge_attr_emb] -> 3 * hid_dim
            # - other: [src_emb + dst_emb, edge_attr_emb] -> 2 * hid_dim
            head_input_dim = 3 * self.hidden_dim if self.src_dst_agg == 'concat' else 2 * self.hidden_dim

        if self.task == 'regression':
            dim_out = 1
        elif self.task == 'classification':
            dim_out = args.num_classes
        else:
            raise ValueError('Invalid task')

        # Decoder head: MLP that produces final predictions
        self.head_layers = MLP(
            in_channels=head_input_dim,
            hidden_channels=self.hidden_dim,
            out_channels=dim_out,
            num_layers=args.num_head_layers,
        )

        # Batch normalization (optional)
        self.use_bn = args.use_bn
        if self.use_bn:
            self.bn_node = nn.BatchNorm1d(self.hidden_dim)

        # Activation function
        if args.act_fn == 'relu':
            self.activation = nn.ReLU()
        elif args.act_fn == 'elu':
            self.activation = nn.ELU()
        elif args.act_fn == 'leakyrelu':
            self.activation = nn.LeakyReLU()
        else:
            raise ValueError('Invalid activation')

        # Dropout
        self.dropout = nn.Dropout(args.dropout)

    def forward(self, batch):
        """Forward pass through the model.

        Args:
            batch: PyG Batch object containing:
                - x: Node features [num_nodes, feature_dim]
                - edge_index: Edge indices [2, num_edges]
                - edge_attr: Edge features [num_edges, feature_dim]
                - y: Labels (node/edge/graph)
                - edge_label_index: Edge indices for edge tasks
                - edge_label: Edge labels
                - global_y: Graph-level labels

        Returns:
            For node tasks: (pred, true_class, true_label)
            For edge tasks: (pred, true_label, true_label)
            For graph tasks: (pred, true_label, true_label)
        """
        # Encode features
        batch = self.feature_encoder(batch)
        x = batch.x  # [num_nodes, hidden_dim]
        edge_emb = batch.edge_attr  # [num_edges, hidden_dim]

        # GNN message passing
        for conv in self.layers:
            # Only pass edge attributes to models that support them
            if self.model == 'gine' or self.model == 'resgatedgcn' or self.model == 'gat':
                x = conv(x, batch.edge_index, edge_attr=edge_emb)
            else:
                x = conv(x, batch.edge_index)

            if self.use_bn:
                x = self.bn_node(x)
            x = self.activation(x)
            x = self.dropout(x)

        # Handle output based on task level
        if self.task_level == 'node':
            # Node-level prediction: valid nodes are those with labels != -1
            valid_mask = (batch.y != -1)
            pred = self.head_layers(x)

            # Prepare labels: only keep valid node labels
            if self.task == 'regression':
                true_label = batch.y.masked_fill(~valid_mask, -1)
                true_class = torch.full_like(batch.y, -1, dtype=torch.long)
            else:  # classification
                true_class = batch.y.masked_fill(~valid_mask, -1).long()
                true_label = torch.full_like(batch.y, -1, dtype=torch.float)

            return pred, true_class, true_label

        elif self.task_level == 'edge':
            # Edge-level prediction
            edge_label_index = batch.edge_label_index if hasattr(batch, 'edge_label_index') else batch.edge_index

            src_emb = x[edge_label_index[0]]
            dst_emb = x[edge_label_index[1]]

            # Gather edge embeddings for labeled edges
            edge_attr_emb = None
            if edge_emb is not None:
                try:
                    sub_ei = batch.edge_index.detach().cpu()
                    sub_ea = edge_emb.detach().cpu()
                    mapping = {}
                    for k in range(sub_ei.size(1)):
                        u = int(sub_ei[0, k])
                        v = int(sub_ei[1, k])
                        mapping[(u, v)] = sub_ea[k]
                        mapping[(v, u)] = sub_ea[k]
                    lbl_ei = edge_label_index.detach().cpu()
                    gathered = []
                    hid = edge_emb.size(1)
                    zero = torch.zeros(hid)
                    for k in range(lbl_ei.size(1)):
                        u = int(lbl_ei[0, k])
                        v = int(lbl_ei[1, k])
                        gathered.append(mapping.get((u, v), zero))
                    edge_attr_emb = torch.stack(gathered, dim=0).to(edge_emb.device)
                except Exception:
                    edge_attr_emb = torch.zeros(edge_label_index.size(1), self.hidden_dim, device=x.device)

            # Aggregate source, destination, and edge embeddings
            if self.src_dst_agg == 'concat':
                if edge_attr_emb is None:
                    edge_feat = torch.cat([src_emb, dst_emb], dim=1)
                else:
                    edge_feat = torch.cat([src_emb, dst_emb, edge_attr_emb], dim=1)
            elif self.src_dst_agg == 'mean':
                combined = (src_emb + dst_emb) / 2
                if edge_attr_emb is None:
                    edge_feat = combined
                else:
                    edge_feat = torch.cat([combined, edge_attr_emb], dim=1)
            elif self.src_dst_agg == 'add':
                combined = src_emb + dst_emb
                if edge_attr_emb is None:
                    edge_feat = combined
                else:
                    edge_feat = torch.cat([combined, edge_attr_emb], dim=1)
            else:
                # Default to addition for other options
                combined = src_emb + dst_emb
                if edge_attr_emb is None:
                    edge_feat = combined
                else:
                    edge_feat = torch.cat([combined, edge_attr_emb], dim=1)

            pred = self.head_layers(edge_feat)
            true_label = batch.edge_label if hasattr(batch, 'edge_label') else torch.zeros(edge_label_index.size(1), device=x.device)

            return pred, true_label, true_label

        elif self.task_level == 'graph':
            # Graph-level prediction: use pooling
            graph_emb = self.pooling_fun(x, batch.batch)
            pred = self.head_layers(graph_emb)
            true_label = batch.global_y if hasattr(batch, 'global_y') else torch.zeros(len(graph_emb))
            return pred, true_label, true_label

        else:
            raise ValueError(f"Invalid task level: {self.task_level}")
