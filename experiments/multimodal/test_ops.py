import torch
from mm_models import splat, gather, scatter_mean, build
torch.manual_seed(0)
nx, ny, R, C = 7, 5, 30, 3
x0 = torch.randint(0, nx, (R,)); y0 = torch.randint(0, ny, (R,))
x1 = torch.minimum(x0 + torch.randint(0, 4, (R,)), torch.tensor(nx - 1)); y1 = torch.minimum(y0 + torch.randint(0, 3, (R,)), torch.tensor(ny - 1))
rects = torch.stack([x0, y0, x1, y1], 1); vals = torch.randn(R, C, dtype=torch.float64)
ref = torch.zeros(C, nx, ny, dtype=torch.float64); refd = ref.clone()
for r in range(R):
    a = (x1[r] - x0[r] + 1) * (y1[r] - y0[r] + 1)
    ref[:, x0[r]:x1[r] + 1, y0[r]:y1[r] + 1] += vals[r][:, None, None]
    refd[:, x0[r]:x1[r] + 1, y0[r]:y1[r] + 1] += vals[r][:, None, None] / a
assert torch.allclose(splat(rects, vals, nx, ny, density=False), ref), "splat"
assert torch.allclose(splat(rects, vals, nx, ny, density=True), refd), "splat density"
grid = torch.randn(C, nx, ny, dtype=torch.float64)
gref = torch.stack([grid[:, x0[r]:x1[r] + 1, y0[r]:y1[r] + 1].mean((1, 2)) for r in range(R)])
assert torch.allclose(gather(grid, rects), gref), "gather"
idx = torch.tensor([0, 2, 2, 1, 0]); src = torch.arange(10.).view(5, 2)
assert torch.allclose(scatter_mean(src, idx, 3), torch.tensor([[4., 5.], [6., 7.], [3., 4.]])), "scatter_mean"
# models run forward/backward on a toy graph
g = {"master": torch.randint(0, 10, (40,)), "cell_num": torch.randn(40, 6), "net_num": torch.randn(15, 3),
     "pin_cell": torch.randint(0, 40, (90,)), "pin_net": torch.randint(0, 15, (90,)), "pin_xy": torch.rand(90, 2),
     "cell_foot": torch.stack([torch.randint(0, 20, (40,))] * 2 + [torch.randint(0, 20, (40,))] * 2, 1),
     "net_foot": torch.tensor([[0, 0, 19, 18]] * 15)}
for m in ("unet", "gnn", "graft", "exchange"):
    net = build(m, 10); y = net(torch.randn(1, 5, 20, 19), g); y.mean().backward()
    assert y.shape == (1, 1, 20, 19), (m, y.shape)
print("all ops and model shapes OK")
