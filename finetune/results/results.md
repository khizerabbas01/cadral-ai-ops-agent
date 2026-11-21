Test set: 57 held-out messages (templates unseen in training)

| Model | Accuracy | Macro-F1 | Invalid | p50 latency | p95 latency | Cost / 1k requests | Measured on |
|---|---|---|---|---|---|---|---|
| Qwen2.5-1.5B-Instruct (base, zero-shot) | 70.2% | 0.705 | 0 | 72.9 ms | 118.0 ms | $0.0080 | Tesla T4 |
| Qwen2.5-1.5B-Instruct + LoRA (fine-tuned) | 98.2% | 0.984 | 0 | 80.9 ms | 128.9 ms | $0.0081 | Tesla T4 |
| openai/gpt-oss-120b (large API model) | 94.7% | 0.952 | 0 | 433.1 ms | 534.4 ms | $0.0386 | Groq API |
