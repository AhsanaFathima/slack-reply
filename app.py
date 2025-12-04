# app.py  (debugable version - prints/logs for Render)
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
    Extensive debug logging added to help diagnose issues.
    """
    app.logger.info("SEARCH: starting search for order=%s in channel=%s", order_number, channel_id)
    print(f"[DEBUG] SEARCH START order={order_number} channel={channel_id}")

    if not SLACK_BOT_TOKEN:
        app.logger.error("Cannot search Slack: SLACK_BOT_TOKEN not set")
        print("[DEBUG] No SLACK_BOT_TOKEN")
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
        app.logger.info("SEARCH: conversations.history HTTP status=%s", resp.status_code)
        print(f"[DEBUG] conversations.history status={resp.status_code}")
        data = resp.json()
        if not data.get("ok"):
            app.logger.error("Slack API error searching conversations.history: %s", data.get("error"))
            print(f"[DEBUG] Slack API error: {data.get('error')}")
            return None

        messages = data.get("messages", [])
        app.logger.info("SEARCH: fetched %d messages", len(messages))
        print(f"[DEBUG] fetched {len(messages)} messages")
        # print first 3 messages for quick debugging
        for i, m in enumerate(messages[:5]):
            snippet = (m.get("text") or "")[:200].replace("\n", " ")
            app.logger.info("SEARCH: msg[%d] ts=%s text=%s", i, m.get("ts"), snippet)
            print(f"[DEBUG] msg[{i}] ts={m.get('ts')} text={snippet}")

        onum = str(order_number).lstrip("#").strip()  # canonical digits/text
        app.logger.info("SEARCH: normalized order number => '%s'", onum)
        print(f"[DEBUG] normalized order number = '{onum}'")

        needles = [
            f"st.order #{onum}",
            f"st.order {onum}",
            f"order #{onum}",
            f"order {onum}",
            f"#{onum}",
            f"{onum}"
        ]
        app.logger.info("SEARCH: needles=%s", needles)
        print(f"[DEBUG] needles={needles}")

        for msg_index, msg in enumerate(messages):
            raw_text = msg.get("text", "") or ""
            text = normalize_text(raw_text)
            # debug: print the normalized text for first few messages
            if msg_index < 6:
                app.logger.debug("SEARCH: checking msg[%d] text(normalized)=%s", msg_index, text[:200])
                print(f"[DEBUG] checking msg[{msg_index}] text(normalized)={text[:200]}")

            # Quick wins: if 'st.order' + number both present
            if "st.order" in text and onum in text:
                ts = msg.get("ts")
                app.logger.info("SEARCH: FOUND match (st.order + number) on msg[%d] ts=%s", msg_index, ts)
                print(f"[DEBUG] FOUND match (st.order + number) on msg[{msg_index}] ts={ts}")
                return ts

            # Otherwise try needles
            for needle in needles:
                if needle in text:
                    # For plain numeric needle, require extra context to avoid false positives
                    if needle == onum:
                        if ("order " in text) or ("st.order" in text) or (f"#{onum}" in text):
                            ts = msg.get("ts")
                            app.logger.info("SEARCH: FOUND match (plain-number with context) msg[%d] needle=%s ts=%s", msg_index, needle, ts)
                            print(f"[DEBUG] FOUND match (plain-number with context) msg[{msg_index}] needle={needle} ts={ts}")
                            return ts
                        else:
                            app.logger.debug("SEARCH: plain-number needle matched but no context; skipping for msg[%d]", msg_index)
                            print(f"[DEBUG] plain-number matched but no context - skipping msg[{msg_index}]")
                            continue
                    else:
                        ts = msg.get("ts")
                        app.logger.info("SEARCH: FOUND match msg[%d] needle=%s ts=%s", msg_index, needle, ts)
                        print(f"[DEBUG] FOUND match msg[{msg_index}] needle={needle} ts={ts}")
                        return ts

        app.logger.info("SEARCH: No message found for order %s in channel %s (checked %d messages)",
                        order_number, channel_id, len(messages))
        print(f"[DEBUG] SEARCH END: no message found for order {order_number}")
        return None

    except Exception as e:
        app.logger.exception("Error while searching for order message: %s", str(e))
        print(f"[DEBUG] Exception during search: {e}")
        return None

def save_mapping(order_number, thread_ts, channel=SLACK_CHANNEL_ID, last_payment=None, last_fulfillment=None):
    current = order_threads.get(str(order_number), {})
    order_threads[str(order_number)] = {
        "ts": thread_ts,
        "channel": channel,
        "last_payment": last_payment if last_payment is not None else current.get("last_payment"),
        "last_fulfillment": last_fulfillment if last_fulfillment is not None else current.get("last_fulfillment")
    }
    app.logger.info("MAPPING: saved order %s => ts=%s channel=%s payment=%s fulfillment=%s",
                    order_number, thread_ts, channel,
                    order_threads[str(order_number)].get("last_payment"),
                    order_threads[str(order_number)].get("last_fulfillment"))
    print(f"[DEBUG] MAPPING saved order={order_number} ts={thread_ts} channel={channel} payment={order_threads[str(order_number)].get('last_payment')} fulfillment={order_threads[str(order_number)].get('last_fulfillment')}")

def get_mapping(order_number):
    mapping = order_threads.get(str(order_number))
    print(f"[DEBUG] get_mapping order={order_number} -> {mapping}")
    return mapping

def post_thread_message(channel, thread_ts, text):
    """Post a message into a thread using chat.postMessage"""
    if not SLACK_BOT_TOKEN:
        app.logger.error("No SLACK_BOT_TOKEN configured")
        print("[DEBUG] cannot post: no SLACK_BOT_TOKEN")
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
    app.logger.info("POST: posting thread message to channel=%s thread_ts=%s text=%.60s", channel, thread_ts, text.replace("\n"," ")[:60])
    print(f"[DEBUG] POST thread to {channel} ts={thread_ts} text={text[:120].replace(chr(10),' ')}")
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        data = resp.json()
        if data.get("ok"):
            app.logger.info("POST: chat.postMessage ok ts=%s", data.get("ts"))
            print(f"[DEBUG] POST ok ts={data.get('ts')}")
            return True
        else:
            app.logger.error("POST: Slack chat.postMessage error: %s", data.get("error"))
            print(f"[DEBUG] POST error: {data.get('error')}")
            return False
    except Exception as e:
        app.logger.exception("Error posting thread message: %s", str(e))
        print(f"[DEBUG] Exception posting thread message: {e}")
        return False

def ensure_thread_for_order(order_number, channel_id=SLACK_CHANNEL_ID):
    mapping = get_mapping(order_number)
    if mapping:
        app.logger.info("MAPPING: found in-memory mapping for order %s -> %s", order_number, mapping)
        print(f"[DEBUG] mapping exists for {order_number}")
        return mapping

    # not found in memory -> attempt to find message in slack channel
    ts = find_message_ts_for_order(order_number, channel_id=channel_id)
    if ts:
        # Save mapping; last statuses remain None until updated by webhooks
        save_mapping(order_number, ts, channel=channel_id)
        app.logger.info("MAPPING: created new mapping for order %s ts=%s", order_number, ts)
        print(f"[DEBUG] created mapping for {order_number} ts={ts}")
        return get_mapping(order_number)

    app.logger.info("MAPPING: could not find message for order %s", order_number)
    print(f"[DEBUG] could not find message for {order_number}")
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
    app.logger.info("WEBHOOK: received /webhook/shopify")
    try:
        data = request.get_json()
        print(f"[DEBUG] WEBHOOK raw payload keys: {list(data.keys()) if isinstance(data, dict) else 'no-data'}")
        if not data:
            return jsonify({'error': 'No data received'}), 400

        webhook_topic = (request.headers.get('X-Shopify-Topic') or '').lower()
        print(f"[DEBUG] WEBHOOK header X-Shopify-Topic = {webhook_topic}")
        order_number = data.get('order_number') or data.get('name') or str(data.get('id', 'unknown'))
        financial_status = data.get('financial_status') or data.get('payment_status') or ''
        fulfillment_status = data.get('fulfillment_status') or ''
        print(f"[DEBUG] parsed order_number={order_number} financial_status={financial_status} fulfillment_status={fulfillment_status}")

        # normalize a few statuses
        status_mapping = {
            'pending': 'payment pending',
            'partially_fulfilled': 'partially fulfilled',
            'partial': 'partially fulfilled'
        }
        payment_status = status_mapping.get((financial_status or '').lower(), financial_status)
        fulfillment_status_mapped = status_mapping.get((fulfillment_status or '').lower(), fulfillment_status)

        print(f"[DEBUG] normalized payment_status={payment_status} fulfillment_status_mapped={fulfillment_status_mapped}")

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

        app.logger.info("WEBHOOK: handling flags payment=%s fulfillment=%s topic=%s", should_handle_payment, should_handle_fulfillment, webhook_topic_lower)
        print(f"[DEBUG] handling flags payment={should_handle_payment} fulfillment={should_handle_fulfillment} topic={webhook_topic_lower}")

        if webhook_topic_lower in ('orders/updated', 'orders/paid', 'fulfillments/create', 'fulfillments/update', 'orders/paid'):
            mapping = ensure_thread_for_order(order_number)
            if not mapping:
                app.logger.warning("WEBHOOK: No Slack message found for order %s yet. Will wait for a copy into channel.", order_number)
                print(f"[DEBUG] No Slack message found for order {order_number}, returning 202")
                return jsonify({'ok': False, 'reason': 'no slack message found for order yet'}), 202

            app.logger.info("WEBHOOK: mapping found for %s => %s", order_number, mapping)
            print(f"[DEBUG] mapping found for {order_number}: {mapping}")

            last_payment = mapping.get("last_payment")
            last_fulfillment = mapping.get("last_fulfillment")
            print(f"[DEBUG] last_known payment={last_payment} fulfillment={last_fulfillment}")

            # PAYMENT: only post if this webhook is allowed to handle payment and payment_status changed
            if should_handle_payment and payment_status and (str(payment_status) != str(last_payment)):
                details = {}
                if data.get('gateway'):
                    details['Method'] = data.get('gateway')
                text = create_status_text('payment', payment_status, details)
                app.logger.info("WEBHOOK: posting payment update for %s -> %s", order_number, payment_status)
                print(f"[DEBUG] posting payment update for {order_number} -> {payment_status}")
                posted = post_thread_message(mapping['channel'], mapping['ts'], text)
                if posted:
                    mapping['last_payment'] = payment_status
                    save_mapping(order_number, mapping['ts'], channel=mapping['channel'], last_payment=payment_status, last_fulfillment=mapping.get('last_fulfillment'))
                else:
                    print(f"[DEBUG] payment post failed for {order_number}")

            # FULFILLMENT: only post if this webhook is allowed to handle fulfillment and fulfillment_status changed
            if should_handle_fulfillment and fulfillment_status and (str(fulfillment_status_mapped) != str(last_fulfillment)):
                fdetails = {}
                if data.get('tracking_numbers'):
                    fdetails['Tracking'] = ', '.join(data.get('tracking_numbers', []))
                if data.get('tracking_company'):
                    fdetails['Carrier'] = data.get('tracking_company')
                text2 = create_status_text('fulfillment', fulfillment_status_mapped, fdetails)
                app.logger.info("WEBHOOK: posting fulfillment update for %s -> %s", order_number, fulfillment_status_mapped)
                print(f"[DEBUG] posting fulfillment update for {order_number} -> {fulfillment_status_mapped}")
                posted2 = post_thread_message(mapping['channel'], mapping['ts'], text2)
                if posted2:
                    mapping['last_fulfillment'] = fulfillment_status_mapped
                    save_mapping(order_number, mapping['ts'], channel=mapping['channel'], last_payment=mapping.get('last_payment'), last_fulfillment=fulfillment_status_mapped)
                else:
                    print(f"[DEBUG] fulfillment post failed for {order_number}")

            return jsonify({'ok': True, 'order': order_number}), 200

        if webhook_topic_lower == 'orders/create':
            app.logger.info("WEBHOOK: Received orders/create for %s: doing nothing (Flow handles creation).", order_number)
            print(f"[DEBUG] orders/create received for {order_number} - ignoring")
            return jsonify({'ok': True, 'note': 'creation handled by Flow/Incoming Webhook'}), 200

        return jsonify({'ok': True}), 200

    except Exception as e:
        app.logger.exception("❌ ERROR in webhook handler: %s", str(e))
        print(f"[DEBUG] Exception in webhook handler: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy'}), 200

@app.route('/', methods=['GET'])
def home():
    return """
    <html><body>
      <h2>Shopify → Slack status updater (debug mode)</h2>
      <p>This service posts payment/fulfillment status updates as thread replies under the message
      that contains the order number in the configured Slack channel.</p>
      <p>Environment variables required: <code>SLACK_BOT_TOKEN</code> and <code>SLACK_CHANNEL_ID</code>.</p>
      <p>The app now logs extensive debugging information to help troubleshoot message matching.</p>
    </body></html>
    """

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
