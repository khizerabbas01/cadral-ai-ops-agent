"""QLoRA fine-tune of a small open model (default Qwen2.5-1.5B-Instruct) as the Router classifier.
Sized for a free Colab T4.

    pip install -r finetune/requirements.txt
    python finetune/build_dataset.py
    python finetune/train.py --out finetune/out/router-lora

Writes the LoRA adapter to --out and a short training record to finetune/results/train.json.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import peft
import torch
import transformers
from datasets import Dataset
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, Trainer, TrainingArguments

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
BATCH = 16


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
    cuda = torch.cuda.is_available()

    tok = AutoTokenizer.from_pretrained(args.base)
    tok.pad_token = tok.pad_token or tok.eos_token
    quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16,
                               bnb_4bit_use_double_quant=True) if cuda else None
    model = AutoModelForCausalLM.from_pretrained(args.base, quantization_config=quant, device_map="auto")
    model = prepare_model_for_kbit_training(model) if quant else model
    model = get_peft_model(model, LoraConfig(r=args.rank, lora_alpha=args.rank * 2, lora_dropout=0.05, task_type="CAUSAL_LM",
                                             target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]))
    model.print_trainable_parameters()
    trainable, total = model.get_nb_trainable_parameters()

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

    steps = math.ceil(len(train) / BATCH) * args.epochs
    trainer = Trainer(model=model, train_dataset=train, eval_dataset=val, data_collator=collate, args=TrainingArguments(
        output_dir=args.out, num_train_epochs=args.epochs, learning_rate=args.lr, per_device_train_batch_size=BATCH,
        gradient_accumulation_steps=1, warmup_steps=max(1, round(steps * 0.05)), lr_scheduler_type="cosine", logging_steps=10,
        eval_strategy="epoch", save_strategy="no", bf16=cuda, report_to="none",
        # a PEFT wrapper hides the base model's signature, so the Trainer has to be told which key holds the labels
        label_names=["labels"], prediction_loss_only=True))
    result = trainer.train()
    model.save_pretrained(args.out)
    tok.save_pretrained(args.out)
    print(f"saved LoRA adapter to {args.out}")

    record = {"base": args.base, "method": "QLoRA (4-bit NF4)" if quant else "LoRA", "rank": args.rank, "epochs": args.epochs,
              "learning_rate": args.lr, "batch_size": BATCH, "train_examples": len(train), "val_examples": len(val),
              "trainable_parameters": trainable, "total_parameters": total, "steps": result.global_step,
              "train_loss": round(result.training_loss, 4), "train_runtime_s": round(result.metrics.get("train_runtime", 0), 1),
              "val_loss_by_epoch": [round(h["eval_loss"], 4) for h in trainer.state.log_history if "eval_loss" in h],
              "hardware": torch.cuda.get_device_name(0) if cuda else "cpu",
              "versions": {"torch": torch.__version__, "transformers": transformers.__version__, "peft": peft.__version__}}
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "train.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record))


if __name__ == "__main__":
    main()
