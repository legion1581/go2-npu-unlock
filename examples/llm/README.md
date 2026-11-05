# LLMs on the Go2 NPU

Needs the NPU running with driver 0.9.8 (`go2-npu status`).

## 1. Runtime and benchmark (on the robot)

```bash
./setup.sh
```

Downloads `librkllmrt.so` and `rkllm.h` from Rockchip's
[rknn-llm](https://github.com/airockchip/rknn-llm) `release-v1.3.1`, checks the library's md5,
and builds `rkllm_bench`.

## 2. A model

rknn-llm runs `.rkllm` files, converted from a Hugging Face model on a PC (x86_64) with Rockchip's
rkllm-toolkit 1.3.1:

```bash
# on the PC, in a Python 3.10 venv
pip install rkllm_toolkit-1.3.1-cp310-cp310-linux_x86_64.whl   # from rknn-llm/rkllm-toolkit/packages
huggingface-cli download Qwen/Qwen3.5-2B --local-dir Qwen3.5-2B
# Rockchip's calibration prompts, used for the 8-bit quantization
curl -fLO https://raw.githubusercontent.com/airockchip/rknn-llm/release-v1.3.1/examples/rkllm_api_demo/export/data_quant.json
python3 export_rkllm.py Qwen3.5-2B Qwen3.5-2B_w8a8_rk3588.rkllm 4096
scp Qwen3.5-2B_w8a8_rk3588.rkllm root@<robot-ip>:/root/go2-npu-unlock/examples/llm/models/
```

Settings used: W8A8, RK3588, 3 NPU cores, `optimization_level 0`, 4096 context.
RAM needed for conversion: about 18 GB for 2B. For 4B, load in bfloat16
(`export_rkllm.py <dir> <out> 4096 0 bfloat16`) and add swap.

## 3. Chat

```bash
python3 rkllm_chat.py models/Qwen3.5-2B_w8a8_rk3588.rkllm
```

A terminal chat with live metrics on the right: token rate (live, average, peak), time to first token,
context use, memory, NPU load/clock/voltage/temperature, CPU clock and load.

| Key / command | |
|---|---|
| Enter | send |
| Esc | stop the answer |
| PgUp / PgDn | scroll |
| `/clear` | forget the conversation |
| `/think` | toggle thinking mode |
| `/max N` | answer length limit |
| `/sys TEXT` | system prompt |
| `/perf` | performance mode on/off (restored on exit) |
| `/quit` | quit |

Without the UI: `python3 rkllm_chat.py --once "What is a quadruped robot?"`

## 4. Benchmark

```bash
./rkllm_bench models/Qwen3.5-2B_w8a8_rk3588.rkllm 128 4096 3 "Explain how a quadruped robot keeps its balance."
# args: model, new tokens, context, runs, prompt [ignore_eos=1] [print=0] [think=0]
```

Measured on the Go2 (rknn-llm 1.3.1, 128 tokens):

| Model | RAM | Decode, stock | Decode, performance mode |
|---|---|---|---|
| Qwen3.5-0.8B | 1.1 GB | 14.7 tok/s | 17.6 tok/s |
| Qwen3.5-2B | 2.2 GB | 8.6 tok/s | 10.1 tok/s |
| Qwen3.5-4B | 4.7 GB | 4.1 tok/s | 4.75 tok/s |

`data_quant.json` is Rockchip's calibration set from rknn-llm (`examples/rkllm_api_demo/export`),
downloaded rather than bundled because it is under Rockchip's rknn-llm licence.
