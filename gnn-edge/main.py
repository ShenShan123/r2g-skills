# main.py
# Main entry point for training graph neural networks on R2G circuit graph datasets.
# This script orchestrates data loading, model creation, and training pipeline.
#
# Usage:
#     python main.py --dataset place_merged_B_homograph --task_level edge --model gine \
#                   --num_gnn_layers 4 --num_head_layers 2 --hid_dim 256 --epochs 100

import os
import argparse
import torch
import logging
from dataset import MergedHomographDataset  # Dataset class for loading processed circuit graphs
from sampling import dataset_sampling  # Sampling utilities for train/val/test splits
from model import GraphHead  # GNN model with encoder and decoder head
from downstream_train import downstream_train  # Training loop and evaluation
import sys
import atexit
from datetime import datetime

def parse_args():
    """Parse command line arguments for model training.

    Returns:
        argparse.Namespace: Parsed arguments
    """
    parser = argparse.ArgumentParser(description='Homograph Learning for R2G circuit graph datasets')

    # Data-related arguments (dataset selection)
    parser.add_argument('--data_root', type=str, default='data/',
                        help='Root directory for datasets')
    parser.add_argument('--dataset', type=str, default='place_merged_D_homograph',
                        choices=['place_merged_D_homograph', 'place_merged_E_homograph', 'place_merged_F_homograph',
                                'route_merged_B_homograph', 'route_merged_C_homograph', 'route_merged_D_homograph',
                                'route_merged_E_homograph', 'route_merged_F_homograph',
                                'route_B_homograph', 'route_C_homograph', 'route_E_homograph',
                                'place_B_homograph', 'place_C_homograph'],
                        help='Dataset name (e.g., place_merged_B_homograph, route_merged_B_homograph)')
    parser.add_argument('--task_level', type=str, default='edge',
                        choices=['node', 'edge', 'graph'],
                        help='Task level: node-level, edge-level, or graph-level prediction')
    parser.add_argument('--task', type=str, default='regression',
                        choices=['regression', 'classification'],
                        help='Task type: regression or classification')
    parser.add_argument('--num_classes', type=int, default=5,
                        help='Number of classes for classification task')

    # Sampling-related arguments
    parser.add_argument('--num_hops', type=int, default=2,
                        help='Number of hops for neighbor sampling')
    parser.add_argument('--num_neighbors', type=int, default=10,
                        help='Number of neighbors to sample at each hop')
    parser.add_argument('--batch_size', type=int, default=512,
                        help='Batch size for training')
    parser.add_argument('--num_workers', type=int, default=3,
                        help='Number of worker processes for data loading')
    parser.add_argument('--sample_ratio', type=float, default=1,
                        help='Node/edge sampling ratio (between 0 and 1)')
    parser.add_argument('--random_test', action='store_true',
                        help='Enable random split for test set')

    # Model-related arguments
    parser.add_argument('--model', type=str, default='resgatedgcn',
                        choices=['resgatedgcn', 'gat', 'gine'],
                        help='GNN model type: resgatedgcn (GatedGCN), gat, gine')
    parser.add_argument('--hid_dim', type=int, default=256,
                        help='Hidden dimension')
    parser.add_argument('--num_gnn_layers', type=int, default=4,
                        help='Number of GNN layers')
    parser.add_argument('--num_head_layers', type=int, default=2,
                        help='Number of MLP layers in decoder head')
    parser.add_argument('--src_dst_agg', type=str, default='add',
                        choices=['concat', 'add', 'mean', 'pooladd', 'poolmean', 'globalattn'],
                        help='Aggregation method for source and destination nodes (edge-level tasks)')
    parser.add_argument('--act_fn', type=str, default='leakyrelu',
                        choices=['relu', 'elu', 'leakyrelu'],
                        help='Activation function')
    parser.add_argument('--use_bn', action='store_true',
                        help='Use batch normalization')
    parser.add_argument('--dropout', type=float, default=0.3,
                        help='Dropout rate')
    parser.add_argument('--lr_scheduler', type=str, default='plateau',
                        choices=['step', 'cosine', 'plateau'],
                        help='Learning rate scheduler')
    
    # Training-related arguments
    parser.add_argument('--epochs', type=int, default=100,
                        help='Number of training epochs')
    parser.add_argument('--lr', type=float, default=0.0001,
                        help='Learning rate')
    parser.add_argument('--weight_decay', type=float, default=1e-5,
                        help='Weight decay (L2 regularization)')
    parser.add_argument('--gpu', type=int, default=2,
                        help='GPU ID, -1 for CPU')

    # Other arguments
    parser.add_argument('--save_dir', type=str, default='results',
                        help='Directory to save results')
    parser.add_argument('--seed', type=int, default=42,
                        help='Random seed')

    # Encoder-related arguments
    parser.add_argument('--node_encoder', type=str, default='homograph_node',
                        help='Node encoder type')
    parser.add_argument('--edge_encoder', type=str, default='homograph_edge',
                        help='Edge encoder type')
    parser.add_argument('--node_encoder_bn', action='store_true',
                        help='Apply batch normalization after node encoder')
    parser.add_argument('--edge_encoder_bn', action='store_true',
                        help='Apply batch normalization after edge encoder')

    return parser.parse_args()


