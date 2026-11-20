"""Compare base vs fine-tuned (vs an optional large API model) on the held-out test set.
Reports accuracy, macro-F1, latency and cost, and writes finetune/results/results.{json,md}.

    python finetune/evaluate.py --adapter finetune/out/router-lora            # base + fine-tuned
    GROQ_API_KEY=... python finetune/evaluate.py --adapter ... --api-model openai/gpt-oss-120b
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LABELS = ["support", "orders", "escalation"]
T4_USD_PER_HOUR = 0.35  # typical on-demand T4 price; free on Colab/Kaggle


def load_test() -> list[dict]:
    return [json.loads(l) for l in (HERE / "data" / "test.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


def parse(out: str) -> str:
    out = out.strip().lower()
    for l in LABELS:
        if out.startswith(l):
            return l
    return next((l for l in LABELS if l in out), "invalid")


def metrics(name: str, preds: list[str], gold: list[str], lat: list[float], cost_per_1k: float, extra: dict | None = None) -> dict:
    acc = sum(p == g for p, g in zip(preds, gold)) / len(gold)
    f1s = []
    for l in LABELS:
        tp = sum(p == l and g == l for p, g in zip(preds, gold))
        fp = sum(p == l and g != l for p, g in zip(preds, gold))
        fn = sum(p != l and g == l for p, g in zip(preds, gold))
        f1s.append(2 * tp / (2 * tp + fp + fn) if tp else 0.0)
    return {"model": name, "accuracy": round(acc, 4), "macro_f1": round(sum(f1s) / 3, 4),
            "invalid_outputs": sum(p == "invalid" for p in preds),
            "latency_ms_p50": round(statistics.median(lat) * 1000, 1), "latency_ms_p95": round(sorted(lat)[int(len(lat) * 0.95) - 1] * 1000, 1),
            "cost_usd_per_1k_requests": round(cost_per_1k, 4), **(extra or {})}


def run_local(base: str, adapter: str | None, test: list[dict]) -> dict:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(base)
    model = AutoModelForCausalLM.from_pretrained(base, torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32, device_map="auto")
    if adapter:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, adapter).merge_and_unload()
    model.eval()
    preds, lat = [], []
    for i, r in enumerate(test):
        prompt = tok.apply_chat_template(r["messages"][:2], tokenize=False, add_generation_prompt=True)
        ids = tok(prompt, return_tensors="pt").to(model.device)
        if i == 0:  # warm-up, not timed
            model.generate(**ids, max_new_tokens=4, do_sample=False)
        t0 = time.perf_counter()
        with torch.no_grad():
            out = model.generate(**ids, max_new_tokens=4, do_sample=False, pad_token_id=tok.eos_token_id)
        lat.append(time.perf_counter() - t0)
        preds.append(parse(tok.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True)))
    cost = statistics.mean(lat) * 1000 * T4_USD_PER_HOUR / 3600  # GPU-seconds for 1k sequential requests
    name = f"{base.split('/')[-1]} + LoRA (fine-tuned)" if adapter else f"{base.split('/')[-1]} (base, zero-shot)"
    del model
    torch.cuda.empty_cache() if torch.cuda.is_available() else None
    return metrics(name, preds, [r["label"] for r in test], lat, cost, {"hardware": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu"})


def run_api(model: str, test: list[dict]) -> dict:
    import httpx
    key = os.environ.get("GROQ_API_KEY") or os.environ["LLM_API_KEY"]
    price = {"openai/gpt-oss-120b": (0.15, 0.60), "openai/gpt-oss-20b": (0.075, 0.30)}.get(model, (0.5, 1.0))
    preds, lat, cost = [], [], 0.0
    with httpx.Client(timeout=30) as c:
        for r in test:
            for attempt in range(5):
                t0 = time.perf_counter()
                resp = c.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {key}"},
                              # gpt-oss models reason before answering, so leave room for that before the one-word label
                              json={"model": model, "messages": r["messages"][:2], "temperature": 0, "max_tokens": 400})
                if resp.status_code == 429:
                    time.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                break
            lat.append(time.perf_counter() - t0)
            d = resp.json()
            preds.append(parse(d["choices"][0]["message"]["content"]))
            cost += d["usage"]["prompt_tokens"] / 1e6 * price[0] + d["usage"]["completion_tokens"] / 1e6 * price[1]
    return metrics(f"{model} (large API model)", preds, [r["label"] for r in test], lat, cost / len(test) * 1000, {"hardware": "Groq API"})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="Qwen/Qwen2.5-1.5B-Instruct")
    ap.add_argument("--adapter", default="finetune/out/router-lora")
    ap.add_argument("--api-model", default=None)
    ap.add_argument("--skip-local", action="store_true")
    args = ap.parse_args()
    test = load_test()
    rows = []
    if not args.skip_local:
        rows.append(run_local(args.base, None, test))
        rows.append(run_local(args.base, args.adapter, test))
    if args.api_model:
        rows.append(run_api(args.api_model, test))
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    (out / "results.json").write_text(json.dumps({"test_size": len(test), "rows": rows}, indent=2))
    md = ["| Model | Accuracy | Macro-F1 | Invalid | p50 latency | p95 latency | Cost / 1k requests |", "|---|---|---|---|---|---|---|"]
    md += [f"| {r['model']} | {r['accuracy'] * 100:.1f}% | {r['macro_f1']:.3f} | {r['invalid_outputs']} | {r['latency_ms_p50']} ms | {r['latency_ms_p95']} ms | ${r['cost_usd_per_1k_requests']:.4f} |" for r in rows]
    (out / "results.md").write_text(f"Test set: {len(test)} held-out messages (templates unseen in training)\n\n" + "\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
