#!/usr/bin/env python3
"""Convert a Hugging Face LLM to .rkllm for RK3588 (rkllm-toolkit 1.3.1).
usage: export_rkllm.py <hf_model_dir> <out.rkllm> [max_context=4096] [opt_level=0] [dtype=float32]
Run on a PC (x86_64, ~18 GB RAM for a 2B model) with rkllm-toolkit 1.3.1 installed.
Calibration data: RKLLM_DATASET (default: data_quant.json next to this script; download it from
https://raw.githubusercontent.com/airockchip/rknn-llm/release-v1.3.1/examples/rkllm_api_demo/export/data_quant.json)."""
import os
import sys, time
from rkllm.api import RKLLM
src, dst = sys.argv[1], sys.argv[2]
max_ctx = int(sys.argv[3]) if len(sys.argv) > 3 else 4096
opt = int(sys.argv[4]) if len(sys.argv) > 4 else 0
t = time.time()
llm = RKLLM()
assert llm.load_huggingface(model=src, model_lora=None, device='cpu', dtype=(sys.argv[5] if len(sys.argv) > 5 else 'float32'), custom_config=None, load_weight=True) == 0, 'load failed'
assert llm.build(do_quantization=True, optimization_level=opt, quantized_dtype='W8A8', quantized_algorithm='normal',
                 target_platform='RK3588', num_npu_core=3, extra_qparams=None,
                 dataset=os.environ.get('RKLLM_DATASET', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data_quant.json')), hybrid_rate=0, max_context=max_ctx) == 0, 'build failed'
assert llm.export_rkllm(dst) == 0, 'export failed'
print(f'exported {dst} in {time.time()-t:.0f} s')
