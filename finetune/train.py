"""QLoRA fine-tune of a small open model (default Qwen2.5-1.5B-Instruct) as the Router classifier.
Runs in ~6 minutes on a free Colab T4.

    pip install -r finetune/requirements.txt
    python finetune/build_dataset.py
    python finetune/train.py --out finetune/out/router-lora
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from datasets import Dataset
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import (AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, DataCollatorForLanguageModeling,
                          Trainer, TrainingArguments)

DATA = Path(__file__).resolve().parent / "data"


def load(split: str) -> list[dict]:
    return [json.loads(l) for l in (DATA / f"{split}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="Qwen/Qwen2.5-1.5B-Instruct")
    ap.add_argument("--out", default="finetune/out/router-lora")
    ap.add_argument("--epochs", type=float, default=3)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--rank", type=int, default=16)
    args = ap.parse_args()

    tok = AutoTokenizer.from_pretrained(args.base)
    tok.pad_token = tok.pad_token or tok.eos_token
    quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16,
                               bnb_4bit_use_double_quant=True) if torch.cuda.is_available() else None
    model = AutoModelForCausalLM.from_pretrained(args.base, quantization_config=quant, device_map="auto")
    model = prepare_model_for_kbit_training(model) if quant else model
    model = get_peft_model(model, LoraConfig(r=args.rank, lora_alpha=args.rank * 2, lora_dropout=0.05, task_type="CAUSAL_LM",
                                             target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]))
    model.print_trainable_parameters()

    def encode(rec: dict) -> dict:
        prompt = tok.apply_chat_template(rec["messages"][:2], tokenize=False, add_generation_prompt=True)
        full = prompt + rec["messages"][2]["content"] + tok.eos_token
        ids = tok(full, truncation=True, max_length=256)
        n_prompt = len(tok(prompt)["input_ids"])
        ids["labels"] = [-100] * n_prompt + ids["input_ids"][n_prompt:]  # loss only on the label tokens
        return ids

    train = Dataset.from_list(load("train")).map(encode, remove_columns=["messages", "text", "label"])
    val = Dataset.from_list(load("val")).map(encode, remove_columns=["messages", "text", "label"])

    def collate(batch):
        m = max(len(b["input_ids"]) for b in batch)
        pad = lambda seq, v: seq + [v] * (m - len(seq))
        return {"input_ids": torch.tensor([pad(b["input_ids"], tok.pad_token_id) for b in batch]),
                "attention_mask": torch.tensor([pad(b["attention_mask"], 0) for b in batch]),
                "labels": torch.tensor([pad(b["labels"], -100) for b in batch])}

    trainer = Trainer(model=model, train_dataset=train, eval_dataset=val, data_collator=collate, args=TrainingArguments(
        output_dir=args.out, num_train_epochs=args.epochs, learning_rate=args.lr, per_device_train_batch_size=16,
        gradient_accumulation_steps=1, warmup_ratio=0.05, lr_scheduler_type="cosine", logging_steps=10,
        eval_strategy="epoch", save_strategy="no", bf16=torch.cuda.is_available(), report_to=[]))
    trainer.train()
    model.save_pretrained(args.out)
    tok.save_pretrained(args.out)
    print(f"saved LoRA adapter to {args.out}")


if __name__ == "__main__":
    _ = DataCollatorForLanguageModeling  # noqa: imported for parity with HF examples
    main()
