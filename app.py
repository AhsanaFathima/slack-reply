import os
import re
import requests
import certifi
from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)

# ---------------- ENV ----------------
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
SHOPIFY_SHOP_NAME = os.getenv("SHOPIFY_SHOP_NAME")

CHANNELS_TO_SEARCH = [
    "C0A02M2VCTB",  # order
    "C0A068PHZMY"   # shopify-slack
]

order_tracking = {}

print("🚀 App started")
print("Shop:", SHOPIFY_SHOP_NAME)

# --------------------------------------------------
def is_new_order_message(text, order_number):
    if not text:
        return False

    text = text.lower().strip()
    blacklist = ["fulfilled", "tracking", "report", "generated", "payment"]
    if any(word in text for word in blacklist):
        return False

    match = re.search(r"\bst\.order\s+#?(\d+)\b", text)
    return bool(match and match.group(1) == order_number)

# --------------------------------------------------
def find_new_order_message(order_number):
    print(f"🔍 Searching Slack thread for order {order_number}")

    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}

    for channel in CHANNELS_TO_SEARCH:
        print(f"➡️ Checking channel {channel}")

        resp = requests.get(
            "https://slack.com/api/conversations.history",
            headers=headers,
            params={"channel": channel, "limit": 100},
            timeout=10
        )

        data = resp.json()
        if not data.get("ok"):
            print("❌ Slack API error")
            continue

        for msg in reversed(data.get("messages", [])):
            if is_new_order_message(msg.get("text", ""), order_number):
                print("✅ Found original order message")
                return msg["ts"], channel

    print("❌ No Slack message found")
    return None, None

# --------------------------------------------------
def post_thread_message(channel, thread_ts, text):
    print(f"💬 Posting to Slack: {text}")

    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    payload = {
        "channel": channel,
        "thread_ts": thread_ts,
        "text": text
    }

    r = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers=headers,
        json=payload,
        timeout=10
    )

    ok = r.json().get("ok", False)
    print("Slack response:", r.json())
    return ok

# --------------------------------------------------
def payment_message(status):
    return {
        "pending": "⏳ Payment Pending",
        "authorized": "🔒 Payment Authorized",
        "paid": "✅ Payment Paid",
        "voided": "❌ Payment Voided",
        "refunded": "↩️ Payment Refunded"
    }.get(status, f"💳 Payment {status}")

def fulfillment_message(status):
    return {
        "fulfilled": "🚀 Fulfilled",
        "unfulfilled": "📭 Unfulfilled"
    }.get(status, f"📦 {status}")

def stock_message(status):
    return "📦 Stock Available" if status.lower() == "stock available" else f"📦 Stock Status: {status}"

# --------------------------------------------------
def fetch_stock_status(order_id):
    print(f"📦 Fetching stock metafield for order ID {order_id}")

    try:
        url = f"https://{SHOPIFY_SHOP_NAME}.myshopify.com/admin/api/2025-01/graphql.json"

        query = """
        query ($id: ID!) {
          order(id: $id) {
            metafield(namespace: "custom", key: "stock_status") {
              value
            }
          }
        }
        """

        resp = requests.post(
            url,
            headers={
                "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
                "Content-Type": "application/json"
            },
            json={
                "query": query,
                "variables": {"id": f"gid://shopify/Order/{order_id}"}
            },
            timeout=10,
            verify=certifi.where()
        )

        print("Shopify status code:", resp.status_code)
        data = resp.json()
        print("Shopify response:", data)

        return (
            data.get("data", {})
            .get("order", {})
            .get("metafield", {})
            .get("value")
        )

    except Exception as e:
        print("❌ Stock fetch error:", e)
        return None

# --------------------------------------------------
@app.route("/webhook/shopify", methods=["POST"])
def shopify_webhook():
    print("\n🔔 Shopify webhook received")

    data = request.get_json(force=True)
    order = data.get("order", data)

    order_number = str(order.get("name", "")).replace("#", "").strip()
    print("Order number:", order_number)

    is_metafield_update = bool(order.get("metafields"))
    print("Metafield update:", is_metafield_update)

    if order_number not in order_tracking:
        ts, channel = find_new_order_message(order_number)
        if not ts:
            print("⚠️ Thread not found, skipping")
            return jsonify({"ok": False}), 202

        order_tracking[order_number] = {
            "ts": ts,
            "channel": channel,
            "payment": None,
            "fulfillment": None,
            "stock": None
        }

    track = order_tracking[order_number]
    time_now = datetime.now().strftime("%I:%M %p")

    # PAYMENT
    if not is_metafield_update:
        payment_status = order.get("financial_status")
        print("Payment status:", payment_status)

        if payment_status and payment_status != track["payment"]:
            if post_thread_message(track["channel"], track["ts"],
                                   f"{payment_message(payment_status)} • {time_now}"):
                track["payment"] = payment_status

    # FULFILLMENT
    if not is_metafield_update:
        fulfillment_status = order.get("fulfillment_status")
        print("Fulfillment status:", fulfillment_status)

        if fulfillment_status and fulfillment_status != track["fulfillment"]:
            if post_thread_message(track["channel"], track["ts"],
                                   f"{fulfillment_message(fulfillment_status)} • {time_now}"):
                track["fulfillment"] = fulfillment_status

    # STOCK
    stock_status = fetch_stock_status(order.get("id"))
    print("Stock status fetched:", stock_status)

    if stock_status and stock_status != track["stock"]:
        if post_thread_message(track["channel"], track["ts"],
                               f"{stock_message(stock_status)} • {time_now}"):
            track["stock"] = stock_status

    return jsonify({"ok": True}), 200

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
