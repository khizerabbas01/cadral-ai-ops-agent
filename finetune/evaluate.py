"""Compare base vs fine-tuned (vs an optional large API model) on the held-out test set.
Reports accuracy, macro-F1, latency and cost, and writes finetune/results/results.{json,md}.

    python finetune/evaluate.py --adapter finetune/out/router-lora                   # base + fine-tuned (GPU)
    GROQ_API_KEY=... python finetune/evaluate.py --skip-local --api-model openai/gpt-oss-120b   # adds the API row

Every model's row is saved as soon as it finishes and merged into the existing results file, so the GPU rows and
the API row can come from different runs and a failure in one cannot lose the others. Rows are only merged when
they were measured on the same test set.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
LABELS = ["support", "orders", "escalation"]
ORDER = {"base": 0, "finetuned": 1, "api": 2}
T4_USD_PER_HOUR = 0.35  # typical on-demand T4 price; free on Colab/Kaggle
PRICES = {"openai/gpt-oss-120b": (0.15, 0.60), "openai/gpt-oss-20b": (0.075, 0.30)}  # USD per 1M input / output tokens


def load_test() -> list[dict]:
    return [json.loads(l) for l in (HERE / "data" / "test.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


def fingerprint(test: list[dict]) -> str:
    """Identifies the test set independent of line endings, so rows from two machines can be checked against it."""
    return hashlib.sha256(json.dumps([[r["text"], r["label"]] for r in test]).encode("ascii")).hexdigest()[:12]


def parse(out: str | None) -> str:
    out = (out or "").strip().lower()
    for l in LABELS:
        if out.startswith(l):
            return l
    return next((l for l in LABELS if l in out), "invalid")


def p50(values: list[float]) -> float:
    return round(statistics.median(values) * 1000, 1)


def p95(values: list[float]) -> float:
    return round(sorted(values)[max(0, math.ceil(len(values) * 0.95) - 1)] * 1000, 1)  # nearest rank


def score(preds: list[str], gold: list[str]) -> tuple[float, float]:
    f1s = []
    for l in LABELS:
        tp = sum(p == l and g == l for p, g in zip(preds, gold))
        fp = sum(p == l and g != l for p, g in zip(preds, gold))
        fn = sum(p != l and g == l for p, g in zip(preds, gold))
        f1s.append(2 * tp / (2 * tp + fp + fn) if tp else 0.0)
    return round(sum(p == g for p, g in zip(preds, gold)) / len(gold), 4), round(sum(f1s) / len(LABELS), 4)


def metrics(kind: str, name: str, test: list[dict], preds: list[str], lat: list[float], cost_per_1k: float,
            extra: dict | None = None) -> dict:
    gold = [r["label"] for r in test]
    acc, f1 = score(preds, gold)
    return {"kind": kind, "model": name, "accuracy": acc, "macro_f1": f1,
            "invalid_outputs": sum(p == "invalid" for p in preds),
            "latency_ms_p50": p50(lat), "latency_ms_p95": p95(lat), "cost_usd_per_1k_requests": round(cost_per_1k, 4),
            "by_label": {l: {"n": gold.count(l), "correct": sum(p == g == l for p, g in zip(preds, gold))} for l in LABELS},
            "errors": [{"text": r["text"], "gold": g, "predicted": p} for r, g, p in zip(test, gold, preds) if p != g],
            "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **(extra or {})}


def run_local(base: str, adapter: str | None, test: list[dict]) -> dict:
    import torch
    import transformers
    from transformers import AutoModelForCausalLM, AutoTokenizer
    cuda = torch.cuda.is_available()
    dtype = torch.bfloat16 if cuda else torch.float32
    dtype_arg = "dtype" if int(transformers.__version__.split(".")[0]) >= 5 else "torch_dtype"  # renamed in transformers 5
    tok = AutoTokenizer.from_pretrained(base)
    model = AutoModelForCausalLM.from_pretrained(base, device_map="auto", **{dtype_arg: dtype})
    versions = {"torch": torch.__version__, "transformers": transformers.__version__}
    if adapter:
        import peft
        model = peft.PeftModel.from_pretrained(model, adapter).merge_and_unload()
        versions["peft"] = peft.__version__
    model.eval()
    prompts = [tok(tok.apply_chat_template(r["messages"][:2], tokenize=False, add_generation_prompt=True),
                   return_tensors="pt").to(model.device) for r in test]
    # Plain greedy decoding: the model's own generation config adds sampling settings and a repetition penalty
    greedy = dict(max_new_tokens=8, do_sample=False, repetition_penalty=1.0, pad_token_id=tok.eos_token_id)

    # 1. Free-text answer, parsed the same way as the API model's answer
    preds, lat = [], []
    with torch.no_grad():
        model.generate(**prompts[0], **greedy)  # warm-up, not timed
        for ids in prompts:
            t0 = time.perf_counter()
            out = model.generate(**ids, **greedy)
            lat.append(time.perf_counter() - t0)
            preds.append(parse(tok.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True)))

    # 2. The way finetune/serve.py answers: one forward pass, label picked from the three labels' first-token logits
    first = [tok(l, add_special_tokens=False)["input_ids"][0] for l in LABELS]
    served, served_lat = [], []
    if len(set(first)) == len(LABELS):
        with torch.no_grad():
            model(**prompts[0])  # warm-up, not timed
            for ids in prompts:
                t0 = time.perf_counter()
                i = int(model(**ids).logits[0, -1, first].float().argmax())  # int() waits for the GPU to finish
                served_lat.append(time.perf_counter() - t0)
                served.append(LABELS[i])

    cost = statistics.mean(lat) * 1000 * T4_USD_PER_HOUR / 3600  # GPU-seconds for 1k sequential requests
    name = f"{base.split('/')[-1]} + LoRA (fine-tuned)" if adapter else f"{base.split('/')[-1]} (base, zero-shot)"
    extra = {"hardware": torch.cuda.get_device_name(0) if cuda else "cpu", "dtype": str(dtype).replace("torch.", ""),
             "versions": versions}
    if served:
        acc, f1 = score(served, [r["label"] for r in test])
        extra["single_pass"] = {"accuracy": acc, "macro_f1": f1, "latency_ms_p50": p50(served_lat), "latency_ms_p95": p95(served_lat)}
    del model
    torch.cuda.empty_cache() if cuda else None
    return metrics("finetuned" if adapter else "base", name, test, preds, lat, cost, extra)


def run_api(model: str, test: list[dict]) -> dict:
    import httpx
    key = os.environ.get("GROQ_API_KEY") or os.environ["LLM_API_KEY"]
    price = PRICES[model]
    body = {"model": model, "temperature": 0, "max_tokens": 400}
    if "gpt-oss" in model:  # reasoning models: same settings as the application (backend/app/llm.py)
        body |= {"reasoning_effort": "low", "include_reasoning": False}
    preds, lat, server, cost, tokens = [], [], [], 0.0, 0
    with httpx.Client(timeout=30) as c:
        for r in test:
            for attempt in range(8):
                t0 = time.perf_counter()
                try:
                    resp = c.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {key}"},
                                  json=body | {"messages": r["messages"][:2]})
                except httpx.TransportError:
                    time.sleep(2 ** min(attempt, 5))
                    continue
                dt = time.perf_counter() - t0  # only the attempt that succeeds is timed
                if resp.status_code == 429 or resp.status_code >= 500:
                    try:
                        wait = float(resp.headers.get("retry-after", ""))
                    except ValueError:
                        wait = 2 ** min(attempt, 5)
                    time.sleep(min(wait, 60) + 0.5)
                    continue
                resp.raise_for_status()
                break
            else:
                raise RuntimeError(f"{model}: no answer after 8 attempts (rate limit or network)")
            d = resp.json()
            usage = d.get("usage") or {}
            lat.append(dt)
            preds.append(parse(d["choices"][0]["message"].get("content")))
            cost += usage.get("prompt_tokens", 0) / 1e6 * price[0] + usage.get("completion_tokens", 0) / 1e6 * price[1]
            tokens += usage.get("total_tokens", 0)
            if "total_time" in usage:  # the provider's own queue + processing time, without the network
                server.append(usage.get("queue_time", 0) + usage["total_time"])
    extra = {"hardware": "Groq API", "tokens_per_request": round(tokens / len(test))}
    if "reasoning_effort" in body:
        extra["reasoning_effort"] = body["reasoning_effort"]
    if len(server) == len(test):
        extra["server_ms_p50"] = p50(server)
    return metrics("api", f"{model} (large API model)", test, preds, lat, cost / len(test) * 1000, extra)


def table(rows: list[dict]) -> str:
    md = ["| Model | Accuracy | Macro-F1 | Invalid | p50 latency | p95 latency | Cost / 1k requests | Measured on |", "|---|---|---|---|---|---|---|---|"]
    md += [f"| {r['model']} | {r['accuracy'] * 100:.1f}% | {r['macro_f1']:.3f} | {r['invalid_outputs']} | {r['latency_ms_p50']} ms | "
           f"{r['latency_ms_p95']} ms | ${r['cost_usd_per_1k_requests']:.4f} | {r.get('hardware', '')} |" for r in rows]
    return "\n".join(md)


def save(row: dict, test: list[dict]) -> list[dict]:
    """Merge one finished row into results.json (replacing an older row of the same kind) and rewrite results.md."""
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    path, fp, rows = out / "results.json", fingerprint(test), []
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
        if old.get("test_fingerprint") == fp:
            rows = [r for r in old.get("rows", []) if r.get("kind") != row["kind"]]
        else:
            print("existing results were measured on a different test set - starting a new results file")
    except (OSError, ValueError):
        pass
    rows = sorted(rows + [row], key=lambda r: ORDER.get(r.get("kind"), 9))
    path.write_text(json.dumps({"test_size": len(test), "test_fingerprint": fp, "rows": rows}, indent=2) + "\n", encoding="utf-8")
    (out / "results.md").write_text(f"Test set: {len(test)} held-out messages (templates unseen in training)\n\n{table(rows)}\n", encoding="utf-8")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="Qwen/Qwen2.5-1.5B-Instruct")
    ap.add_argument("--adapter", default="finetune/out/router-lora")
    ap.add_argument("--api-model", default=None, choices=sorted(PRICES))
    ap.add_argument("--skip-local", action="store_true")
    args = ap.parse_args()
    test = load_test()
    rows: list[dict] = []
    if not args.skip_local:
        rows = save(run_local(args.base, None, test), test)
        rows = save(run_local(args.base, args.adapter, test), test)
    if args.api_model:
        rows = save(run_api(args.api_model, test), test)
    print(table(rows))


if __name__ == "__main__":
    main()
