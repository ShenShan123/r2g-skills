"""Shared publication figure style; inspired by the user's Nature reference.

Colours are our adapted palette, not an official journal palette or sampled colours.
All output remains vector PDF with embedded, editable text.
"""
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

BLUE = '#3C5488'
TEAL = '#238C89'
CORAL = '#CE7668'
GOLD = '#BE944D'
PALE = '#EFF1F4'
INK = '#28323F'
MUTED = '#7E8B9B'
PASS = '#D8EDE8'
FAIL = '#F3DDD6'
UNKNOWN = '#E9ECF0'

def configure():
    plt.rcParams.update({
        'font.family': 'sans-serif', 'font.sans-serif': ['Liberation Sans', 'DejaVu Sans'],
        'font.size': 9, 'axes.labelsize': 9, 'xtick.labelsize': 8.5, 'ytick.labelsize': 9,
        'text.color': INK, 'axes.labelcolor': INK, 'xtick.color': INK, 'ytick.color': INK,
        'axes.edgecolor': MUTED, 'axes.linewidth': .55, 'axes.spines.top': False,
        'axes.spines.right': False, 'xtick.major.width': .55, 'xtick.major.size': 3,
        'pdf.fonttype': 42, 'ps.fonttype': 42, 'hatch.linewidth': .45,
        'legend.fontsize': 8.5, 'figure.facecolor': 'white', 'savefig.facecolor': 'white',
    })

def save(fig, root, name):
    for ext in ['pdf', 'png']:
        fig.savefig(Path(root)/f'figures/{name}.{ext}', dpi=260, facecolor='white')
    plt.close(fig)
