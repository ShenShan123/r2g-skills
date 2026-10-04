#!/bin/bash

# R2G: Head Layers Ablation Experiment Script
# ================================================
# Purpose: Compare impact of decoder head depth (1-4 layers) on performance
# Models: GINE, ResGatedGCN, GAT
# Dataset: route_B_homograph, place_B_homograph
#
# Paper correspondence: Table 6-7 (Placement/Routing Head Layers Ablation)
# ================================================

set -e

# Script directory and log directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$SCRIPT_DIR/logs"

# Conda environment
ENV_NAME="gnn_env"
CONDA_BASE="/opt/miniconda3"
CONDA_SH="$CONDA_BASE/etc/profile.d/conda.sh"
if [ ! -f "$CONDA_SH" ]; then
  echo "Error: conda activation script not found: $CONDA_SH"
  exit 1
fi

if ! command -v tmux &>/dev/null; then
  echo "Error: tmux not detected, please install tmux first (apt install tmux or yum install tmux)"
  exit 1
fi

# Session name
SESSION_NAME="${SESSION_NAME:-gnn_ablation_head}"

# Delete if session already exists
if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  echo "Detected existing $SESSION_NAME session, deleting..."
  tmux kill-session -t "$SESSION_NAME"
fi

# Maximum window index (loop 1..MAX_WINDOWS). 8 experiments (3 models × 4 head layers)
MAX_WINDOWS=${MAX_WINDOWS:-8}

###############################################
# Switch section: 1=enable, 0=disable
# 8 experiments: 3 models × 4 head layers
###############################################
ENABLE_1=1  # GINE, head=1
ENABLE_2=1  # GINE, head=2
ENABLE_3=1  # GINE, head=3
ENABLE_4=1  # GINE, head=4
ENABLE_5=1  # ResGatedGCN, head=1
ENABLE_6=1  # ResGatedGCN, head=2
ENABLE_7=1  # ResGatedGCN, head=3
ENABLE_8=1  # ResGatedGCN, head=4

###############################################
# Experiment configuration: Head layers ablation (1, 2, 3, 4 layers)
# Models: GINE, ResGatedGCN, GAT
# Dataset: route_B_homograph, place_B_homograph
###############################################

# GINE on route_B - different Head layers (1-4 layers, fixed GNN=4)
MODEL_1="gine"
DATASET_1="route_B_homograph"
HID_DIM_1=256
NUM_GNN_LAYERS_1=4
LR_1=0.0001
EPOCHS_1=100
DROPOUT_1=0.3
WEIGHT_DECAY_1=1e-4
GPU_1=1
DATA_ROOT_1="data/"
num_head_layers_1=1

MODEL_2="gine"
DATASET_2="route_B_homograph"
HID_DIM_2=256
NUM_GNN_LAYERS_2=4
LR_2=0.0001
EPOCHS_2=100
DROPOUT_2=0.3
WEIGHT_DECAY_2=1e-4
GPU_2=1
DATA_ROOT_2="data/"
num_head_layers_2=2

MODEL_3="gine"
DATASET_3="route_B_homograph"
HID_DIM_3=256
NUM_GNN_LAYERS_3=4
LR_3=0.0001
EPOCHS_3=100
DROPOUT_3=0.3
WEIGHT_DECAY_3=1e-4
GPU_3=1
DATA_ROOT_3="data/"
num_head_layers_3=3

MODEL_4="gine"
DATASET_4="route_B_homograph"
HID_DIM_4=256
NUM_GNN_LAYERS_4=4
LR_4=0.0001
EPOCHS_4=100
DROPOUT_4=0.3
WEIGHT_DECAY_4=1e-4
GPU_4=2
DATA_ROOT_4="data/"
num_head_layers_4=4

# ResGatedGCN on route_B - different Head layers (1-4 layers, fixed GNN=4)
MODEL_5="resgatedgcn"
DATASET_5="route_B_homograph"
HID_DIM_5=256
NUM_GNN_LAYERS_5=4
LR_5=0.0001
EPOCHS_5=100
DROPOUT_5=0.3
WEIGHT_DECAY_5=1e-4
GPU_5=2
DATA_ROOT_5="data/"
num_head_layers_5=1

MODEL_6="resgatedgcn"
DATASET_6="route_B_homograph"
HID_DIM_6=256
NUM_GNN_LAYERS_6=4
LR_6=0.0001
EPOCHS_6=100
DROPOUT_6=0.3
WEIGHT_DECAY_6=1e-4
GPU_6=3
DATA_ROOT_6="data/"
num_head_layers_6=2

