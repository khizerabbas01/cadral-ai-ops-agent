"""Generates the knowledge base for the fictional UK retailer "Harbour & Lane":
PDF policies, HTML help-centre pages, Markdown FAQs and a products CSV (24 documents total).

    python -m scripts.generate_docs        (run from backend/)
"""
from __future__ import annotations

import csv
import random
from pathlib import Path

from fpdf import FPDF

OUT = Path(__file__).resolve().parent.parent / "data" / "docs"

COMPANY = "Harbour & Lane Ltd"

# ---------------------------------------------------------------- PDF policies
PDFS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "returns-policy.pdf": ("Returns Policy", [
        ("1. Return window", "You can return most items within 30 days of delivery for a full refund. The item must be unused, in its original packaging and with all tags attached. Christmas purchases made between 1 November and 24 December can be returned until 31 January."),
        ("2. Items that cannot be returned", "For hygiene reasons we cannot accept returns of opened pillows, duvets, mattress toppers, earrings or personalised items unless they are faulty. Gift cards and e-gift cards cannot be returned."),
        ("3. How to start a return", "Log in to your account, open My Orders, choose the order and click Start a Return. You will receive a prepaid Royal Mail or Evri label by email. You can also drop items at any InPost locker using the QR code in the email."),
        ("4. Return postage costs", "Returns are free for UK customers. Customers outside the UK pay their own return postage unless the item is faulty or we sent the wrong item."),
        ("5. Large items", "Furniture and items over 20kg are collected by our courier partner. Collection is free and can be booked within 30 days of delivery. Collections are available Monday to Saturday."),
        ("6. Exchanges", "We do not offer direct exchanges. Please return the original item for a refund and place a new order. This is the fastest way to secure stock."),
    ]),
    "refund-policy.pdf": ("Refund Policy", [
        ("1. When refunds are issued", "Refunds are issued once the returned item has been received and inspected at our warehouse in Leicester. Inspection normally takes up to 3 working days after the parcel arrives."),
        ("2. Refund timing", "Refunds are made to the original payment method. Card refunds usually appear within 5 to 7 working days after we issue them. PayPal refunds appear within 24 hours. Klarna refunds update your Klarna balance within 2 working days."),
        ("3. Delivery charges", "If you return your whole order within 30 days we refund the original standard delivery charge. Next-day and nominated-day delivery upgrades are not refundable unless the order arrived late."),
        ("4. Late deliveries", "If a next-day order arrives late, we automatically refund the delivery upgrade fee. Customers can also claim it by contacting support within 14 days."),
        ("5. Partial refunds", "We may issue a partial refund if a returned item is damaged, used or missing parts. We will always email photos explaining any deduction."),
        ("6. Refund approval limits", "Customer service agents can approve refunds up to £150 without manager sign-off. Refunds above £150 require approval from a team leader and are processed within 2 working days."),
    ]),
    "warranty-policy.pdf": ("Warranty & Faulty Items", [
        ("1. Statutory rights", "Under the Consumer Rights Act 2015 you are entitled to a refund, repair or replacement if goods are faulty. Within the first 30 days you are entitled to a full refund for a faulty item."),
        ("2. Harbour & Lane guarantee", "All electrical items and small appliances carry a 2-year Harbour & Lane guarantee. Furniture carries a 5-year frame guarantee. Textiles and soft furnishings carry a 1-year guarantee against manufacturing defects."),
        ("3. Making a claim", "Contact support with your order number, a description of the fault and a photo or short video. We aim to respond to warranty claims within 2 working days."),
        ("4. What is not covered", "The guarantee does not cover accidental damage, normal wear and tear, misuse, commercial use or items repaired by someone other than our approved engineers."),
        ("5. Damaged in transit", "If an item arrives damaged, please report it within 48 hours of delivery with photos of the item and packaging. We will send a replacement or issue a refund at no cost."),
    ]),
    "terms-and-conditions.pdf": ("Terms and Conditions of Sale", [
        ("1. About us", "Harbour & Lane Ltd is a company registered in England and Wales under company number 09876543. Our registered office is 14 Canal Street, Leicester, LE1 4AB. VAT number GB 123 4567 89."),
        ("2. Orders", "Your order is accepted when we send a dispatch confirmation email. We may cancel an order if an item is out of stock or the price was displayed incorrectly, in which case we will refund you in full."),
        ("3. Prices", "All prices are in pounds sterling and include VAT at the current UK rate. Delivery charges are shown at checkout."),
        ("4. Cancelling an order", "You can cancel an order free of charge before it has been dispatched by contacting support or using My Orders. Once dispatched, the order must be returned under the Returns Policy. Under the Consumer Contracts Regulations you have 14 days from delivery to cancel."),
        ("5. Liability", "Nothing in these terms limits our liability for death or personal injury caused by our negligence, or for fraud."),
        ("6. Governing law", "These terms are governed by the law of England and Wales."),
    ]),
    "privacy-policy.pdf": ("Privacy Policy", [
        ("1. Data we collect", "We collect your name, contact details, delivery address, order history and payment confirmation. We never store full card numbers; payments are processed by Stripe and PayPal."),
        ("2. How we use it", "We use your data to fulfil orders, provide customer support, prevent fraud and, if you opt in, send marketing emails."),
        ("3. AI-assisted support", "Customer messages may be handled by an AI assistant supervised by our support team. Conversations are stored for 24 months to improve service quality. You can ask for a human at any time."),
        ("4. Your rights", "Under UK GDPR you can request a copy of your data, ask us to correct or delete it, or object to processing. Email privacy@harbourandlane.co.uk. We respond within one month."),
        ("5. Data retention", "Order records are kept for 6 years for tax purposes. Marketing preferences are kept until you unsubscribe."),
    ]),
    "loyalty-programme.pdf": ("Lane Rewards Loyalty Programme", [
        ("1. Earning points", "Members earn 1 Lane Point for every £1 spent on full-price items. Points are added 30 days after delivery, once the return window has closed."),
        ("2. Redeeming points", "Every 100 Lane Points can be redeemed for a £5 voucher at checkout. Vouchers cannot be exchanged for cash."),
        ("3. Tiers", "Members who spend over £500 in a calendar year become Gold members and receive free next-day delivery and early access to sales for the following year."),
        ("4. Expiry", "Points expire after 12 months of account inactivity."),
        ("5. Returns and points", "If you return an item, the points earned on it are removed from your balance."),
    ]),
    "product-care-guide.pdf": ("Product Care Guide", [
        ("Bedding", "Wash cotton bedding at 40 degrees with similar colours. Linen bedding can be washed at 40 degrees and line dried; it softens with every wash. Do not tumble dry the Harbour duvet above low heat."),
        ("Wooden furniture", "Dust with a soft dry cloth. Treat oak furniture with the Harbour & Lane oak care oil every 6 months. Avoid direct sunlight and placing hot items directly on the surface."),
        ("Ceramics", "Our stoneware tableware is dishwasher and microwave safe. Hand-glazed pieces may show small variations which are not faults."),
        ("Candles", "Trim the wick to 5mm before each burn and never leave a candle burning unattended. Burn for no more than 4 hours at a time."),
        ("Rugs", "Vacuum regularly without the beater bar. Spot clean with a damp cloth. Wool rugs may shed for the first few weeks; this is normal."),
    ]),
    "product-recall-notice.pdf": ("Product Safety & Recall Notice", [
        ("Affected product", "The Nordic 2-Slice Toaster (SKU HL-EL-1007) sold between 1 March 2025 and 30 June 2025 with batch codes starting T25A may overheat."),
        ("What to do", "Stop using the toaster immediately and unplug it. Do not return it to a store. Contact customer support with your order number for a free collection and a full refund, including delivery costs."),
        ("Other batches", "Toasters with batch codes starting T25B or later are not affected and are safe to use."),
    ]),
}

