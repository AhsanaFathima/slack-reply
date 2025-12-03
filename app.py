import os
import json
import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from datetime import datetime
import hashlib
import hmac

load_dotenv()

app = Flask(__name__)

# ========== YOUR CONFIGURATION ==========
SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN')  # From Shopify Order Status Bot
SLACK_CHANNEL = 'shopify-slack'  # Channel name where to post
SHOPIFY_SHOP_NAME = 'fragrantsouq.com'  # Your shop name
SHOPIFY_WEBHOOK_SECRET = os.getenv('SHOPIFY_WEBHOOK_SECRET', '')
RENDER_URL = 'https://slack-reply.onrender.com'  # Your render URL

# ========== EMOJI CONFIG ==========
EMOJI_CONFIG = {
    'pending': {'emoji': '⏳', 'color': '#FFA500', 'text': 'Payment Pending'},
    'paid': {'emoji': '✅', 'color': '#36A64F', 'text': 'Payment Received'},
    'authorized': {'emoji': '🔒', 'color': '#2EB67D', 'text': 'Authorized'},
    'refunded': {'emoji': '↩️', 'color': '#E01E5A', 'text': 'Refunded'},
    'partially_paid': {'emoji': '💰', 'color': '#ECB22E', 'text': 'Partial Payment'},
    'created': {'emoji': '📦', 'color': '#611F69', 'text': 'Order Created'},
    'cancelled': {'emoji': '❌', 'color': '#000000', 'text': 'Cancelled'},
    'fulfilled': {'emoji': '🚀', 'color': '#00B0D6', 'text': 'Fulfilled'},
    'shipped': {'emoji': '🚚', 'color': '#4A154B', 'text': 'Shipped'}
}

# ========== THREAD STORAGE ==========
order_threads = {}  # In production, use Redis or database

# ========== SLACK HELPER FUNCTIONS ==========
def get_channel_id(channel_name):
    """Get channel ID from channel name"""
    headers = {
        'Authorization': f'Bearer {SLACK_BOT_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    try:
        # List all channels
        response = requests.get(
            'https://slack.com/api/conversations.list?types=public_channel,private_channel',
            headers=headers
        )
        
        if response.status_code == 200:
            channels = response.json().get('channels', [])
            for channel in channels:
                if channel.get('name') == channel_name:
                    return channel.get('id')
    except Exception as e:
        print(f"Error getting channel ID: {e}")
    
    return None

def create_order_thread(order_number, customer_name, amount):
    """Create a new thread for an order in #shopify-slack"""
    channel_id = get_channel_id(SLACK_CHANNEL)
    if not channel_id:
        print(f"❌ Could not find channel: {SLACK_CHANNEL}")
        return None
    
    headers = {
        'Authorization': f'Bearer {SLACK_BOT_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    # Create parent message (thread starter)
    parent_message = {
        'channel': channel_id,
        'text': f'📦 Order #{order_number} - ${amount}',
        'blocks': [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"📦 Order #{order_number}",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Customer:*\n{customer_name}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Amount:*\n${amount}"
                    }
                ]
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "All updates will appear as replies below 👇"
                    }
                ]
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
                channel = result['channel']
                print(f"✅ Thread created for order #{order_number} in #{SLACK_CHANNEL}")
                return {'thread_ts': thread_ts, 'channel_id': channel}
            else:
                print(f"❌ Slack error: {result.get('error')}")
    except Exception as e:
        print(f"Error creating thread: {e}")
    
    return None

