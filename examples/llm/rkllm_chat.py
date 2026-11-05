#!/usr/bin/env python3
"""rkllm_chat — terminal chat UI for .rkllm models on the RK3588 NPU (rknn-llm 1.3.1)
with live metrics: decode token rate, TTFT, prefill rate, context use, memory, NPU load,
clocks and temperatures.

usage:  rkllm_chat.py [model.rkllm] [--ctx 4096] [--max-new 1024] [--lib lib/librkllmrt.so]
        rkllm_chat.py --once "question"      # no UI: answer + metrics on stdout

keys:   Enter send · Esc abort generation · PgUp/PgDn scroll · Ctrl-C quit
cmds:   /clear  /think  /max N  /sys TEXT  /perf  /help  /quit
"""
import argparse
import ctypes
import curses
import locale
import os
import queue
import re
import sys
import textwrap
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL = os.path.join(HERE, "models", "Qwen3.5-2B_w8a8_rk3588.rkllm")
DEFAULT_LIB = os.path.join(HERE, "lib", "librkllmrt.so")
NPU_LOADER = "systemctl start go2-npu   (install it with go2-npu-unlock/install.sh)"

# ---------------------------------------------------------------- rkllm C API (rkllm.h, v1.3.1)
c = ctypes
RKLLM_RUN_NORMAL, RKLLM_RUN_WAITING, RKLLM_RUN_FINISH, RKLLM_RUN_ERROR = 0, 1, 2, 3
RKLLM_INPUT_PROMPT = 0
RKLLM_INFER_GENERATE = 0


class RKLLMExtendParam(c.Structure):
    _fields_ = [("base_domain_id", c.c_int32), ("embed_flash", c.c_int8), ("enabled_cpus_num", c.c_int8),
                ("enabled_cpus_mask", c.c_uint32), ("n_batch", c.c_uint8), ("use_cross_attn", c.c_int8),
                ("reserved", c.c_uint8 * 104)]


class RKLLMParam(c.Structure):
    _fields_ = [("model_path", c.c_char_p), ("max_context_len", c.c_int32), ("max_new_tokens", c.c_int32),
                ("top_k", c.c_int32), ("n_keep", c.c_int32), ("top_p", c.c_float), ("temperature", c.c_float),
                ("repeat_penalty", c.c_float), ("frequency_penalty", c.c_float), ("presence_penalty", c.c_float),
                ("mirostat", c.c_int32), ("mirostat_tau", c.c_float), ("mirostat_eta", c.c_float),
                ("skip_special_token", c.c_bool), ("ignore_eos_token", c.c_bool), ("is_async", c.c_bool),
                ("extend_param", RKLLMExtendParam)]


class RKLLMEmbedInput(c.Structure):
    _fields_ = [("embed", c.POINTER(c.c_float)), ("n_tokens", c.c_size_t)]


class RKLLMTokenInput(c.Structure):
    _fields_ = [("input_ids", c.POINTER(c.c_int32)), ("n_tokens", c.c_size_t)]


class RKLLMImageInput(c.Structure):
    _fields_ = [("image_embed", c.POINTER(c.c_float)), ("n_image_tokens", c.c_size_t), ("n_image", c.c_size_t),
                ("image_start", c.c_char_p), ("image_end", c.c_char_p), ("image_content", c.c_char_p),
                ("image_width", c.c_size_t), ("image_height", c.c_size_t)]


class RKLLMVideoInput(c.Structure):
    _fields_ = [("video_embed", c.POINTER(c.c_float)), ("n_frame_tokens", c.c_size_t),
                ("n_frame_per_video", c.c_size_t), ("n_video", c.c_size_t), ("video_start", c.c_char_p),
                ("video_end", c.c_char_p), ("video_content", c.c_char_p), ("frame_width", c.c_size_t),
                ("frame_height", c.c_size_t)]


class RKLLMAudioInput(c.Structure):
    _fields_ = [("audio_embed", c.POINTER(c.c_float)), ("n_audio_tokens", c.c_size_t), ("n_audio", c.c_size_t),
                ("audio_start", c.c_char_p), ("audio_end", c.c_char_p), ("audio_content", c.c_char_p)]


class RKLLMMultiModalInput(c.Structure):
    _fields_ = [("prompt", c.c_char_p), ("image", RKLLMImageInput), ("video", RKLLMVideoInput),
                ("audio", RKLLMAudioInput)]


