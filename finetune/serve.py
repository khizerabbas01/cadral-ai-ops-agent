"""Serves the fine-tuned Router classifier. Point the backend at it with CLASSIFIER_URL=http://host:8090/classify

    uvicorn finetune.serve:app --port 8090        (needs finetune/requirements.txt + the adapter directory)

Returns {"label": "support|orders|escalation", "confidence": 0..1}; confidence is the softmax over the
first-token logits of the three labels, so the backend can fall back to the large model when it is low.
"""
from __future__ import annotations

import os

import torch
from fastapi import FastAPI
from peft import PeftModel
from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoTokenizer

from finetune.build_dataset import SYSTEM

BASE = os.environ.get("BASE_MODEL", "Qwen/Qwen2.5-1.5B-Instruct")
ADAPTER = os.environ.get("ADAPTER_DIR", "finetune/out/router-lora")
LABELS = ["support", "orders", "escalation"]

tok = AutoTokenizer.from_pretrained(BASE)
model = AutoModelForCausalLM.from_pretrained(BASE, torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32, device_map="auto")
model = PeftModel.from_pretrained(model, ADAPTER).merge_and_unload().eval()
FIRST = [tok(l, add_special_tokens=False)["input_ids"][0] for l in LABELS]

app = FastAPI(title="Router classifier")


class In(BaseModel):
    text: str


@app.post("/classify")
def classify(body: In) -> dict:
    prompt = tok.apply_chat_template([{"role": "system", "content": SYSTEM}, {"role": "user", "content": body.text[:2000]}],
                                     tokenize=False, add_generation_prompt=True)
    ids = tok(prompt, return_tensors="pt").to(model.device)
    with torch.no_grad():
        logits = model(**ids).logits[0, -1, FIRST].float()
    probs = torch.softmax(logits, dim=-1)
    i = int(probs.argmax())
    return {"label": LABELS[i], "confidence": round(float(probs[i]), 4)}
