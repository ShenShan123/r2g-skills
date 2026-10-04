# main.py
"""
R2G: Multi-View Circuit Graph Benchmark Suite - Training Script

Training script for GNN models on circuit graphs for placement and routing tasks.

Main Features:
1. Load circuit graph datasets (converted from DEF files)
2. Support node-level (placement) and edge-level (routing) tasks
3. Support three baseline models: GINE, ResGatedGCN, GAT
4. Provide ablation experiment scripts: GNN layers and Head layers comparison

Key Findings from CVPR 2026 Paper:
- View (b) performs best on placement and routing tasks
- GNN depth: 3-4 layers are sufficient, deeper layers cause over-smoothing
- Head depth: 3-4 layers significantly improve accuracy and stability

Experiment Scripts:
- run_train_gnn_layers.sh: GNN layers ablation (3, 4, 5, 6 layers)
- run_train_head.sh: Head layers ablation (1, 2, 3, 4 layers)
"""

import os
import argparse
import logging
from datetime import datetime
import numpy as np
import sys

def parse_args():
    parser = argparse.ArgumentParser(description='Homograph Learning for merged datasets')
    # Data-related parameters (new: support specifying dataset name)
    parser.add_argument('--data_root', type=str, default='data/', help='Dataset root directory')
    parser.add_argument('--dataset', type=str, default='place_B_homograph', help='Dataset name')
    # No longer support explicit dataset_path, unified layout <data_root>/raw/<dataset>.pt
    parser.add_argument('--task_level', type=str, default='node', choices=['node', 'edge', 'graph'], help='Task level')
    parser.add_argument('--task', type=str, default='regression', choices=['regression', 'classification'], help='Task type')
    parser.add_argument('--num_classes', type=int, default=5, help='Number of classes for classification tasks')

    # Sampling-related parameters
    parser.add_argument('--num_hops', type=int, default=2, help='Number of hops for neighbor sampling')
    parser.add_argument('--num_neighbors', type=int, default=10, help='Number of neighbors per hop')
    parser.add_argument('--batch_size', type=int, default=1024, help='Batch size')
    parser.add_argument('--num_workers', type=int, default=4, help='Number of data loading workers')
    parser.add_argument('--sample_ratio', type=float, default=1.0, help='Node sampling ratio (between 0-1)')
    parser.add_argument('--random_test', action='store_true', help='Enable random test set split')
    # Test/Validation split control (new)
    parser.add_argument('--test_ratio', type=float, default=0.2, help='Random test set ratio (only effective when --random_test is True)')
    parser.add_argument('--val_ratio', type=float, default=0.2, help='Validation set ratio (random split when not fixed)')
    parser.add_argument('--fixed_test_ids', type=str, default='25, 27, 29, 6, 20, 15, 7, 13', help='Fixed test set graph_id list, comma-separated, e.g., "2,9,21"; overrides --random_test when provided')
    parser.add_argument('--fixed_val_ids', type=str, default='5, 14, 1, 24, 26', help='Fixed validation set graph_id list, comma-separated, e.g., "1,3,5"')

    # Model-related parameters
    # Three baseline models based on CVPR 2026 paper
    # Paper findings: GINE and ResGatedGCN perform best on view (b); GAT has advantage on view (e)
    parser.add_argument('--model', type=str, default='resgatedgcn',
                     choices=['resgatedgcn', 'gat', 'gine'],
                     help='GNN model type: resgatedgcn (GatedGCN), gat, gine')
    parser.add_argument('--hid_dim', type=int, default=128, help='Hidden layer dimension')
    parser.add_argument('--num_gnn_layers', type=int, default=3,
                     help='GNN message passing layer depth (encoder depth). Paper recommends 3-4 layers, deeper layers cause over-smoothing')
    parser.add_argument('--num_head_layers', type=int, default=2,
                     help='Head MLP layers (decoder depth). Paper recommends 3-4 layers for best performance')
    parser.add_argument('--src_dst_agg', type=str, default='concat', choices=['concat', 'add', 'pooladd', 'poolmean', 'globalattn'], help='Aggregation method for source and destination nodes')

    parser.add_argument('--use_bn', action='store_true', help='Use batch normalization')
    parser.add_argument('--dropout', type=float, default=0.2, help='Dropout rate')
    parser.add_argument('--act_fn', type=str, default='leakyrelu', choices=['relu', 'elu', 'leakyrelu'],help='Activation function type (default: relu)')

    # Training-related parameters
    parser.add_argument('--epochs', type=int, default=100, help='Number of training epochs')
    parser.add_argument('--lr', type=float, default=0.001, help='Learning rate')
    parser.add_argument('--weight_decay', type=float, default=1e-5, help='Weight decay')
    parser.add_argument('--gpu', type=int, default=0, help='GPU index, -1 for CPU')

    # Learning rate scheduler parameters (new)
    parser.add_argument('--lr_scheduler', type=str, default='plateau', choices=['none', 'plateau', 'cosine', 'step'], help='Learning rate scheduler type')
    parser.add_argument('--lr_patience', type=int, default=5, help='Patience for ReduceLROnPlateau')
    parser.add_argument('--lr_factor', type=float, default=0.5, help='Factor for ReduceLROnPlateau')
    parser.add_argument('--lr_min', type=float, default=1e-6, help='Minimum learning rate (for Cosine/Plateau)')
    parser.add_argument('--lr_step_size', type=int, default=30, help='Step size for StepLR')
    parser.add_argument('--lr_gamma', type=float, default=0.1, help='Gamma for StepLR')
    parser.add_argument('--warmup_epochs', type=int, default=0, help='Number of warmup epochs (0 means no warmup)')

    # Whether to print test metrics each epoch (default on, can be disabled with --no_log_test_each_epoch)
    parser.add_argument('--log_test_each_epoch', dest='log_test_each_epoch', action='store_true', help='Print test set metrics each epoch during training')
    parser.add_argument('--no_log_test_each_epoch', dest='log_test_each_epoch', action='store_false', help='Disable test set evaluation and logging each epoch')

    # Other parameters
    parser.add_argument('--save_dir', type=str, default='results', help='Results save directory')
    parser.add_argument('--seed', type=int, default=42, help='Random seed')

    # Encoder-related parameters
    parser.add_argument('--node_encoder', type=str, default='homograph_node', help='Node encoder type')
    parser.add_argument('--edge_encoder', type=str, default='homograph_edge', help='Edge encoder type')
    parser.add_argument('--node_encoder_bn', action='store_true', help='Use batch normalization after node encoder')
    parser.add_argument('--edge_encoder_bn', action='store_true', help='Use batch normalization after edge encoder')

    # Enable test metrics printing each epoch by default
    parser.set_defaults(log_test_each_epoch=True)
    args = parser.parse_args()

    # Parse comma-separated fixed ID list
    def parse_id_list(s: str):
        if s is None:
            return []
        s = s.strip()
        if s == '':
            return []
        out = []
        for t in s.split(','):
            t = t.strip()
            if t == '':
                continue
            try:
                out.append(int(t))
            except ValueError:
                print(f"[WARN] Invalid graph_id: '{t}', ignored")
        return out

    args.fixed_test_ids_list = parse_id_list(getattr(args, 'fixed_test_ids', ''))
    args.fixed_val_ids_list = parse_id_list(getattr(args, 'fixed_val_ids', ''))

    # If fixed test IDs are provided, disable random test split
    if len(args.fixed_test_ids_list) > 0:
        args.random_test = False

    # Validation protection
    if args.test_ratio <= 0 or args.test_ratio >= 1:
        print(f"[WARN] Invalid test_ratio={args.test_ratio}, reset to 0.2")
        args.test_ratio = 0.2
    if args.val_ratio <= 0 or args.val_ratio >= 1:
        print(f"[WARN] Invalid val_ratio={args.val_ratio}, reset to 0.2")
        args.val_ratio = 0.2

    return args

