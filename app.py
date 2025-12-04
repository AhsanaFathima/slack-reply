# app.py (FIXED - no formatting error)
import os
import requests
from flask import Flask, request, jsonify
from datetime import datetime
import re

app = Flask(__name__)

# Configuration
SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN')  # xoxb-...
SHOPIFY_SLACK_CHANNEL_ID = os.getenv('SHOPIFY_SLACK_CHANNEL_ID', 'C0A068PHZMY')  # shopify-slack channel

if not SLACK_BOT_TOKEN:
    app.logger.warning("No SLACK_BOT_TOKEN configured. Set SLACK_BOT_TOKEN in environment variables.")

# In-memory store for order -> thread mapping
order_threads = {}

def extract_order_number_from_text(text):
    """
    Extract order number from your specific format:
    "ST.order #1278 | test test | +971 54 598 2212 | Abdul Samad ..."
    """
    if not text:
        return None
    
    # Pattern 1: ST.order #1278
    match = re.search(r'ST\.order\s*#?(\d+)', text, re.IGNORECASE)
    if match:
        return match.group(1)
    
    # Pattern 2: Just look for # followed by numbers
    match = re.search(r'#(\d+)', text)
    if match:
        return match.group(1)
    
    # Pattern 3: Look for 4+ digit number (common for order numbers)
    match = re.search(r'\b(\d{4,})\b', text)
    if match:
        return match.group(1)
    
    return None

def find_order_message_in_channel(order_number):
    """Search for order message in shopify-slack channel"""
    if not SLACK_BOT_TOKEN:
        app.logger.error("Cannot search Slack: SLACK_BOT_TOKEN not set")
        return None

    url = "https://slack.com/api/conversations.history"
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    params = {
        "channel": SHOPIFY_SLACK_CHANNEL_ID,
        "limit": 100  # Check recent 100 messages
    }

    try:
        app.logger.info(f"🔍 Searching for order #{order_number} in shopify-slack channel")
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        data = resp.json()
        
        if not data.get("ok"):
            app.logger.error(f"❌ Slack API error: {data.get('error')}")
            return None

        messages = data.get("messages", [])
        app.logger.info(f"📨 Found {len(messages)} messages in channel")
        
        # Clean order number (remove #)
        clean_order_num = str(order_number).replace("#", "").strip()
        
        for msg in messages:
            raw_text = msg.get("text", "") or ""
            
            # Skip messages that are already in threads (replies)
            if msg.get("thread_ts") and msg.get("thread_ts") != msg.get("ts"):
                continue
            
            # Extract order number from message text
            extracted_num = extract_order_number_from_text(raw_text)
            
            if extracted_num and extracted_num == clean_order_num:
                app.logger.info(f"✅ Found message for order #{order_number} at timestamp: {msg.get('ts')}")
                app.logger.info(f"📝 Message preview: {raw_text[:100]}...")
                return msg.get("ts")
        
        app.logger.warning(f"⚠️ Order #{order_number} not found in shopify-slack channel")
        return None

    except Exception as e:
        app.logger.exception("❌ Error searching for order message: %s", str(e))
        return None

def save_order_mapping(order_number, thread_ts, last_payment=None, last_fulfillment=None):
    """Save order mapping to memory"""
    order_threads[str(order_number)] = {
        "ts": thread_ts,
        "channel": SHOPIFY_SLACK_CHANNEL_ID,
        "last_payment": last_payment,
        "last_fulfillment": last_fulfillment
    }
    app.logger.info(f"💾 Saved mapping for order #{order_number}")

def get_order_mapping(order_number):
    """Get order mapping from memory"""
    return order_threads.get(str(order_number))

def post_thread_reply(thread_ts, text):
    """Post a reply in thread"""
    if not SLACK_BOT_TOKEN:
        app.logger.error("❌ No SLACK_BOT_TOKEN configured")
        return False

    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "channel": SHOPIFY_SLACK_CHANNEL_ID,
        "thread_ts": thread_ts,
        "text": text
    }
    
    try:
        app.logger.info(f"📤 Posting thread reply...")
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        data = resp.json()
        
        if not data.get("ok"):
            app.logger.error(f"❌ Slack API error: {data.get('error')}")
            return False
        
        app.logger.info("✅ Thread reply posted successfully")
        return True
    except Exception as e:
        app.logger.exception(f"❌ Error posting thread message: {str(e)}")
        return False

