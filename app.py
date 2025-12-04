# app.py (clean, no debug prints, no shelve)
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

# In-memory store for order -> thread mapping and last-known statuses
# Structure: order_threads[order_number] = {"ts": "...", "channel": "...", "last_payment": "...", "last_fulfillment": "..."}
order_threads = {}

def normalize_text(s: str) -> str:
    if not s:
        return ""
    return " ".join(str(s).lower().split())

def find_message_ts_for_order(order_number, channel_id=SLACK_CHANNEL_ID):
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
        onum = str(order_number).lstrip("#").strip()

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

            if "st.order" in text and onum in text:
                return msg.get("ts")

            for needle in needles:
                if needle in text:
                    if needle == onum:
                        if ("order " in text) or ("st.order" in text) or (f"#{onum}" in text):
                            return msg.get("ts")
                        else:
                            continue
                    else:
                        return msg.get("ts")
        return None

    except Exception as e:
        app.logger.exception("Error while searching for order message: %s", str(e))
        return None

def save_mapping(order_number, thread_ts, channel=SLACK_CHANNEL_ID, last_payment=None, last_fulfillment=None):
    current = order_threads.get(str(order_number), {})
    order_threads[str(order_number)] = {
        "ts": thread_ts,
        "channel": channel,
        "last_payment": last_payment if last_payment is not None else current.get("last_payment"),
        "last_fulfillment": last_fulfillment if last_fulfillment is not None else current.get("last_fulfillment")
    }

def get_mapping(order_number):
    return order_threads.get(str(order_number))

def post_thread_message(channel, thread_ts, text):
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
        return data.get("ok", False)
    except Exception:
        app.logger.exception("Error posting thread message")
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
    app.logger.info("Shopify webhook received")
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data received'}), 400

        webhook_topic = (request.headers.get('X-Shopify-Topic') or '').lower()
        order_number = data.get('order_number') or data.get('name') or str(data.get('id', 'unknown'))
        financial_status = data.get('financial_status') or data.get('payment_status') or ''
        fulfillment_status = data.get('fulfillment_status') or ''

        status_mapping = {
            'pending': 'payment pending',
            'partially_fulfilled': 'partially fulfilled',
            'partial': 'partially fulfilled'
        }
        payment_status = status_mapping.get((financial_status or '').lower(), financial_status) if financial_status else None
        fulfillment_status_mapped = status_mapping.get((fulfillment_status or '').lower(), fulfillment_status) if fulfillment_status else None

        webhook_topic_lower = webhook_topic or ''
        should_handle_payment = True
        should_handle_fulfillment = True

        if 'fulfill' in webhook_topic_lower:
            should_handle_payment = False
            should_handle_fulfillment = True
        elif 'paid' in webhook_topic_lower or 'payment' in webhook_topic_lower:
            should_handle_payment = True
            should_handle_fulfillment = False
        elif webhook_topic_lower == 'orders/updated':
            should_handle_payment = True
            should_handle_fulfillment = True
        else:
            should_handle_payment = True
            should_handle_fulfillment = True

        if webhook_topic_lower in ('orders/updated', 'orders/paid', 'fulfillments/create', 'fulfillments/update', 'orders/paid'):
            mapping = ensure_thread_for_order(order_number)
            if not mapping:
                return jsonify({'ok': False, 'reason': 'no slack message found for order yet'}), 202

            last_payment = mapping.get("last_payment")
            last_fulfillment = mapping.get("last_fulfillment")

            # PAYMENT
            if should_handle_payment and payment_status and (str(payment_status) != str(last_payment)):
                details = {}
                if data.get('gateway'):
                    details['Method'] = data.get('gateway')
                text = create_status_text('payment', payment_status, details)
                if post_thread_message(mapping['channel'], mapping['ts'], text):
                    mapping['last_payment'] = payment_status
                    save_mapping(order_number, mapping['ts'], channel=mapping['channel'], last_payment=payment_status, last_fulfillment=mapping.get('last_fulfillment'))

            # FULFILLMENT
            if should_handle_fulfillment and fulfillment_status_mapped and (str(fulfillment_status_mapped) != str(last_fulfillment)):
                fdetails = {}
                if data.get('tracking_numbers'):
                    fdetails['Tracking'] = ', '.join(data.get('tracking_numbers', []))
                if data.get('tracking_company'):
                    fdetails['Carrier'] = data.get('tracking_company')
                text2 = create_status_text('fulfillment', fulfillment_status_mapped, fdetails)
                if post_thread_message(mapping['channel'], mapping['ts'], text2):
                    mapping['last_fulfillment'] = fulfillment_status_mapped
                    save_mapping(order_number, mapping['ts'], channel=mapping['channel'], last_payment=mapping.get('last_payment'), last_fulfillment=fulfillment_status_mapped)

            return jsonify({'ok': True, 'order': order_number}), 200

        if webhook_topic_lower == 'orders/create':
            return jsonify({'ok': True, 'note': 'creation handled by Flow/Incoming Webhook'}), 200

        return jsonify({'ok': True}), 200

    except Exception as e:
        app.logger.exception("ERROR in webhook handler: %s", str(e))
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy'}), 200

@app.route('/', methods=['GET'])
def home():
    return """
    <html><body>
      <h2>Shopify → Slack status updater</h2>
      <p>Posts payment/fulfillment status updates as thread replies under the message
      that contains the order number in the configured Slack channel.</p>
    </body></html>
    """

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
