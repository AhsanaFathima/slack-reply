# app.py
import os
import requests
from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)

# Configuration - set these in Render env vars
SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN')            # xoxb-...
SLACK_CHANNEL_ID = os.getenv('SLACK_CHANNEL_ID', 'C0A068PHZMY')  # channel where copied messages appear (#shopify-slack)
CONVERSATIONS_HISTORY_LIMIT = int(os.getenv('CONVERSATIONS_HISTORY_LIMIT', "200"))

if not SLACK_BOT_TOKEN:
    app.logger.warning("No SLACK_BOT_TOKEN configured. Set SLACK_BOT_TOKEN in environment variables.")

# In-memory store for order -> thread_ts mapping (ephemeral)
order_threads = {}

def normalize_text(s: str) -> str:
    """Lowercase and normalize whitespace for safer substring checks."""
    if not s:
        return ""
    return " ".join(str(s).lower().split())

def find_message_ts_for_order(order_number, channel_id=SLACK_CHANNEL_ID):
    """
    Search recent messages in the given channel for the order_number in several common formats.
    Returns the ts if found, otherwise None.
    """
    if not SLACK_BOT_TOKEN:
        app.logger.error("Cannot search Slack: SLACK_BOT_TOKEN not set")
        return None

    url = "https://slack.com/api/conversations.history"
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    params = {
        "channel": channel_id,
        "limit": CONVERSATIONS_HISTORY_LIMIT
    }

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        data = resp.json()
        if not data.get("ok"):
            app.logger.error("Slack API error searching conversations.history: %s", data.get("error"))
            return None

        messages = data.get("messages", [])
        onum = str(order_number).lstrip("#").strip()  # canonical digits/text

        # needles to check; we will also check combined conditions (e.g., 'st.order' present AND order number present)
        # include lower-case forms
        needles = [
            f"st.order #{onum}",
            f"st.order {onum}",
            f"order #{onum}",
            f"order {onum}",
            f"#{onum}",
            f"{onum}"
        ]

        for msg in messages:
            raw_text = msg.get("text", "") or ""
            text = normalize_text(raw_text)

            # Quick wins: if 'st.order' + order number both present
            if "st.order" in text and onum in text:
                ts = msg.get("ts")
                app.logger.info("Found ST.order style message for order %s ts=%s", order_number, ts)
                return ts

            # Otherwise try the needles in order
            for needle in needles:
                if needle in text:
                    # To reduce false positives when matching plain number (e.g. '1273'),
                    # require that either the needle includes a prefix (# or order) or the message also contains 'order' / 'st.order'
                    if needle == onum:
                        # plain number match: only accept if message also contains 'order' or 'st.order'
                        if ("order " in text) or ("st.order" in text) or (f"#{onum}" in text):
                            ts = msg.get("ts")
                            app.logger.info("Found message for order %s (plain-number match) ts=%s", order_number, ts)
                            return ts
                        else:
                            # skip this plain numeric match as likely false positive
                            continue
                    else:
                        ts = msg.get("ts")
                        app.logger.info("Found message for order %s matching needle '%s' ts=%s", order_number, needle, ts)
                        return ts

        app.logger.info("No message found for order %s in channel %s (checked %d messages)",
                        order_number, channel_id, len(messages))
        return None

    except Exception as e:
        app.logger.exception("Error while searching for order message: %s", str(e))
        return None

def save_mapping(order_number, thread_ts, channel=SLACK_CHANNEL_ID):
    if order_number and thread_ts:
        order_threads[str(order_number)] = {"ts": thread_ts, "channel": channel}
        app.logger.info("Saved mapping for order %s -> %s@%s", order_number, thread_ts, channel)

def get_mapping(order_number):
    return order_threads.get(str(order_number))

def post_thread_message(channel, thread_ts, text):
    """Post a message into a thread using chat.postMessage"""
    if not SLACK_BOT_TOKEN:
        app.logger.error("No SLACK_BOT_TOKEN configured")
        return False

    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "channel": channel,
        "thread_ts": thread_ts,
        "text": text
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        data = resp.json()
        if data.get("ok"):
            app.logger.info("Posted thread message to %s (ts=%s)", channel, thread_ts)
            return True
        else:
            app.logger.error("Slack chat.postMessage error: %s", data.get("error"))
            return False
    except Exception as e:
        app.logger.exception("Error posting thread message: %s", str(e))
        return False

def ensure_thread_for_order(order_number, channel_id=SLACK_CHANNEL_ID):
    mapping = get_mapping(order_number)
    if mapping:
        return mapping

    ts = find_message_ts_for_order(order_number, channel_id=channel_id)
    if ts:
        save_mapping(order_number, ts, channel=channel_id)
        return get_mapping(order_number)

    return None