def create_status_update(status_type, status, details=None):
    """Create formatted status update text"""
    payment_status_map = {
        'paid': {'emoji': '✅', 'text': 'Payment Paid'},
        'pending': {'emoji': '⏳', 'text': 'Payment Pending'},
        'payment pending': {'emoji': '⏳', 'text': 'Payment Pending'},
        'authorized': {'emoji': '🔒', 'text': 'Payment Authorized'},
        'refunded': {'emoji': '↩️', 'text': 'Payment Refunded'},
        'voided': {'emoji': '❌', 'text': 'Payment Voided'},
        'partially_paid': {'emoji': '💰', 'text': 'Partially Paid'},
    }

    fulfillment_status_map = {
        'fulfilled': {'emoji': '🚀', 'text': 'Fulfilled'},
        'unfulfilled': {'emoji': '📦', 'text': 'Unfulfilled'},
        'partially_fulfilled': {'emoji': '📤', 'text': 'Partially Fulfilled'},
        'partially fulfilled': {'emoji': '📤', 'text': 'Partially Fulfilled'},
        'in_progress': {'emoji': '⚙️', 'text': 'In Progress'},
        'on_hold': {'emoji': '⏸️', 'text': 'On Hold'},
        'scheduled': {'emoji': '📅', 'text': 'Scheduled'},
    }

    if status_type == 'payment':
        status_map = payment_status_map
        prefix = '💳 *Payment Status:*'
    else:  # fulfillment
        status_map = fulfillment_status_map
        prefix = '📦 *Fulfillment Status:*'

    if status:
        s = str(status).lower()
        cfg = status_map.get(s, {'emoji': '📝', 'text': s.title()})
    else:
        cfg = {'emoji': '❓', 'text': 'Unknown'}

    time_now = datetime.now().strftime("%I:%M %p")
    text = f"{prefix} {cfg['emoji']} **{cfg['text']}** • {time_now}"
    
    if details:
        for key, value in details.items():
            if value:
                text += f"\n• {key}: {value}"
    
    return text

