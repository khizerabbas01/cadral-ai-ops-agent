"""Builds the intent-classification dataset used to fine-tune the Router model.

Labels = the three specialist agents: support | orders | escalation.
Examples are generated from templates x slot values with noise (typos, casing, greetings), then split
train / val / test with NO template shared between train and test (so the test set measures generalisation,
not memorisation). Human corrections exported from the dashboard (`/api/learning/dataset.jsonl`) can be
appended with --extra.

    python finetune/build_dataset.py            # writes finetune/data/{train,val,test}.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

OUT = Path(__file__).resolve().parent / "data"
SYSTEM = ("Classify the customer message for a UK online homeware retailer into exactly one label: "
          "support (general policy/product question), orders (lookup or action on the customer's own order), "
          "escalation (angry, abusive, legal/chargeback threat, safety incident, fraud, or asks for a human). "
          "Reply with the label only.")

PRODUCTS = ["duvet cover", "oak coffee table", "toaster", "kettle", "wool rug", "dining table", "armchair", "candle", "towel bale",
            "bookcase", "table lamp", "sofa", "casserole dish", "mirror", "heater"]
PLACES = ["Scotland", "Northern Ireland", "France", "Germany", "the USA", "Ireland", "the Channel Islands", "Canada"]
PAY = ["Klarna", "PayPal", "Apple Pay", "American Express", "a gift card", "Google Pay"]
ORDER = lambda r: f"UK-{r.randint(10001, 10999)}"

SUPPORT = [
    "How much is delivery to {place}?", "Do you deliver to {place}?", "What is your returns policy?", "How long do I have to return a {product}?",
    "Can I pay with {pay}?", "Is the {product} in stock?", "What's the warranty on a {product}?", "How do I wash the {product}?",
    "Do you offer next-day delivery?", "How long does standard delivery take?", "What are your opening hours?", "Do you price match?",
    "How do loyalty points work?", "Do you have a student discount?", "Is your packaging recyclable?", "Can I return something bought in the sale?",
    "How do I reset my password?", "Do you offer furniture assembly?", "How long do refunds normally take?", "What's the price of the {product}?",
    "Can I use two discount codes?", "Do gift cards expire?", "Do you ship furniture to {place}?", "Where is the {product} made?",
    "Is there a recall on the {product}?", "how does {pay} work with you", "what happens if my parcel is late in general?", "Do you have a showroom?",
    "Can I collect in store?", "How do I make a warranty claim?", "What's your policy on damaged items?", "Are returns free?",
    "how much is nominated day delivery", "Can I cancel an order after it ships, in general?", "do you sell {product}s?", "What sizes does the {product} come in?",
]
ORDERS = [
    "Where is my order {order}?", "Can you track {order} for me", "My {product} hasn't arrived yet, order {order}", "I'd like a refund for {order}, the {product} is faulty",
    "Please cancel order {order}", "Change the delivery address on {order} to 4 Oak Road, Leeds LS6 2AB", "My {product} arrived damaged, can I get my money back?",
    "I received the wrong item in {order}", "There's an item missing from my parcel", "When will my {product} be delivered?", "has {order} shipped yet",
    "I ordered a {product} last week and heard nothing", "Can I get a refund on my {product}? it stopped working", "need to cancel my order asap",
    "I never received my refund for {order}", "Tracking for {order} hasn't updated in 3 days", "The {product} I received is chipped, order {order}",
    "Can you resend my order confirmation for {order}?", "I was charged twice for {order}", "My order says delivered but I don't have it",
    "pls update address for my order, moving house", "I want to return the {product} from {order}", "What's the status of my last order?",
    "can you check when my parcel will arrive", "refund me £20 for the late delivery on {order}", "Part of my order {order} is missing",
    "my {product} came without the fittings", "Is it too late to change my order {order}?", "I'd like to cancel the {product} I bought yesterday",
    "order {order} – the courier left it in the rain and the {product} is ruined, refund please",
]
ESCALATION = [
    "This is unacceptable, I want to speak to a manager", "I'm going to take legal action if this isn't resolved", "You are a bunch of useless idiots",
    "I'll be contacting my solicitor about order {order}", "I'm disputing this with my bank, expect a chargeback", "Your {product} caught fire!",
    "The {product} gave me an electric shock", "I want a human, not a bot", "This is the third time I've contacted you and nobody helps",
    "I'm reporting you to Trading Standards", "Worst company ever. I'm posting this everywhere on social media", "Someone used my card on your site, this is fraud",
    "ABSOLUTELY DISGUSTING SERVICE!!! {order}", "I want to make a formal complaint", "My child was injured by your {product}", "you scammers stole my money",
    "Get me a real person right now", "I am furious. {order} is a joke and so is your company", "I'll go to the press about this", "If I don't get my money today I'm going to court",
    "f*** this, sort it out NOW", "This is a scam and I am calling the police", "let me talk to a supervisor please", "I've been lied to three times by your staff. Escalate this.",
    "The {product} started smoking and burnt my worktop", "There's a charge on my account I did not authorise",
]
# Hard negatives: polite/neutral messages that contain 'scary' words, and upset-but-routine order issues
HARD = [
    ("support", "What is your complaints procedure?"), ("support", "Is the heater safe to leave on overnight? Any fire risk?"),
    ("support", "Do you have a human support team and what are their hours?"), ("orders", "A bit disappointed {order} is late - when will it arrive?"),
    ("orders", "Not happy, the {product} in {order} is scratched. Can I have a refund?"), ("support", "What happens if I do a chargeback - sorry, I mean a price match?"),
    ("orders", "Could a person check why {order} hasn't moved? No rush."), ("support", "Does the warranty cover electrical faults?"),
    ("escalation", "I asked politely twice. Now I want this escalated to someone senior."), ("orders", "Third parcel this month, love it! Where's {order}?"),
]
GREET = ["", "", "", "Hi, ", "Hello, ", "Hey - ", "Good morning. ", "hi there ", "Hiya, "]
TAIL = ["", "", "", " Thanks", " thanks!", " Please help.", " cheers", " ASAP"]


def noise(rng: random.Random, s: str) -> str:
    r = rng.random()
    if r < 0.15:
        s = s.lower()
    elif r < 0.2:
        s = s.upper()
    if rng.random() < 0.12 and len(s) > 12:  # typo: swap two adjacent characters
        i = rng.randrange(3, len(s) - 2)
        s = s[:i] + s[i + 1] + s[i] + s[i + 2:]
    if rng.random() < 0.15:
        s = s.rstrip("?.!")
    return rng.choice(GREET) + s + rng.choice(TAIL)


def fill(rng: random.Random, t: str) -> str:
    return t.format(product=rng.choice(PRODUCTS), place=rng.choice(PLACES), pay=rng.choice(PAY), order=ORDER(rng))


def record(text: str, label: str) -> dict:
    return {"messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text},
                         {"role": "assistant", "content": label}], "text": text, "label": label}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-template", type=int, default=6)
    ap.add_argument("--extra", help="JSONL exported from the dashboard feedback loop")
    args = ap.parse_args()
    rng = random.Random(13)
    splits = {"train": [], "val": [], "test": []}
    for label, templates in (("support", SUPPORT), ("orders", ORDERS), ("escalation", ESCALATION)):
        ts = templates[:]
        rng.shuffle(ts)
        n_test, n_val = max(4, len(ts) // 5), max(2, len(ts) // 10)
        parts = {"test": ts[:n_test], "val": ts[n_test:n_test + n_val], "train": ts[n_test + n_val:]}
        for split, tpls in parts.items():
            for t in tpls:
                seen = set()
                for _ in range(args.per_template if split == "train" else 3):
                    text = noise(rng, fill(rng, t))
                    if text not in seen:
                        seen.add(text)
                        splits[split].append(record(text, label))
    hard = HARD[:]
    rng.shuffle(hard)
    for i, (label, t) in enumerate(hard):
        split = "test" if i < 4 else "train"
        for _ in range(1 if split == "test" else 4):
            splits[split].append(record(noise(rng, fill(rng, t)) if split == "train" else fill(rng, t), label))
    if args.extra and Path(args.extra).exists():
        for line in Path(args.extra).read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            agent = r["messages"][0]["content"].split(" agent")[0].split()[-1]
            if agent in {"support", "orders", "escalation"}:
                splits["train"].append(record(r["messages"][1]["content"], agent))
    OUT.mkdir(parents=True, exist_ok=True)
    for split, rows in splits.items():
        rng.shuffle(rows)
        (OUT / f"{split}.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        counts = {l: sum(r["label"] == l for r in rows) for l in ("support", "orders", "escalation")}
        print(f"{split:<5} {len(rows):>4} examples  {counts}")


if __name__ == "__main__":
    main()