# ---------------------------------------------------------------- HTML help-centre pages
HTML: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "delivery-information.html": ("Delivery Information", [
        ("UK delivery options", "Standard delivery costs £3.99 and arrives in 3 to 5 working days. Standard delivery is free on orders over £40. Next-day delivery costs £6.99 when ordered before 8pm Sunday to Thursday. Nominated-day delivery costs £5.99."),
        ("Scottish Highlands and islands", "Deliveries to the Scottish Highlands, Northern Ireland, the Isle of Man and the Channel Islands take 4 to 7 working days. Next-day delivery is not available to these areas."),
        ("Furniture delivery", "Furniture is delivered by a two-person team to the room of your choice within 7 to 14 working days. The delivery charge is £29 per order. We will text you a 2-hour time slot the day before."),
        ("Tracking", "You will receive a tracking link by email and SMS when your order is dispatched. Tracking can take up to 24 hours to update."),
    ]),
    "international-shipping.html": ("International Shipping", [
        ("Where we ship", "We ship to all EU countries, Norway, Switzerland, the USA and Canada. Furniture can only be delivered within mainland UK."),
        ("Costs and times", "EU delivery costs £12.99 and takes 5 to 10 working days. USA and Canada delivery costs £19.99 and takes 7 to 14 working days."),
        ("Duties and taxes", "EU orders under €150 are shipped with duties prepaid (DDP), so there is nothing extra to pay on delivery. Orders to the USA and Canada may be subject to local import taxes which are paid by the customer."),
    ]),
    "payment-methods.html": ("Payment Methods", [
        ("Accepted payments", "We accept Visa, Mastercard, American Express, PayPal, Apple Pay, Google Pay and Klarna."),
        ("Klarna", "Klarna lets you pay in 3 interest-free instalments on orders between £30 and £1,000. Klarna is only available to UK customers."),
        ("When you are charged", "Your card is authorised when you place the order and charged when the order is dispatched."),
        ("Failed payments", "If a payment fails, the order is held for 24 hours so you can update your payment details in My Orders."),
    ]),
    "contact-us.html": ("Contact Us", [
        ("Customer service hours", "Our human support team is available Monday to Friday 8am to 8pm and Saturday 9am to 5pm UK time. Our AI assistant is available 24/7."),
        ("Ways to contact us", "Email help@harbourandlane.co.uk, chat with us on the website, or message us on Telegram at @HarbourLaneBot. Phone support is available on 0116 496 0123."),
        ("Response times", "We reply to emails within 24 hours. Complaints are acknowledged within 1 working day and resolved within 8 weeks, in line with our complaints procedure."),
    ]),
    "gift-cards.html": ("Gift Cards", [
        ("Buying gift cards", "Gift cards are available from £10 to £250. E-gift cards are delivered by email within 2 hours."),
        ("Validity", "Gift cards are valid for 24 months from the date of purchase. They cannot be exchanged for cash or used to buy other gift cards."),
        ("Lost gift cards", "We can replace a lost e-gift card if you provide the original order number. Physical gift cards cannot be replaced if lost."),
    ]),
    "price-match.html": ("Price Match Promise", [
        ("How it works", "If you find the identical item cheaper at a UK retailer within 14 days of your purchase, we will refund the difference."),
        ("Exclusions", "Price match does not apply to marketplace sellers, clearance items, outlet stores, Black Friday prices or bundle offers."),
        ("How to claim", "Send us a link to the cheaper item and your order number. We will check and refund the difference to your original payment method."),
    ]),
    "order-changes.html": ("Changing or Cancelling an Order", [
        ("Changing your address", "You can change the delivery address while the order status is processing. Once an order has been dispatched the address cannot be changed, but you may be able to redirect it using the carrier tracking link."),
        ("Adding items", "Items cannot be added to an existing order. Please place a new order; standard delivery is free over £40."),
        ("Cancelling", "Orders can be cancelled free of charge before dispatch. Cancelled orders are refunded in full within 3 working days."),
    ]),
    "missing-parcel.html": ("Missing or Late Parcels", [
        ("Parcel marked delivered but not received", "Check with neighbours and any safe place you specified. If it has not turned up after 48 hours, contact us and we will open an investigation with the carrier."),
        ("Investigation", "Carrier investigations take up to 5 working days. If the parcel cannot be found we will send a replacement or a full refund, whichever you prefer."),
        ("Late parcels", "If your parcel has not arrived 3 working days after the estimated delivery date, contact us and we will chase it with the carrier."),
    ]),
    "promotions-and-discount-codes.html": ("Promotions and Discount Codes", [
        ("Using a code", "Enter your discount code in the basket before checkout. Only one discount code can be used per order."),
        ("Welcome offer", "New newsletter subscribers receive 15% off their first order, up to a maximum discount of £50. The code is valid for 30 days."),
        ("Codes not working", "Codes cannot be applied to sale items, gift cards or furniture assembly. Codes cannot be added after an order has been placed."),
    ]),
    "furniture-assembly.html": ("Furniture Assembly Service", [
        ("Booking", "Assembly is booked at checkout for £35 per item and takes place on the delivery day by the same two-person team."),
        ("Coverage", "Assembly is available in mainland England and Wales only. It is not available for orders delivered to Scotland or Northern Ireland."),
        ("Self assembly", "Every flat-pack item includes illustrated instructions and all fittings. Video guides are available on each product page."),
    ]),
}

