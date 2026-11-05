#!/bin/sh
# Fetch Rockchip's LLM runtime (rknn-llm release-v1.3.1, from GitHub) and build the benchmark.
# Run on the robot, inside examples/llm. Needs internet on the robot (or copy lib/ over by hand).
set -eu
cd "$(dirname "$0")"
TAG=release-v1.3.1
BASE="https://raw.githubusercontent.com/airockchip/rknn-llm/$TAG/rkllm-runtime/Linux/librkllm_api"
mkdir -p lib include models
[ -f lib/librkllmrt.so ] || curl -fL -o lib/librkllmrt.so "$BASE/aarch64/librkllmrt.so"
[ -f include/rkllm.h ] || curl -fL -o include/rkllm.h "$BASE/include/rkllm.h"
echo "2a4867ece83de3735a37f6459811eb92  lib/librkllmrt.so" | md5sum -c -
g++ -O2 -o rkllm_bench rkllm_bench.cpp -Iinclude -Llib -lrkllmrt -Wl,-rpath,'$ORIGIN/lib'
echo
echo "ready. Put a .rkllm model in models/ (see README.md), then:"
echo "  python3 rkllm_chat.py models/Qwen3.5-2B_w8a8_rk3588.rkllm"
echo "  ./rkllm_bench models/Qwen3.5-2B_w8a8_rk3588.rkllm 128 4096 3 \"Hello\""
