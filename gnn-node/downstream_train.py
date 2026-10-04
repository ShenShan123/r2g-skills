# downstream_train.py
"""
R2G: Multi-View Circuit Graph Benchmark Suite - Training Module

Training and evaluation module, responsible for model training loop, validation, and testing.

Core Functions:
1. Training loop: Forward pass, loss calculation, backpropagation, optimizer update
2. Evaluation: Calculate metrics on train/validation/test sets
3. Learning rate scheduling: Plateau/Cosine/Step three strategies
4. Model saving: Save best model (based on validation loss)
5. Result output: Save evaluation metrics and prediction results

Evaluation Metrics:
- Regression: MAE, RMSE, R² (coefficient of determination)
- Classification: Accuracy, F1, Classification Report
"""

import os
import torch
import logging
import numpy as np
import torch.nn.functional as F
from tqdm import tqdm
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.metrics import accuracy_score, f1_score, classification_report


# ============================================================
# Training and evaluation functions
# ============================================================

def train_epoch(model, loader, optimizer, criterion, device, task):
    """
    Train one epoch

    Args:
        model: GNN model
        loader: Training data loader
        optimizer: Optimizer
        criterion: Loss function
        device: Computing device
        task: Task type ('regression' or 'classification')

    Returns:
        Average loss
    """
    model.train()
    total_loss = 0
    total_samples = 0

    for batch in tqdm(loader, desc="Training"):
        batch = batch.to(device)
        optimizer.zero_grad()

        pred, true_class, true_label = model(batch)

        # Only calculate loss for valid nodes (ignore nodes with label -1)
        if task == 'regression':
            mask = (true_label != -1)
            if mask.sum() == 0:
                continue  # No valid labels, skip this batch
            loss = criterion(pred[mask].squeeze(), true_label[mask].float())
            batch_samples = mask.sum().item()
        else:  # classification
            mask = (true_class != -1)
            if mask.sum() == 0:
                continue
            loss = criterion(pred[mask], true_class[mask])
            batch_samples = mask.sum().item()

        loss.backward()
        optimizer.step()
        total_loss += loss.item() * batch_samples
        total_samples += batch_samples

    # Calculate average loss
    return total_loss / total_samples if total_samples > 0 else 0

def evaluate(model, loader, criterion, device, task, norm_params=None):
    """Evaluate model performance"""
    model.eval()
    total_loss = 0
    all_preds = []
    all_labels = []
    total_samples = 0

    with torch.no_grad():
        for batch in tqdm(loader, desc="Evaluating"):
            batch = batch.to(device)
            pred, true_class, true_label = model(batch)

            # Only consider valid labels
            if task == 'regression':
                mask = (true_label != -1)
                if mask.sum() == 0:
                    continue
                loss = criterion(pred[mask].squeeze(), true_label[mask].float())

                # If normalization parameters are provided, convert predictions back to original space
                if norm_params is not None:
                    pred_raw = pred[mask].cpu().numpy().squeeze()
                    pred_raw = inverse_standardize(pred_raw, norm_params['mean'], norm_params['std'])
                    pred_raw = inverse_log_transform(pred_raw, norm_params['epsilon'])
                    pred_raw -= norm_params['offset']  # Remove offset
                    all_preds.append(pred_raw)

                    # Convert true labels
                    true_raw = true_label[mask].cpu().numpy()
                    true_raw = inverse_standardize(true_raw, norm_params['mean'], norm_params['std'])
                    true_raw = inverse_log_transform(true_raw, norm_params['epsilon'])
                    true_raw -= norm_params['offset']
                    all_labels.append(true_raw)
                else:
                    all_preds.append(pred[mask].cpu().numpy().squeeze())
                    all_labels.append(true_label[mask].cpu().numpy())

                batch_samples = mask.sum().item()
            else:
                mask = (true_class != -1)
                if mask.sum() == 0:
                    continue
                loss = criterion(pred[mask], true_class[mask])
                all_preds.append(F.softmax(pred[mask], dim=1).argmax(dim=1).cpu().numpy())
                all_labels.append(true_class[mask].cpu().numpy())
                batch_samples = mask.sum().item()

            total_loss += loss.item() * batch_samples
            total_samples += batch_samples

    # Calculate average loss
    avg_loss = total_loss / total_samples if total_samples > 0 else 0

    # Merge all batch results
    if all_preds and all_labels:
        all_preds = np.concatenate(all_preds)
        all_labels = np.concatenate(all_labels)
    else:
        all_preds = np.array([])
        all_labels = np.array([])

    # Calculate evaluation metrics
    metrics = {'loss': avg_loss}
    if task == 'regression' and len(all_preds) > 0:
        metrics['mae'] = mean_absolute_error(all_labels, all_preds)
        metrics['rmse'] = np.sqrt(mean_squared_error(all_labels, all_preds))
        metrics['r2'] = r2_score(all_labels, all_preds)
    elif task == 'classification' and len(all_preds) > 0:
        metrics['accuracy'] = accuracy_score(all_labels, all_preds)
        metrics['f1'] = f1_score(all_labels, all_preds, average='weighted')
        # Detailed classification report
        metrics['report'] = classification_report(all_labels, all_preds)

    return metrics, all_preds, all_labels

