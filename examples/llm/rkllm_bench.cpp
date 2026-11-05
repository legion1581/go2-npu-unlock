// rkllm_bench: measure TTFT and decode tokens/s of an .rkllm model on RK3588.
// usage: rkllm_bench model.rkllm max_new_tokens max_context runs "prompt" [ignore_eos=1] [print=0] [think=0]
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include "rkllm.h"

using clk = std::chrono::steady_clock;

struct Run {
    clk::time_point start, first;
    std::vector<clk::time_point> tok;
    RKLLMPerfStat perf{};
    bool print = false, error = false;
    std::string text;
};

static int cb(RKLLMResult *r, void *ud, LLMCallState st) {
    Run *run = static_cast<Run *>(ud);
    if (st == RKLLM_RUN_NORMAL) {
        auto now = clk::now();
        if (run->tok.empty()) run->first = now;
        run->tok.push_back(now);
        if (r->text) {
            run->text += r->text;
            if (run->print) { fputs(r->text, stdout); fflush(stdout); }
        }
    } else if (st == RKLLM_RUN_FINISH) {
        run->perf = r->perf;
    } else if (st == RKLLM_RUN_ERROR) {
        run->error = true;
    }
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 6) {
        fprintf(stderr, "usage: %s model max_new max_ctx runs prompt [ignore_eos=1] [print=0] [think=0]\n", argv[0]);
        return 1;
    }
    int max_new = atoi(argv[2]), max_ctx = atoi(argv[3]), runs = atoi(argv[4]);
    const char *prompt = argv[5];
    bool ignore_eos = argc > 6 ? atoi(argv[6]) : 1;
    bool print = argc > 7 ? atoi(argv[7]) : 0;
    bool think = argc > 8 ? atoi(argv[8]) : 0;

    RKLLMParam p = rkllm_createDefaultParam();
    p.model_path = argv[1];
    p.max_new_tokens = max_new;
    p.max_context_len = max_ctx;
    p.top_k = 1;
    p.top_p = 0.95f;
    p.temperature = 0.8f;
    p.repeat_penalty = getenv("REPEAT") ? atof(getenv("REPEAT")) : 1.1f;
    p.skip_special_token = true;
    p.ignore_eos_token = ignore_eos;
    p.extend_param.base_domain_id = 0;
    p.extend_param.embed_flash = getenv("EMBED_FLASH") ? atoi(getenv("EMBED_FLASH")) : 1;
    p.extend_param.enabled_cpus_mask = getenv("CPUMASK") ? strtoul(getenv("CPUMASK"), nullptr, 0) : 0xF0;  // default: Cortex-A76 cluster (cpu4-7)
    p.extend_param.enabled_cpus_num = __builtin_popcount(p.extend_param.enabled_cpus_mask);

    RKLLMCallback cbs{};
    cbs.result_callback = cb;
    Run run;
    cbs.result_userdata = &run;

    LLMHandle h = nullptr;
    auto t0 = clk::now();
    if (rkllm_init(&h, &p, &cbs) != 0) { fprintf(stderr, "rkllm_init failed\n"); return 1; }
    double load_s = std::chrono::duration<double>(clk::now() - t0).count();
    printf("load: %.2f s\n", load_s);

    RKLLMInput in{};
    in.role = "user";
    in.enable_thinking = think;
    in.input_type = RKLLM_INPUT_PROMPT;
    in.prompt_input = prompt;
    RKLLMInferParam ip{};
    ip.mode = RKLLM_INFER_GENERATE;
    ip.keep_history = 0;

    double sum_tps = 0, sum_ttft = 0;
    for (int i = 0; i < runs; i++) {
        run = Run();
        run.print = print && i == 0;
        rkllm_clear_kv_cache(h, 0, nullptr, nullptr);
        run.start = clk::now();
        if (rkllm_run(h, &in, &ip, &run) != 0 || run.error) { fprintf(stderr, "run %d failed\n", i); break; }
        if (run.print) printf("\n");
        size_t n = run.tok.size();
        double ttft = n ? std::chrono::duration<double, std::milli>(run.first - run.start).count() : 0;
        double dec = n > 1 ? std::chrono::duration<double>(run.tok.back() - run.tok.front()).count() : 0;
        double tps = dec > 0 ? (n - 1) / dec : 0;
        printf("run %d: ttft %.0f ms | %zu tokens | decode %.2f tok/s (wall) | runtime: prefill %d tok %.0f ms (%.1f tok/s), "
               "generate %d tok %.0f ms (%.2f tok/s), VmHWM %.0f MB\n",
               i, ttft, n, tps, run.perf.prefill_tokens, run.perf.prefill_time_ms,
               run.perf.prefill_time_ms > 0 ? run.perf.prefill_tokens * 1000.0 / run.perf.prefill_time_ms : 0,
               run.perf.generate_tokens, run.perf.generate_time_ms,
               run.perf.generate_time_ms > 0 ? run.perf.generate_tokens * 1000.0 / run.perf.generate_time_ms : 0,
               run.perf.memory_usage_mb);
        sum_tps += tps;
        sum_ttft += ttft;
    }
    if (runs > 0) printf("AVG: ttft %.0f ms, decode %.2f tok/s\n", sum_ttft / runs, sum_tps / runs);
    rkllm_destroy(h);
    return 0;
}
