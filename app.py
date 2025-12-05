import os
import requests
from flask import Flask, request, jsonify
from datetime import datetime
import re

app = Flask(__name__)

SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN')

# List of channels to search for orders
CHANNELS_TO_SEARCH = [
    'C0A02M2VCTB',  # "order" channel ID
    'C0A068PHZMY'   # "shopify-slack" channel ID
]

if not SLACK_BOT_TOKEN:
    print("⚠️ WARNING: SLACK_BOT_TOKEN not configured")

# Store: {order_number: {'ts': 'thread_timestamp', 'channel': 'channel_id', 'payment': 'status', 'fulfillment': 'status'}}
order_tracking = {}

def find_order_message_in_channels(order_number):
    """Search for order message in ALL specified channels"""
    if not SLACK_BOT_TOKEN:
        return None, None
    
    clean_num = str(order_number).replace("#", "").strip()
    
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    
    for channel_id in CHANNELS_TO_SEARCH:
        params = {"channel": channel_id, "limit": 50}
        
        try:
            resp = requests.get("https://slack.com/api/conversations.history", 
                              headers=headers, params=params, timeout=10)
            data = resp.json()
            
            if data.get("ok"):
                for msg in data.get("messages", []):
                    text = msg.get("text", "")
                    # Look for order number in various formats
                    if check_if_order_message(text, clean_num):
                        print(f"✅ Found order #{order_number} in channel {channel_id}")
                        return msg.get("ts"), channel_id
                        
        except Exception as e:
            print(f"Error searching channel {channel_id}: {e}")
    
    print(f"❌ Order #{order_number} not found in any channel")
    return None, None

def check_if_order_message(text, order_number):
    """Check if message contains the order number"""
    if not text:
        return False
    
    text_lower = text.lower()
    
    # Pattern 1: ST.order #1234 (from shopify-slack channel)
    match = re.search(r'st\.order\s*#?(\d+)', text_lower)
    if match and match.group(1) == order_number:
        return True
    
    # Pattern 2: Just the order number (for order channel messages)
    if f"#{order_number}" in text or f"order #{order_number}" in text_lower:
        return True
    
    # Pattern 3: Order number mentioned in various formats
    patterns = [
        rf'order.*#{order_number}',
        rf'order.*{order_number}',
        rf'#{order_number}.*order',
        rf'\b{order_number}\b.*order',
        rf'order.*\b{order_number}\b'
    ]
    
    for pattern in patterns:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return True
    
    return False

