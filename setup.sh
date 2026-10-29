#!/usr/bin/env bash
# Set up the project from a fresh clone: virtualenv, dependencies, models.
#
#   ./setup.sh          # GPU install (NVIDIA + CUDA 12.8)
#   ./setup.sh --cpu    # CPU only: works, but roughly 10x slower per frame
#
# Safe to re-run: every step checks whether it is already done.

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

CPU_ONLY=false
[ "${1:-}" = "--cpu" ] && CPU_ONLY=true

TORCH_INDEX="https://download.pytorch.org/whl/cu128"
MODELS=(yolov8n.pt yolov8n-seg.pt yolov8n-pose.pt)
MODEL_BASE="https://github.com/ultralytics/assets/releases/download/v8.3.0"

say() { printf "\n\033[1m%s\033[0m\n" "$1"; }

say "1/5  Python tekshirilmoqda"
command -v python3 >/dev/null || { echo "python3 topilmadi"; exit 1; }
python3 --version

say "2/5  Virtual muhit"
if [ -d venv ]; then
  echo "  venv allaqachon bor"
else
  python3 -m venv venv
  echo "  yaratildi"
fi
PY=venv/bin/python
# pip's temp dir defaults to /tmp, which is a small tmpfs on many machines;
# torch alone unpacks to several GB and fails there with "disk quota exceeded".
export TMPDIR="${TMPDIR:-$HOME/.pip-tmp}"
mkdir -p "$TMPDIR"
$PY -m pip install --quiet --upgrade pip

say "3/5  PyTorch"
if $PY -c "import torch" 2>/dev/null; then
  $PY -c "import torch; print('  o\'rnatilgan:', torch.__version__, '| CUDA:', torch.cuda.is_available())"
elif $CPU_ONLY; then
  echo "  CPU versiyasi o'rnatilmoqda (sekinroq)"
  $PY -m pip install torch torchvision
else
  echo "  CUDA 12.8 versiyasi o'rnatilmoqda (~3 GB, uzoq davom etadi)"
  $PY -m pip install torch torchvision --index-url "$TORCH_INDEX"
fi

say "4/5  Qolgan kutubxonalar"
$PY -m pip install --quiet -r requirements.txt
# onnxruntime-gpu is deliberately installed on its own and after the rest:
# if plain `onnxruntime` ever lands in the same environment it shadows the GPU
# build, and recognition silently drops to the CPU with no error anywhere.
if $CPU_ONLY; then
  $PY -m pip install --quiet onnxruntime
else
  $PY -m pip uninstall -y onnxruntime 2>/dev/null || true
  $PY -m pip install --quiet onnxruntime-gpu
fi
echo "  tayyor"

say "5/5  Modellar"
mkdir -p backend/models
for model in "${MODELS[@]}"; do
  if [ -f "backend/models/$model" ]; then
    echo "  $model — bor"
  else
    echo "  $model yuklanmoqda…"
    curl -fL --progress-bar -o "backend/models/$model" "$MODEL_BASE/$model"
  fi
done
echo "  Yuz modeli (buffalo_l, ~326 MB) birinchi ishga tushirishda o'zi yuklanadi."

say "Tayyor"
cat <<'EOF'
  Keyingi qadamlar:

    cp .env.example .env          # Telegram kerak bo'lsa, tokenni yozing
    chmod 600 .env
    cp examples/config.example.json data/config.json    # ixtiyoriy

    ./emon start                  # ishga tushirish
    ./tests/run.sh                # testlar

  Parollar birinchi ishga tushirishda o'zi yaratiladi:
    data/admin_pass.txt   — dashboardga kirish
    data/manage_pass.txt  — xodim qo'shish, tuzatish
EOF
