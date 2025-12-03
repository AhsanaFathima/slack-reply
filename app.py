import os
import json
import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from datetime import datetime
import hashlib
import hmac
import time

load_dotenv()

app = Flask(__name__)

# ========== YOUR CONFIGURATION ==========
SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN')
SLACK_CHANNEL_ID = 'C0A068PHZMY'  # Your channel ID for #shopify-slack
SHOPIFY_SHOP_NAME = 'fragrantsouq.com'
SHOPIFY_WEBHOOK_SECRET = os.getenv('SHOPIFY_WEBHOOK_SECRET', '')
RENDER_URL = 'https://slack-reply.onrender.com'

# ========== EMOJI CONFIG ==========
EMOJI_CONFIG = {
    'pending': {'emoji': '⏳', 'color': '#FFA500', 'text': 'Payment Pending'},
    'paid': {'emoji': '✅', 'color': '#36A64F', 'text': 'Payment Received'},
    'authorized': {'emoji': '🔒', 'color': '#2EB67D', 'text': 'Authorized'},
    'refunded': {'emoji': '↩️', 'color': '#E01E5A', 'text': 'Refunded'},
    'created': {'emoji': '📦', 'color': '#611F69', 'text': 'Order Created'},
    'cancelled': {'emoji': '❌', 'color': '#000000', 'text': 'Cancelled'},
    'fulfilled': {'emoji': '🚀', 'color': '#00B0D6', 'text': 'Fulfilled'},
    'shipped': {'emoji': '🚚', 'color': '#4A154B', 'text': 'Shipped'}
}

# ========== THREAD STORAGE ==========
order_threads = {}

# ========== SLACK HELPER FUNCTIONS ==========
def test_channel_access():
    """Test if bot can access the channel"""
    headers = {
        'Authorization': f'Bearer {SLACK_BOT_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    try:
        response = requests.get(
            f'https://slack.com/api/conversations.info?channel={SLACK_CHANNEL_ID}',
            headers=headers
        )
        
        if response.status_code == 200:
            result = response.json()
            if result.get('ok'):
                return {'success': True, 'is_member': True}
            else:
                return {'success': False, 'error': result.get('error')}
    except Exception as e:
        return {'success': False, 'error': str(e)}
    
    return {'success': False, 'error': 'Unknown error'}

def create_order_thread(order_number, customer_name, amount):
    """Create a new thread for an order - SIMPLIFIED"""
    headers = {
        'Authorization': f'Bearer {SLACK_BOT_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    # Create SIMPLE parent message (thread starter)
    parent_message = {
        'channel': SLACK_CHANNEL_ID,
        'text': f'📦 Order #{order_number} - ${amount}',
        'blocks': [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*📦 Order #{order_number}*\n_Customer: {customer_name}_"
                }
            }
        ]
    }
    
    try:
        response = requests.post(
            'https://slack.com/api/chat.postMessage',
            headers=headers,
            json=parent_message
        )
        
        if response.status_code == 200:
            result = response.json()
            if result.get('ok'):
                thread_ts = result['ts']
                print(f"✅ Thread created for order #{order_number} - TS: {thread_ts}")
                return thread_ts
            else:
                print(f"❌ Slack error: {result.get('error')}")
    except Exception as e:
        print(f"❌ Error creating thread: {e}")
    
    return None

