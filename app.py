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
# ✅ YOUR ACTUAL CHANNEL ID FOR #shopify-slack
SLACK_CHANNEL_ID = 'C0A068PHZMY'  # ← This is your channel ID
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
        # Test channel info
        response = requests.get(
            f'https://slack.com/api/conversations.info?channel={SLACK_CHANNEL_ID}',
            headers=headers
        )
        
        if response.status_code == 200:
            result = response.json()
            if result.get('ok'):
                channel_info = result.get('channel', {})
                return {
                    'success': True,
                    'channel_name': channel_info.get('name'),
                    'channel_id': channel_info.get('id'),
                    'is_member': channel_info.get('is_member', False)
                }
            else:
                return {
                    'success': False,
                    'error': result.get('error'),
                    'needs_invite': result.get('error') == 'not_in_channel'
                }
    except Exception as e:
        return {'success': False, 'error': str(e)}
    
    return {'success': False, 'error': 'Unknown error'}

def invite_bot_to_channel():
    """Invite bot to channel if not already a member"""
    headers = {
        'Authorization': f'Bearer {SLACK_BOT_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    # First check if bot is already in channel
    channel_test = test_channel_access()
    if channel_test.get('is_member'):
        print("✅ Bot is already a member of the channel")
        return True
    
    print("🤖 Inviting bot to channel...")
    
    # Invite bot to channel
    try:
        response = requests.post(
            'https://slack.com/api/conversations.join',
            headers=headers,
            json={'channel': SLACK_CHANNEL_ID}
        )
        
        if response.status_code == 200:
            result = response.json()
            if result.get('ok'):
                print(f"✅ Bot invited to channel {SLACK_CHANNEL_ID}")
                return True
            else:
                print(f"❌ Failed to join channel: {result.get('error')}")
                return False
    except Exception as e:
        print(f"❌ Error inviting bot: {e}")
    
    return False

def create_order_thread(order_number, customer_name, amount):
    """Create a new thread for an order"""
    # Ensure bot is in channel
    if not invite_bot_to_channel():
        print("❌ Bot cannot access channel")
        return None
    
    headers = {
        'Authorization': f'Bearer {SLACK_BOT_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    # Create parent message (thread starter)
    parent_message = {
        'channel': SLACK_CHANNEL_ID,
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
                print(f"✅ Thread created for order #{order_number}")
                return thread_ts
            else:
                print(f"❌ Slack error creating thread: {result.get('error')}")
    except Exception as e:
        print(f"❌ Error creating thread: {e}")
    
    return None

def send_thread_reply(order_number, status, details):
    """Send reply in existing thread or create new thread"""
    # Check if we have thread for this order
    if order_number not in order_threads:
        # Create new thread
        thread_ts = create_order_thread(
            order_number=order_number,
            customer_name=details.get('customer_name', 'Customer'),
            amount=details.get('total_price', '0.00')
        )
        
        if not thread_ts:
            return False
        
        order_threads[order_number] = thread_ts
        # Wait a moment for thread to be established
        time.sleep(1)
    
    thread_ts = order_threads[order_number]
    
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
    details_fields = []
    if 'customer_email' in details:
        details_fields.append({
            "type": "mrkdwn",
            "text": f"*Email:*\n{details['customer_email']}"
        })
    
    if 'gateway' in details:
        details_fields.append({
            "type": "mrkdwn",
            "text": f"*Payment:*\n{details['gateway']}"
        })
    
    if 'items' in details:
        details_fields.append({
            "type": "mrkdwn",
            "text": f"*Items:*\n{details['items']}"
        })
    
    if details_fields:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "fields": details_fields
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
        'channel': SLACK_CHANNEL_ID,
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
                return False
    except Exception as e:
        print(f"❌ Error sending reply: {e}")
    
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
    """Handle Shopify webhooks"""
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
                'channel': SLACK_CHANNEL_ID,
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
        {'status': 'fulfilled', 'customer': 'John Doe', 'amount': '149.99'}
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
        'channel_id': SLACK_CHANNEL_ID,
        'results': results,
        'message': f'Check #shopify-slack channel for threaded messages'
    })

@app.route('/debug', methods=['GET'])
def debug_info():
    """Debug information about the setup"""
    channel_test = test_channel_access()
    
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
    
    # Try to invite bot if not in channel
    if channel_test.get('needs_invite'):
        invite_result = invite_bot_to_channel()
        channel_test['invite_attempted'] = invite_result
    
    return jsonify({
        'app_name': 'Shopify Order Status Bot',
        'shop': SHOPIFY_SHOP_NAME,
        'render_url': RENDER_URL,
        'slack_channel': {
            'id': SLACK_CHANNEL_ID,
            'name': channel_test.get('channel_name', 'Unknown'),
            'is_member': channel_test.get('is_member', False),
            'access_test': channel_test
        },
        'slack_auth': auth_test,
        'webhook_url': f'{RENDER_URL}/webhook/shopify',
        'status': 'operational' if channel_test.get('success') else 'needs_configuration'
    })

@app.route('/invite-bot', methods=['GET'])
def invite_bot():
    """Manually invite bot to channel"""
    result = invite_bot_to_channel()
    return jsonify({
        'success': result,
        'channel_id': SLACK_CHANNEL_ID,
        'message': 'Bot invite attempted' if result else 'Bot invite failed'
    })

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'healthy',
        'service': 'Shopify to Slack Threaded Notifier',
        'channel_id': SLACK_CHANNEL_ID,
        'shop': SHOPIFY_SHOP_NAME
    }), 200

@app.route('/', methods=['GET'])
def home():
    return '''
    <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h1>✅ Shopify to Slack Threaded Notifier</h1>
            <p><strong>Channel:</strong> #shopify-slack (ID: C0A068PHZMY)</p>
            <p><strong>Shop:</strong> fragrantsouq.com</p>
            <p><strong>URL:</strong> https://slack-reply.onrender.com</p>
            <hr>
            <h3>Quick Tests:</h3>
            <ul>
                <li><a href="/health">/health</a> - Health check</li>
                <li><a href="/debug">/debug</a> - Debug info</li>
                <li><a href="/invite-bot">/invite-bot</a> - Invite bot to channel</li>
                <li><a href="/test/order/TEST-123">/test/order/TEST-123</a> - Test thread</li>
            </ul>
            <hr>
            <h3>Shopify Webhook URL:</h3>
            <code>https://slack-reply.onrender.com/webhook/shopify</code>
        </body>
    </html>
    '''

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)