def send_thread_reply(order_number, status, details):
    """Send reply in existing thread or create new thread"""
    # Check if we have thread for this order
    if order_number not in order_threads:
        # Create new thread
        thread_info = create_order_thread(
            order_number=order_number,
            customer_name=details.get('customer_name', 'Customer'),
            amount=details.get('total_price', '0.00')
        )
        
        if not thread_info:
            return False
        
        order_threads[order_number] = thread_info
        # Wait a moment for thread to be established
        import time
        time.sleep(1)
    
    thread_info = order_threads[order_number]
    channel_id = thread_info['channel_id']
    thread_ts = thread_info['thread_ts']
    
    # Get status configuration
    status_config = EMOJI_CONFIG.get(status.lower(), EMOJI_CONFIG['created'])
    timestamp = datetime.now().strftime("%I:%M %p")
    
    # Build reply message
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{status_config['emoji']} *{status_config['text']}* • {timestamp}"
            }
        }
    ]
    
    # Add order details
    if any(key in details for key in ['customer_email', 'gateway', 'items']):
        blocks.append({"type": "divider"})
        
        fields = []
        if 'customer_email' in details:
            fields.append({
                "type": "mrkdwn",
                "text": f"*Email:*\n{details['customer_email']}"
            })
        
        if 'gateway' in details:
            fields.append({
                "type": "mrkdwn",
                "text": f"*Payment:*\n{details['gateway']}"
            })
        
        if 'items' in details:
            fields.append({
                "type": "mrkdwn",
                "text": f"*Items:*\n{details['items']}"
            })
        
        if 'note' in details:
            fields.append({
                "type": "mrkdwn",
                "text": f"*Note:*\n{details['note']}"
            })
        
        if fields:
            blocks.append({
                "type": "section",
                "fields": fields
            })
    
    # Add Shopify link button
    if 'order_id' in details:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Open in Shopify",
                        "emoji": True
                    },
                    "url": f"https://{SHOPIFY_SHOP_NAME}/admin/orders/{details['order_id']}",
                    "style": "primary"
                }
            ]
        })
    
    # Send the threaded reply
    headers = {
        'Authorization': f'Bearer {SLACK_BOT_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    message_data = {
        'channel': channel_id,
        'thread_ts': thread_ts,
        'blocks': blocks,
        'text': f"Order #{order_number} - {status_config['text']}"
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
                print(f"✅ Reply sent to thread for order #{order_number}")
                return True
            else:
                print(f"❌ Slack error: {result.get('error')}")
    except Exception as e:
        print(f"Error sending reply: {e}")
    
    return False

# ========== SHOPIFY WEBHOOK VERIFICATION ==========
def verify_shopify_webhook(data, hmac_header):
    """Verify Shopify webhook signature"""
    if not SHOPIFY_WEBHOOK_SECRET:
        print("⚠️ Webhook secret not set, skipping verification")
        return True
    
    calculated_hmac = hmac.new(
        SHOPIFY_WEBHOOK_SECRET.encode('utf-8'),
        data,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(calculated_hmac, hmac_header)

# ========== ROUTES ==========
@app.route('/webhook/shopify', methods=['POST'])
def shopify_webhook():
    """Handle Shopify webhooks - ONLY for #shopify-slack channel"""
    # Verify webhook signature
    hmac_header = request.headers.get('X-Shopify-Hmac-Sha256', '')
    if not verify_shopify_webhook(request.data, hmac_header):
        return jsonify({'error': 'Invalid signature'}), 401
    
    try:
        data = request.json
        webhook_topic = request.headers.get('X-Shopify-Topic', '')
        
        print(f"📩 Webhook received: {webhook_topic}")
        print(f"Order: {data.get('id')}, Status: {data.get('financial_status')}")
        
        # Extract order info
        order_id = data.get('id')
        order_number = data.get('order_number') or data.get('name', f'#{order_id}')
        financial_status = data.get('financial_status', 'pending')
        fulfillment_status = data.get('fulfillment_status', 'unfulfilled')
        
        # Get customer info
        customer = data.get('customer', {})
        customer_name = customer.get('name', 'Customer')
        customer_email = data.get('contact_email') or customer.get('email', 'No email')
        
        # Prepare details for Slack
        details = {
            'order_id': order_id,
            'customer_name': customer_name,
            'customer_email': customer_email,
            'total_price': data.get('total_price', '0.00'),
            'gateway': data.get('gateway', 'Not specified'),
            'items': str(len(data.get('line_items', []))),
            'note': data.get('note', '')[:100] + '...' if data.get('note') else None
        }
        
        # Determine status to display
        if webhook_topic == 'orders/create':
            display_status = 'created'
        elif fulfillment_status and fulfillment_status != 'unfulfilled':
            display_status = fulfillment_status
        else:
            display_status = financial_status
        
        # Send to Slack thread
        success = send_thread_reply(
            order_number=order_number,
            status=display_status,
            details=details
        )
        
        if success:
            return jsonify({
                'success': True,
                'channel': SLACK_CHANNEL,
                'order': order_number,
                'status': display_status
            }), 200
        else:
            return jsonify({'error': 'Failed to send to Slack'}), 500
            
    except Exception as e:
        print(f"❌ Webhook processing error: {str(e)}")
        return jsonify({'error': str(e)}), 400

@app.route('/test/order/<order_number>', methods=['GET'])
def test_order(order_number):
    """Test endpoint - simulate order updates"""
    test_cases = [
        {'status': 'created', 'customer': 'John Doe', 'amount': '149.99'},
        {'status': 'pending', 'customer': 'John Doe', 'amount': '149.99'},
        {'status': 'paid', 'customer': 'John Doe', 'amount': '149.99'},
        {'status': 'fulfilled', 'customer': 'John Doe', 'amount': '149.99'},
        {'status': 'shipped', 'customer': 'John Doe', 'amount': '149.99'}
    ]
    
    results = []
    for test in test_cases:
        details = {
            'order_id': '123456789',
            'customer_name': test['customer'],
            'customer_email': 'john@example.com',
            'total_price': test['amount'],
            'gateway': 'Shopify Payments',
            'items': '3'
        }
        
        success = send_thread_reply(
            order_number=order_number,
            status=test['status'],
            details=details
        )
        
        results.append({
            'status': test['status'],
            'success': success
        })
    
    return jsonify({
        'test_order': order_number,
        'channel': SLACK_CHANNEL,
        'results': results,
        'message': f'Check #{SLACK_CHANNEL} channel for threaded messages'
    })

@app.route('/debug', methods=['GET'])
def debug_info():
    """Debug information about the setup"""
    channel_id = get_channel_id(SLACK_CHANNEL)
    
    # Test Slack API
    headers = {
        'Authorization': f'Bearer {SLACK_BOT_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    auth_test = {}
    try:
        response = requests.post('https://slack.com/api/auth.test', headers=headers)
        auth_test = response.json()
    except Exception as e:
        auth_test = {'error': str(e)}
    
    return jsonify({
        'app_name': 'Shopify Order Status Bot',
        'shop': SHOPIFY_SHOP_NAME,
        'render_url': RENDER_URL,
        'slack_channel': {
            'name': SLACK_CHANNEL,
            'id': channel_id or 'Not found'
        },
        'slack_auth': auth_test,
        'webhook_url': f'{RENDER_URL}/webhook/shopify',
        'status': 'operational' if channel_id and auth_test.get('ok') else 'needs_configuration'
    })

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'healthy',
        'service': 'Shopify to Slack Threaded Notifier',
        'channel': f'#{SLACK_CHANNEL}',
        'shop': SHOPIFY_SHOP_NAME
    }), 200

@app.route('/', methods=['GET'])
def home():
    return '''
    <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h1>✅ Shopify to Slack Threaded Notifier</h1>
            <p>Service is running for <strong>fragrantsouq.com</strong></p>
            <p>Slack Channel: <code>#shopify-slack</code></p>
            <p>Render URL: <code>https://slack-reply.onrender.com</code></p>
            <hr>
            <h3>Endpoints:</h3>
            <ul>
                <li><a href="/health">/health</a> - Health check</li>
                <li><a href="/debug">/debug</a> - Debug info</li>
                <li><a href="/test/order/TEST-123">/test/order/ORDER_NUMBER</a> - Test thread</li>
                <li><strong>/webhook/shopify</strong> - Shopify webhook endpoint</li>
            </ul>
            <hr>
            <h3>Setup Instructions:</h3>
            <ol>
                <li>Add Slack bot token to Render environment</li>
                <li>Setup Shopify webhooks to point here</li>
                <li>Test with /test/order/TEST-123</li>
            </ol>
        </body>
    </html>
    '''

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)