def downstream_train(args, model, train_loader, val_loader, test_loader, max_label=None):
    """Main model training function"""
    # More robust device selection and logging
    # In main.py, physical GPU index is mapped to in-process cuda:0 via CUDA_VISIBLE_DEVICES
    # Here strictly use GPU-only mode: require cuda:0, no fallback to CPU
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA not available in GPU-only mode, please check CUDA environment")
    num_gpus = torch.cuda.device_count()
    if num_gpus < 1:
        raise RuntimeError("No visible GPU after CUDA_VISIBLE_DEVICES is set")
    torch.cuda.set_device(0)
    device = torch.device("cuda:0")
    logging.info(f"Using device: {device} (mapped from physical GPU {args.gpu})")
    model = model.to(device)

    # Get normalization parameters (for regression tasks)
    norm_params = None
    if args.task == 'regression' and hasattr(train_loader.dataset, 'norm_params'):
        norm_params = train_loader.dataset.norm_params

    # Define optimizer and loss function
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    # Learning rate scheduler (select different strategies based on main.py parameters)
    if getattr(args, 'lr_scheduler', 'plateau') == 'plateau':
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer, mode='min', patience=getattr(args, 'lr_patience', 5), factor=getattr(args, 'lr_factor', 0.5), min_lr=getattr(args, 'lr_min', 1e-6)
        )
        scheduler_type = 'plateau'
    elif args.lr_scheduler == 'cosine':
        # CosineAnnealingLR needs total epoch count
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
            optimizer, T_max=args.epochs, eta_min=getattr(args, 'lr_min', 1e-6)
        )
        scheduler_type = 'cosine'
    elif args.lr_scheduler == 'step':
        scheduler = torch.optim.lr_scheduler.StepLR(
            optimizer, step_size=getattr(args, 'lr_step_size', 30), gamma=getattr(args, 'lr_gamma', 0.1)
        )
        scheduler_type = 'step'
    else:
        scheduler = None
        scheduler_type = 'none'

    if args.task == 'regression':
        criterion = torch.nn.SmoothL1Loss()
    else:
        criterion = torch.nn.CrossEntropyLoss()

    # Create save directory
    os.makedirs(args.save_dir, exist_ok=True)

    best_val_loss = float('inf')
    best_train_loss = float('inf')
    best_train_epoch = -1
    best_val_epoch = -1
    # Use "minimum validation loss" as best epoch criterion
    best_val_loss_for_selection = float('inf')
    best_val_epoch_for_selection = -1

    # Final test set results are obtained by loading "best validation model" for evaluation after training ends.
    # Here we don't track "best test loss/epoch" during training to avoid confusion with model selection criteria.

    # Record three sets of metrics for "best validation" corresponding epoch, for easy summary at training end
    # Three sets of metrics corresponding to "best epoch (by minimum validation loss)"
    best_epoch_train_metrics = None
    best_epoch_val_metrics = None
    best_epoch_test_metrics = None
    best_model_path = os.path.join(args.save_dir, 'best_model.pt')

    # Training loop
    for epoch in range(args.epochs):
        logging.info(f"\nEpoch {epoch+1}/{args.epochs}")
        logging.info("-" * 50)

        # Train one epoch (update parameters)
        _train_loss_epoch = train_epoch(model, train_loader, optimizer, criterion, device, args.task)
        # Evaluate Train set after training to get comparable loss
        train_metrics_epoch, _, _ = evaluate(model, train_loader, criterion, device, args.task, norm_params)
        logging.info(f"Train Loss: {train_metrics_epoch['loss']:.4f}")
        # Only print loss for training set, not other metrics

        # Record best training loss (using comparable loss from evaluation)
        if train_metrics_epoch['loss'] < best_train_loss:
            best_train_loss = train_metrics_epoch['loss']
            best_train_epoch = epoch

        # Validation
        val_metrics_epoch, _, _ = evaluate(model, val_loader, criterion, device, args.task, norm_params)
        logging.info(f"Val Loss: {val_metrics_epoch['loss']:.4f}")
        if args.task == 'regression':
            logging.info(f"Val MAE: {val_metrics_epoch['mae']:.4f}, Val RMSE: {val_metrics_epoch['rmse']:.4f}, Val R2: {val_metrics_epoch['r2']:.4f}")
        else:
            logging.info(f"Val Accuracy: {val_metrics_epoch['accuracy']:.4f}, Val F1: {val_metrics_epoch['f1']:.4f}")

        # Test set evaluation: evaluate every 10 epochs for "best test loss" selection
        test_metrics_epoch = {'loss': float('inf')}
        if (epoch + 1) % 10 == 0 or epoch == args.epochs - 1:
            test_metrics_epoch, _, _ = evaluate(model, test_loader, criterion, device, args.task, norm_params)
            logging.info(f"Test Loss: {test_metrics_epoch['loss']:.4f}")
            if args.task == 'regression':
                logging.info(f"Test MAE: {test_metrics_epoch['mae']:.4f}, Test RMSE: {test_metrics_epoch['rmse']:.4f}, Test R2: {test_metrics_epoch['r2']:.4f}")
            else:
                logging.info(f"Test Accuracy: {test_metrics_epoch['accuracy']:.4f}, Test F1: {test_metrics_epoch['f1']:.4f}")

        else:
            # Use inf loss during non-evaluation epochs to avoid affecting best epoch selection
            test_metrics_epoch = {'loss': float('inf')}

        # Learning rate scheduling
        if scheduler_type == 'plateau':
            scheduler.step(val_metrics_epoch['loss'])
        elif scheduler_type in ['cosine', 'step']:
            scheduler.step()
        # Linear warmup (optional, linearly increase lr for first warmup_epochs)
        if getattr(args, 'warmup_epochs', 0) > 0 and epoch < args.warmup_epochs:
            # Simple linear warmup, scale current lr to (epoch+1)/warmup_epochs ratio
            warm_ratio = float(epoch + 1) / float(args.warmup_epochs)
            for pg in optimizer.param_groups:
                base_lr = args.lr
                # Fix: removed extra quotes after 1e-6 to prevent syntax errors and bracket parsing exceptions
                pg['lr'] = max(getattr(args, 'lr_min', 1e-6), base_lr * warm_ratio)

        # Save best model
        if val_metrics_epoch['loss'] < best_val_loss:
            best_val_loss = val_metrics_epoch['loss']
            best_val_epoch = epoch
            torch.save(model.state_dict(), best_model_path)
            logging.info(f"Saved best model with val loss: {best_val_loss:.4f}")

        # Record three sets of metrics for epoch corresponding to "best validation loss"
        if val_metrics_epoch['loss'] < best_val_loss_for_selection:
            best_val_loss_for_selection = val_metrics_epoch['loss']
            best_val_epoch_for_selection = epoch
            best_epoch_train_metrics = train_metrics_epoch
            best_epoch_val_metrics = val_metrics_epoch
            best_epoch_test_metrics = test_metrics_epoch

    # Load best model and evaluate on Train/Val/Test
    model.load_state_dict(torch.load(best_model_path))
    train_metrics, train_preds, train_labels = evaluate(model, train_loader, criterion, device, args.task, norm_params)
    val_metrics_final, val_preds, val_labels = evaluate(model, val_loader, criterion, device, args.task, norm_params)
    test_metrics, test_preds, test_labels = evaluate(model, test_loader, criterion, device, args.task, norm_params)

    # Print "Best Epoch Summary"
    logging.info("\nBest Epoch Summary (based on min Validation Loss):")
    logging.info("-" * 50)
    logging.info(f"Best Epoch: {best_val_epoch_for_selection + 1}")
    if args.task == 'regression':
        if best_epoch_train_metrics:
            logging.info(f"[Train] Loss: {best_epoch_train_metrics['loss']:.4f}, MAE: {best_epoch_train_metrics.get('mae', 0):.4f}, RMSE: {best_epoch_train_metrics.get('rmse', 0):.4f}, R2: {best_epoch_train_metrics.get('r2', 0):.4f}")
        if best_epoch_val_metrics:
            logging.info(f"[Val]   Loss: {best_epoch_val_metrics['loss']:.4f}, MAE: {best_epoch_val_metrics.get('mae', 0):.4f}, RMSE: {best_epoch_val_metrics.get('rmse', 0):.4f}, R2: {best_epoch_val_metrics.get('r2', 0):.4f}")

    else:
        if best_epoch_train_metrics:
            logging.info(f"[Train] Accuracy: {best_epoch_train_metrics.get('accuracy', 0):.4f}, F1: {best_epoch_train_metrics.get('f1', 0):.4f}")
        if best_epoch_val_metrics:
            logging.info(f"[Val]   Accuracy: {best_epoch_val_metrics.get('accuracy', 0):.4f}, F1: {best_epoch_val_metrics.get('f1', 0):.4f}")

    # Save evaluation results
    np.savez(os.path.join(args.save_dir, 'test_results.npz'), preds=test_preds, labels=test_labels)
    np.savez(os.path.join(args.save_dir, 'train_eval.npz'), preds=train_preds, labels=train_labels)
    np.savez(os.path.join(args.save_dir, 'val_eval.npz'), preds=val_preds, labels=val_labels)

    # Return complete results for main function logging and plotting
    return {
        'best_train_loss': best_train_loss,
        'best_train_epoch': best_train_epoch + 1,
        # Define best_epoch as epoch with "minimum validation loss"
        'best_epoch': best_val_epoch_for_selection + 1,
        'best_val_loss': best_val_loss_for_selection,
        'best_val_epoch': best_val_epoch_for_selection + 1,
        'best_epoch_train_metrics': best_epoch_train_metrics,
        'best_epoch_val_metrics': best_epoch_val_metrics,
        'best_epoch_test_metrics': best_epoch_test_metrics,
        'train_metrics': train_metrics,
        'train_preds': train_preds,
        'train_labels': train_labels,
        'val_metrics': val_metrics_final,
        'val_preds': val_preds,
        'val_labels': val_labels,
        'test_metrics': test_metrics,
        'test_preds': test_preds,
        'test_labels': test_labels,
    }

# Import utility functions from dataset
from dataset import inverse_standardize, inverse_log_transform