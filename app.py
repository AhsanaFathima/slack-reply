import os
import requests
from flask import Flask, request, jsonify
from datetime import datetime
import re

app = Flask(__name__)

SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN')
SHOPIFY_SLACK_CHANNEL_ID = os.getenv('SHOPIFY_SLACK_CHANNEL_ID', 'C0A068PHZMY')

order_threads = {}

def find_order_message(order_number):
    """Find order message in Slack channel"""
    if not SLACK_BOT_TOKEN:
        return None
    
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    params = {"channel": SHOPIFY_SLACK_CHANNEL_ID, "limit": 100}
    
    try:
        resp = requests.get("https://slack.com/api/conversations.history", 
                          headers=headers, params=params, timeout=10)
        data = resp.json()
        
        if not data.get("ok"):
            return None
        
        clean_num = str(order_number).replace("#", "").strip()
        
        for msg in data.get("messages", []):
            text = msg.get("text", "")
            # Look for ST.order #1234 pattern
            match = re.search(r'ST\.order\s*#?(\d+)', text, re.IGNORECASE)
            if match and match.group(1) == clean_num:
                return msg.get("ts")
                
    except Exception:
        pass
    
    return None

def post_slack_reply(thread_ts, text):
    """Post reply to Slack thread"""
    if not SLACK_BOT_TOKEN:
        return False
    
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    payload = {
        "channel": SHOPIFY_SLACK_CHANNEL_ID,
        "thread_ts": thread_ts,
        "text": text
    }
    
    try:
        resp = requests.post("https://slack.com/api/chat.postMessage", 
                           headers=headers, json=payload, timeout=10)
        return resp.json().get("ok", False)
    except Exception:
        return False

def create_status_message(status_type, status):
    """Create status message text"""
    
    status_map = {
        'payment': {
            'payment pending': '⏳ Payment Pending',
            'paid': '✅ Payment Paid',
            'refunded': '↩️ Payment Refunded',
            'voided': '❌ Payment Voided',
            'authorized': '🔒 Payment Authorized',
        },
        'fulfillment': {
            'fulfilled': '🚀 Fulfilled',
            'partially fulfilled': '📤 Partially Fulfilled',
            'on hold': '⏸️ On Hold',
            'in progress': '⚙️ In Progress',
            'unfulfilled': '📦 Unfulfilled',
        }
    }
    
    status_lower = str(status).lower()
    status_text = status_map[status_type].get(status_lower, f'📝 {status}')
    
    time_now = datetime.now().strftime("%I:%M %p")
    return f"{status_text} • {time_now}"

@app.route('/webhook/shopify', methods=['POST'])
def shopify_webhook():
    """Handle Shopify webhooks"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data'}), 400
        
        # Get order data
        order_data = data.get('order') or data
        
        # Get order number
        order_number = order_data.get('name') or order_data.get('order_number') or order_data.get('id')
        if not order_number:
            return jsonify({'error': 'No order number'}), 400
        
        clean_order = str(order_number).replace("#", "").strip()
        
        # Get statuses
        financial_status = order_data.get('financial_status', '').lower()
        fulfillment_status = order_data.get('fulfillment_status', '').lower()
        
        # Map to our status names
        payment_map = {
            'pending': 'payment pending',
            'paid': 'paid',
            'refunded': 'refunded',
            'voided': 'voided',
            'authorized': 'authorized',
        }
        
        fulfillment_map = {
            'fulfilled': 'fulfilled',
            'partially_fulfilled': 'partially fulfilled',
            'partially fulfilled': 'partially fulfilled',
            'partial': 'partially fulfilled',
            'on_hold': 'on hold',
            'on hold': 'on hold',
            'in_progress': 'in progress',
            'in progress': 'in progress',
            'unfulfilled': 'unfulfilled',
        }
        
        payment_status = payment_map.get(financial_status, financial_status) if financial_status else None
        fulfillment_status_mapped = fulfillment_map.get(fulfillment_status, fulfillment_status) if fulfillment_status else None
        
        # Find Slack message
        if clean_order not in order_threads:
            thread_ts = find_order_message(clean_order)
            if not thread_ts:
                return jsonify({'ok': False, 'message': 'Order not found in Slack'}), 202
            order_threads[clean_order] = {'ts': thread_ts, 'last_payment': None, 'last_fulfillment': None}
        
        mapping = order_threads[clean_order]
        
        # Check and post payment update
        if payment_status and payment_status != mapping.get('last_payment'):
            message = create_status_message('payment', payment_status)
            if post_slack_reply(mapping['ts'], message):
                mapping['last_payment'] = payment_status
        
        # Check and post fulfillment update
        if fulfillment_status_mapped and fulfillment_status_mapped != mapping.get('last_fulfillment'):
            message = create_status_message('fulfillment', fulfillment_status_mapped)
            if post_slack_reply(mapping['ts'], message):
                mapping['last_fulfillment'] = fulfillment_status_mapped
        
        return jsonify({'ok': True, 'order': clean_order}), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/test/<order_number>', methods=['GET'])
def test_order(order_number):
    """Test if order exists"""
    ts = find_order_message(order_number)
    if ts:
        return jsonify({'found': True, 'order': order_number}), 200
    return jsonify({'found': False, 'order': order_number}), 404

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'ok',
        'orders_tracked': len(order_threads),
        'channel': SHOPIFY_SLACK_CHANNEL_ID
    }), 200

@app.route('/', methods=['GET'])
def home():
    return """
    <h2>Shopify Slack Status</h2>
    <p>Posts order status updates to Slack thread</p>
    <p>Orders tracked: {}</p>
    <p><a href="/health">Health</a> | <a href="/test/1278">Test Order 1278</a></p>
    """.format(len(order_threads))

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)