class Tee:
    """Utility class to duplicate output to both console and file.

    This enables logging to both stdout/stderr and a log file simultaneously.
    """
    def __init__(self, stream, file):
        self.stream = stream
        self.file = file

    def write(self, data):
        self.stream.write(data)
        self.stream.flush()
        self.file.write(data)
        self.file.flush()

    def flush(self):
        self.stream.flush()
        self.file.flush()


def main():
    """Main training pipeline.

    This function orchestrates:
    1. Setup logging with Tee to file and console
    2. Set random seeds for reproducibility
    3. Load and preprocess the dataset
    4. Sample data into train/val/test splits
    5. Create and initialize the model
    6. Train the model and evaluate
    7. Save results and logs
    """
    args = parse_args()

    # Setup tee logging to file (logs/{num_head_layers}_{dataset}_{num_gnn_layers}_{act_fn}_{src_dst_agg}_{model}_{lr}_{timestamp}.log)
    time_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    logs_dir = os.path.join('logs')
    os.makedirs(logs_dir, exist_ok=True)
    log_file_path = os.path.join(
        logs_dir, f"{args.num_head_layers}_{args.dataset}_{args.num_gnn_layers}_{args.act_fn}_{args.src_dst_agg}_{args.model}_{args.lr}_{time_str}.log"
    )

    orig_stdout, orig_stderr = sys.stdout, sys.stderr
    log_fh = open(log_file_path, 'a', encoding='utf-8')
    sys.stdout = Tee(orig_stdout, log_fh)
    sys.stderr = Tee(orig_stderr, log_fh)
    print(vars(args))
    atexit.register(lambda: (setattr(sys, 'stdout', orig_stdout),
                             setattr(sys, 'stderr', orig_stderr),
                             log_fh.close()))

    # Set random seeds for reproducibility
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed(args.seed)
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False

    # Create save directory
    args.save_dir = os.path.join(args.save_dir, f"{args.dataset}_{args.model}_{args.task_level}")
    os.makedirs(args.save_dir, exist_ok=True)

    # Configure logging (output to stderr which is Tee'd to file)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(stream=sys.stderr)],
        force=True  # Overwrite to ensure only one handler
    )

    # Load dataset (using the MergedHomographDataset class)
    logging.info(f"Loading dataset {args.dataset}...")
    try:
        dataset = MergedHomographDataset(
            root=args.data_root,
            dataset_name=args.dataset,
            args=args,
            task_level=args.task_level,
            task_type=args.task
        )
        logging.info(f"Dataset loaded: {len(dataset)} graphs")
    except Exception as e:
        logging.error(f"Failed to load dataset: {str(e)}", exc_info=True)
        raise

    # Data sampling: create train/val/test loaders
    logging.info("Sampling data...")
    try:
        train_loader, val_loader, test_loader, max_label = dataset_sampling(args, dataset)
        logging.info(f"Data sampling completed. Train: {len(train_loader)}, Val: {len(val_loader)}, Test: {len(test_loader)}")
    except Exception as e:
        logging.error(f"Data sampling failed: {str(e)}", exc_info=True)
        raise

    # Create model
    logging.info("Creating model...")
    try:
        model = GraphHead(args)
        logging.info(f"Model created: {args.model} with {args.num_gnn_layers} layers")

        # Parameter count statistics (total, trainable, and per-module)
        total_params = sum(p.numel() for p in model.parameters())
        trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
        head_params = sum(p.numel() for p in model.head_layers.parameters())
        gnn_params = sum(p.numel() for p in model.layers.parameters())
        encoder_params = sum(p.numel() for p in model.feature_encoder.parameters())
        bn_params = sum(p.numel() for p in model.bn_node.parameters()) if getattr(model, "use_bn", False) else 0
        logging.info(
            f"Params | total={total_params:,} trainable={trainable_params:,} "
            f"head={head_params:,} gnn={gnn_params:,} encoders={encoder_params:,} bn={bn_params:,}"
        )

    except Exception as e:
        logging.error(f"Model creation failed: {str(e)}", exc_info=True)
        raise

    # Train model
    logging.info("Starting training...")
    try:
        test_metrics = downstream_train(args, model, train_loader, val_loader, test_loader, max_label)
    except Exception as e:
        logging.error(f"Training failed: {str(e)}", exc_info=True)
        raise

    # Log final results
    logging.info("Final test metrics:")
    for key, value in test_metrics.items():
        if key != 'report':
            logging.info(f"{key}: {value}")
    if 'report' in test_metrics:
        logging.info(f"Classification Report:\n{test_metrics['report']}")

    logging.info("Training completed!")


if __name__ == "__main__":
    main()