@app.route('/webhook/shopify', methods=['POST'])
def shopify_webhook():
    """Handle Shopify webhooks"""
    app.logger.info("🛍️ Shopify webhook received")
    
    # Get webhook topic
    webhook_topic = request.headers.get('X-Shopify-Topic', '').lower()
    app.logger.info(f"📋 Webhook topic: {webhook_topic}")
    
    try:
        data = request.get_json()
        if not data:
            app.logger.error("❌ No JSON data received")
            return jsonify({'error': 'No data received'}), 400

        # LOG ALL DATA FOR DEBUGGING (remove in production)
        app.logger.info(f"📦 Webhook data received")
        app.logger.info(f"📊 Data keys: {list(data.keys())}")
        
        # Extract order information
        order_data = data.get('order') or data
        
        # Get order number - Shopify sends it in 'name' field (like #1278)
        order_number = None
        
        # Method 1: Check 'name' field (usually contains #1278)
        if 'name' in order_data:
            order_number = order_data.get('name')
            app.logger.info(f"📝 Found order number in 'name': {order_number}")
        
        # Method 2: Check 'order_number' field
        if not order_number and 'order_number' in order_data:
            order_number = order_data.get('order_number')
            app.logger.info(f"📝 Found order number in 'order_number': {order_number}")
        
        # Method 3: Check 'id' as fallback
        if not order_number and 'id' in order_data:
            order_number = order_data.get('id')
            app.logger.info(f"📝 Using order 'id' as number: {order_number}")
        
        if not order_number:
            app.logger.error("❌ Could not extract order number")
            return jsonify({'error': 'No order number found'}), 400
        
        # Clean order number (remove #)
        clean_order_number = str(order_number).replace("#", "").strip()
        app.logger.info(f"🔢 Processing order: #{clean_order_number}")
        
        # Get statuses
        financial_status = order_data.get('financial_status', '')
        fulfillment_status = order_data.get('fulfillment_status', '')
        
        app.logger.info(f"💰 Financial status: {financial_status}")
        app.logger.info(f"📦 Fulfillment status: {fulfillment_status}")
        
        # Normalize statuses
        status_mapping = {
            'pending': 'payment pending',
            'partially_fulfilled': 'partially fulfilled',
            'authorized': 'authorized',
            'paid': 'paid',
            'refunded': 'refunded',
            'voided': 'voided',
            'fulfilled': 'fulfilled',
            'unfulfilled': 'unfulfilled',
        }
        
        payment_status = status_mapping.get(financial_status.lower(), financial_status) if financial_status else None
        fulfillment_status_mapped = status_mapping.get(fulfillment_status.lower(), fulfillment_status) if fulfillment_status else None
        
        app.logger.info(f"🔄 Mapped - Payment: {payment_status}, Fulfillment: {fulfillment_status_mapped}")
        
        # Check if we have mapping for this order
        mapping = get_order_mapping(clean_order_number)
        
        if not mapping:
            # Try to find the message in channel
            app.logger.info(f"🔍 Looking for order #{clean_order_number} in slack channel...")
            thread_ts = find_order_message_in_channel(clean_order_number)
            
            if thread_ts:
                # Save new mapping
                save_order_mapping(clean_order_number, thread_ts)
                mapping = get_order_mapping(clean_order_number)
                app.logger.info(f"✅ Created new mapping for order #{clean_order_number}")
            else:
                app.logger.warning(f"⚠️ No Slack message found for order #{clean_order_number}")
                return jsonify({
                    'ok': False, 
                    'message': f'No Slack message found for order #{clean_order_number}. Please copy-paste it to shopify-slack channel first.'
                }), 202
        
        # Track if any updates were posted
        updates_posted = False
        
        # Handle payment status update
        if payment_status and payment_status != 'unknown':
            current_payment = mapping.get('last_payment')
            
            if payment_status != current_payment:
                app.logger.info(f"🔄 Payment status changed: {current_payment} -> {payment_status}")
                
                # Create payment update message
                details = {}
                gateway = order_data.get('gateway')
                if gateway:
                    details['Gateway'] = gateway
                
                payment_text = create_status_update('payment', payment_status, details)
                
                # Post to thread
                if post_thread_reply(mapping['ts'], payment_text):
                    # Update mapping
                    mapping['last_payment'] = payment_status
                    save_order_mapping(clean_order_number, mapping['ts'], 
                                     last_payment=payment_status,
                                     last_fulfillment=mapping.get('last_fulfillment'))
                    updates_posted = True
                    app.logger.info(f"✅ Posted payment update for order #{clean_order_number}")
                else:
                    app.logger.error(f"❌ Failed to post payment update for order #{clean_order_number}")
        
        # Handle fulfillment status update
        if fulfillment_status_mapped and fulfillment_status_mapped != 'unknown':
            current_fulfillment = mapping.get('last_fulfillment')
            
            if fulfillment_status_mapped != current_fulfillment:
                app.logger.info(f"🔄 Fulfillment status changed: {current_fulfillment} -> {fulfillment_status_mapped}")
                
                # Create fulfillment update message
                details = {}
                
                # Check for tracking info
                tracking_numbers = []
                if order_data.get('tracking_numbers'):
                    tracking_numbers = order_data.get('tracking_numbers', [])
                elif order_data.get('tracking_number'):
                    tracking_numbers = [order_data.get('tracking_number')]
                
                if tracking_numbers:
                    details['Tracking'] = ', '.join([str(t) for t in tracking_numbers if t])
                
                tracking_company = order_data.get('tracking_company')
                if tracking_company:
                    details['Carrier'] = tracking_company
                
                fulfillment_text = create_status_update('fulfillment', fulfillment_status_mapped, details)
                
                # Post to thread
                if post_thread_reply(mapping['ts'], fulfillment_text):
                    # Update mapping
                    mapping['last_fulfillment'] = fulfillment_status_mapped
                    save_order_mapping(clean_order_number, mapping['ts'],
                                     last_payment=mapping.get('last_payment'),
                                     last_fulfillment=fulfillment_status_mapped)
                    updates_posted = True
                    app.logger.info(f"✅ Posted fulfillment update for order #{clean_order_number}")
                else:
                    app.logger.error(f"❌ Failed to post fulfillment update for order #{clean_order_number}")
        
        if updates_posted:
            return jsonify({
                'ok': True, 
                'order': clean_order_number,
                'message': 'Status updates posted to Slack thread'
            }), 200
        else:
            return jsonify({
                'ok': True,
                'order': clean_order_number,
                'message': 'No status changes detected'
            }), 200

    except Exception as e:
        app.logger.exception("❌ ERROR in webhook handler: %s", str(e))
        return jsonify({'error': str(e)}), 500