# ---------------------------------------------------------------- Markdown FAQs
MD: dict[str, str] = {
    "faq-account.md": """# Account FAQ

## How do I reset my password?
Click "Forgot password" on the login page and we will email you a reset link valid for 1 hour.

## Can I check out as a guest?
Yes. Guest orders can be tracked using the order number and email address. Guest customers do not earn Lane Points.

## How do I delete my account?
Email privacy@harbourandlane.co.uk from the address on your account. Accounts are deleted within 30 days; order records are retained for 6 years for tax purposes.

## How do I unsubscribe from emails?
Use the unsubscribe link at the bottom of any marketing email or update your preferences in My Account.
""",
    "faq-sustainability.md": """# Sustainability FAQ

## Is your packaging recyclable?
Yes. Since 2024 all our packaging is plastic-free and 100% recyclable. Furniture is wrapped in recycled cardboard and paper honeycomb.

## Do you offer a take-back scheme?
Our Second Life scheme collects old bedding and towels when you buy new ones. Items are donated or recycled with our partner TextileCycle. Book a collection when you order.

## Where are your products made?
Our oak furniture is made in Portugal and Vietnam from FSC-certified wood. Our cotton bedding uses Oeko-Tex certified organic cotton from India.
""",
    "faq-stores-and-services.md": """# Services FAQ

## Do you have physical stores?
No. Harbour & Lane is online only, which helps us keep prices low. Our showroom in Leicester is open by appointment on Saturdays.

## Do you offer assembly?
Yes. Furniture assembly costs £35 per item and must be booked at checkout. Assembly is available in mainland England and Wales only.

## Do you offer student or NHS discounts?
NHS staff and students get 10% off full-price items when verified through Student Beans or Health Service Discounts. The discount cannot be combined with other offers.
""",
    "complaints-procedure.md": """# Complaints Procedure

## How to make a complaint
Email complaints@harbourandlane.co.uk or ask any support agent to log a complaint for you. Please include your order number.

## Our commitments
We acknowledge complaints within 1 working day. A team leader will investigate and give you a final response within 8 weeks.

## If you are not satisfied
If you are unhappy with our final response you can refer your complaint to the Retail Ombudsman free of charge.

## Goodwill gestures
Team leaders may offer a goodwill voucher of up to £25 where our service fell short.
""",
    "faq-orders.md": """# Orders FAQ

## How do I track my order?
Use the tracking link in your dispatch email, or ask our assistant with your order number (for example UK-10012).

## Why has my order been split into multiple parcels?
Items ship from different warehouses. Each parcel has its own tracking number and you only pay delivery once.

## My order says processing - when will it ship?
In-stock items are dispatched within 1 working day. Furniture is dispatched within 5 working days.

## Can I collect my order?
We do not offer click and collect. Orders can be delivered to an InPost locker at checkout.
""",
}


