#!/bin/bash
# Isolated Xavier CUDA/PyTorch environment for the reconstructed benchmarks.
# All extra libraries live under gedge_bot_validation_20261107/ or existing
# device source trees; system libraries are never modified.
P=/root/gedge_bot_validation_20261107
V=/mnt/nvme_data/root/src/sample/.venv-tts-0.22/lib/python3.8/site-packages
R=$P/cuda_isolated/root
C=$R/usr/lib/aarch64-linux-gnu
B=$C/openblas-pthread
O=/mnt/nvme_data/root/ollama/lib/ollama/cuda_jetpack5
U=$R/usr/local/cuda-11.4/targets/aarch64-linux/lib
TV=/root/src/sample/torchvision_src/build/lib.linux-aarch64-cpython-38
# torchvision built against torch 2.1.0a0+nv23.06 must precede the generic wheel
export PYTHONPATH=$TV:$P/py38_site:$V
export LD_LIBRARY_PATH=$B:$O:$C:$U
# sklearn's bundled libgomp otherwise fails with "cannot allocate memory in static TLS block"
export LD_PRELOAD=$V/scikit_learn.libs/libgomp-d22c30c5.so.1.0.0
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export HF_HOME=/root/src/gedge_bot/VoiceAsistant/.hf_cache
export GEDGEBOT_YOLO_MODEL=/root/src/sample/yolov8n.pt
export GEDGEBOT_WHISPER_MODEL=$P/models/whisper-small
export GEDGEBOT_NLLB_MODEL=/root/src/sample/models/nllb-200
export GEDGEBOT_AUDIO_10S=$P/paper/evidence/bench_audio_10s.wav