def create_status_text(status_type, status, details=None):
    payment_status = {
        'paid': {'emoji': '✅', 'text': 'Payment Paid'},
        'payment pending': {'emoji': '⏳', 'text': 'Payment Pending'},
        'authorized': {'emoji': '🔒', 'text': 'Payment Authorized'},
        'refunded': {'emoji': '↩️', 'text': 'Payment Refunded'},
        'voided': {'emoji': '❌', 'text': 'Payment Voided'},
    }

    fulfillment_status = {
        'fulfilled': {'emoji': '🚀', 'text': 'Fulfilled'},
        'unfulfilled': {'emoji': '📦', 'text': 'Unfulfilled'},
        'partially fulfilled': {'emoji': '📤', 'text': 'Partially Fulfilled'},
        'in progress': {'emoji': '⚙️', 'text': 'In Progress'},
        'on hold': {'emoji': '⏸️', 'text': 'On Hold'},
    }

    if status_type == 'payment':
        status_map = payment_status
        prefix = '💳'
    elif status_type == 'fulfillment':
        status_map = fulfillment_status
        prefix = '📦'
    else:
        status_map = {}
        prefix = '📝'

    if status:
        s = status.lower()
        cfg = status_map.get(s, {'emoji': '📝', 'text': (status.title() if status else 'Unknown')})
    else:
        cfg = {'emoji': '❓', 'text': 'Unknown Status'}

    time_now = datetime.now().strftime("%I:%M %p")
    text = f"{prefix} {cfg['emoji']} *{cfg['text']}* • {time_now}"
    if details:
        for k, v in (details.items() if isinstance(details, dict) else []):
            if v:
                text += f"\n{k}: {v}"
    return text

@app.route('/webhook/shopify', methods=['POST'])
def shopify_webhook():
    app.logger.info("📩 Shopify webhook received")
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data received'}), 400

        webhook_topic = request.headers.get('X-Shopify-Topic', 'unknown')
        order_number = data.get('order_number') or data.get('name') or str(data.get('id', 'unknown'))
        financial_status = data.get('financial_status') or data.get('payment_status') or ''
        fulfillment_status = data.get('fulfillment_status') or ''
        total_price = data.get('total_price') or data.get('total_price_set', {}).get('presentment_money', {}).get('amount')

        status_mapping = {
            'pending': 'payment pending',
            'partially_fulfilled': 'partially fulfilled',
            'partial': 'partially fulfilled'
        }
        payment_status = status_mapping.get((financial_status or '').lower(), financial_status)
        fulfillment_status_mapped = status_mapping.get((fulfillment_status or '').lower(), fulfillment_status)

        # Only handle updates (we do NOT create initial message here)
        if webhook_topic in ('orders/updated', 'orders/paid', 'fulfillments/create', 'fulfillments/update'):
            mapping = ensure_thread_for_order(order_number)
            if not mapping:
                app.logger.warning("No Slack message found for order %s yet. Will wait for a copy into channel.", order_number)
                return jsonify({'ok': False, 'reason': 'no slack message found for order yet'}), 202

            if financial_status:
                details = {}
                if total_price:
                    details['Amount'] = f"${total_price}"
                if data.get('gateway'):
                    details['Method'] = data.get('gateway')
                text = create_status_text('payment', payment_status, details)
                post_thread_message(mapping['channel'], mapping['ts'], text)

            if fulfillment_status:
                fdetails = {}
                if data.get('tracking_numbers'):
                    fdetails['Tracking'] = ', '.join(data.get('tracking_numbers', []))
                if data.get('tracking_company'):
                    fdetails['Carrier'] = data.get('tracking_company')
                text2 = create_status_text('fulfillment', fulfillment_status_mapped, fdetails)
                post_thread_message(mapping['channel'], mapping['ts'], text2)

            return jsonify({'ok': True, 'order': order_number}), 200

        if webhook_topic == 'orders/create':
            app.logger.info("Received orders/create for %s: doing nothing (Flow handles creation).", order_number)
            return jsonify({'ok': True, 'note': 'creation handled by Flow/Incoming Webhook'}), 200

        return jsonify({'ok': True}), 200

    except Exception as e:
        app.logger.exception("❌ ERROR in webhook handler: %s", str(e))
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy'}), 200

@app.route('/', methods=['GET'])
def home():
    return """
    <html><body>
      <h2>Shopify → Slack status updater</h2>
      <p>This service posts payment/fulfillment status updates as thread replies under the message
      that contains the order number in the configured Slack channel.</p>
      <p>Environment variables required: <code>SLACK_BOT_TOKEN</code> and <code>SLACK_CHANNEL_ID</code>.</p>
      <p>Set <code>SLACK_CHANNEL_ID</code> to your <strong>#shopify-slack</strong> channel id.</p>
      <p><small>Note: this app does not post the initial order creation message (Flow does that via incoming webhook).</small></p>
    </body></html>
    """

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