def _products() -> list[dict]:
    random.seed(7)
    cats = {
        "Bedding": [("Linen Duvet Cover Double", 89), ("Organic Cotton Sheet Set King", 64), ("Harbour Duvet 10.5 tog", 75), ("Waffle Throw Sage", 39), ("Feather Pillow Pair", 45)],
        "Furniture": [("Oak Dining Table 6 Seater", 799), ("Oak Coffee Table", 329), ("Velvet Armchair Teal", 449), ("Rattan Bedside Table", 129), ("Oak Bookcase Tall", 389), ("Linen 3 Seater Sofa", 1199)],
        "Kitchen": [("Stoneware Dinner Set 12pc", 85), ("Cast Iron Casserole 24cm", 69), ("Glass Storage Jar Set", 24), ("Marble Chopping Board", 35), ("Copper Kettle Stovetop", 55)],
        "Electricals": [("Nordic 2-Slice Toaster", 49), ("Nordic Kettle 1.7L", 45), ("Smart Table Lamp", 79), ("Ceramic Heater Fan", 59), ("Coffee Grinder Burr", 89), ("Bluetooth Speaker Oak", 119)],
        "Decor": [("Hand-poured Candle Fig", 22), ("Wool Rug Ivory 160x230", 249), ("Round Mirror Brass 80cm", 135), ("Ceramic Vase Large", 42), ("Linen Cushion Cover Rust", 25), ("Framed Print Coastline", 59)],
        "Bath": [("Turkish Cotton Towel Bale", 59), ("Bath Mat Waffle", 19), ("Bamboo Bath Caddy", 29), ("Robe Waffle Unisex", 49)],
    }
    rows, n = [], 1001
    for cat, items in cats.items():
        prefix = {"Bedding": "BD", "Furniture": "FN", "Kitchen": "KT", "Electricals": "EL", "Decor": "DC", "Bath": "BT"}[cat]
        for name, price in items:
            sku = f"HL-{prefix}-{n}" if name != "Nordic 2-Slice Toaster" else "HL-EL-1007"
            stock = random.choice([0, 3, 12, 25, 40, 75])
            warranty = "2 years" if cat == "Electricals" else ("5 years frame" if cat == "Furniture" else "1 year")
            rows.append({"sku": sku, "name": name, "category": cat, "price_gbp": f"{price:.2f}", "stock": stock,
                         "warranty": warranty,
                         "description": f"{name} from the Harbour & Lane {cat.lower()} collection."})
            n += 1
    return rows


