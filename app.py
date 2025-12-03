import os
import hmac
import hashlib
import base64
from flask import Flask, request, abort
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

app = Flask(__name__)

# ---- Environment variables (set these in Render later) ----
SHOPIFY_WEBHOOK_SECRET = os.environ.get("SHOPIFY_WEBHOOK_SECRET")  # shpss_...
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID")

slack_client = WebClient(token=SLACK_BOT_TOKEN)


# ---------- Helper: verify Shopify webhook ----------
def verify_shopify_webhook(raw_body: bytes, hmac_header: str) -> bool:
    if not SHOPIFY_WEBHOOK_SECRET or not hmac_header:
        return False

    digest = hmac.new(
        SHOPIFY_WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256
    ).digest()

    computed_hmac = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(computed_hmac, hmac_header)


# ---------- Simple health check (fixes Render 404 on "/") ----------
@app.route("/", methods=["GET"])
def home():
    return "Slack reply app is running", 200


# ---------- Shopify payment / order webhook ----------
@app.route("/webhook/payment", methods=["POST"])
def webhook_payment():
    # 1. Verify Shopify signature
    raw_body = request.get_data()
    hmac_header = request.headers.get("X-Shopify-Hmac-Sha256")

    if not verify_shopify_webhook(raw_body, hmac_header):
        abort(401)

    payload = request.get_json()

    # 2. Get order id + financial status
    order_id = payload.get("id")
    financial_status = payload.get("financial_status", "unknown")

    # 3. Map financial_status → message
    messages = {
        "pending":   f"⏳ Payment is *pending* for order #{order_id}.",
        "paid":      f"✅ Payment *received* for order #{order_id}.",
        "refunded":  f"🔄 Payment *refunded* for order #{order_id}.",
        "voided":    f"❌ Payment *voided* for order #{order_id}.",
        "partially_paid": f"💸 Payment *partially paid* for order #{order_id}.",
    }

    text = messages.get(
        financial_status,
        f"ℹ️ Order #{order_id}: financial status changed to *{financial_status}*."
    )

    # 4. Send Slack message
    try:
        slack_client.chat_postMessage(
            channel=SLACK_CHANNEL_ID,
            text=text
        )
    except SlackApiError as e:
        # Log error to Render logs
        print(f"Slack error: {e.response['error']}")

    # 5. Return 200 so Shopify knows it succeeded
    return "OK", 200


# ---------- For local testing ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
