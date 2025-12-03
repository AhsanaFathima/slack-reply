import os
import requests
from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)

# Configuration
SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN')
SLACK_CHANNEL_ID = "C0A068PHZMY"  # Your #shopify-slack channel

# Store thread timestamps {order_number: thread_ts}
order_threads = {}

def format_phone(phone):
    """Format phone number like Shopify does"""
    if not phone:
        return "N/A"
    # Remove spaces and special characters
    phone = ''.join(filter(str.isdigit, str(phone)))
    if phone.startswith('971') and len(phone) == 12:
        return f"+{phone[:3]}{phone[3:]}"
    return phone

def create_order_message(order_data):
    """Create EXACT Shopify-style order notification"""
    order_number = order_data.get('order_number', 'N/A')
    customer = order_data.get('customer', {})
    customer_name = customer.get('name', 'Customer')
    phone = format_phone(customer.get('phone', 'N/A'))
    
    # Get items like Shopify format
    line_items = order_data.get('line_items', [])
    items_text = ""
    
    for item in line_items:
        item_name = item.get('name', '')
        variant = item.get('variant_title', '')
        quantity = item.get('quantity', 1)
        
        # Format: "Product Name Variant | quantity"
        item_line = f"{item_name}"
        if variant:
            item_line += f" {variant}"
        item_line += f" | {quantity}"
        
        items_text += f"{item_line}\n"
    
    if not items_text:
        items_text = "Item | 1"
    
    # Create EXACT Shopify format
    message_text = f"New Shopify Order Received!\n\n#{order_number} | {customer_name} | {phone} | {items_text.strip()}"
    
    return message_text

def send_order_notification(order_data):
    """Send order notification in EXACT Shopify format"""
    headers = {
        'Authorization': f'Bearer {SLACK_BOT_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    order_number = order_data.get('order_number', 'N/A')
    message_text = create_order_message(order_data)
    
    # Send the order notification
    message = {
        'channel': SLACK_CHANNEL_ID,
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
                thread_ts = result['ts']
                order_threads[order_number] = thread_ts
                print(f"✅ Order #{order_number} notification sent")
                return thread_ts
    except Exception as e:
        print(f"❌ Error: {e}")
    
    return None

def send_payment_update(order_number, status, amount="", method=""):
    """Send payment update as threaded reply"""
    if order_number not in order_threads:
        print(f"❌ Order #{order_number} not found")
        return False
    
    thread_ts = order_threads[order_number]
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
    
    # Create threaded reply
    message_text = f"{config['emoji']} {config['text']} • {time_now}"
    if amount:
        message_text += f"\nAmount: ${amount}"
    if method:
        message_text += f"\nMethod: {method}"
    
    message = {
        'channel': SLACK_CHANNEL_ID,
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
                print(f"✅ Payment update for #{order_number}: {status}")
                return True
    except Exception as e:
        print(f"❌ Error: {e}")
    
    return False

@app.route('/webhook/shopify', methods=['POST'])
def shopify_webhook():
    """Handle Shopify webhooks"""
    print("📩 Shopify webhook received")
    
    try:
        data = request.json
        webhook_topic = request.headers.get('X-Shopify-Topic', '')
        
        print(f"📦 Topic: {webhook_topic}")
        
        order_number = data.get('order_number', data.get('name', 'N/A'))
        financial_status = data.get('financial_status', 'pending')
        total_price = data.get('total_price', '0.00')
        
        if webhook_topic == 'orders/create':
            # New order - send notification
            thread_ts = send_order_notification(data)
            if thread_ts:
                # Send initial status
                send_payment_update(order_number, 'created', total_price)
                return jsonify({'success': True}), 200
        
        elif webhook_topic == 'orders/updated':
            # Order update - send payment status
            # First, ensure we have a thread for this order
            if order_number not in order_threads:
                send_order_notification(data)
            
            # Send payment update
            success = send_payment_update(
                order_number, 
                financial_status, 
                total_price,
                data.get('gateway', '')
            )
            
            if success:
                return jsonify({'success': True}), 200
        
        return jsonify({'success': True}), 200
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return jsonify({'error': str(e)}), 400

@app.route('/test-exact-format', methods=['GET'])
def test_exact_format():
    """Test EXACT format you want"""
    # Test data matching your screenshot
    test_data = {
        'order_number': '1252',
        'customer': {
            'name': 'test test',
            'phone': '+971545982212'
        },
        'line_items': [
            {
                'name': 'Abercrombie & Fitch Authentic Night 100 ml',
                'variant_title': 'EDP Women Perfume',
                'quantity': 1
            }
        ],
        'total_price': '149.99',
        'financial_status': 'pending'
    }
    
    # Send order notification
    thread_ts = send_order_notification(test_data)
    
    if not thread_ts:
        return jsonify({'error': 'Failed to create order'}), 500
    
    # Simulate payment updates
    updates = [
        {'status': 'pending', 'amount': '149.99'},
        {'status': 'paid', 'amount': '149.99', 'method': 'Credit Card'},
        {'status': 'refunded', 'amount': '149.99', 'method': 'Credit Card'}
    ]
    
    for update in updates:
        send_payment_update(
            test_data['order_number'],
            update['status'],
            update['amount'],
            update.get('method', '')
        )
    
    return jsonify({
        'success': True,
        'order': '1252',
        'message': 'Check #shopify-slack for EXACT Shopify format with threaded payment updates'
    })

@app.route('/test-real-order', methods=['GET'])
def test_real_order():
    """Test with realistic order data"""
    test_data = {
        'order_number': '1251',
        'customer': {
            'name': 'test test',
            'phone': '+971545982212'
        },
        'line_items': [
            {
                'name': 'Abdul Samad Al Qurashi Safari Extreme 75 ml',
                'variant_title': 'EDP Unisex',
                'quantity': 1
            }
        ],
        'total_price': '129.99',
        'financial_status': 'paid',
        'gateway': 'Shopify Payments'
    }
    
    # Send order notification
    thread_ts = send_order_notification(test_data)
    
    if thread_ts:
        # Send payment status
        send_payment_update(
            test_data['order_number'],
            test_data['financial_status'],
            test_data['total_price'],
            test_data['gateway']
        )
        
        return jsonify({
            'success': True,
            'order': test_data['order_number'],
            'customer': test_data['customer']['name'],
            'amount': test_data['total_price'],
            'message': 'Realistic order posted to #shopify-slack'
        })
    
    return jsonify({'error': 'Failed'}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy'}), 200

@app.route('/', methods=['GET'])
def home():
    return '''
    <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h1>✅ Shopify → Slack (Exact Format)</h1>
            <p><strong>Output Format:</strong> Exact Shopify notification style</p>
            <p><strong>Channel:</strong> #shopify-slack</p>
            <hr>
            <h3>Test Endpoints:</h3>
            <ul>
                <li><a href="/test-exact-format">/test-exact-format</a> - EXACT format you want</li>
                <li><a href="/test-real-order">/test-real-order</a> - Realistic order</li>
                <li><a href="/health">/health</a> - Health check</li>
            </ul>
            <hr>
            <h3>Expected Output in Slack:</h3>
            <pre>
New Shopify Order Received!

#1252 | test test | +971545982212 | Abercrombie & Fitch Authentic Night 100 ml EDP Women Perfume | 1
  ↓ (click thread to see)
     ⏳ Payment Pending • 2:30 PM
     ✅ Payment Received • 2:31 PM
     ↩️ Refund Processed • 2:32 PM
            </pre>
        </body>
    </html>
    '''

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)