class RKLLMInputUnion(c.Union):
    _fields_ = [("prompt_input", c.c_char_p), ("embed_input", RKLLMEmbedInput),
                ("token_input", RKLLMTokenInput), ("multimodal_input", RKLLMMultiModalInput)]


class RKLLMInput(c.Structure):
    _anonymous_ = ("input_data",)
    _fields_ = [("role", c.c_char_p), ("enable_thinking", c.c_bool), ("input_type", c.c_int),
                ("input_data", RKLLMInputUnion)]


class RKLLMInferParam(c.Structure):
    _fields_ = [("mode", c.c_int), ("lora_params", c.c_void_p), ("prompt_cache_params", c.c_void_p),
                ("sampling_params", c.c_void_p), ("keep_history", c.c_int), ("max_new_tokens", c.c_int32)]


class RKLLMResultLastHiddenLayer(c.Structure):
    _fields_ = [("hidden_states", c.POINTER(c.c_float)), ("embd_size", c.c_int), ("num_tokens", c.c_int)]


class RKLLMResultLogits(c.Structure):
    _fields_ = [("logits", c.POINTER(c.c_float)), ("vocab_size", c.c_int), ("num_tokens", c.c_int)]


class RKLLMPerfStat(c.Structure):
    _fields_ = [("prefill_time_ms", c.c_float), ("prefill_tokens", c.c_int), ("generate_time_ms", c.c_float),
                ("generate_tokens", c.c_int), ("memory_usage_mb", c.c_float)]


class RKLLMResult(c.Structure):
    _fields_ = [("text", c.c_char_p), ("token_id", c.c_int), ("last_hidden_layer", RKLLMResultLastHiddenLayer),
                ("logits", RKLLMResultLogits), ("perf", RKLLMPerfStat)]


ResultCB = c.CFUNCTYPE(c.c_int, c.POINTER(RKLLMResult), c.c_void_p, c.c_int)
TokenizerCB = c.CFUNCTYPE(c.c_int, c.c_void_p, c.c_char_p, c.c_int32, c.POINTER(c.c_int32), c.c_int32)
EmbedCB = c.CFUNCTYPE(c.c_int, c.c_void_p, c.POINTER(c.c_int32), c.c_uint64, c.c_void_p, c.c_uint64)


class RKLLMCallback(c.Structure):
    _fields_ = [("result_callback", ResultCB), ("result_userdata", c.c_void_p),
                ("tokenizer_callback", TokenizerCB), ("tokenizer_userdata", c.c_void_p),
                ("embed_callback", EmbedCB), ("embed_userdata", c.c_void_p)]


# ---------------------------------------------------------------- system metrics
def _read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def _thermal_zones():
    zones = {}
    base = "/sys/class/thermal"
    for z in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        if z.startswith("thermal_zone"):
            zones[_read(f"{base}/{z}/type")] = f"{base}/{z}/temp"
    return zones


class SysMetrics:
    NPU_DEVFREQ = "/sys/class/devfreq/fdab0000.npu"

    def __init__(self):
        self.zones = _thermal_zones()
        self.cpu_prev = None

    def temp(self, name):
        v = _read(self.zones.get(name, ""), "")
        return f"{int(v) / 1000:.0f}°C" if v.isdigit() else "-"

    def npu_load(self):
        s = _read("/sys/kernel/debug/rknpu/load", "")
        vals = [p.split(":")[1].strip().rstrip("%,").strip() for p in s.replace("NPU load:", "").split(",")
                if ":" in p]
        return " ".join(f"{v:>3}%" for v in vals) if vals else "n/a"

    @staticmethod
    def npu_volt():
        v = _read("/sys/kernel/debug/rknpu/volt", "")
        return f"{int(v) / 1000:.1f} mV" if v.isdigit() else "-"

    def cpu_util(self):
        try:
            with open("/proc/stat") as f:
                v = [int(x) for x in f.readline().split()[1:]]
        except OSError:
            return 0.0
        idle, total = v[3] + v[4], sum(v)
        prev, self.cpu_prev = self.cpu_prev, (idle, total)
        if not prev or total == prev[1]:
            return 0.0
        return 100.0 * (1 - (idle - prev[0]) / (total - prev[1]))

    @staticmethod
    def proc_mem():
        rss = hwm = 0
        for line in _read("/proc/self/status").splitlines():
            if line.startswith("VmRSS:"):
                rss = int(line.split()[1]) // 1024
            elif line.startswith("VmHWM:"):
                hwm = int(line.split()[1]) // 1024
        return rss, hwm

    @staticmethod
    def sys_mem():
        info = {}
        for line in _read("/proc/meminfo").splitlines():
            k, _, v = line.partition(":")
            info[k] = int(v.split()[0]) // 1024 if v.split() else 0
        return info.get("MemTotal", 0), info.get("MemAvailable", 0)

    def snapshot(self):
        npu_f = _read(f"{self.NPU_DEVFREQ}/cur_freq", "0")
        cpu_f = _read("/sys/devices/system/cpu/cpufreq/policy4/scaling_cur_freq", "0")
        rss, hwm = self.proc_mem()
        tot, avail = self.sys_mem()
        return {
            "npu_load": self.npu_load(),
            "npu_freq": f"{int(npu_f) // 1_000_000} MHz" if npu_f.isdigit() else "-",
            "npu_volt": self.npu_volt(),
            "npu_temp": self.temp("npu-thermal"),
            "soc_temp": self.temp("soc-thermal"),
            "cpu_big": f"{int(cpu_f) // 1000} MHz" if cpu_f.isdigit() else "-",
            "cpu_util": self.cpu_util(),
            "rss": rss, "hwm": hwm, "mem_total": tot, "mem_avail": avail,
            "load1": _read("/proc/loadavg", "0").split()[0],
        }


