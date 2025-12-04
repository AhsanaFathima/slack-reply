import os
import requests
from flask import Flask, request, jsonify
from datetime import datetime
import re

app = Flask(__name__)

SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN')
SHOPIFY_SLACK_CHANNEL_ID = os.getenv('SHOPIFY_SLACK_CHANNEL_ID', 'C0A068PHZMY')

# Store: {order_number: {'ts': 'thread_timestamp', 'payment': 'status', 'fulfillment': 'status'}}
order_tracking = {}

def find_order_message(order_number):
    """Find order message in Slack channel"""
    if not SLACK_BOT_TOKEN:
        return None
    
    clean_num = str(order_number).replace("#", "").strip()
    
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    params = {"channel": SHOPIFY_SLACK_CHANNEL_ID, "limit": 50}
    
    try:
        resp = requests.get("https://slack.com/api/conversations.history", 
                          headers=headers, params=params, timeout=10)
        data = resp.json()
        
        if data.get("ok"):
            for msg in data.get("messages", []):
                text = msg.get("text", "")
                # Look for order number
                match = re.search(r'ST\.order\s*#?(\d+)', text, re.IGNORECASE)
                if match and match.group(1) == clean_num:
                    return msg.get("ts")
    except Exception:
        pass
    
    return None

def post_to_slack(thread_ts, text):
    """Post message to Slack thread"""
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

def get_payment_message(status):
    """Create payment status message"""
    status_map = {
        'payment pending': '⏳ Payment Pending',
        'paid': '✅ Payment Paid',
        'refunded': '↩️ Payment Refunded',
        'voided': '❌ Payment Voided',
        'authorized': '🔒 Payment Authorized',
    }
    status_lower = status.lower() if status else ''
    return status_map.get(status_lower, f'💳 {status}')

def get_fulfillment_message(status):
    """Create fulfillment status message"""
    status_map = {
        'fulfilled': '🚀 Fulfilled',
        'partially fulfilled': '📤 Partially Fulfilled',
        'on hold': '⏸️ On Hold',
        'in progress': '⚙️ In Progress',
        'unfulfilled': '📦 Unfulfilled',
    }
    status_lower = status.lower() if status else ''
    return status_map.get(status_lower, f'📦 {status}')

def normalize_status(status, status_type='payment'):
    """Normalize Shopify status to our format"""
    if not status:
        return None
    
    status_lower = status.lower()
    
    if status_type == 'payment':
        if status_lower in ['pending']:
            return 'payment pending'
        elif status_lower in ['paid', 'partially_paid']:
            return 'paid'
        elif status_lower == 'refunded':
            return 'refunded'
        elif status_lower == 'voided':
            return 'voided'
        elif status_lower == 'authorized':
            return 'authorized'
    else:  # fulfillment
        if status_lower == 'fulfilled':
            return 'fulfilled'
        elif status_lower in ['partially_fulfilled', 'partial']:
            return 'partially fulfilled'
        elif status_lower in ['on_hold', 'on hold']:
            return 'on hold'
        elif status_lower in ['in_progress', 'in progress']:
            return 'in progress'
        elif status_lower == 'unfulfilled':
            return 'unfulfilled'
    
    return status_lower

