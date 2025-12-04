# app.py - Shopify -> Slack status updater (shelve persistence + mappings endpoint)
import os
import requests
import shelve
from flask import Flask, request, jsonify, abort
from datetime import datetime

app = Flask(__name__)

# Config from environment
SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN')            # xoxb-...
SLACK_CHANNEL_ID = os.getenv('SLACK_CHANNEL_ID', 'C0A068PHZMY')
CONVERSATIONS_HISTORY_LIMIT = int(os.getenv('CONVERSATIONS_HISTORY_LIMIT', "200"))
MAPPINGS_SECRET = os.getenv('MAPPINGS_SECRET', 'local-debug-secret')

# Shelve file path (in the app directory)
STORE_FILENAME = os.path.join(os.path.dirname(__file__), "order_mappings_shelf.db")

if not SLACK_BOT_TOKEN:
    app.logger.warning("No SLACK_BOT_TOKEN configured. Set SLACK_BOT_TOKEN in environment variables.")

def shelve_get(order_number):
    try:
        with shelve.open(STORE_FILENAME) as db:
            return db.get(str(order_number))
    except Exception:
        app.logger.exception("shelve_get error")
        return None

def shelve_set(order_number, mapping):
    try:
        with shelve.open(STORE_FILENAME, writeback=True) as db:
            db[str(order_number)] = mapping
    except Exception:
        app.logger.exception("shelve_set error")

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
                app.logger.debug("find_message_ts: matched st.order format")
                return msg.get("ts")

            for needle in needles:
                if needle in text:
                    if needle == onum:
                        if ("order " in text) or ("st.order" in text) or (f"#{onum}" in text):
                            app.logger.debug("find_message_ts: matched plain number with context")
                            return msg.get("ts")
                        else:
                            continue
                    else:
                        app.logger.debug("find_message_ts: matched needle '%s'", needle)
                        return msg.get("ts")
        return None

    except Exception:
        app.logger.exception("Error while searching for order message")
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
        if not data.get("ok"):
            app.logger.error("Slack chat.postMessage error: %s", data.get("error"))
        return data.get("ok", False)
    except Exception:
        app.logger.exception("Error posting thread message")
        return False

def ensure_thread_for_order(order_number, channel_id=SLACK_CHANNEL_ID, init_from=None):
    # load persisted mapping first
    mapping = shelve_get(order_number)
    if mapping:
        return mapping

    ts = find_message_ts_for_order(order_number, channel_id=channel_id)
    if not ts:
        return None

    # initialize last-known statuses from init_from (if provided) to avoid duplicate posts
    last_payment = None
    last_fulfillment = None
    if isinstance(init_from, dict):
        fin = init_from.get('financial_status') or init_from.get('payment_status') or ''
        ful = init_from.get('fulfillment_status') or ''
        status_mapping = {
            'pending': 'payment pending',
            'partially_fulfilled': 'partially fulfilled',
            'partial': 'partially fulfilled'
        }
        last_payment = status_mapping.get((fin or '').lower(), fin) if fin else None
        last_fulfillment = status_mapping.get((ful or '').lower(), ful) if ful else None

    mapping = {
        "ts": ts,
        "channel": channel_id,
        "last_payment": last_payment,
        "last_fulfillment": last_fulfillment
    }
    shelve_set(order_number, mapping)
    app.logger.info("MAPPING saved order=%s ts=%s channel=%s payment=%s fulfillment=%s",
                    order_number, ts, channel_id, last_payment, last_fulfillment)
    return mapping

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

        # Determine which statuses this webhook should handle based on topic
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
            mapping = ensure_thread_for_order(order_number, init_from=data)
            if not mapping:
                app.logger.warning("No Slack message found for order %s yet. Will wait for a copy into channel.", order_number)
                return jsonify({'ok': False, 'reason': 'no slack message found for order yet'}), 202

            stored = shelve_get(order_number)
            last_payment = stored.get('last_payment') if stored else None
            last_fulfillment = stored.get('last_fulfillment') if stored else None

            # PAYMENT: only post if allowed and changed
            if should_handle_payment and payment_status and (str(payment_status) != str(last_payment)):
                details = {}
                if data.get('gateway'):
                    details['Method'] = data.get('gateway')
                text = create_status_text('payment', payment_status, details)
                if post_thread_message(stored['channel'], stored['ts'], text):
                    stored['last_payment'] = payment_status
                    shelve_set(order_number, stored)
                    app.logger.info("Posted payment update for %s -> %s", order_number, payment_status)

            # FULFILLMENT: only post if allowed and changed
            if should_handle_fulfillment and fulfillment_status_mapped and (str(fulfillment_status_mapped) != str(last_fulfillment)):
                fdetails = {}
                if data.get('tracking_numbers'):
                    fdetails['Tracking'] = ', '.join(data.get('tracking_numbers', []))
                if data.get('tracking_company'):
                    fdetails['Carrier'] = data.get('tracking_company')
                text2 = create_status_text('fulfillment', fulfillment_status_mapped, fdetails)
                if post_thread_message(stored['channel'], stored['ts'], text2):
                    stored['last_fulfillment'] = fulfillment_status_mapped
                    shelve_set(order_number, stored)
                    app.logger.info("Posted fulfillment update for %s -> %s", order_number, fulfillment_status_mapped)

            return jsonify({'ok': True, 'order': order_number}), 200

        if webhook_topic_lower == 'orders/create':
            app.logger.info("Received orders/create for %s: ignoring (Flow handles creation)", order_number)
            return jsonify({'ok': True, 'note': 'creation handled by Flow/Incoming Webhook'}), 200

        return jsonify({'ok': True}), 200

    except Exception:
        app.logger.exception("ERROR in webhook handler")
        return jsonify({'error': 'internal error'}), 500

@app.route('/mappings', methods=['GET'])
def mappings():
    secret = request.args.get("secret", "")
    if secret != MAPPINGS_SECRET:
        abort(401)
    safe = {}
    try:
        with shelve.open(STORE_FILENAME) as db:
            for k in db.keys():
                v = db.get(k, {})
                safe[str(k)] = {
                    "ts": v.get("ts"),
                    "channel": v.get("channel"),
                    "last_payment": v.get("last_payment"),
                    "last_fulfillment": v.get("last_fulfillment")
                }
    except Exception:
        app.logger.exception("mappings view error")
    return jsonify({"mappings": safe}), 200

@app.route('/clear-mapping', methods=['POST'])
def clear_mapping():
    secret = request.args.get("secret", "")
    order = request.args.get("order", "")
    if secret != MAPPINGS_SECRET:
        abort(401)
    if not order:
        return jsonify({"error": "missing order param"}), 400
    try:
        with shelve.open(STORE_FILENAME, writeback=True) as db:
            if str(order) in db:
                del db[str(order)]
        return jsonify({"cleared": order}), 200
    except Exception:
        app.logger.exception("clear mapping error")
        return jsonify({"error": "internal error"}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy'}), 200

@app.route('/', methods=['GET'])
def home():
    return """
    <html><body>
      <h2>Shopify → Slack status updater (shelve enabled)</h2>
      <p>This service posts payment/fulfillment status updates as thread replies under the message
      that contains the order number in the configured Slack channel.</p>
      <p>Use <code>/mappings?secret=...</code> to view persisted mappings.</p>
    </body></html>
    """

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
