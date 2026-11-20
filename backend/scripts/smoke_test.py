"""End-to-end API smoke test against a running server.

    python -m scripts.smoke_test [http://localhost:8000]     (reads ADMIN_/AGENT_ credentials from the environment/.env)

Run it against a fresh database: it issues a real refund on order UK-10011 and expects that to succeed.
"""
from __future__ import annotations

import sys
import time

import httpx

from app.config import settings

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=60)
    ok = lambda name, cond, extra="": print(f"  [{'ok' if cond else 'FAIL'}] {name} {extra}") or bool(cond)
    results = []
    h = c.get("/api/health").json()
    results.append(ok("health", h["ok"], f"llm={h['llm']['provider']} enabled={h['llm']['enabled']} rag={h['rag']}"))

    tok = c.post("/api/auth/login", json={"email": settings.admin_email, "password": settings.admin_password}).json()["token"]
    agent = c.post("/api/auth/login", json={"email": settings.agent_email, "password": settings.agent_password}).json()["token"]
    A, G = {"Authorization": f"Bearer {tok}"}, {"Authorization": f"Bearer {agent}"}
    results.append(ok("login rejects bad password", c.post("/api/auth/login", json={"email": settings.admin_email, "password": "nope"}).status_code == 401))
    results.append(ok("role guard (agent blocked from admin API)", c.get("/api/jobs", headers=G).status_code == 403))
    results.append(ok("dashboard API requires auth", c.get("/api/conversations").status_code == 401))

    def wait_reply(conv_id: int, n_ai: int = 1) -> dict:
        for _ in range(120):
            d = c.get(f"/api/conversations/{conv_id}", headers=A).json()
            if sum(m["role"] == "assistant" for m in d["messages"]) >= n_ai:
                return d
            time.sleep(0.5)
        raise TimeoutError("no AI reply")

    # channel 1: web chat
    r = c.post("/api/chat/send", json={"text": "How much is next-day delivery?", "email": "emma.clarke@example.co.uk"}).json()
    d = wait_reply(r["conversation_id"])
    ai = d["messages"][-1]
    results.append(ok("web chat -> support agent with citations", ai["agent"] == "support" and ai["citations"], ai["content"][:90]))
    results.append(ok("customer token required for chat history", c.get(f"/api/chat/{r['conversation_id']}/messages?token=bad").status_code == 403))

    # channel 2: email -> orders agent -> real refund in DB
    conv = c.post("/api/demo/simulate-email", headers=G, json={"from_email": "grace.davies@example.co.uk", "name": "Grace Davies", "subject": "Chipped casserole",
                                                                  "text": "My order UK-10011 arrived chipped. Please refund me £69."}).json()["conversation_id"]
    d = wait_reply(conv)
    tools = [s["title"].split("(")[0] for s in d["traces"][-1]["steps"] if s["type"] == "tool_call"]
    refunds = c.get("/api/refunds", headers=A).json()
    results.append(ok("email -> orders agent called issue_refund", "issue_refund" in tools, str(tools)))
    results.append(ok("refund row written to DB", any(x["order_id"] == "UK-10011" and x["status"] == "issued" for x in refunds)))

    # escalation
    r = c.post("/api/chat/send", json={"text": "This is the third time I've asked. I want a manager or I'm doing a chargeback!", "email": "mia.thompson@example.co.uk"}).json()
    d = wait_reply(r["conversation_id"])
    results.append(ok("escalation created with summary", d["conversation"]["status"] == "escalated" and d["escalations"] and d["escalations"][0]["summary"]))

    # self-improving loop: edit a reply -> feedback + example + (rule)
    mid = ai["id"]
    fb = c.post(f"/api/messages/{mid}/review", headers=G, json={"action": "edit", "text": "Next-day delivery is £6.99 if you order before 8pm Sunday to Thursday. Anything else I can help with?",
                                                                 "reason": "Always mention the 8pm cut-off for next-day delivery."})
    time.sleep(2.5)
    L = c.get("/api/learning", headers=A).json()
    results.append(ok("edit saved as feedback", fb.status_code == 200 and any(f["message_id"] == mid for f in L["feedback"])))
    results.append(ok("feedback distilled into a prompt guideline", any(r_["source_feedback_id"] for r_ in L["rules"]), str([r_["rule"] for r_ in L["rules"]][:2])))
    results.append(ok("re-training dataset export", L["dataset_size"] >= 1 and "messages" in c.get("/api/learning/dataset.jsonl", headers=A).text))

    a = c.get("/api/analytics?days=7", headers=A).json()["summary"]
    results.append(ok("analytics", a["messages_in"] >= 3 and a["auto_resolution_rate"] is not None, f"auto={a['auto_resolution_rate']} cost/conv={a['cost_usd']['per_conversation']}"))
    results.append(ok("daily report renders", "daily report" in c.get("/api/admin/report/preview", headers=A).json()["html"]))
    j = c.get("/api/jobs", headers=A).json()
    results.append(ok("job queue has no dead jobs", not [x for x in j if x["status"] == "dead"], f"{len(j)} jobs"))

    # dashboard read models
    d = c.get("/api/dashboard?days=7", headers=G).json()
    results.append(ok("dashboard KPIs and approval queue", d["kpis"]["conversations"]["value"] >= 3 and len(d["volume"]) == 7
                      and d["approvals"]["total"] >= 1, f"approvals={d['approvals']['total']} health={d['health']['label']}"))
    traces = c.get("/api/traces?limit=20", headers=G).json()
    results.append(ok("trace explorer shows the tool call", any("issue_refund" in t["tools"] for t in traces)))
    results.append(ok("agent cannot change settings", c.patch("/api/settings", headers=G, json={"reply_mode": "review"}).status_code == 403))
    changed = c.patch("/api/settings", headers=A, json={"refund_auto_limit_gbp": 150}).json()
    results.append(ok("admin can change settings at runtime", changed.get("applied") == {"refund_auto_limit_gbp": 150.0}))
    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
