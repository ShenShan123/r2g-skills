"""
VeriHGN loss functions: binary weighted MSE + variance regularization.
"""

import torch


def weighted_mse(pred, target, alpha=10.0):
    if target.numel() == 0:
        return torch.tensor(0.0, device=pred.device)

    weights = torch.where(target > 0, alpha, 1.0)
    loss = (weights * (pred - target) ** 2).mean()
    return loss


def variance_reg(pred, target):
    """Penalize when prediction std < target std."""
    if pred.numel() < 2:
        return torch.tensor(0.0, device=pred.device)
    pred_std = pred.std()
    target_std = target.std()
    return torch.relu(target_std - pred_std) ** 2


def verihgn_loss(cell_pred, cell_target, grid_pred, grid_target,
                 alpha=10.0, lambda_var=0.5, **kwargs):
    """Total VeriHGN loss."""
    cell_wmse = weighted_mse(cell_pred, cell_target, alpha)
    grid_wmse = weighted_mse(grid_pred, grid_target, alpha)
    var_cell = variance_reg(cell_pred, cell_target)
    var_grid = variance_reg(grid_pred, grid_target)

    total = cell_wmse + grid_wmse + lambda_var * (var_cell + var_grid)

    return {
        'total': total,
        'cell_wmse': cell_wmse.item(),
        'grid_wmse': grid_wmse.item(),
        'var_reg': (lambda_var * (var_cell + var_grid)).item(),
    }