# ---------------------------------------------------------------- performance mode (Rockchip fix_freq)
class PerfMode:
    def __init__(self):
        self.saved = None

    @property
    def on(self):
        return self.saved is not None

    def enable(self):
        if self.saved is not None:
            return
        saved = {}
        cpufreq = "/sys/devices/system/cpu/cpufreq"
        for p in ("policy0", "policy4", "policy6"):
            path = f"{cpufreq}/{p}/scaling_governor"
            if os.path.exists(path):
                saved[path] = _read(path)
        npu = f"{SysMetrics.NPU_DEVFREQ}/governor"
        if os.path.exists(npu):
            saved[npu] = _read(npu)
        idle = [f"/sys/devices/system/cpu/cpu{i}/cpuidle/state1/disable" for i in range(8)]
        for path in idle:
            if os.path.exists(path):
                saved[path] = _read(path)
        try:
            for p in ("policy0", "policy4", "policy6"):
                self._w(f"{cpufreq}/{p}/scaling_governor", "performance")
            for path in idle:
                self._w(path, "1")
            self._w(npu, "userspace")
            self._w(f"{SysMetrics.NPU_DEVFREQ}/userspace/set_freq", "1000000000")
        except OSError:
            self.saved = saved
            self.disable()
            raise
        self.saved = saved

    def disable(self):
        if self.saved is None:
            return
        npu_gov = f"{SysMetrics.NPU_DEVFREQ}/governor"
        if npu_gov in self.saved:
            # rknpu*_ondemand only re-targets on NPU power-on: leave the NPU at max
            # before handing it back, as the stock governor would.
            try:
                self._w(f"{SysMetrics.NPU_DEVFREQ}/userspace/set_freq", "1000000000")
            except OSError:
                pass
        for path, val in self.saved.items():
            try:
                self._w(path, val)
            except OSError:
                pass
        self.saved = None

    @staticmethod
    def _w(path, val):
        with open(path, "w") as f:
            f.write(val)


