# model.py
"""
R2G: Multi-View Circuit Graph Benchmark Suite - Model Implementation

This module implements GNN models for placement and routing tasks on circuit graphs.
Based on the CVPR 2026 paper, we focus on three baseline models:
- GINE: Graph Isomorphism Network with Edge features
- ResGatedGCN: Residual Gated Graph Convolution (GatedGCN)
- GAT: Graph Attention Network

The paper demonstrates that:
1. View choice (b-f) strongly affects performance
2. Node-centric views (b/c) are preferred over edge-only views
3. Decoder-head depth (3-4 layers) is critical for accuracy and stability
4. Compact message-passing depth (3-4 layers) is sufficient

Reference:
"R2G: A Multi-View Circuit Graph Benchmark Suite from RTL to GDSII"
CVPR 2026
"""

import torch
from torch import nn
from torch_geometric.nn import GINEConv, ResGatedGraphConv, GATConv, global_add_pool, global_mean_pool
from torch_geometric.nn.models.mlp import MLP
from dataset import EDGE_TYPES, NODE_TYPES
from encoders import FeatureEncoder
import torch.nn.functional as F


class GraphHead(nn.Module):
    """
    GNN model for node-level (placement) and edge-level (routing) prediction
    on homogeneous circuit graphs with unified node/edge types.

    Supports three baseline models from the CVPR 2026 paper:
    - GINE: Graph Isomorphism Network with Edge features
    - ResGatedGCN: Residual Gated Graph Convolution (GatedGCN)
    - GAT: Graph Attention Network

    Architecture:
    1. FeatureEncoder: Encodes gate/net/IO/pin features from DEF
    2. GNN layers: Message passing (num_gnn_layers, typically 3-4)
    3. Head MLP: Decoder for final prediction (num_head_layers, typically 3-4)
    """
    def __init__(self, args, feature_encoder=None):
        super().__init__()
        self.args = args
        self.hidden_dim = args.hid_dim
        self.task = args.task
        self.task_level = args.task_level
        self.model = args.model

        # 节点类型和边类型的数量（从数据集定义）
        self.num_node_types = len(NODE_TYPES)
        self.num_edge_types = max(EDGE_TYPES.values()) + 1

        # 特征编码器：将原始 DEF 特征编码为统一维度的嵌入
        self.feature_encoder = feature_encoder if feature_encoder is not None else FeatureEncoder(args, num_node_types=self.num_node_types, num_edge_types=self.num_edge_types)

        # GNN 消息传递层
        self.layers = nn.ModuleList()

        # 根据模型类型初始化 GNN 层
        for _ in range(args.num_gnn_layers):
            if args.model == 'resgatedgcn':
                # ResGatedGCN (GatedGCN from Bresson et al. 2017)
                # 使用门控机制聚合邻居特征，支持边特征
                # 论文中在 view (b/c) 上表现稳定且准确
                self.layers.append(ResGatedGraphConv(
                    in_channels=self.hidden_dim,
                    out_channels=self.hidden_dim,
                    edge_dim=self.hidden_dim  # 使用编码后的边嵌入维度
                ))
            elif args.model == 'gat':
                # GAT (Graph Attention Network, Veličković et al. 2018)
                # 使用注意力机制加权邻居聚合，支持边特征
                # 论文中在 view (e) 上表现突出
                self.layers.append(GATConv(
                    in_channels=self.hidden_dim,
                    out_channels=self.hidden_dim,
                    heads=1,  # 单头注意力
                    edge_dim=self.hidden_dim,  # 使用编码后的边嵌入维度
                    fill_value='mean'  # 自环边特征填充方式
                ))
            elif args.model == 'gine':
                # GINE (Graph Isomorphism Network with Edge features)
                # 使用 MLP 聚合邻居和边特征，理论上对图同构任务最优
                # 论文中在 view (b) 上表现最佳
                mlp = MLP(
                    in_channels=self.hidden_dim,
                    hidden_channels=self.hidden_dim,
                    out_channels=self.hidden_dim,
                    num_layers=2,
                    norm=None,
                )
                self.layers.append(GINEConv(
                    nn=mlp,
                    train_eps=True,
                    edge_dim=self.hidden_dim  # 使用编码后的边嵌入维度
                ))
            else:
                raise ValueError(f'Unsupported model: {args.model}. '
                               f'Only GINE, ResGatedGCN, and GAT are supported.')

        # 池化函数（用于图级别任务）
        self.src_dst_agg = args.src_dst_agg
        if self.src_dst_agg == 'pooladd':
            self.pooling_fun = global_add_pool
        elif self.src_dst_agg == 'poolmean':
            self.pooling_fun = global_mean_pool

        # 输出层配置（解码器 Head）
        head_input_dim = (2 * self.hidden_dim
                          if self.task_level == 'edge' and self.src_dst_agg in ('concat', 'sumabs')
                          else self.hidden_dim)
        if self.task == 'regression':
            dim_out = 1  # 回归任务：输出单个值（如 HPWL, wire_length）
        elif self.task == 'classification':
            dim_out = args.num_classes  # 分类任务：输出类别数
        else:
            raise ValueError(f'Invalid task: {args.task}')

        # 头部 MLP（解码器）
        # 论文关键发现：3-4 层的 head 能显著提升准确性和稳定性
        self.head_layers = MLP(
            in_channels=head_input_dim,
            hidden_channels=self.hidden_dim,
            out_channels=dim_out,
            num_layers=args.num_head_layers,
            norm=None,
        )

        # 批归一化（可选）
        self.use_bn = args.use_bn
        if self.use_bn:
            self.bn_node = nn.BatchNorm1d(self.hidden_dim)

        # 激活函数
        if args.act_fn == 'relu':
            self.activation = nn.ReLU()
        elif args.act_fn == 'elu':
            self.activation = nn.ELU()
        elif args.act_fn == 'leakyrelu':
            self.activation = nn.LeakyReLU()
        else:
            raise ValueError(f'Invalid activation function: {args.act_fn}')

        # Dropout
        self.dropout = nn.Dropout(args.dropout)
        self.layer_norms = nn.ModuleList([
            nn.LayerNorm(self.hidden_dim) for _ in self.layers
        ]) if getattr(args, 'layer_norm', False) else None

    def forward(self, batch):
        """
        前向传播

        Args:
            batch: PyG Batch 对象，包含图数据

        Returns:
            pred: 预测结果
            true_class: 分类任务的真实标签（节点级别）
            true_label: 回归任务的真实标签（节点级别）
        """
        # 1. 特征编码
        batch = self.feature_encoder(batch)
        x = batch.x  # 节点特征 [num_nodes, feature_dim]
        edge_emb = batch.edge_attr  # 边特征 [num_edges, edge_dim]

        # 2. GNN 消息传递层
        for i, conv in enumerate(self.layers):
            # 所有三个模型都支持边特征
            # GINE, GAT, ResGatedGCN 都需要传入 edge_attr
            x = conv(x, batch.edge_index, edge_attr=edge_emb)

            if self.layer_norms is not None:
                x = self.layer_norms[i](x)

            # 批归一化
            if self.use_bn:
                x = self.bn_node(x)

            # 激活函数
            x = self.activation(x)

            # Dropout
            x = self.dropout(x)

        # 3. 根据任务级别处理输出
        if self.task_level == 'node':
            # 节点级别预测（Placement: 预测节点相关指标）
            # 有效节点为标签 != -1 的节点
            valid_mask = getattr(batch, 'valid_mask', torch.isfinite(batch.y) & (batch.y != -1)).clone()
            # NeighborLoader puts supervision seeds before context-only nodes.
            seed_count = getattr(batch, 'batch_size', batch.num_nodes)
            valid_mask[seed_count:] = False
            if not torch.isfinite(batch.y[valid_mask]).all():
                raise ValueError('Valid supervision contains nonfinite labels')
            pred = self.head_layers(x[valid_mask])

            # 准备标签
            if self.task == 'regression':
                true_label = batch.y[valid_mask]
                true_class = torch.full_like(true_label, -1, dtype=torch.long)
            else:  # classification
                true_class = batch.y[valid_mask].long()
                true_label = torch.full_like(true_class, -1, dtype=torch.float)

            return pred, true_class, true_label

        elif self.task_level == 'edge':
            # Supervision edges are identities only and are never message-passing
            # edges. This keeps post-route RC topology out of the encoder.
            src, dst = batch.edge_label_index
            src_emb = x[src]
            dst_emb = x[dst]

            if self.src_dst_agg == 'concat':
                edge_emb = torch.cat([src_emb, dst_emb], dim=1)
            elif self.src_dst_agg == 'sumabs':
                edge_emb = torch.cat([src_emb + dst_emb, torch.abs(src_emb - dst_emb)], dim=1)
            else:
                edge_emb = src_emb + dst_emb

            pred = self.head_layers(edge_emb)
            if not hasattr(batch, 'edge_label') or not torch.isfinite(batch.edge_label).all():
                raise ValueError('Edge supervision is missing or nonfinite')
            true_label = batch.edge_label
            true_class = torch.full_like(true_label, -1, dtype=torch.long)
            return pred, true_class, true_label

        elif self.task_level == 'graph':
            # 图级别预测
            graph_emb = self.pooling_fun(x, batch.batch)
            pred = self.head_layers(graph_emb)
            true_label = batch.global_y if hasattr(batch, 'global_y') else torch.zeros(len(graph_emb))
            return pred, true_label, true_label

        else:
            raise ValueError(f"Invalid task level: {self.task_level}")
