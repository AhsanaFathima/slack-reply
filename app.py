import os
import requests
from flask import Flask, request, jsonify
from datetime import datetime
import re

app = Flask(__name__)

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")

CHANNELS_TO_SEARCH = [
    "C0A02M2VCTB",
    "C0A068PHZMY"
]

order_tracking = {}

# --------------------------------------------------
def is_new_order_message(text, order_number):
    if not text:
        return False

    text_lower = text.lower().strip()
    blacklist = ["fulfilled", "tracking", "report", "generated", "payment"]
    if any(word in text_lower for word in blacklist):
        return False

    match = re.search(r"\bst\.order\s+#?(\d+)\b", text_lower)
    return bool(match and match.group(1) == order_number)


# --------------------------------------------------
def find_new_order_message(order_number):
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}

    for channel_id in CHANNELS_TO_SEARCH:
        try:
            print(f"🔍 Searching order {order_number} in channel {channel_id}")

            resp = requests.get(
                "https://slack.com/api/conversations.history",
                headers=headers,
                params={"channel": channel_id, "limit": 100},
                timeout=10
            )

            data = resp.json()
            if not data.get("ok"):
                print("❌ Slack API error:", data)
                continue

            for msg in reversed(data.get("messages", [])):
                if is_new_order_message(msg.get("text", ""), order_number):
                    print(f"✅ Found new order message in {channel_id} at ts={msg['ts']}")
                    return msg["ts"], channel_id

        except Exception as e:
            print(f"🔥 Slack search exception: {e}")

    print(f"⚠️ Order {order_number} not found in Slack")
    return None, None


# --------------------------------------------------
def post_thread_message(channel, thread_ts, text):
    print(f"📤 Posting to Slack thread {thread_ts}: {text}")

    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    payload = {
        "channel": channel,
        "thread_ts": thread_ts,
        "text": text
    }

    r = requests.post("https://slack.com/api/chat.postMessage",
                      headers=headers, json=payload, timeout=10)

    print("📨 Slack response:", r.json())
    return r.json().get("ok", False)


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
@app.route("/webhook/shopify", methods=["POST"])
def shopify_webhook():
    print("🔔 Webhook received")

    data = request.get_json(force=True)
    print("📦 Payload:", data)

    order = data.get("order", data)
    order_number = str(order.get("name", "")).replace("#", "").strip()
    print("🆔 Order Number:", order_number)

    if not order_number:
        print("❌ Order number missing")
        return jsonify({"error": "order number missing"}), 400

    event_id = request.headers.get("X-Shopify-Webhook-Id")
    print("🔑 Webhook Event ID:", event_id)

    if order_number not in order_tracking:
        print("🧠 Order not cached. Searching Slack...")

        ts, channel = find_new_order_message(order_number)
        if not ts:
            print("❌ Slack message not found for order")
            return jsonify({"ok": False, "message": "New order message not found"}), 202

        order_tracking[order_number] = {
            "ts": ts,
            "channel": channel,
            "payment": None,
            "fulfillment": None,
            "last_event_id": None
        }

        print("✅ Order cached:", order_tracking[order_number])

    track = order_tracking[order_number]

    if event_id and track.get("last_event_id") == event_id:
        print("♻️ Duplicate webhook ignored")
        return jsonify({"ok": True, "duplicate": True}), 200

    track["last_event_id"] = event_id

    time_now = datetime.now().strftime("%I:%M %p")

    payment_status = order.get("financial_status")
    print("💰 Payment Status:", payment_status)

    if payment_status and payment_status != track["payment"]:
        msg = f"{payment_message(payment_status)} • {time_now}"
        if post_thread_message(track["channel"], track["ts"], msg):
            track["payment"] = payment_status
            print("✅ Payment message sent")

    fulfillment_status = order.get("fulfillment_status")
    print("📦 Fulfillment Status:", fulfillment_status)

    tracking_no = None
    courier = None

    if order.get("fulfillments"):
        f = order["fulfillments"][-1]
        tracking_no = f.get("tracking_number")
        courier = f.get("tracking_company")
        print("🚚 Tracking:", tracking_no, "Courier:", courier)

    if fulfillment_status and fulfillment_status != track["fulfillment"]:
        msg = f"{fulfillment_message(fulfillment_status, tracking_no, courier)} • {time_now}"
        if post_thread_message(track["channel"], track["ts"], msg):
            track["fulfillment"] = fulfillment_status
            print("✅ Fulfillment message sent")

    print("🎯 Webhook processed successfully\n")
    return jsonify({"ok": True}), 200


# --------------------------------------------------
@app.route("/health")
def health():
    print("❤️ Health check called")
    return jsonify({
        "status": "ok",
        "tracked_orders": len(order_tracking)
    })


# --------------------------------------------------
if __name__ == "__main__":
    print("🚀 Shopify Slack Thread Service Started")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
