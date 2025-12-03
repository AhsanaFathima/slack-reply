import os
import requests
from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)

# Configuration
SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN')
SLACK_CHANNEL_ID = "C0A068PHZMY"  # Your #shopify-slack channel

# Debug mode - disable webhook verification for testing
DEBUG_MODE = True  # Set to False in production

# Store order threads
order_threads = {}

def send_to_slack(order_number, status, customer_name, amount, customer_email=""):
    """Send message to Slack"""
    headers = {
        'Authorization': f'Bearer {SLACK_BOT_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    # Status emojis
    emojis = {
        'pending': '⏳',
        'paid': '✅',
        'authorized': '🔒',
        'refunded': '↩️',
        'created': '📦'
    }
    
    emoji = emojis.get(status, '📦')
    time_now = datetime.now().strftime("%I:%M %p")
    
    # Create message
    message_text = f"{emoji} *{status.upper()}* • {time_now}\n"
    message_text += f"Order: #{order_number}\n"
    message_text += f"Customer: {customer_name}\n"
    if customer_email:
        message_text += f"Email: {customer_email}\n"
    message_text += f"Amount: ${amount}"
    
    # Check if we have thread for this order
    if order_number in order_threads:
        # Reply in existing thread
        thread_ts = order_threads[order_number]
        data = {
            'channel': SLACK_CHANNEL_ID,
            'thread_ts': thread_ts,
            'text': message_text
        }
    else:
        # Create new message
        data = {
            'channel': SLACK_CHANNEL_ID,
            'text': message_text
        }
        
        try:
            response = requests.post(
                'https://slack.com/api/chat.postMessage',
                headers=headers,
                json=data
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('ok'):
                    order_threads[order_number] = result['ts']
                    print(f"✅ Created thread for order #{order_number}")
                    return True
        except Exception as e:
            print(f"❌ Error creating thread: {e}")
            return False
    
    # Send threaded reply
    try:
        response = requests.post(
            'https://slack.com/api/chat.postMessage',
            headers=headers,
            json=data
        )
        
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Error sending message: {e}")
        return False

@app.route('/webhook/shopify', methods=['POST'])
def shopify_webhook():
    """Receive Shopify webhooks - NO VERIFICATION FOR NOW"""
    print("📩 Shopify webhook received!")
    
    try:
        # Log headers for debugging
        print(f"Headers: {dict(request.headers)}")
        
        # Get the data
        data = request.get_json()
        if not data:
            print("❌ No JSON data received")
            return jsonify({'error': 'No data'}), 400
        
        print(f"📦 Webhook data: {data}")
        
        # Extract order info
        order_id = data.get('id')
        order_number = data.get('order_number', f"#{order_id}")
        financial_status = data.get('financial_status', 'pending')
        total_price = data.get('total_price', '0')
        
        # Get customer info
        customer = data.get('customer', {})
        customer_name = customer.get('name', 'Customer')
        customer_email = data.get('contact_email', customer.get('email', ''))
        
        # Check webhook type
        webhook_topic = request.headers.get('X-Shopify-Topic', '')
        print(f"📝 Webhook topic: {webhook_topic}")
        
        # For new orders, use 'created' status
        if webhook_topic == 'orders/create':
            status = 'created'
        else:
            status = financial_status
        
        # Send to Slack
        success = send_to_slack(
            order_number=order_number,
            status=status,
            customer_name=customer_name,
            amount=total_price,
            customer_email=customer_email
        )
        
        if success:
            print(f"✅ Successfully processed order #{order_number}")
            return jsonify({'success': True}), 200
        else:
            print(f"❌ Failed to send to Slack for order #{order_number}")
            return jsonify({'error': 'Failed to send to Slack'}), 500
            
    except Exception as e:
        print(f"❌ Error processing webhook: {str(e)}")
        return jsonify({'error': str(e)}), 400

@app.route('/test-webhook', methods=['POST', 'GET'])
def test_webhook():
    """Test endpoint to simulate Shopify webhook"""
    test_data = {
        'id': 123456789,
        'order_number': 'TEST-001',
        'financial_status': 'paid',
        'total_price': '149.99',
        'contact_email': 'test@example.com',
        'customer': {
            'name': 'Test Customer',
            'email': 'test@example.com'
        }
    }
    
    # Simulate sending to webhook
    success = send_to_slack(
        order_number='TEST-001',
        status='paid',
        customer_name='Test Customer',
        amount='149.99',
        customer_email='test@example.com'
    )
    
    if success:
        return jsonify({
            'success': True,
            'message': 'Test webhook processed. Check #shopify-slack channel.'
        }), 200
    else:
        return jsonify({'error': 'Test failed'}), 500

@app.route('/debug', methods=['GET'])
def debug():
    """Debug endpoint"""
    return jsonify({
        'status': 'running',
        'channel_id': SLACK_CHANNEL_ID,
        'tracked_orders': list(order_threads.keys()),
        'webhook_url': 'https://slack-reply.onrender.com/webhook/shopify'
    })

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy'}), 200

@app.route('/', methods=['GET'])
def home():
    return '''
    <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h1>✅ Shopify to Slack Webhook</h1>
            <p><strong>Status:</strong> Running</p>
            <p><strong>Webhook URL:</strong> https://slack-reply.onrender.com/webhook/shopify</p>
            <p><strong>Slack Channel:</strong> #shopify-slack</p>
            <hr>
            <h3>Endpoints:</h3>
            <ul>
                <li><a href="/health">/health</a> - Health check</li>
                <li><a href="/debug">/debug</a> - Debug info</li>
                <li><a href="/test-webhook">/test-webhook</a> - Test webhook</li>
                <li><strong>/webhook/shopify</strong> - Shopify webhook endpoint (POST only)</li>
            </ul>
            <hr>
            <h3>For Shopify Setup:</h3>
            <ol>
                <li>Create webhook in Shopify</li>
                <li>URL: <code>https://slack-reply.onrender.com/webhook/shopify</code></li>
                <li>Events: "Order creation" and "Order update"</li>
            </ol>
        </body>
    </html>
    '''

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)