@app.route('/test/<order_number>', methods=['GET'])
def test_order_search(order_number):
    """Test if order exists in channel"""
    ts = find_order_message_in_channel(order_number)
    if ts:
        return jsonify({
            'found': True,
            'order': order_number,
            'message': 'Order found in shopify-slack channel',
            'timestamp': ts
        }), 200
    else:
        return jsonify({
            'found': False,
            'order': order_number,
            'message': 'Order not found. Please copy-paste it to shopify-slack channel.'
        }), 404

@app.route('/webhook/test', methods=['POST'])
def test_webhook():
    """Test webhook endpoint (for debugging)"""
    app.logger.info("🧪 Test webhook received")
    
    # Simulate a Shopify webhook
    test_data = {
        'order': {
            'name': '#1278',
            'order_number': 1278,
            'financial_status': 'paid',
            'fulfillment_status': 'unfulfilled',
            'gateway': 'Cash on Delivery (COD)',
            'tracking_numbers': ['TRK123456789'],
            'tracking_company': 'Aramex'
        }
    }
    
    return jsonify({
        'message': 'Test webhook processed',
        'data': test_data,
        'instructions': 'Copy "ST.order #1278 | test test | +971 54 598 2212 | ..." to shopify-slack channel'
    }), 200

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'healthy',
        'service': 'Shopify-Slack Status Updater',
        'orders_tracked': len(order_threads),
        'channel': 'shopify-slack',
        'channel_id': SHOPIFY_SLACK_CHANNEL_ID
    }), 200

@app.route('/', methods=['GET'])
def home():
    """Simple home page without formatting issues"""
    return f"""
    <html>
    <head>
        <title>Shopify → Slack Status Updater</title>
        <style>
            body {{ font-family: Arial, sans-serif; padding: 30px; max-width: 800px; margin: 0 auto; }}
            .card {{ background: #f8f9fa; padding: 25px; border-radius: 10px; margin: 20px 0; border-left: 5px solid #4CAF50; }}
            .success {{ color: #4CAF50; }}
            .warning {{ color: #ff9800; }}
            .error {{ color: #f44336; }}
            .emoji {{ font-size: 24px; }}
            code {{ background: #e8e8e8; padding: 2px 6px; border-radius: 3px; }}
        </style>
    </head>
    <body>
        <h1>🛍️ Shopify → Slack Status Updater</h1>
        
        <div class="card">
            <h2>📍 Working ONLY in: <code>shopify-slack</code> channel</h2>
            <p><strong>Channel ID:</strong> <code>{SHOPIFY_SLACK_CHANNEL_ID}</code></p>
        </div>
        
        <div class="card">
            <h2>📋 How It Works</h2>
            <ol>
                <li>Copy order notifications from <code>order</code> channel to <code>shopify-slack</code> channel</li>
                <li>Format must be: <code>ST.order #1278 | test test | +971 54 598 2212 | ...</code></li>
                <li>When Shopify sends status updates via webhook, this app finds the message</li>
                <li>Posts updates as thread replies</li>
            </ol>
        </div>
        
        <div class="card">
            <h2>📊 Current Status</h2>
            <p class="emoji">✅ <strong>Service is running</strong></p>
            <p>📈 <strong>Orders being tracked:</strong> {len(order_threads)}</p>
            <p>🔗 <strong>Shopify Webhooks:</strong> Configured for status updates</p>
        </div>
        
        <div class="card">
            <h2>🔧 Tools & Testing</h2>
            <p><a href="/health">📊 Health Check</a></p>
            <p><a href="/test/1278">🧪 Test Order 1278</a> - Check if order #1278 exists in channel</p>
            <p><a href="/webhook/test">🛠️ Test Webhook</a> - Simulate a Shopify webhook</p>
        </div>
        
        <div class="card">
            <h2>⚙️ Setup Checklist</h2>
            <ul>
                <li>✅ Bot added to <code>shopify-slack</code> channel</li>
                <li>✅ Shopify webhooks configured</li>
                <li>✅ Render environment variables set</li>
                <li>📝 Copy-paste order notifications to test</li>
            </ul>
        </div>
    </body>
    </html>
    """

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    debug_mode = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug_mode)