"""Seed demo data: staff users, customers, products (from the catalogue CSV) and orders."""
from __future__ import annotations

import csv
import random
import secrets
from datetime import timedelta

from sqlalchemy import select

from .auth import hash_password
from .config import settings
from .db import Base, engine, session_scope
from .models import Customer, Order, Product, PromptRule, User, utcnow

CUSTOMERS = [
    ("Emma Clarke", "emma.clarke@example.co.uk", "standard"),
    ("James Patel", "james.patel@example.co.uk", "standard"),
    ("Sophie Turner", "sophie.turner@example.co.uk", "standard"),
    ("Oliver Hughes", "oliver.hughes@example.co.uk", "standard"),
    ("Amelia Wright", "amelia.wright@example.co.uk", "vip"),
    ("Harry Evans", "harry.evans@example.co.uk", "standard"),
    ("Isla Roberts", "isla.roberts@example.co.uk", "vip"),
    ("George Walker", "george.walker@example.co.uk", "standard"),
    ("Mia Thompson", "mia.thompson@example.co.uk", "standard"),
    ("Noah Wilson", "noah.wilson@example.co.uk", "standard"),
    ("Grace Davies", "grace.davies@example.co.uk", "standard"),
    ("Leo Robinson", "leo.robinson@example.co.uk", "standard"),
]

ADDRESSES = ["12 Mill Lane, Leeds LS1 4DY", "7 Queen Street, Bristol BS1 4JQ", "44 Park Road, Manchester M14 5RL",
             "3 Station Road, Cambridge CB1 2JW", "18 High Street, Edinburgh EH1 1SR", "91 Church Lane, Cardiff CF10 1BH",
             "5 Victoria Avenue, Brighton BN1 3FS", "27 King's Road, London SW3 4RP", "60 London Road, Leicester LE2 0QB",
             "2 Harbour View, Plymouth PL1 2LS", "10 Castle Street, Inverness IV2 3EA", "33 Elm Grove, Nottingham NG7 2AB"]

# Hand-crafted orders used by the demo script and the eval set: (id, customer_idx, status, [(sku, qty)], carrier, days_ago)
SCRIPTED = [
    ("UK-10001", 0, "delivered", [("HL-BD-1001", 1), ("HL-DC-1023", 1)], "Royal Mail", 9),
    ("UK-10002", 1, "in_transit", [("HL-FN-1007", 1)], "DPD", 3),
    ("UK-10003", 2, "processing", [("HL-KT-1012", 1), ("HL-KT-1014", 2)], None, 0),
    ("UK-10004", 3, "delivered", [("HL-EL-1007", 1)], "Evri", 40),
    ("UK-10005", 4, "dispatched", [("HL-DC-1025", 1), ("HL-BT-1030", 2)], "DPD", 1),
    ("UK-10006", 5, "delivered", [("HL-FN-1006", 1)], "Two-person team", 12),
    ("UK-10007", 6, "delivered", [("HL-EL-1018", 1)], "Royal Mail", 5),
    ("UK-10008", 7, "in_transit", [("HL-BD-1003", 1), ("HL-BD-1005", 1)], "Evri", 8),
    ("UK-10009", 8, "cancelled", [("HL-DC-1027", 1)], None, 20),
    ("UK-10010", 9, "processing", [("HL-FN-1011", 1)], None, 1),
    ("UK-10011", 10, "delivered", [("HL-KT-1013", 1)], "Royal Mail", 15),
    ("UK-10012", 11, "dispatched", [("HL-EL-1020", 1), ("HL-DC-1026", 1)], "DPD", 2),
]

TRANSIT_DAYS = {"processing": None, "dispatched": 2, "in_transit": 1, "delivered": None, "cancelled": None}


def seed_products(s) -> dict[str, Product]:
    if s.scalar(select(Product).limit(1)) is None:
        with open(settings.docs_dir / "products.csv", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                s.add(Product(sku=row["sku"], name=row["name"], category=row["category"],
                              price_gbp=float(row["price_gbp"]), stock=int(row["stock"])))
        s.flush()
    return {p.sku: p for p in s.scalars(select(Product))}


def _make_order(oid, cust, status, lines, carrier, days_ago, products, rng) -> Order:
    items = []
    for sku, qty in lines:
        p = products.get(sku) or rng.choice(list(products.values()))
        items.append({"sku": p.sku, "name": p.name, "qty": qty, "price_gbp": p.price_gbp})
    subtotal = sum(i["qty"] * i["price_gbp"] for i in items)
    delivery = 0.0 if subtotal >= 40 else 3.99
    created = utcnow() - timedelta(days=days_ago + 2)
    eta = None
    if status in {"dispatched", "in_transit"}:
        eta = utcnow() + timedelta(days=TRANSIT_DAYS[status] or 2)
    elif status == "delivered":
        eta = utcnow() - timedelta(days=days_ago)
    return Order(id=oid, customer_id=cust.id, status=status, items=items, total_gbp=round(subtotal + delivery, 2),
                 shipping_address=ADDRESSES[(cust.id - 1) % len(ADDRESSES)], carrier=carrier,
                 tracking_number=(f"{(carrier or 'XX')[:2].upper()}{rng.randint(10**9, 10**10 - 1)}GB" if carrier else None),
                 estimated_delivery=eta, created_at=created, updated_at=created)


def seed() -> None:
    Base.metadata.create_all(engine)
    if not (settings.docs_dir / "products.csv").exists():
        from scripts.generate_docs import main as gen
        gen()
    with session_scope() as s:
        if s.scalar(select(User).limit(1)) is None:
            for email, name, role, pw in ((settings.admin_email, "Admin", "admin", settings.admin_password),
                                          (settings.agent_email, "Sam Support", "agent", settings.agent_password)):
                if not pw:  # no password defaults in code: generate one and show it once in the server log
                    pw = secrets.token_urlsafe(9)
                    print(f"[seed] generated password for {role} {email}: {pw}  (set {role.upper()}_PASSWORD to choose your own)")
                s.add(User(email=email, name=name, role=role, password_hash=hash_password(pw)))
        products = seed_products(s)
        if s.scalar(select(Customer).limit(1)) is not None:
            return
        customers = []
        for name, email, tier in CUSTOMERS:
            c = Customer(name=name, email=email, tier=tier)
            s.add(c)
            customers.append(c)
        s.flush()
        rng = random.Random(42)
        for oid, ci, status, lines, carrier, days in SCRIPTED:
            s.add(_make_order(oid, customers[ci], status, lines, carrier, days, products, rng))
        statuses = ["delivered"] * 5 + ["in_transit", "dispatched", "processing"]
        for n in range(13, 41):
            cust = rng.choice(customers)
            status = rng.choice(statuses)
            lines = [(rng.choice(list(products)), rng.choice([1, 1, 2])) for _ in range(rng.choice([1, 1, 2]))]
            carrier = None if status == "processing" else rng.choice(["Royal Mail", "DPD", "Evri"])
            s.add(_make_order(f"UK-100{n:02d}", cust, status, lines, carrier, rng.randint(0, 30), products, rng))
        s.add(PromptRule(agent="all", rule="Write in British English (e.g. 'colour', 'organise') and use £ for prices."))