def _write_pdf(path: Path, title: str, sections: list[tuple[str, str]]) -> None:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, f"{COMPANY} - {title}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, "Version 2025.2 - Applies to orders placed in the United Kingdom", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    for heading, body in sections:
        pdf.set_font("Helvetica", "B", 12)
        pdf.multi_cell(0, 7, heading, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(0, 6, body, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
    pdf.output(str(path))


def _write_html(path: Path, title: str, sections: list[tuple[str, str]]) -> None:
    body = "\n".join(f"<section><h2>{h}</h2><p>{p}</p></section>" for h, p in sections)
    path.write_text(f"""<!doctype html><html lang="en-GB"><head><meta charset="utf-8"><title>{title} | Harbour &amp; Lane Help Centre</title></head>
<body><nav>Home &gt; Help Centre &gt; {title}</nav><main><h1>{title}</h1>
{body}
</main><footer>&copy; Harbour &amp; Lane Ltd. Need more help? Contact us.</footer></body></html>""", encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (title, sections) in PDFS.items():
        _write_pdf(OUT / name, title, sections)
    for name, (title, sections) in HTML.items():
        _write_html(OUT / name, title, sections)
    for name, text in MD.items():
        (OUT / name).write_text(text, encoding="utf-8")
    rows = _products()
    with open(OUT / "products.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    total = len(PDFS) + len(HTML) + len(MD) + 1
    print(f"wrote {total} documents to {OUT}")


if __name__ == "__main__":
    main()
