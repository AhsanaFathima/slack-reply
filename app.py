import os
import requests
from flask import Flask, request, jsonify
from datetime import datetime
import re

app = Flask(__name__)

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")

# Channels where NEW ORDER notifications may exist
CHANNELS_TO_SEARCH = [
    "C0A02M2VCTB",  # order
    "C0A068PHZMY"   # shopify-slack
]

# In-memory store (replace with DB/Redis in production)
order_tracking = {}

# --------------------------------------------------
# 🔒 STRICT MATCH: ONLY "ST.order #1234"
# --------------------------------------------------
def is_new_order_message(text, order_number):
    if not text:
        return False

    text_lower = text.lower().strip()

    # ❌ Ignore reports, fulfillment lists, summaries
    blacklist = ["fulfilled", "tracking", "report", "generated", "payment"]
    if any(word in text_lower for word in blacklist):
        return False

    # ✅ Only allow exact new order format
    match = re.search(r"\bst\.order\s+#?(\d+)\b", text_lower)
    return bool(match and match.group(1) == order_number)


# --------------------------------------------------
# 🔍 FIND ORIGINAL NEW ORDER MESSAGE
# --------------------------------------------------
def find_new_order_message(order_number):
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}

    for channel_id in CHANNELS_TO_SEARCH:
        try:
            resp = requests.get(
                "https://slack.com/api/conversations.history",
                headers=headers,
                params={"channel": channel_id, "limit": 100},
                timeout=10
            )

            data = resp.json()
            if not data.get("ok"):
                continue

            # Oldest → newest (important!)
            for msg in reversed(data.get("messages", [])):
                if is_new_order_message(msg.get("text", ""), order_number):
                    return msg["ts"], channel_id

        except Exception as e:
            print(f"Slack search error: {e}")

    return None, None


# --------------------------------------------------
# 📤 POST THREAD MESSAGE
# --------------------------------------------------
def post_thread_message(channel, thread_ts, text):
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    payload = {
        "channel": channel,
        "thread_ts": thread_ts,
        "text": text
    }
    r = requests.post("https://slack.com/api/chat.postMessage",
                      headers=headers, json=payload, timeout=10)
    return r.json().get("ok", False)


# --------------------------------------------------
# 🏷️ MESSAGE BUILDERS
# --------------------------------------------------
def payment_message(status):
    return {
        "pending": "⏳ Payment Pending",
        "paid": "✅ Payment Paid",
        "authorized": "🔒 Payment Authorized",
        "voided": "❌ Payment Voided",
        "refunded": "↩️ Payment Refunded"
    }.get(status, f"💳 Payment {status}")


def fulfillment_message(status, tracking=None, courier=None):
    msg = {
        "fulfilled": "🚀 Fulfilled",
        "partially_fulfilled": "📤 Partially Fulfilled",
        "unfulfilled": "📦 Unfulfilled",
        "on_hold": "⏸️ On Hold",
        "in_progress": "⚙️ In Progress"
    }.get(status, f"📦 {status}")

    details = []
    if tracking:
        details.append(f"Tracking: {tracking}")
    if courier:
        details.append(f"Courier: {courier}")

    if details:
        msg += f" ({', '.join(details)})"

    return msg


# --------------------------------------------------
# 🛒 SHOPIFY WEBHOOK
# --------------------------------------------------
@app.route("/webhook/shopify", methods=["POST"])
def shopify_webhook():
    data = request.get_json(force=True)

    order = data.get("order", data)
    order_number = str(order.get("name", "")).replace("#", "").strip()

    if not order_number:
        return jsonify({"error": "order number missing"}), 400

    # Find or cache thread
    if order_number not in order_tracking:
        ts, channel = find_new_order_message(order_number)
        if not ts:
            return jsonify({"ok": False, "message": "New order message not found"}), 202

        order_tracking[order_number] = {
            "ts": ts,
            "channel": channel,
            "payment": None,
            "fulfillment": None
        }

    track = order_tracking[order_number]
    time_now = datetime.now().strftime("%I:%M %p")

    # ---------------- PAYMENT ----------------
    payment_status = order.get("financial_status")
    if payment_status and payment_status != track["payment"]:
        msg = f"{payment_message(payment_status)} • {time_now}"
        if post_thread_message(track["channel"], track["ts"], msg):
            track["payment"] = payment_status

    # ---------------- FULFILLMENT ----------------
    fulfillment_status = order.get("fulfillment_status")
    tracking_no = None
    courier = None

    if order.get("fulfillments"):
        f = order["fulfillments"][-1]
        tracking_no = f.get("tracking_number")
        courier = f.get("tracking_company")

    if fulfillment_status and fulfillment_status != track["fulfillment"]:
        msg = f"{fulfillment_message(fulfillment_status, tracking_no, courier)} • {time_now}"
        if post_thread_message(track["channel"], track["ts"], msg):
            track["fulfillment"] = fulfillment_status

    return jsonify({"ok": True}), 200


# --------------------------------------------------
# 🧪 HEALTH
# --------------------------------------------------
@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "tracked_orders": len(order_tracking)
    })


# --------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