def send_thread_reply(order_number, status, details):
    """Send reply in existing thread - FIXED THREADING"""
    # Get or create thread
    if order_number not in order_threads:
        thread_ts = create_order_thread(
            order_number=order_number,
            customer_name=details.get('customer_name', 'Customer'),
            amount=details.get('total_price', '0.00')
        )
        
        if not thread_ts:
            return False
        
        order_threads[order_number] = thread_ts
        time.sleep(1)  # Brief pause
    
    thread_ts = order_threads[order_number]
    
    # Get status configuration
    status_config = EMOJI_CONFIG.get(status.lower(), EMOJI_CONFIG['created'])
    timestamp = datetime.now().strftime("%I:%M %p")
    
    # Build SIMPLER reply message
    message_text = f"{status_config['emoji']} *{status_config['text']}* • {timestamp}"
    
    # Add details to text
    details_text = ""
    if 'customer_email' in details:
        details_text += f"Email: {details['customer_email']}\n"
    if 'gateway' in details:
        details_text += f"Payment: {details['gateway']}\n"
    if 'items' in details:
        details_text += f"Items: {details['items']}"
    
    if details_text:
        message_text += f"\n```{details_text}```"
    
    # Send the threaded reply
    headers = {
        'Authorization': f'Bearer {SLACK_BOT_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    # ⚠️ CRITICAL: Use thread_ts for threading
    message_data = {
        'channel': SLACK_CHANNEL_ID,
        'thread_ts': thread_ts,  # This makes it a threaded reply
        'text': message_text,
        'reply_broadcast': False  # Don't broadcast to channel
    }
    
    try:
        response = requests.post(
            'https://slack.com/api/chat.postMessage',
            headers=headers,
            json=message_data
        )
        
        if response.status_code == 200:
            result = response.json()
            if result.get('ok'):
                print(f"✅ Threaded reply sent for order #{order_number}")
                return True
            else:
                print(f"❌ Slack API error: {result.get('error')}")
                # Try without blocks
                return send_simple_thread_reply(order_number, thread_ts, message_text)
    except Exception as e:
        print(f"❌ Error sending reply: {e}")
    
    return False

def send_simple_thread_reply(order_number, thread_ts, message_text):
    """Fallback: Send simple text reply in thread"""
    headers = {
        'Authorization': f'Bearer {SLACK_BOT_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    message_data = {
        'channel': SLACK_CHANNEL_ID,
        'thread_ts': thread_ts,
        'text': message_text
    }
    
    try:
        response = requests.post(
            'https://slack.com/api/chat.postMessage',
            headers=headers,
            json=message_data
        )
        
        if response.status_code == 200:
            result = response.json()
            if result.get('ok'):
                print(f"✅ Simple threaded reply sent for order #{order_number}")
                return True
    except Exception as e:
        print(f"❌ Error sending simple reply: {e}")
    
    return False

# ========== SHOPIFY WEBHOOK ==========
@app.route('/webhook/shopify', methods=['POST'])
def shopify_webhook():
    """Handle Shopify webhooks"""
    try:
        data = request.json
        webhook_topic = request.headers.get('X-Shopify-Topic', '')
        
        print(f"📩 Webhook: {webhook_topic}")
        
        # Extract order info
        order_id = data.get('id')
        order_number = data.get('order_number') or data.get('name', f'#{order_id}')
        financial_status = data.get('financial_status', 'pending')
        
        # Get customer info
        customer = data.get('customer', {})
        customer_name = customer.get('name', 'Customer')
        customer_email = data.get('contact_email') or customer.get('email', 'No email')
        
        # Prepare details
        details = {
            'order_id': order_id,
            'customer_name': customer_name,
            'customer_email': customer_email,
            'total_price': data.get('total_price', '0.00'),
            'gateway': data.get('gateway', 'Not specified'),
            'items': str(len(data.get('line_items', [])))
        }
        
        # Determine status
        if webhook_topic == 'orders/create':
            display_status = 'created'
        else:
            display_status = financial_status
        
        # Send to thread
        success = send_thread_reply(
            order_number=order_number,
            status=display_status,
            details=details
        )
        
        if success:
            return jsonify({'success': True}), 200
        else:
            return jsonify({'error': 'Failed to send to Slack'}), 500
            
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return jsonify({'error': str(e)}), 400

@app.route('/test/order/<order_number>', methods=['GET'])
def test_order(order_number):
    """Test threaded replies"""
    # Clear existing thread for fresh test
    if order_number in order_threads:
        del order_threads[order_number]
    
    details = {
        'order_id': '123456',
        'customer_name': 'Test Customer',
        'customer_email': 'test@example.com',
        'total_price': '99.99',
        'gateway': 'Credit Card',
        'items': '2'
    }
    
    # Test sequence
    statuses = ['created', 'pending', 'paid', 'fulfilled']
    
    results = []
    for i, status in enumerate(statuses):
        success = send_thread_reply(
            order_number=order_number,
            status=status,
            details=details
        )
        
        results.append({
            'status': status,
            'success': success,
            'message': f'Check #shopify-slack channel. Click "X replies" to see thread.'
        })
        
        time.sleep(1)  # Space out messages
    
    return jsonify({
        'test_order': order_number,
        'results': results,
        'note': 'Messages should appear INSIDE the thread. Click the thread to view replies.'
    })

@app.route('/debug', methods=['GET'])
def debug_info():
    """Debug endpoint"""
    channel_test = test_channel_access()
    
    return jsonify({
        'channel_id': SLACK_CHANNEL_ID,
        'channel_access': channel_test,
        'webhook_url': f'{RENDER_URL}/webhook/shopify',
        'note': 'Use /test/order/TEST-123 to test threaded replies'
    })

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy'}), 200

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)