def post_to_slack(channel_id, thread_ts, text):
    """Post message to Slack thread in specific channel"""
    if not SLACK_BOT_TOKEN:
        return False
    
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    payload = {
        "channel": channel_id,
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
    """Handle Shopify webhooks"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data'}), 400
        
        # Get webhook topic
        topic = request.headers.get('X-Shopify-Topic', '').lower()
        print(f"📋 Webhook topic: {topic}")
        
        # Get order data
        order_data = data.get('order') or data
        
        # Get order number
        order_number = order_data.get('name') or order_data.get('order_number')
        if not order_number:
            order_number = order_data.get('id')
        
        if not order_number:
            print("❌ No order number found")
            return jsonify({'error': 'No order number'}), 400
        
        # Clean order number
        clean_order = str(order_number).replace("#", "").strip()
        print(f"🔢 Processing order: #{clean_order}")
        
        # Get statuses
        financial_status = order_data.get('financial_status')
        fulfillment_status = order_data.get('fulfillment_status')
        
        print(f"💰 Financial: {financial_status}, 📦 Fulfillment: {fulfillment_status}")
        
        # Normalize statuses
        payment_status = normalize_status(financial_status, 'payment') if financial_status else None
        fulfillment_status_norm = normalize_status(fulfillment_status, 'fulfillment') if fulfillment_status else None
        
        print(f"🔄 Normalized - Payment: {payment_status}, Fulfillment: {fulfillment_status_norm}")
        
        # Check if we already have this order tracked
        if clean_order in order_tracking:
            tracking = order_tracking[clean_order]
            print(f"📊 Found existing tracking for order #{clean_order} in channel {tracking.get('channel')}")
        else:
            # Search for order in all channels
            print(f"🔍 Searching for order #{clean_order} in all channels...")
            thread_ts, found_channel = find_order_message_in_channels(clean_order)
            
            if not thread_ts:
                print(f"❌ Order #{clean_order} not found in any channel")
                return jsonify({'ok': False, 'message': f'Order #{clean_order} not found in Slack'}), 202
            
            print(f"✅ Found order #{clean_order} in channel {found_channel}")
            order_tracking[clean_order] = {
                'ts': thread_ts,
                'channel': found_channel,
                'payment': None,
                'fulfillment': None
            }
            tracking = order_tracking[clean_order]
        
        time_now = datetime.now().strftime("%I:%M %p")
        
        # Debug current state
        print(f"📝 Current tracking - Channel: {tracking.get('channel')}, Payment: {tracking.get('payment')}, Fulfillment: {tracking.get('fulfillment')}")
        
        # Handle payment update
        if payment_status and payment_status != tracking.get('payment'):
            print(f"🔄 Payment status changed: {tracking.get('payment')} -> {payment_status}")
            status_text = get_payment_message(payment_status)
            message = f"{status_text} • {time_now}"
            
            print(f"📤 Posting payment update to channel {tracking['channel']}: {message}")
            if post_to_slack(tracking['channel'], tracking['ts'], message):
                tracking['payment'] = payment_status
                print(f"✅ Payment update posted successfully")
            else:
                print(f"❌ Failed to post payment update")
        
        # Handle fulfillment update
        if fulfillment_status_norm and fulfillment_status_norm != tracking.get('fulfillment'):
            print(f"🔄 Fulfillment status changed: {tracking.get('fulfillment')} -> {fulfillment_status_norm}")
            status_text = get_fulfillment_message(fulfillment_status_norm)
            message = f"{status_text} • {time_now}"
            
            print(f"📤 Posting fulfillment update to channel {tracking['channel']}: {message}")
            if post_to_slack(tracking['channel'], tracking['ts'], message):
                tracking['fulfillment'] = fulfillment_status_norm
                print(f"✅ Fulfillment update posted successfully")
            else:
                print(f"❌ Failed to post fulfillment update")
        
        return jsonify({'ok': True, 'order': clean_order, 'channel': tracking.get('channel')}), 200
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/find-order/<order_number>', methods=['GET'])
def find_order(order_number):
    """Find an order in any channel"""
    clean_order = str(order_number).replace("#", "").strip()
    thread_ts, channel_id = find_order_message_in_channels(clean_order)
    
    if thread_ts:
        return jsonify({
            'found': True,
            'order': clean_order,
            'channel': channel_id,
            'thread_ts': thread_ts
        }), 200
    else:
        return jsonify({
            'found': False,
            'order': clean_order,
            'message': 'Not found in any channel'
        }), 404

@app.route('/test-update/<order_number>/<status_type>/<status>', methods=['GET'])
def test_update(order_number, status_type, status):
    """Test update for specific order"""
    clean_order = str(order_number).replace("#", "").strip()
    
    if clean_order in order_tracking:
        tracking = order_tracking[clean_order]
    else:
        thread_ts, channel_id = find_order_message_in_channels(clean_order)
        if not thread_ts:
            return jsonify({'error': 'Order not found'}), 404
        
        order_tracking[clean_order] = {
            'ts': thread_ts,
            'channel': channel_id,
            'payment': None,
            'fulfillment': None
        }
        tracking = order_tracking[clean_order]
    
    time_now = datetime.now().strftime("%I:%M %p")
    
    if status_type == 'payment':
        status_text = get_payment_message(status)
        tracking['payment'] = status
    else:
        status_text = get_fulfillment_message(status)
        tracking['fulfillment'] = status
    
    message = f"{status_text} • {time_now}"
    success = post_to_slack(tracking['channel'], tracking['ts'], message)
    
    return jsonify({
        'ok': success,
        'order': clean_order,
        'channel': tracking['channel'],
        'message': message,
        'status_type': status_type,
        'status': status
    })

@app.route('/tracked-orders', methods=['GET'])
def tracked_orders():
    """Show all tracked orders"""
    return jsonify({
        'total_orders': len(order_tracking),
        'orders': order_tracking
    }), 200

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'ok',
        'orders_tracked': len(order_tracking),
        'channels_searched': CHANNELS_TO_SEARCH,
        'channels_count': len(CHANNELS_TO_SEARCH)
    }), 200

@app.route('/', methods=['GET'])
def home():
    return f"""
    <h2>🛍️ Shopify Status Bot - MULTI-CHANNEL</h2>
    
    <div style="background: #f0f8ff; padding: 20px; border-radius: 10px; margin: 20px 0;">
        <h3>📍 Working in BOTH channels:</h3>
        <ul>
            <li><strong>order channel</strong> (C0A02M2VCTB) - Original notifications</li>
            <li><strong>shopify-slack channel</strong> (C0A068PHZMY) - Copy-pasted notifications</li>
        </ul>
    </div>
    
    <p><strong>📊 Orders currently tracked:</strong> {len(order_tracking)}</p>
    
    <h3>🔧 Tools:</h3>
    <ul>
        <li><a href="/health">Health Check</a></li>
        <li><a href="/tracked-orders">All Tracked Orders</a></li>
        <li><a href="/find-order/1281">Find Order 1281</a></li>
        <li><a href="/test-update/1281/payment/paid">Test Payment Update</a></li>
        <li><a href="/test-update/1281/fulfillment/fulfilled">Test Fulfillment Update</a></li>
    </ul>
    
    <h3>🎯 How it works:</h3>
    <ol>
        <li>When Shopify sends a webhook, bot searches BOTH channels for the order</li>
        <li>Finds the order message (wherever it exists)</li>
        <li>Posts status update in thread of that message</li>
        <li>Remembers which channel each order is in</li>
    </ol>
    """

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)