# ---------------------------------------------------------------- LLM engine
class Engine:
    def __init__(self, lib_path, model, ctx, max_new):
        self.lib = c.CDLL(lib_path)
        self.lib.rkllm_createDefaultParam.restype = RKLLMParam
        self.lib.rkllm_init.argtypes = [c.POINTER(c.c_void_p), c.POINTER(RKLLMParam), c.POINTER(RKLLMCallback)]
        self.lib.rkllm_init.restype = c.c_int
        self.lib.rkllm_run.argtypes = [c.c_void_p, c.POINTER(RKLLMInput), c.POINTER(RKLLMInferParam), c.c_void_p]
        self.lib.rkllm_run.restype = c.c_int
        self.lib.rkllm_abort.argtypes = [c.c_void_p]
        self.lib.rkllm_abort.restype = c.c_int
        self.lib.rkllm_clear_kv_cache.argtypes = [c.c_void_p, c.c_int, c.POINTER(c.c_int), c.POINTER(c.c_int)]
        self.lib.rkllm_clear_kv_cache.restype = c.c_int
        self.lib.rkllm_set_chat_template.argtypes = [c.c_void_p, c.c_char_p, c.c_char_p, c.c_char_p]
        self.lib.rkllm_set_chat_template.restype = c.c_int
        self.lib.rkllm_destroy.argtypes = [c.c_void_p]
        self.lib.rkllm_destroy.restype = c.c_int

        self.model, self.ctx, self.max_new = model, ctx, max_new
        self.events = queue.Queue()
        self._cb = ResultCB(self._on_result)  # keep a reference: the runtime calls it from its own thread
        self._pending = b""

        p = self.lib.rkllm_createDefaultParam()
        p.model_path = model.encode()
        p.max_context_len = ctx
        p.max_new_tokens = max_new
        p.top_k = 20
        p.top_p = 0.8
        p.temperature = 0.7
        p.repeat_penalty = 1.05
        p.skip_special_token = True
        p.ignore_eos_token = False
        p.is_async = False
        p.extend_param.base_domain_id = 0
        p.extend_param.embed_flash = 1
        p.extend_param.enabled_cpus_num = 4
        p.extend_param.enabled_cpus_mask = 0xF0  # Cortex-A76 cluster (cpu4-7)
        self._param = p
        cb = RKLLMCallback()
        cb.result_callback = self._cb
        self._cbs = cb
        self.handle = c.c_void_p()
        t0 = time.time()
        if self.lib.rkllm_init(c.byref(self.handle), c.byref(self._param), c.byref(self._cbs)) != 0:
            raise RuntimeError("rkllm_init failed (is the 0.9.8 NPU driver loaded?)")
        self.load_s = time.time() - t0
        self.thread = None

    def _on_result(self, result, _userdata, state):
        r = result.contents
        if state == RKLLM_RUN_NORMAL:
            raw = self._pending + (r.text or b"")
            try:
                text, self._pending = raw.decode("utf-8"), b""
            except UnicodeDecodeError as e:  # keep an incomplete multi-byte tail for the next token
                text, self._pending = raw[:e.start].decode("utf-8", "replace"), raw[e.start:]
            self.events.put(("token", text, time.time()))
        elif state == RKLLM_RUN_FINISH:
            p = r.perf
            self.events.put(("finish", (p.prefill_time_ms, p.prefill_tokens, p.generate_time_ms,
                                        p.generate_tokens, p.memory_usage_mb), time.time()))
        elif state == RKLLM_RUN_ERROR:
            self.events.put(("error", "runtime reported an error", time.time()))
        return 0

    def generate(self, prompt, think, max_new):
        # a custom (system-prompt) template has to carry Qwen's thinking switch itself
        if getattr(self, "sys_text", None) is not None and getattr(self, "tpl_think", None) != think:
            self._apply_template(think)

        def work():
            inp = RKLLMInput()
            inp.role = b"user"
            inp.enable_thinking = think
            inp.input_type = RKLLM_INPUT_PROMPT
            inp.prompt_input = prompt.encode()
            ip = RKLLMInferParam()
            ip.mode = RKLLM_INFER_GENERATE
            ip.keep_history = 1
            ip.max_new_tokens = max_new
            self._pending = b""
            ret = self.lib.rkllm_run(self.handle, c.byref(inp), c.byref(ip), None)
            self.events.put(("done", ret, time.time()))

        self.events.put(("start", None, time.time()))
        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()

    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def abort(self):
        if self.busy():
            self.lib.rkllm_abort(self.handle)

    def clear(self):
        self.lib.rkllm_clear_kv_cache(self.handle, 0, None, None)

    def set_system(self, text, think=False):
        # Qwen chat template with an explicit system turn
        self.sys_text = text
        self._apply_template(think)

    def _apply_template(self, think):
        sys_part = f"<|im_start|>system\n{self.sys_text}<|im_end|>\n" if self.sys_text else ""
        # like Qwen3.5's own template: with thinking off, pre-fill an empty think block
        suffix = "<|im_end|>\n<|im_start|>assistant\n" + ("" if think else "<think>\n\n</think>\n\n")
        self.lib.rkllm_set_chat_template(self.handle, sys_part.encode(), b"<|im_start|>user\n", suffix.encode())
        self.tpl_think = think

    def close(self):
        self.abort()
        if self.thread:
            self.thread.join(timeout=5)
        self.lib.rkllm_destroy(self.handle)


