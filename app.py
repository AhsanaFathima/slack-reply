import os
import requests
from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)

# Configuration
SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN')
TEST_CHANNEL_ID = "C0A068PHZMY"  # Your #shopify-slack channel for testing

# Store test order messages {order_number: thread_ts}
test_orders = {}

def create_test_order(order_number, customer_name, phone, item, quantity):
    """Create a test order message in #shopify-slack (simulating #order channel)"""
    headers = {
        'Authorization': f'Bearer {SLACK_BOT_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    # Format like your Shopify notifications
    message_text = f"New Shopify Order Received!\n\n#{order_number} | {customer_name} | {phone} | {item} | {quantity}"
    
    message = {
        'channel': TEST_CHANNEL_ID,
        'text': message_text,
        'blocks': [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": message_text
                }
            }
        ]
    }
    
    try:
        response = requests.post(
            'https://slack.com/api/chat.postMessage',
            headers=headers,
            json=message
        )
        
        if response.status_code == 200:
            result = response.json()
            if result.get('ok'):
                thread_ts = result['ts']
                test_orders[order_number] = thread_ts
                print(f"✅ Test order #{order_number} created in #shopify-slack")
                return thread_ts
    except Exception as e:
        print(f"❌ Error: {e}")
    
    return None

def send_payment_update(order_number, status, amount="", method=""):
    """Send payment update as threaded reply to existing order"""
    # Check if we have this test order
    if order_number not in test_orders:
        print(f"❌ Order #{order_number} not found. Create it first with /create-test-order")
        return False
    
    thread_ts = test_orders[order_number]
    headers = {
        'Authorization': f'Bearer {SLACK_BOT_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    # Status mapping
    status_config = {
        'pending': {'emoji': '⏳', 'text': 'Payment Pending'},
        'paid': {'emoji': '✅', 'text': 'Payment Received'},
        'authorized': {'emoji': '🔒', 'text': 'Payment Authorized'},
        'refunded': {'emoji': '↩️', 'text': 'Refund Processed'},
        'partially_paid': {'emoji': '💰', 'text': 'Partially Paid'}
    }
    
    config = status_config.get(status, {'emoji': '📝', 'text': status.title()})
    time_now = datetime.now().strftime("%I:%M %p")
    
    # Create simple threaded reply
    message_text = f"{config['emoji']} *{config['text']}* • {time_now}"
    if amount:
        message_text += f"\nAmount: ${amount}"
    if method:
        message_text += f"\nMethod: {method}"
    
    message = {
        'channel': TEST_CHANNEL_ID,
        'thread_ts': thread_ts,
        'text': message_text
    }
    
    try:
        response = requests.post(
            'https://slack.com/api/chat.postMessage',
            headers=headers,
            json=message
        )
        
        if response.status_code == 200:
            result = response.json()
            if result.get('ok'):
                print(f"✅ Payment update sent for order #{order_number}: {status}")
                return True
    except Exception as e:
        print(f"❌ Error: {e}")
    
    return False

@app.route('/create-test-order', methods=['GET'])
def create_test_order_endpoint():
    """Create a test order in #shopify-slack"""
    order_number = f"TEST-{datetime.now().strftime('%H%M%S')}"
    
    thread_ts = create_test_order(
        order_number=order_number,
        customer_name="Test Customer",
        phone="+971501234567",
        item="Test Product 100ml EDP",
        quantity="1"
    )
    
    if thread_ts:
        return jsonify({
            'success': True,
            'order_number': order_number,
            'thread_ts': thread_ts,
            'message': f'Test order created in #shopify-slack. Use /update-payment/{order_number}/paid to test updates'
        })
    
    return jsonify({'error': 'Failed to create test order'}), 500

@app.route('/update-payment/<order_number>/<status>', methods=['GET'])
def update_payment(order_number, status):
    """Test payment update for an order"""
    if order_number not in test_orders:
        return jsonify({
            'error': f'Order #{order_number} not found. Create it first with /create-test-order',
            'available_orders': list(test_orders.keys())
        }), 404
    
    success = send_payment_update(
        order_number=order_number,
        status=status,
        amount="149.99",
        method="Credit Card"
    )
    
    if success:
        return jsonify({
            'success': True,
            'order': order_number,
            'status': status,
            'message': f'Payment update sent. Check thread in #shopify-slack'
        })
    
    return jsonify({'error': 'Failed to send update'}), 500

@app.route('/webhook/shopify', methods=['POST'])
def shopify_webhook():
    """Real Shopify webhook handler - for when you're ready"""
    try:
        data = request.json
        webhook_topic = request.headers.get('X-Shopify-Topic', '')
        
        if webhook_topic == 'orders/updated':
            order_number = data.get('order_number')
            financial_status = data.get('financial_status')
            
            # In production, you would:
            # 1. Find the existing order message in #order channel
            # 2. Send threaded reply with payment status
            
            print(f"📦 Order #{order_number} updated: {financial_status}")
            
            # For now, just acknowledge
            return jsonify({'success': True}), 200
        
        return jsonify({'success': True}), 200
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return jsonify({'error': str(e)}), 400

@app.route('/test-all', methods=['GET'])
def test_all():
    """Full test: Create order and send multiple updates"""
    # Create test order
    order_number = f"FULLTEST-{datetime.now().strftime('%H%M%S')}"
    
    thread_ts = create_test_order(
        order_number=order_number,
        customer_name="Full Test Customer",
        phone="+971551234567",
        item="Premium Perfume 200ml EDP",
        quantity="2"
    )
    
    if not thread_ts:
        return jsonify({'error': 'Failed to create order'}), 500
    
    # Send multiple payment updates
    updates = [
        {'status': 'pending', 'amount': '299.98'},
        {'status': 'paid', 'amount': '299.98', 'method': 'Apple Pay'},
        {'status': 'refunded', 'amount': '299.98', 'method': 'Credit Card'}
    ]
    
    results = []
    for update in updates:
        success = send_payment_update(
            order_number=order_number,
            status=update['status'],
            amount=update['amount'],
            method=update.get('method', '')
        )
        results.append({'status': update['status'], 'success': success})
    
    return jsonify({
        'success': True,
        'order': order_number,
        'updates': results,
        'note': 'Check #shopify-slack channel. Click the order message to see threaded payment updates.'
    })

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy'}), 200

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)