@app.route('/webhook/shopify', methods=['POST'])
def shopify_webhook():
    """Handle Shopify webhooks - SIMPLE VERSION"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data'}), 400
        
        # Get webhook topic
        topic = request.headers.get('X-Shopify-Topic', '').lower()
        print(f"Webhook topic: {topic}")
        
        # Get order data
        order_data = data.get('order') or data
        
        # Get order number
        order_number = order_data.get('name') or order_data.get('order_number')
        if not order_number:
            order_number = order_data.get('id')
        
        if not order_number:
            print("No order number found")
            return jsonify({'error': 'No order number'}), 400
        
        # Clean order number
        clean_order = str(order_number).replace("#", "").strip()
        print(f"Processing order: #{clean_order}")
        
        # Get statuses
        financial_status = order_data.get('financial_status')
        fulfillment_status = order_data.get('fulfillment_status')
        
        print(f"Financial: {financial_status}, Fulfillment: {fulfillment_status}")
        
        # Normalize statuses
        payment_status = normalize_status(financial_status, 'payment') if financial_status else None
        fulfillment_status_norm = normalize_status(fulfillment_status, 'fulfillment') if fulfillment_status else None
        
        print(f"Normalized - Payment: {payment_status}, Fulfillment: {fulfillment_status_norm}")
        
        # Find or create tracking
        if clean_order not in order_tracking:
            print(f"Looking for order #{clean_order} in Slack...")
            thread_ts = find_order_message(clean_order)
            if not thread_ts:
                print(f"Order #{clean_order} not found in Slack")
                return jsonify({'ok': False, 'message': 'Order not found in Slack'}), 202
            
            print(f"Found order #{clean_order} at timestamp: {thread_ts}")
            order_tracking[clean_order] = {
                'ts': thread_ts,
                'payment': None,
                'fulfillment': None
            }
        
        tracking = order_tracking[clean_order]
        time_now = datetime.now().strftime("%I:%M %p")
        
        # Debug current state
        print(f"Current tracking - Payment: {tracking.get('payment')}, Fulfillment: {tracking.get('fulfillment')}")
        
        # Handle payment update
        if payment_status and payment_status != tracking.get('payment'):
            print(f"Payment status changed: {tracking.get('payment')} -> {payment_status}")
            status_text = get_payment_message(payment_status)
            message = f"{status_text} • {time_now}"
            
            print(f"Posting payment update: {message}")
            if post_to_slack(tracking['ts'], message):
                tracking['payment'] = payment_status
                print(f"Payment update posted successfully")
            else:
                print(f"Failed to post payment update")
        
        # Handle fulfillment update
        if fulfillment_status_norm and fulfillment_status_norm != tracking.get('fulfillment'):
            print(f"Fulfillment status changed: {tracking.get('fulfillment')} -> {fulfillment_status_norm}")
            status_text = get_fulfillment_message(fulfillment_status_norm)
            message = f"{status_text} • {time_now}"
            
            print(f"Posting fulfillment update: {message}")
            if post_to_slack(tracking['ts'], message):
                tracking['fulfillment'] = fulfillment_status_norm
                print(f"Fulfillment update posted successfully")
            else:
                print(f"Failed to post fulfillment update")
        
        return jsonify({'ok': True, 'order': clean_order}), 200
        
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/force-update/<order_number>/<status_type>/<status>', methods=['GET'])
def force_update(order_number, status_type, status):
    """Force update for testing"""
    clean_order = str(order_number).replace("#", "").strip()
    
    if clean_order not in order_tracking:
        thread_ts = find_order_message(clean_order)
        if not thread_ts:
            return jsonify({'error': 'Order not found'}), 404
        order_tracking[clean_order] = {'ts': thread_ts, 'payment': None, 'fulfillment': None}
    
    tracking = order_tracking[clean_order]
    time_now = datetime.now().strftime("%I:%M %p")
    
    if status_type == 'payment':
        status_text = get_payment_message(status)
        tracking['payment'] = status
    else:
        status_text = get_fulfillment_message(status)
        tracking['fulfillment'] = status
    
    message = f"{status_text} • {time_now}"
    success = post_to_slack(tracking['ts'], message)
    
    return jsonify({
        'ok': success,
        'order': clean_order,
        'message': message,
        'status_type': status_type,
        'status': status
    })

@app.route('/debug/<order_number>', methods=['GET'])
def debug_order(order_number):
    """Debug order tracking"""
    clean_order = str(order_number).replace("#", "").strip()
    
    if clean_order in order_tracking:
        return jsonify({
            'found': True,
            'order': clean_order,
            'tracking': order_tracking[clean_order]
        })
    
    # Try to find in Slack
    thread_ts = find_order_message(clean_order)
    if thread_ts:
        return jsonify({
            'found_in_slack': True,
            'order': clean_order,
            'thread_ts': thread_ts,
            'tracking': 'Not yet tracked'
        })
    
    return jsonify({
        'found': False,
        'order': clean_order,
        'message': 'Not found in Slack or tracking'
    })

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'ok',
        'orders_tracked': len(order_tracking),
        'channel': SHOPIFY_SLACK_CHANNEL_ID
    }), 200

@app.route('/', methods=['GET'])
def home():
    return f"""
    <h2>Shopify Status Bot - DEBUG VERSION</h2>
    <p><strong>Orders tracked:</strong> {len(order_tracking)}</p>
    <p><strong>Channel:</strong> {SHOPIFY_SLACK_CHANNEL_ID}</p>
    <p><strong>Issue:</strong> Payment updates not posting</p>
    
    <h3>Debug Tools:</h3>
    <ul>
        <li><a href="/health">Health Check</a></li>
        <li><a href="/debug/1278">Debug Order 1278</a></li>
        <li><a href="/force-update/1278/payment/paid">Force Payment Update</a></li>
        <li><a href="/force-update/1278/fulfillment/fulfilled">Force Fulfillment Update</a></li>
    </ul>
    
    <h3>Current Tracking:</h3>
    <pre>{order_tracking}</pre>
    """

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)