# ---------------------------------------------------------------- turn statistics
class Turn:
    def __init__(self):
        self.t_send = time.time()
        self.t_first = None
        self.t_last = None
        self.n = 0
        self.stamps = []
        self.perf = None
        self.state = "prefill"

    def token(self, t):
        if self.t_first is None:
            self.t_first = t
            self.state = "generating"
        self.t_last = t
        self.n += 1
        self.stamps.append(t)

    @property
    def ttft_ms(self):
        return (self.t_first - self.t_send) * 1000 if self.t_first else None

    @property
    def avg_tps(self):
        if self.n < 2:
            return 0.0
        return (self.n - 1) / max(self.t_last - self.t_first, 1e-6)

    def inst_tps(self, now, window=1.5):
        recent = [t for t in self.stamps if now - t <= window]
        if len(recent) < 2:
            return 0.0
        return (len(recent) - 1) / max(recent[-1] - recent[0], 1e-6)


# ---------------------------------------------------------------- UI
HELP = ("/clear  reset conversation   /think  toggle thinking   /max N  max new tokens\n"
        "/sys TEXT  set system prompt (clears)   /perf  toggle performance clocks\n"
        "/quit  exit      keys: Esc abort · PgUp/PgDn scroll · Ctrl-C quit")


class ChatUI:
    def __init__(self, scr, eng, args):
        self.scr, self.eng, self.args = scr, eng, args
        self.sysm = SysMetrics()
        self.perf = PerfMode()
        self.msgs = []           # [role, text]  role: user | bot | info | stat
        self.inp = ""
        self.scroll = 0
        self.turn = None
        self.last = None         # last finished Turn
        self.think = False
        self.max_new = args.max_new
        self.ctx_used = 0
        self.history = []
        self.hist_i = None
        self.snap = self.sysm.snapshot()
        self.t_snap = 0
        self.peak_tps = 0.0
        curses.curs_set(1)
        curses.use_default_colors()
        for i, col in enumerate((curses.COLOR_CYAN, curses.COLOR_GREEN, curses.COLOR_YELLOW,
                                 curses.COLOR_MAGENTA, curses.COLOR_RED, curses.COLOR_BLUE), start=1):
            curses.init_pair(i, col, -1)
        self.scr.timeout(100)
        self.scr.keypad(True)
        self.info(f"Model {os.path.basename(eng.model)} loaded in {eng.load_s:.1f} s — "
                  f"context {eng.ctx}, max new {self.max_new}. Type /help for commands.")

    # -- transcript helpers
    @staticmethod
    def _hide_empty_think(text):
        # Qwen3.5's template emits "<think>\n\n</think>" even with thinking off: don't show an empty block
        m = re.match(r"\s*<think>(.*?)(</think>|$)", text, re.S)
        if m and not m.group(1).strip():
            return text[m.end():].lstrip("\n")
        return text

    def info(self, text, role="info"):
        self.msgs.append([role, text])
        self.scroll = 0

    def _wrapped(self, width):
        lines = []
        for role, text in self.msgs:
            prefix = {"user": "you › ", "bot": "llm › ", "info": "  · ", "stat": "    "}[role]
            attr = {"user": curses.color_pair(1) | curses.A_BOLD, "bot": curses.A_NORMAL,
                    "info": curses.color_pair(3), "stat": curses.color_pair(6) | curses.A_DIM}[role]
            if role == "bot":
                text = self._hide_empty_think(text or "")
            first = True
            for para in (text or "").split("\n"):
                chunks = textwrap.wrap(para, max(width - len(prefix), 10), replace_whitespace=False,
                                       drop_whitespace=False) or [""]
                for chunk in chunks:
                    lines.append((prefix if first else " " * len(prefix), chunk, attr))
                    first = False
            lines.append(("", "", 0))
        return lines

    # -- drawing
    def draw(self):
        h, w = self.scr.getmaxyx()
        self.scr.erase()
        side = 34 if w >= 90 else 0
        chat_w = w - side - (1 if side else 0)

        # header
        title = f" rkllm chat · {os.path.basename(self.eng.model)} · NPU driver {self._drv()} "
        self.scr.addnstr(0, 0, title.ljust(w), w, curses.color_pair(4) | curses.A_REVERSE)

        # transcript
        body_h = h - 4
        lines = self._wrapped(chat_w - 1)
        max_scroll = max(len(lines) - body_h, 0)
        self.scroll = min(self.scroll, max_scroll)
        start = max(len(lines) - body_h - self.scroll, 0)
        for row, (pre, txt, attr) in enumerate(lines[start:start + body_h]):
            self._put(1 + row, 0, pre, chat_w, attr if pre.strip() else curses.A_NORMAL)
            self._put(1 + row, len(pre), txt, chat_w - len(pre), attr)
        if self.scroll:
            self._put(1, chat_w - 12, f"[↑{self.scroll}]", 12, curses.color_pair(3))

        # metrics panel
        if side:
            x = chat_w + 1
            for y in range(1, h - 2):
                self.scr.addch(y, chat_w, curses.ACS_VLINE)
            self._panel(x, side - 1, h - 3)

        # status + input
        state = "idle"
        if self.turn:
            state = self.turn.state
        st = (f" {state.upper():10} think:{'on' if self.think else 'off'}  max:{self.max_new}  "
              f"perf:{'ON' if self.perf.on else 'off'}  ctx {self.ctx_used}/{self.eng.ctx} ")
        if not side:
            st += self._compact_metrics()
        self.scr.addnstr(h - 2, 0, st.ljust(w), w, curses.color_pair(6) | curses.A_REVERSE)
        prompt = "› "
        visible = self.inp[-(w - len(prompt) - 1):]
        self._put(h - 1, 0, prompt + visible, w - 1, curses.A_BOLD)
        self.scr.move(h - 1, min(len(prompt) + len(visible), w - 1))
        self.scr.refresh()

    def _put(self, y, x, s, n, attr=0):
        if n <= 0:
            return
        try:
            self.scr.addnstr(y, x, s, n, attr)
        except curses.error:
            pass

    def _drv(self):
        v = _read("/sys/kernel/debug/rknpu/version", "?")
        return v.replace("RKNPU driver: ", "")

    def _live(self):
        now = time.time()
        t = self.turn or self.last
        if not t:
            return None
        live = {
            "ttft": t.ttft_ms,
            "n": t.n,
            "avg": t.avg_tps,
            "inst": t.inst_tps(now) if self.turn else 0.0,
            "elapsed": ((t.t_last or now) - t.t_send) if not self.turn else now - t.t_send,
        }
        if t.perf:
            pt, pn, gt, gn, mem = t.perf
            live.update(prefill_tps=pn * 1000 / pt if pt > 0 else 0, prefill_n=pn, prefill_ms=pt,
                        gen_tps=gn * 1000 / gt if gt > 0 else 0, gen_n=gn, vmhwm=mem)
        return live

    def _panel(self, x, w, h):
        s, L = self.snap, self._live()
        y = 1
        hdr = curses.color_pair(4) | curses.A_BOLD

        def row(label, val, attr=0):
            nonlocal y
            if y >= h:
                return
            self._put(y, x, f"{label:<12}", 12, curses.A_DIM)
            self._put(y, x + 12, str(val), w - 12, attr)
            y += 1

        def head(t):
            nonlocal y
            if y < h:
                self._put(y, x, t, w, hdr)
                y += 1

        head("GENERATION" + (" (live)" if self.turn else " (last)"))
        if L:
            big = curses.color_pair(2) | curses.A_BOLD
            row("decode", f"{(L['inst'] if self.turn else L.get('gen_tps', L['avg'])):6.2f} tok/s", big)
            row("avg", f"{L['avg']:6.2f} tok/s")
            row("peak", f"{self.peak_tps:6.2f} tok/s")
            row("TTFT", f"{L['ttft']:6.0f} ms" if L["ttft"] else "   ...")
            row("tokens", f"{L['n']}")
            row("elapsed", f"{L['elapsed']:6.1f} s")
            if "prefill_tps" in L:
                row("prefill", f"{L['prefill_n']} tok {L['prefill_tps']:5.0f} t/s")
        else:
            row("", "no turn yet")
        y += 1
        head("CONTEXT")
        pct = 100 * self.ctx_used / self.eng.ctx if self.eng.ctx else 0
        bar_w = max(w - 14, 4)
        fill = int(bar_w * min(pct, 100) / 100)
        row("used", f"{self.ctx_used}/{self.eng.ctx}")
        row("", "█" * fill + "░" * (bar_w - fill), curses.color_pair(5 if pct > 85 else 2))
        y += 1
        head("MEMORY")
        row("process", f"{s['rss']} MB")
        row("peak", f"{max(s['hwm'], int(L['vmhwm']) if L and 'vmhwm' in L else 0)} MB")
        row("system", f"{s['mem_total'] - s['mem_avail']}/{s['mem_total']} MB")
        y += 1
        head("NPU")
        row("load", s["npu_load"])
        row("clock", s["npu_freq"])
        row("vdd", s["npu_volt"])
        row("temp", s["npu_temp"])
        y += 1
        head("SYSTEM")
        row("A76 clock", s["cpu_big"])
        row("CPU util", f"{s['cpu_util']:.0f}%  load {s['load1']}")
        row("SoC temp", s["soc_temp"])

    def _compact_metrics(self):
        L, s = self._live(), self.snap
        tps = (L["inst"] if self.turn else L.get("gen_tps", L["avg"])) if L else 0
        return (f"| {tps:5.2f} tok/s  TTFT {L['ttft'] or 0:4.0f}ms  RSS {s['rss']}MB  NPU {s['npu_load']} "
                f"{s['npu_temp']}" if L else f"| RSS {s['rss']}MB NPU {s['npu_load']}")

    # -- events
    def pump(self):
        while True:
            try:
                kind, data, t = self.eng.events.get_nowait()
            except queue.Empty:
                break
            if kind == "start":
                self.turn = Turn()
                self.msgs.append(["bot", ""])
            elif kind == "token" and self.turn:
                self.turn.token(t)
                self.msgs[-1][1] += data
                if self.turn.n > 8:
                    self.peak_tps = max(self.peak_tps, self.turn.inst_tps(t))
            elif kind == "finish" and self.turn:
                self.turn.perf = data
            elif kind == "error":
                self.info(f"error: {data}")
            elif kind == "done" and self.turn:
                tr = self.turn
                if tr.perf:
                    pt, pn, gt, gn, mem = tr.perf
                    self.ctx_used += pn + gn
                    self.info(f"[{gn} tok · {gn * 1000 / gt if gt else 0:.2f} tok/s · TTFT {tr.ttft_ms or 0:.0f} ms · "
                              f"prefill {pn} tok @ {pn * 1000 / pt if pt else 0:.0f} tok/s · peak mem {mem:.0f} MB]",
                              "stat")
                else:
                    self.info(f"[{tr.n} tok · {tr.avg_tps:.2f} tok/s · aborted]", "stat")
                tr.state = "idle"
                self.last, self.turn = tr, None
        if time.time() - self.t_snap > 0.5:
            self.snap = self.sysm.snapshot()
            self.t_snap = time.time()

    def command(self, line):
        cmd, _, arg = line.partition(" ")
        if cmd in ("/quit", "/exit", "/q"):
            return False
        if cmd == "/help":
            self.info(HELP)
        elif cmd == "/clear":
            self.eng.clear()
            self.msgs, self.ctx_used = [], 0
            self.info("conversation cleared")
        elif cmd == "/think":
            self.think = not self.think
            self.info(f"thinking mode {'on' if self.think else 'off'}")
        elif cmd == "/max":
            try:
                self.max_new = max(1, int(arg))
                self.info(f"max new tokens = {self.max_new}")
            except ValueError:
                self.info("usage: /max N")
        elif cmd == "/sys":
            self.eng.set_system(arg.strip())
            self.eng.clear()
            self.ctx_used = 0
            self.info(f"system prompt {'set' if arg.strip() else 'cleared'}; conversation reset")
        elif cmd == "/perf":
            try:
                if self.perf.on:
                    self.perf.disable()
                    self.info("performance mode off (governors restored)")
                else:
                    self.perf.enable()
                    self.info("performance mode on: CPU performance, NPU 1 GHz, cpuidle state1 off")
            except OSError as e:
                self.info(f"perf toggle failed: {e} (needs root)")
        else:
            self.info(f"unknown command {cmd} — /help")
        return True

    def send(self):
        line = self.inp.strip()
        self.inp = ""
        self.hist_i = None
        if not line:
            return True
        self.history.append(line)
        if line.startswith("/"):
            return self.command(line)
        if self.eng.busy():
            self.info("still generating — Esc to abort")
            return True
        if self.ctx_used > self.eng.ctx * 0.9:
            self.info("context almost full — /clear to start over")
        self.msgs.append(["user", line])
        self.scroll = 0
        self.eng.generate(line, self.think, self.max_new)
        return True

    def key(self, ch):
        if isinstance(ch, str):
            if ch in ("\n", "\r"):
                return self.send()
            if ch == "\x1b":
                if self.eng.busy():
                    self.eng.abort()
                    self.info("aborted")
                return True
            if ch in ("\x7f", "\b"):
                self.inp = self.inp[:-1]
            elif ch == "\x15":  # Ctrl-U
                self.inp = ""
            elif ch.isprintable():
                self.inp += ch
            return True
        if ch in (curses.KEY_BACKSPACE,):
            self.inp = self.inp[:-1]
        elif ch == curses.KEY_PPAGE:
            self.scroll += max(self.scr.getmaxyx()[0] - 6, 1)
        elif ch == curses.KEY_NPAGE:
            self.scroll = max(self.scroll - max(self.scr.getmaxyx()[0] - 6, 1), 0)
        elif ch == curses.KEY_UP and self.history:
            self.hist_i = len(self.history) - 1 if self.hist_i is None else max(self.hist_i - 1, 0)
            self.inp = self.history[self.hist_i]
        elif ch == curses.KEY_DOWN and self.hist_i is not None:
            self.hist_i += 1
            if self.hist_i >= len(self.history):
                self.hist_i, self.inp = None, ""
            else:
                self.inp = self.history[self.hist_i]
        elif ch == curses.KEY_ENTER:
            return self.send()
        return True

    def loop(self):
        try:
            while True:
                self.pump()
                self.draw()
                try:
                    ch = self.scr.get_wch()
                except curses.error:
                    continue
                if not self.key(ch):
                    break
        finally:
            self.perf.disable()


