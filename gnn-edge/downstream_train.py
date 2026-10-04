# downstream_train.py
# Training and evaluation functions for graph neural networks.
# This module implements the training loop, evaluation metrics, and model checkpointing.

import os
import torch
import logging
import numpy as np
import torch.nn.functional as F
from tqdm import tqdm
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.metrics import accuracy_score, f1_score, classification_report


def train_epoch(model, loader, optimizer, criterion, device, task, desc="Training"):
    """Train the model for one epoch.

    Args:
        model: PyTorch model
        loader: DataLoader
        optimizer: Optimizer
        criterion: Loss function
        device: Device (CPU/GPU)
        task: Task type ('regression' or 'classification')
        desc: Progress bar description

    Returns:
        Average loss over the epoch
    """
    model.train()
    total_loss = 0
    total_samples = 0

    for batch in tqdm(loader, desc=desc, leave=False):
        batch = batch.to(device)
        optimizer.zero_grad()

        # Forward pass
        pred, true_class, true_label = model(batch)

        # Compute loss only on valid labels (ignore labels == -1)
        if task == 'regression':
            mask = (true_label != -1)
            if mask.sum() == 0:
                continue  # Skip batch if no valid labels
            loss = criterion(pred[mask].squeeze(), true_label[mask].float())
            batch_samples = mask.sum().item()
        else:  # classification
            mask = (true_class != -1)
            if mask.sum() == 0:
                continue
            loss = criterion(pred[mask], true_class[mask])
            batch_samples = mask.sum().item()

        # Backward pass and optimization
        loss.backward()
        optimizer.step()

        total_loss += loss.item() * batch_samples
        total_samples += batch_samples

    # Compute average loss
    return total_loss / total_samples if total_samples > 0 else 0


def evaluate(model, loader, criterion, device, task, norm_params=None, desc="Evaluating"):
    """Evaluate model performance on a dataset.

    Args:
        model: PyTorch model
        loader: DataLoader
        criterion: Loss function
        device: Device (CPU/GPU)
        task: Task type ('regression' or 'classification')
        norm_params: Normalization parameters for inverse transform (regression only)
        desc: Progress bar description

    Returns:
        Tuple of (metrics_dict, predictions_array, labels_array)
    """
    model.eval()
    total_loss = 0
    all_preds = []
    all_labels = []
    total_samples = 0

    with torch.no_grad():
        for batch in tqdm(loader, desc=desc, leave=False):
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
                    # Convert predictions to original space
                    pred_raw = pred[mask].cpu().numpy().squeeze()
                    pred_raw = inverse_standardize(pred_raw, norm_params['mean'], norm_params['std'])
                    pred_raw = inverse_log_transform(pred_raw, norm_params['epsilon'])
                    pred_raw -= norm_params['offset']  # Remove offset
                    all_preds.append(pred_raw)

                    # Convert true labels to original space
                    true_raw = true_label[mask].cpu().numpy()
                    true_raw = inverse_standardize(true_raw, norm_params['mean'], norm_params['std'])
                    true_raw = inverse_log_transform(true_raw, norm_params['epsilon'])
                    true_raw -= norm_params['offset']
                    all_labels.append(true_raw)
                else:
                    # No inverse transform, use normalized values
                    all_preds.append(pred[mask].cpu().numpy().squeeze())
                    all_labels.append(true_label[mask].cpu().numpy())

                batch_samples = mask.sum().item()
            else:  # classification
                mask = (true_class != -1)
                if mask.sum() == 0:
                    continue
                loss = criterion(pred[mask], true_class[mask])

                # Get class predictions (argmax of softmax)
                all_preds.append(F.softmax(pred[mask], dim=1).argmax(dim=1).cpu().numpy())
                all_labels.append(true_class[mask].cpu().numpy())
                batch_samples = mask.sum().item()

            total_loss += loss.item() * batch_samples
            total_samples += batch_samples

    # Compute average loss
    avg_loss = total_loss / total_samples if total_samples > 0 else 0

    # Concatenate all batches
    if all_preds and all_labels:
        all_preds = np.concatenate(all_preds)
        all_labels = np.concatenate(all_labels)
    else:
        all_preds = np.array([])
        all_labels = np.array([])

    # Compute evaluation metrics
    metrics = {'loss': avg_loss}

    if task == 'regression' and len(all_preds) > 0:
        # Regression metrics
        metrics['mae'] = mean_absolute_error(all_labels, all_preds)
        metrics['rmse'] = np.sqrt(mean_squared_error(all_labels, all_preds))
        metrics['r2'] = r2_score(all_labels, all_preds)
    elif task == 'classification' and len(all_preds) > 0:
        # Classification metrics
        metrics['accuracy'] = accuracy_score(all_labels, all_preds)
        metrics['f1'] = f1_score(all_labels, all_preds, average='weighted')
        # Detailed classification report
        metrics['report'] = classification_report(all_labels, all_preds)

    return metrics, all_preds, all_labels