MODEL_7="resgatedgcn"
DATASET_7="route_B_homograph"
HID_DIM_7=256
NUM_GNN_LAYERS_7=4
LR_7=0.0001
EPOCHS_7=100
DROPOUT_7=0.3
WEIGHT_DECAY_7=1e-4
GPU_7=3
DATA_ROOT_7="data/"
num_head_layers_7=3

MODEL_8="resgatedgcn"
DATASET_8="route_B_homograph"
HID_DIM_8=256
NUM_GNN_LAYERS_8=4
LR_8=0.0001
EPOCHS_8=100
DROPOUT_8=0.3
WEIGHT_DECAY_8=1e-4
GPU_8=3
DATA_ROOT_8="data/"
num_head_layers_8=4

echo "Creating tmux session: $SESSION_NAME"

# Dynamically create windows by switch: first enabled model uses new-session, other enabled models use new-window
FIRST_CREATED=""
ENABLED_WINDOWS_LIST=""

for i in $(seq 1 "$MAX_WINDOWS"); do
  # Read switch
  eval "enable=\${ENABLE_${i}:-0}"
  if [ "$enable" != "1" ]; then
    continue
  fi

  # Read configuration (required: MODEL_i, DATASET_i; others can use defaults)
  eval "model=\${MODEL_${i}:-}"
  eval "dataset=\${DATASET_${i}:-}"
  eval "hid=\${HID_DIM_${i}:-256}"
  eval "layers=\${NUM_GNN_LAYERS_${i}:-4}"
  eval "lr=\${LR_${i}:-0.0001}"
  eval "epochs=\${EPOCHS_${i}:-50}"
  eval "dropout=\${DROPOUT_${i}:-0.3}"
  eval "wd=\${WEIGHT_DECAY_${i}:-0.0001}"
  eval "gpu=\${GPU_${i}:-0}"
  eval "data_root=\${DATA_ROOT_${i}:-data/}"
  eval "lr_scheduler=\${LR_SCHEDULER_${i}:-plateau}"
  eval "num_head_layers=\${num_head_layers_${i}:-2}"

  if [ -z "$model" ] || [ -z "$dataset" ]; then
    echo "Error: Enabled window $i (ENABLE_${i}=1) but MODEL_${i} or DATASET_${i} not configured. Please set in section ${i}."
    exit 1
  fi

  CMD="export OMP_NUM_THREADS=4 && export OPENBLAS_NUM_THREADS=4 && export MKL_NUM_THREADS=4 && source $CONDA_SH && conda activate $ENV_NAME && cd \"$SCRIPT_DIR\" && python main.py \
    --data_root ${data_root} --dataset ${dataset} --model ${model} \
    --hid_dim ${hid} --num_gnn_layers ${layers} --lr ${lr} --epochs ${epochs} \
    --dropout ${dropout} --weight_decay ${wd} --gpu ${gpu} --lr_scheduler ${lr_scheduler} \
    --num_head_layers ${num_head_layers} |& tee -a logs/tmux_${dataset}_${model}_gnn${layers}_head${num_head_layers}_${lr_scheduler}.log; exec bash"

  if [ -z "$FIRST_CREATED" ]; then
    tmux new-session -d -s "$SESSION_NAME" -n "$model" "$CMD"
    FIRST_CREATED=1
    ENABLED_WINDOWS_LIST="$i:$model"
  else
    sleep 10  # Delay 10 seconds to avoid data loading conflicts
    tmux new-window -t "$SESSION_NAME" -n "$model" "$CMD"
    if [ -z "$ENABLED_WINDOWS_LIST" ]; then
      ENABLED_WINDOWS_LIST="$i:$model"
    else
      ENABLED_WINDOWS_LIST="$ENABLED_WINDOWS_LIST, $i:$model"
    fi
  fi
done

if [ -z "$FIRST_CREATED" ]; then
  echo "Error: All windows disabled (ENABLE_*=0), no training process created. Please enable at least one section."
  exit 1
fi

# Let tmux automatically keep window numbers continuous (e.g., auto-fill gaps when windows are deleted)
tmux set-option -t "$SESSION_NAME" renumber-windows on
# To start numbering from 1, uncomment the next line
# tmux set-option -t "$SESSION_NAME" base-index 1

echo "✅ Head layers ablation experiment tmux session '$SESSION_NAME' created"
echo "📊 Enabled windows: $ENABLED_WINDOWS_LIST"
echo "🔍 View training progress: tmux attach -t $SESSION_NAME"
echo "📄 View log files: ls -lh logs/tmux_*.log"
echo ""
echo "Experiment configuration:"
echo "  - Models: GINE, ResGatedGCN, GAT"
echo "  - Head layers ablation: 1, 2, 3, 4 layers"
echo "  - Fixed GNN layers: 4 layers"
echo "  - Dataset: route_B_homograph"