# ---------------------------------------------------------------- entry points
def check_npu():
    if not os.path.exists("/sys/kernel/debug/rknpu/version"):
        sys.exit("NPU driver not loaded. Load the 0.9.8 module first:\n  " + NPU_LOADER)
    v = _read("/sys/kernel/debug/rknpu/version")
    if "0.9." not in v:
        sys.exit(f"{v}: rknn-llm needs NPU driver 0.9.8. Install go2-npu-unlock, reboot, then check: go2-npu status")


def run_once(eng, prompt, think, max_new):
    sm = SysMetrics()
    turn = Turn()
    eng.generate(prompt, think, max_new)
    while True:
        kind, data, t = eng.events.get()
        if kind == "token":
            turn.token(t)
            print(data, end="", flush=True)
        elif kind == "finish":
            turn.perf = data
        elif kind == "error":
            print(f"\n[error] {data}")
        elif kind == "done":
            break
    s = sm.snapshot()
    print("\n")
    if turn.perf:
        pt, pn, gt, gn, mem = turn.perf
        print(f"decode   {gn} tok in {gt / 1000:.2f} s = {gn * 1000 / gt if gt else 0:.2f} tok/s")
        print(f"prefill  {pn} tok in {pt:.0f} ms = {pn * 1000 / pt if pt else 0:.0f} tok/s")
        print(f"TTFT     {turn.ttft_ms or 0:.0f} ms   peak mem {mem:.0f} MB   RSS {s['rss']} MB")
    print(f"NPU      {s['npu_freq']} {s['npu_volt']} {s['npu_temp']}   SoC {s['soc_temp']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model", nargs="?", default=DEFAULT_MODEL)
    ap.add_argument("--ctx", type=int, default=4096)
    ap.add_argument("--max-new", type=int, default=1024)
    ap.add_argument("--lib", default=DEFAULT_LIB)
    ap.add_argument("--think", action="store_true", help="start with thinking mode on")
    ap.add_argument("--sys", default="", help="system prompt")
    ap.add_argument("--once", metavar="PROMPT", help="answer one prompt without the UI")
    args = ap.parse_args()

    check_npu()
    if not os.path.exists(args.model):
        sys.exit(f"model not found: {args.model}")
    print(f"loading {args.model} ...", flush=True)
    eng = Engine(args.lib, args.model, args.ctx, args.max_new)
    if args.sys:
        eng.set_system(args.sys)
    try:
        if args.once:
            run_once(eng, args.once, args.think, args.max_new)
            return
        locale.setlocale(locale.LC_ALL, "")

        def ui(scr):
            u = ChatUI(scr, eng, args)
            u.think = args.think
            u.loop()

        curses.wrapper(ui)
    finally:
        eng.close()


if __name__ == "__main__":
    main()