def downstream_train(args, model, train_loader, val_loader, test_loader, max_label=None):
    """Main training function with validation and early stopping.

    This function:
    1. Sets up optimizer and learning rate scheduler
    2. Trains the model for specified epochs
    3. Evaluates on validation set after each epoch
    4. Saves the best model based on validation loss
    5. Evaluates the best model on test set
    6. Saves test results

    Args:
        args: Configuration arguments
        model: PyTorch model
        train_loader: Training DataLoader
        val_loader: Validation DataLoader
        test_loader: Test DataLoader
        max_label: Maximum label value (for classification, unused)

    Returns:
        Test metrics dictionary
    """
    # Setup device
    device = torch.device(f"cuda:{args.gpu}" if torch.cuda.is_available() and args.gpu != -1 else "cpu")
    logging.info(f"Using device: {device}")
    model = model.to(device)

    # Get normalization parameters (for regression tasks)
    norm_params = None
    if args.task == 'regression' and hasattr(train_loader.dataset, 'norm_params'):
        norm_params = train_loader.dataset.norm_params

    # Define optimizer and loss function
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)

    # Learning rate scheduler
    if args.lr_scheduler == 'step':
        scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=20, gamma=0.5)
    elif args.lr_scheduler == 'cosine':
        scheduler = torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(
            optimizer, T_0=20, T_mult=1, eta_min=1e-5
        )
    elif args.lr_scheduler == 'plateau':
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer, mode='min', factor=0.5, patience=5, min_lr=1e-6
        )

    # Loss function
    if args.task == 'regression':
        criterion = torch.nn.SmoothL1Loss()  # Huber loss for regression
    else:
        criterion = torch.nn.CrossEntropyLoss()  # Cross entropy for classification

    # Create save directory
    os.makedirs(args.save_dir, exist_ok=True)

    # Early stopping based on validation loss
    best_val_loss = float('inf')
    best_model_path = os.path.join(args.save_dir, 'best_model.pt')

    # Track best test results
    best_test_metrics = None
    best_test_preds = None
    best_test_labels = None

    # Training loop
    for epoch in range(args.epochs):
        logging.info(f"\nEpoch {epoch+1}/{args.epochs}")
        logging.info("-" * 50)

        # Train for one epoch
        train_loss = train_epoch(model, train_loader, optimizer, criterion, device, args.task)

        # Evaluate on validation set
        val_metrics, _, _ = evaluate(model, val_loader, criterion, device, args.task, norm_params)

        # Update learning rate (plateau scheduler needs validation loss)
        if args.lr_scheduler == 'plateau':
            scheduler.step(val_metrics['loss'])
        else:
            scheduler.step()

        # Only evaluate train/test when validation improves
        if val_metrics['loss'] < best_val_loss:
            best_val_loss = val_metrics['loss']

            # Evaluate on training set (optional, for monitoring)
            train_metrics, _, _ = evaluate(model, train_loader, criterion, device, args.task, norm_params)

            # Evaluate on test set
            test_metrics, test_preds, test_labels = evaluate(model, test_loader, criterion, device, args.task, norm_params)

            # Print all metrics
            logging.info(f"Raw Train Metrics: {train_metrics}")
            logging.info(f"Raw Val Metrics: {val_metrics}")
            logging.info(f"Raw Test Metrics: {test_metrics}")

            # Save best model
            torch.save(model.state_dict(), best_model_path)
            logging.info(f"Saved best model with val loss: {best_val_loss:.4f}")

            # Record test results
            best_test_metrics = test_metrics
            best_test_preds = test_preds
            best_test_labels = test_labels

    # If no best test results were saved (shouldn't happen), load and evaluate
    if best_test_metrics is None:
        if os.path.exists(best_model_path):
            model.load_state_dict(torch.load(best_model_path, map_location=device))
            model = model.to(device)
        best_test_metrics, best_test_preds, best_test_labels = evaluate(
            model, test_loader, criterion, device, args.task, norm_params
        )

    # Save test results
    np.savez(os.path.join(args.save_dir, 'test_results.npz'), preds=best_test_preds, labels=best_test_labels)

    return best_test_metrics


# Import utility functions from dataset module
from dataset import inverse_standardize, inverse_log_transform