def log_and_save_run_info(args):
    """
    Write only three key pieces of information to train.log:
      1) This training validation set: <ID list or (random split)>
      2) This training test set: <ID list or (random split)>
      3) This training hyperparameters: <original CLI parameter string (after python main.py)>
    """
    try:
        # 1) Restore the parameter string entered by user in command line (after python main.py)
        cli_args = " ".join(sys.argv[1:])

        # 2) Validation/test set ID lists (if empty, mark as random split)
        val_list = getattr(args, 'fixed_val_ids_list', [])
        test_list = getattr(args, 'fixed_test_ids_list', [])
        val_str = ", ".join(str(x) for x in val_list) if len(val_list) > 0 else "(random split)"
        test_str = ", ".join(str(x) for x in test_list) if len(test_list) > 0 else "(random split)"

        logging.info(f"This training validation set: {val_str}")
        logging.info(f"This training test set: {test_str}")
        logging.info(f"This training hyperparameters: {cli_args}")
    except Exception as e:
        logging.error(f"Failed to log run info: {str(e)}", exc_info=True)

def main():
    args = parse_args()

    # Clean up environment variables that may cause default device out-of-bounds before importing torch
    for env_key in [
        'CUDA_DEVICE',            # Old CUDA default device variable, may cause default device to be invalid index
        'CUDA_DEFAULT_DEVICE',    # Non-standard but possibly existing variable
    ]:
        if env_key in os.environ:
            del os.environ[env_key]

    # Limit visible GPUs through CUDA_VISIBLE_DEVICES to avoid PyTorch default device out-of-bounds
    # GPU-only mode: no CPU fallback, report error directly if invalid index is passed
    if args.gpu < 0:
        raise ValueError("GPU-only mode: Please provide a valid GPU index (e.g., 0, 1, 2), CPU (-1) is not supported")
    os.environ['CUDA_VISIBLE_DEVICES'] = str(args.gpu)

    # Delayed import to ensure environment variables are cleaned
    import torch
    from dataset import MergedHomographDataset  # Changed to new dataset class
    from sampling import dataset_sampling
    from model import GraphHead
    from downstream_train import downstream_train

    # Set CUDA device first to avoid default device out-of-bounds causing DeferredCudaCallError
    if not torch.cuda.is_available():
        raise RuntimeError("No available CUDA device detected, please confirm GPU driver and CUDA are installed and select a valid GPU index")

    # After setting CUDA_VISIBLE_DEVICES, visible devices in process should be 1; mapping to cuda:0 corresponds to physical GPU args.gpu
    num_gpus = torch.cuda.device_count()
    if num_gpus < 1:
        raise RuntimeError("No visible GPU after CUDA_VISIBLE_DEVICES is set, please confirm the passed GPU index is valid")

    # Set to device 0 in process (mapped to physical GPU args.gpu)
    # Note: Physical GPU is mapped to cuda:0 in process through CUDA_VISIBLE_DEVICES, so this is fixed at 0.
    # To switch physical GPU, pass via startup parameter --gpu N instead of modifying index here.
    torch.cuda.set_device(0)
    print(f"[INFO] Selected physical GPU: {args.gpu}; visible GPUs: {num_gpus}; using device: cuda:0")

    # Set random seed (after setting device to avoid CUDA initialization on wrong device)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed(args.seed)
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
    # Ensure numpy random split is reproducible
    np.random.seed(args.seed)

    # Create save directory (using name without extension)
    dataset_key = os.path.splitext(args.dataset)[0]
    # If user provided dataset_path, override dataset_key with its basename to unify result directory naming
    args.save_dir = os.path.join(args.save_dir, f"{dataset_key}_{args.model}_head{args.num_head_layers}_hid{args.hid_dim}_layers{args.num_gnn_layers}_scheduler_{args.lr_scheduler}")
    os.makedirs(args.save_dir, exist_ok=True)

    # 配置日志
    logging.basicConfig(
        filename=os.path.join(args.save_dir, 'train.log'),
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(console_handler)

    # Record and save split and hyperparameters for this run (including CLI)
    log_and_save_run_info(args)

    # Load dataset (core modification: use new dataset class and pass name)
    logging.info(f"Loading dataset {args.dataset}...")
    try:
        dataset = MergedHomographDataset(
            root=args.data_root,
            dataset_name=args.dataset,  # Support 'place_B_homograph' or 'place_B_homograph.pt'
            args=args,
            task_level=args.task_level,
            task_type=args.task,
            # Use unified layout, no longer pass dataset_path
        )
        logging.info(f"Dataset loaded: {len(dataset)} graphs")
    except Exception as e:
        logging.error(f"Failed to load dataset: {str(e)}", exc_info=True)
        raise

    # Data sampling
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

        # New: print complete model parameters (including all components)
        total_params = sum(p.numel() for p in model.parameters())
        logging.info(f"Total trainable parameters: {total_params:,}")  # Formatted display

    except Exception as e:
        logging.error(f"Model creation failed: {str(e)}", exc_info=True)
        raise

    # Train model
    logging.info("Starting training...")
    try:
        results = downstream_train(args, model, train_loader, val_loader, test_loader, max_label)
    except Exception as e:
        logging.error(f"Training failed: {str(e)}", exc_info=True)
        raise

    # Print final test results
    logging.info("\nFinal Test Result:")
    logging.info("-" * 50)
    test_metrics = results['test_metrics']
    if args.task == 'regression':
        logging.info(f"Test Loss: {test_metrics['loss']:.4f}")
        logging.info(f"Test MAE: {test_metrics.get('mae', float('nan')):.4f}")
        logging.info(f"Test RMSE: {test_metrics.get('rmse', float('nan')):.4f}")
        logging.info(f"Test R2: {test_metrics.get('r2', float('nan')):.4f}")
    else:
        logging.info(f"Test Accuracy: {test_metrics.get('accuracy', 0):.4f}")
        logging.info(f"Test F1: {test_metrics.get('f1', 0):.4f}")
        if 'report' in test_metrics:
            logging.info(f"Test Classification Report:\n{test_metrics['report']}")

    # Plot and save scatter plots (train/val/test)
    if args.task == 'regression':
        try:
            import matplotlib
            matplotlib.use('Agg')  # No display in server environment
            import matplotlib.pyplot as plt

            # Subfolder naming: includes spec and timestamp
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            plots_dir = os.path.join(args.save_dir, f"plots_{args.model}_H{args.hid_dim}_L{args.num_gnn_layers}_{ts}")
            os.makedirs(plots_dir, exist_ok=True)

            def plot_scatter(y_true, y_pred, title, out_path):
                if y_true is None or y_pred is None or len(y_true) == 0:
                    return
                y_true = np.array(y_true).reshape(-1)
                y_pred = np.array(y_pred).reshape(-1)
                plt.figure(figsize=(6,6))
                plt.scatter(y_true, y_pred, s=6, alpha=0.5, c='tab:blue', edgecolors='none')
                # Diagonal line
                mn = float(min(y_true.min(), y_pred.min()))
                mx = float(max(y_true.max(), y_pred.max()))
                plt.plot([mn, mx], [mn, mx], 'r--', linewidth=1)
                plt.xlabel('True')
                plt.ylabel('Predicted')
                plt.title(title)
                plt.grid(True, linestyle='--', alpha=0.3)
                plt.tight_layout()
                plt.savefig(out_path, dpi=150)
                plt.close()

            plot_scatter(results['train_labels'], results['train_preds'], f"Train Scatter ({args.model})", os.path.join(plots_dir, 'train_scatter.png'))
            plot_scatter(results['val_labels'], results['val_preds'], f"Val Scatter ({args.model})", os.path.join(plots_dir, 'val_scatter.png'))
            plot_scatter(results['test_labels'], results['test_preds'], f"Test Scatter ({args.model})", os.path.join(plots_dir, 'test_scatter.png'))
            logging.info(f"Saved scatter plots to: {plots_dir}")
        except Exception as e:
            logging.error(f"Failed to create scatter plots: {str(e)}", exc_info=True)
    else:
        logging.info("Classification task detected: skip scatter plots.")

    logging.info("Training completed!")

if __name__ == "__main__":
    main()
