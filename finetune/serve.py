"""Serves the fine-tuned Router classifier. Point the backend at it with CLASSIFIER_URL=http://host:8090/classify

    uvicorn finetune.serve:app --port 8090        (needs finetune/requirements.txt + the adapter directory)

Returns {"label": "support|orders|escalation", "confidence": 0..1}; confidence is the softmax over the
first-token logits of the three labels, so the backend can fall back to the large model when it is low.
"""
from __future__ import annotations

import os

import torch
import transformers
from fastapi import FastAPI
from peft import PeftModel
from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoTokenizer

from finetune.build_dataset import SYSTEM

BASE = os.environ.get("BASE_MODEL", "Qwen/Qwen2.5-1.5B-Instruct")
ADAPTER = os.environ.get("ADAPTER_DIR", "finetune/out/router-lora")
LABELS = ["support", "orders", "escalation"]

if not torch.cuda.is_available():
    DTYPE = torch.float32
else:  # bfloat16 is native from Ampere (compute capability 8) on; a T4 only emulates it, so use float16 there
    DTYPE = torch.bfloat16 if torch.cuda.get_device_capability(0)[0] >= 8 else torch.float16
DTYPE_ARG = "dtype" if int(transformers.__version__.split(".")[0]) >= 5 else "torch_dtype"  # renamed in transformers 5

tok = AutoTokenizer.from_pretrained(BASE)
model = AutoModelForCausalLM.from_pretrained(BASE, device_map="auto", **{DTYPE_ARG: DTYPE})
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
