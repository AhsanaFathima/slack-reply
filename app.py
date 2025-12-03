import os
import hmac
import hashlib
from flask import Flask, request, jsonify, abort
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

app = Flask(__name__)

SHOPIFY_SECRET = os.environ["SHOPIFY_WEBHOOK_SECRET"]
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_CHANNEL = "C0A068PHZMY"   # your channel ID

slack_client = WebClient(token=SLACK_BOT_TOKEN)


# -------- Verify Webhook (Required by Shopify) --------
def verify_webhook(data, hmac_header):
    digest = hmac.new(
        SHOPIFY_SECRET.encode("utf-8"),
        data,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(digest, hmac_header)


# ------------- Payment Status Webhook -----------------
@app.route("/webhook/payment", methods=["POST"])
def webhook_payment():
    data = request.get_data()
    hmac_header = request.headers.get("X-Shopify-Hmac-Sha256")

    if not verify_webhook(data, hmac_header):
        abort(401)

    payload = request.get_json()

    order_id = payload["id"]
    financial_status = payload.get("financial_status", "unknown")

    # ---- Map status to message ----
    messages = {
        "pending": "⏳ Payment is *pending* for order #" + str(order_id),
        "paid": "💰 Payment *received* for order #" + str(order_id),
        "refunded": "🔄 Order #" + str(order_id) + " has been *refunded*",
        "voided": "❌ Payment *voided* for order #" + str(order_id),
    }

    text = messages.get(financial_status, f"📢 Order #{order_id}: status changed to *{financial_status}*")

    # ---- Send Slack message ----
    try:
        slack_client.chat_postMessage(
            channel=SLACK_CHANNEL,
            text=text
        )
    except SlackApiError as e:
        print("Slack error:", e.response["error"])

    return jsonify({"status": "success"}), 200


if __name__ == "__main__":
    app.run()
