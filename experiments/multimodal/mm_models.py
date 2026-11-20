"""Multimodal (netlist graph + layout image) congestion models. Pure PyTorch, no PyG.

Graph <-> grid operators act on axis-aligned gcell RECTANGLES (a cell's footprint, a net's bounding box):
  splat(rects, vals, nx, ny)  spreads each rect's value uniformly over its gcells (2D difference array + cumsum)
  gather(grid, rects)          mean of the grid over each rect (integral image)
Both are O(#rects + grid) and differentiable; a die-spanning net costs the same as a 1-gcell one.

Models (all predict a [1, nx, ny] utilisation map):
  unet      image only (5 placement channels), 4-level U-Net, the paper's CNN baseline
  gnn       netlist only: bipartite cell<->net message passing, embeddings splatted to the grid, conv head
  graft     Lay-Net-style one-way grafting: graph embeddings splatted onto the grid and injected into the U-Net
            encoder at every scale
  exchange  ours: two-way cross-modal exchange. At each encoder scale the CNN feature map is gathered into the
            nets (rect mean), nets are updated by one more round of message passing with that layout context,
            and the result is splatted back into the next scale. Netlist and layout refine each other instead of
            the netlist being computed once and pasted in.
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


# ---------------------------------------------------------------- rect operators
def splat(rects: torch.Tensor, vals: torch.Tensor, nx: int, ny: int, density: bool = True) -> torch.Tensor:
    """rects [R,4] (x0,y0,x1,y1 inclusive gcell idx), vals [R,C] -> grid [C,nx,ny].
    density=True divides each value by its rect area (RUDY-style), else adds it to every covered gcell."""
    if rects.numel() == 0:
        return vals.new_zeros(vals.shape[1], nx, ny)
    x0, y0 = rects[:, 0].clamp(0, nx - 1), rects[:, 1].clamp(0, ny - 1)
    x1, y1 = rects[:, 2].clamp(0, nx - 1) + 1, rects[:, 3].clamp(0, ny - 1) + 1
    v = vals
    if density:
        area = ((x1 - x0) * (y1 - y0)).clamp(min=1).to(vals.dtype)
        v = vals / area[:, None]
    C = v.shape[1]
    d = vals.new_zeros(C, (nx + 1) * (ny + 1))
    W = ny + 1
    for idx, sgn in ((x0 * W + y0, 1.0), (x1 * W + y0, -1.0), (x0 * W + y1, -1.0), (x1 * W + y1, 1.0)):
        d.index_add_(1, idx, (sgn * v).t())
    d = d.view(C, nx + 1, ny + 1).cumsum(1).cumsum(2)
    return d[:, :nx, :ny]


def gather(grid: torch.Tensor, rects: torch.Tensor) -> torch.Tensor:
    """grid [C,nx,ny], rects [R,4] -> [R,C] mean over each rect."""
    C, nx, ny = grid.shape
    I = F.pad(grid.cumsum(1).cumsum(2), (1, 0, 1, 0))          # [C,nx+1,ny+1]
    x0, y0 = rects[:, 0].clamp(0, nx - 1), rects[:, 1].clamp(0, ny - 1)
    x1, y1 = rects[:, 2].clamp(0, nx - 1) + 1, rects[:, 3].clamp(0, ny - 1) + 1
    s = I[:, x1, y1] - I[:, x0, y1] - I[:, x1, y0] + I[:, x0, y0]
    area = ((x1 - x0) * (y1 - y0)).clamp(min=1).to(grid.dtype)
    return (s / area).t()


def scatter_mean(src: torch.Tensor, index: torch.Tensor, n: int) -> torch.Tensor:
    out = src.new_zeros(n, src.shape[1]).index_add_(0, index, src)
    cnt = src.new_zeros(n).index_add_(0, index, torch.ones_like(index, dtype=src.dtype)).clamp(min=1)
    return out / cnt[:, None]


def mlp(i, o, h=None):
    h = h or o
    return nn.Sequential(nn.Linear(i, h), nn.GELU(), nn.Linear(h, o))


# ---------------------------------------------------------------- graph encoder
class NetlistEncoder(nn.Module):
    """Bipartite cell<->net message passing over pins (a hypergraph with nets as hyperedges)."""

    def __init__(self, n_masters: int, d: int = 32, layers: int = 3):
        super().__init__()
        self.emb = nn.Embedding(n_masters + 2, 16)
        self.cell_in = mlp(16 + 6, d)
        self.net_in = mlp(3, d)
        self.c2n = nn.ModuleList(mlp(2 * d + 2, d) for _ in range(layers))
        self.n2c = nn.ModuleList(mlp(2 * d, d) for _ in range(layers))
        self.norm_c = nn.ModuleList(nn.LayerNorm(d) for _ in range(layers))
        self.norm_n = nn.ModuleList(nn.LayerNorm(d) for _ in range(layers))

    def forward(self, g):
        c = self.cell_in(torch.cat([self.emb(g["master"]), g["cell_num"]], 1))
        n = self.net_in(g["net_num"])
        for c2n, n2c, nc, nn_ in zip(self.c2n, self.n2c, self.norm_c, self.norm_n):
            msg = c2n(torch.cat([c[g["pin_cell"]], n[g["pin_net"]], g["pin_xy"]], 1))
            n = nn_(n + scatter_mean(msg, g["pin_net"], n.shape[0]))
            back = n2c(torch.cat([n[g["pin_net"]], c[g["pin_cell"]]], 1))
            c = nc(c + scatter_mean(back, g["pin_cell"], c.shape[0]))
        return c, n

    def step(self, c, n, g, layer, ctx=None):
        """one more round (used by the exchange model), optionally with layout context added to nets."""
        if ctx is not None:
            n = n + ctx
        msg = self.c2n[layer](torch.cat([c[g["pin_cell"]], n[g["pin_net"]], g["pin_xy"]], 1))
        n = self.norm_n[layer](n + scatter_mean(msg, g["pin_net"], n.shape[0]))
        back = self.n2c[layer](torch.cat([n[g["pin_net"]], c[g["pin_cell"]]], 1))
        c = self.norm_c[layer](c + scatter_mean(back, g["pin_cell"], c.shape[0]))
        return c, n


def graph_to_grid(c, n, g, nx, ny):
    """[2d, nx, ny]: nets spread as density over their bbox (RUDY-like), cells over their footprint."""
    return torch.cat([splat(g["net_foot"], n, nx, ny, density=True) * 8.0,
                      splat(g["cell_foot"], c, nx, ny, density=False)], 0)


# ---------------------------------------------------------------- CNN parts
class Block(nn.Module):
    def __init__(self, i, o):
        super().__init__()
        self.net = nn.Sequential(nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o), nn.GELU(),
                                 nn.Conv2d(o, o, 3, padding=1), nn.BatchNorm2d(o), nn.GELU())

    def forward(self, x):
        return self.net(x)


def pad16(x):
    H, W = x.shape[-2:]
    ph, pw = (-H) % 16, (-W) % 16
    return F.pad(x, (0, pw, 0, ph), mode="replicate"), H, W


class UNetCore(nn.Module):
    """4-level U-Net; `inject[l]` (optional) adds extra channels to encoder level l."""

    def __init__(self, cin, base=32, extra=(0, 0, 0, 0)):
        super().__init__()
        ch = [base, base * 2, base * 4, base * 8]
        self.enc = nn.ModuleList()
        prev = cin
        for l, c in enumerate(ch):
            self.enc.append(Block(prev + extra[l], c))
            prev = c
        self.mid = Block(ch[-1], ch[-1] * 2)
        self.up = nn.ModuleList()
        self.dec = nn.ModuleList()
        prev = ch[-1] * 2
        for c in reversed(ch):
            self.up.append(nn.ConvTranspose2d(prev, c, 2, stride=2))
            self.dec.append(Block(2 * c, c))
            prev = c
        self.head = nn.Conv2d(ch[0], 1, 1)
        self.ch = ch


class UNet(nn.Module):
    def __init__(self, cin=5, base=32):
        super().__init__()
        self.core = UNetCore(cin, base)

    def forward(self, x, g=None):
        x, H, W = pad16(x)
        skips = []
        h = x
        for l, blk in enumerate(self.core.enc):
            h = blk(h)
            skips.append(h)
            h = F.max_pool2d(h, 2)
        h = self.core.mid(h)
        for up, dec, s in zip(self.core.up, self.core.dec, reversed(skips)):
            h = dec(torch.cat([up(h), s], 1))
        return self.core.head(h)[..., :H, :W]


class GNNGrid(nn.Module):
    """netlist only: graph embeddings on the grid + a small conv head (no placement image)."""

    def __init__(self, n_masters, d=32):
        super().__init__()
        self.enc = NetlistEncoder(n_masters, d)
        self.head = nn.Sequential(Block(2 * d, 64), Block(64, 64), nn.Conv2d(64, 1, 1))

    def forward(self, x, g):
        nx, ny = x.shape[-2:]
        c, n = self.enc(g)
        return self.head(graph_to_grid(c, n, g, nx, ny)[None])


class Graft(nn.Module):
    """Lay-Net-style one-way grafting of netlist embeddings into a U-Net at every encoder scale."""

    def __init__(self, n_masters, cin=5, d=32, base=32):
        super().__init__()
        self.enc_g = NetlistEncoder(n_masters, d)
        self.core = UNetCore(cin, base, extra=(2 * d,) * 4)

    def forward(self, x, g):
        nx, ny = x.shape[-2:]
        c, n = self.enc_g(g)
        G = graph_to_grid(c, n, g, nx, ny)[None]
        x, H, W = pad16(x)
        G = F.pad(G, (0, x.shape[-1] - ny, 0, x.shape[-2] - nx))
        skips, h = [], x
        for l, blk in enumerate(self.core.enc):
            Gl = F.adaptive_avg_pool2d(G, h.shape[-2:]) if l else G
            h = blk(torch.cat([h, Gl], 1))
            skips.append(h)
            h = F.max_pool2d(h, 2)
        h = self.core.mid(h)
        for up, dec, s in zip(self.core.up, self.core.dec, reversed(skips)):
            h = dec(torch.cat([up(h), s], 1))
        return self.core.head(h)[..., :H, :W]


class Exchange(nn.Module):
    """Ours: two-way cross-modal exchange between the netlist and the U-Net encoder at every scale."""

    def __init__(self, n_masters, cin=5, d=32, base=32):
        super().__init__()
        self.enc_g = NetlistEncoder(n_masters, d, layers=4)
        self.core = UNetCore(cin, base, extra=(2 * d,) * 4)
        self.to_net = nn.ModuleList(nn.Linear(c, d) for c in self.core.ch[:3])

    def forward(self, x, g):
        nx, ny = x.shape[-2:]
        c, n = self.enc_g.step(*self._init(g), g, 0)
        x, H, W = pad16(x)
        PX, PY = x.shape[-2], x.shape[-1]
        skips, h = [], x
        for l, blk in enumerate(self.core.enc):
            G = graph_to_grid(c, n, g, nx, ny)[None]
            G = F.pad(G, (0, PY - ny, 0, PX - nx))
            Gl = F.adaptive_avg_pool2d(G, h.shape[-2:]) if l else G
            h = blk(torch.cat([h, Gl], 1))
            skips.append(h)
            if l < 3:                                   # layout -> netlist: gather this scale's features into nets
                up = F.interpolate(h, size=(PX, PY), mode="bilinear", align_corners=False)[0, :, :nx, :ny]
                ctx = self.to_net[l](gather(up, g["net_foot"]))
                c, n = self.enc_g.step(c, n, g, l + 1, ctx)
            h = F.max_pool2d(h, 2)
        h = self.core.mid(h)
        for up_, dec, s in zip(self.core.up, self.core.dec, reversed(skips)):
            h = dec(torch.cat([up_(h), s], 1))
        return self.core.head(h)[..., :H, :W]

    def _init(self, g):
        e = self.enc_g
        return e.cell_in(torch.cat([e.emb(g["master"]), g["cell_num"]], 1)), e.net_in(g["net_num"])


def build(name, n_masters):
    return {"unet": lambda: UNet(), "gnn": lambda: GNNGrid(n_masters), "graft": lambda: Graft(n_masters),
            "exchange": lambda: Exchange(n_masters)}[name]()


# ---------------------------------------------------------------- topology-aware (spanning-tree) variants
def tree_to_grid(n, g, nx, ny, wscale):
    """per-net embeddings splatted ALONG each net's rectilinear spanning tree, horizontal and vertical segments
    into separate channel groups (the label is max(h_util, v_util)). wscale [N,2] = learned per-net, per-direction
    demand weight; segment weight = 0.5 per L-shape half."""
    out = []
    for dsel in (0, 1):
        m = g["seg_dir"] == dsel
        r, net, w = g["seg_rect"][m], g["seg_net"][m], g["seg_w"][m]
        v = n[net] * (w * wscale[net, dsel])[:, None]
        out.append(splat(r, v, nx, ny, density=False))
    return torch.cat(out, 0)


class UNetMST(UNet):
    """U-Net with the fixed spanning-tree demand maps as two extra input channels."""

    def __init__(self, cin=7, base=32):
        super().__init__(cin, base)

    def forward(self, x, g=None):
        return super().forward(torch.cat([x, g["mst_x"]], 1), g)


class GraftMST(nn.Module):
    """Graft + topology-aware splatting: the netlist branch is rasterised both over bounding boxes (RUDY-like) and
    along each net's spanning tree (direction-separated, learned per-net demand weights); the fixed tree maps are
    extra image channels."""

    def __init__(self, n_masters, cin=7, d=32, base=32):
        super().__init__()
        self.enc_g = NetlistEncoder(n_masters, d)
        self.wnet = nn.Sequential(nn.Linear(d, 2), nn.Softplus())
        self.core = UNetCore(cin, base, extra=(4 * d,) * 4)

    def forward(self, x, g):
        nx, ny = x.shape[-2:]
        c, n = self.enc_g(g)
        G = torch.cat([graph_to_grid(c, n, g, nx, ny), tree_to_grid(n, g, nx, ny, self.wnet(n))], 0)[None]
        x = torch.cat([x, g["mst_x"]], 1)
        x, H, W = pad16(x)
        G = F.pad(G, (0, x.shape[-1] - ny, 0, x.shape[-2] - nx))
        skips, h = [], x
        for l, blk in enumerate(self.core.enc):
            Gl = F.adaptive_avg_pool2d(G, h.shape[-2:]) if l else G
            h = blk(torch.cat([h, Gl], 1))
            skips.append(h)
            h = F.max_pool2d(h, 2)
        h = self.core.mid(h)
        for up, dec, s in zip(self.core.up, self.core.dec, reversed(skips)):
            h = dec(torch.cat([up(h), s], 1))
        return self.core.head(h)[..., :H, :W]


_build_base = build


def build(name, n_masters):                                   # noqa: F811
    if name == "unet_mst":
        return UNetMST()
    if name == "graft_mst":
        return GraftMST(n_masters)
    return _build_base(name, n_masters)
