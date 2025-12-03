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
    """Format phone number"""
    if not phone:
        return "N/A"
    phone = ''.join(filter(str.isdigit, str(phone)))
    if phone.startswith('971') and len(phone) == 12:
        return f"+{phone[:3]}{phone[3:]}"
    return phone

def create_order_message(order_data):
    """Create Shopify-style order notification"""
    order_number = order_data.get('order_number', 'N/A')
    customer = order_data.get('customer', {})
    customer_name = customer.get('name', 'Customer')
    phone = format_phone(customer.get('phone', 'N/A'))
    
    # Get items
    line_items = order_data.get('line_items', [])
    items_text = ""
    
    for item in line_items:
        item_name = item.get('name', '')
        variant = item.get('variant_title', '')
        quantity = item.get('quantity', 1)
        
        item_line = f"{item_name}"
        if variant:
            item_line += f" {variant}"
        item_line += f" | {quantity}"
        
        items_text += f"{item_line}\n"
    
    if not items_text:
        items_text = "Item | 1"
    
    # Create Shopify format
    message_text = f"New Shopify Order Received!\n\n#{order_number} | {customer_name} | {phone} | {items_text.strip()}"
    
    return message_text

def send_order_notification(order_data):
    """Send order notification"""
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

def send_status_update(order_number, status_type, status, details=None):
    """Send status update as threaded reply"""
    if order_number not in order_threads:
        print(f"❌ Order #{order_number} not found")
        return False
    
    thread_ts = order_threads[order_number]
    headers = {
        'Authorization': f'Bearer {SLACK_BOT_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    # PAYMENT STATUS MAPPING
    payment_status = {
        'pending': {'emoji': '⏳', 'text': 'Payment Pending', 'prefix': '💳'},
        'paid': {'emoji': '✅', 'text': 'Payment Received', 'prefix': '💳'},
        'authorized': {'emoji': '🔒', 'text': 'Payment Authorized', 'prefix': '💳'},
        'refunded': {'emoji': '↩️', 'text': 'Refund Processed', 'prefix': '💳'},
        'voided': {'emoji': '❌', 'text': 'Payment Voided', 'prefix': '💳'},
    }
    
    # FULFILLMENT STATUS MAPPING
    fulfillment_status = {
        'fulfilled': {'emoji': '🚀', 'text': 'Order Fulfilled', 'prefix': '📦'},
        'unfulfilled': {'emoji': '📦', 'text': 'Order Not Fulfilled', 'prefix': '📦'},
        'in_progress': {'emoji': '⚙️', 'text': 'Fulfillment In Progress', 'prefix': '📦'},
        'on_hold': {'emoji': '⏸️', 'text': 'Fulfillment On Hold', 'prefix': '📦'},
        'partially_fulfilled': {'emoji': '📤', 'text': 'Partially Fulfilled', 'prefix': '📦'},
    }
    
    # Combine all statuses
    all_status = {**payment_status, **fulfillment_status}
    
    # Get config for this status
    config = all_status.get(status, {'emoji': '📝', 'text': status.replace('_', ' ').title(), 'prefix': '📝'})
    
    time_now = datetime.now().strftime("%I:%M %p")
    
    # Create message
    message_text = f"{config['prefix']} {config['emoji']} *{config['text']}* • {time_now}"
    
    # Add details if provided
    if details:
        for key, value in details.items():
            if value:
                message_text += f"\n{key}: {value}"
    
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
                print(f"✅ {status_type.title()} update for #{order_number}: {status}")
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
        fulfillment_status = data.get('fulfillment_status', 'unfulfilled')
        total_price = data.get('total_price', '0.00')
        
        if webhook_topic == 'orders/create':
            # New order - send notification
            thread_ts = send_order_notification(data)
            if thread_ts:
                # Send initial payment status
                send_status_update(
                    order_number, 
                    'payment',
                    financial_status,
                    {'Amount': f"${total_price}"}
                )
                return jsonify({'success': True}), 200
        
        elif webhook_topic == 'orders/updated':
            # Order update - check what changed
            
            # First, ensure we have a thread for this order
            if order_number not in order_threads:
                send_order_notification(data)
            
            details = {}
            
            # Check if payment status changed
            if financial_status and financial_status != 'pending':
                details['Amount'] = f"${total_price}"
                if data.get('gateway'):
                    details['Method'] = data.get('gateway')
                
                send_status_update(
                    order_number,
                    'payment',
                    financial_status,
                    details
                )
            
            # Check if fulfillment status changed
            if fulfillment_status and fulfillment_status != 'unfulfilled':
                fulfillment_details = {}
                if data.get('tracking_numbers'):
                    fulfillment_details['Tracking'] = ', '.join(data.get('tracking_numbers', []))
                if data.get('tracking_company'):
                    fulfillment_details['Carrier'] = data.get('tracking_company')
                
                send_status_update(
                    order_number,
                    'fulfillment',
                    fulfillment_status,
                    fulfillment_details
                )
            
            return jsonify({'success': True}), 200
        
        return jsonify({'success': True}), 200
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return jsonify({'error': str(e)}), 400

@app.route('/test-all-statuses', methods=['GET'])
def test_all_statuses():
    """Test ALL payment and fulfillment statuses"""
    test_data = {
        'order_number': 'TEST-STATUS',
        'customer': {
            'name': 'Status Test Customer',
            'phone': '+971501234567'
        },
        'line_items': [
            {
                'name': 'Test Product',
                'variant_title': '100ml EDP',
                'quantity': 1
            }
        ],
        'total_price': '199.99'
    }
    
    # Send order notification
    thread_ts = send_order_notification(test_data)
    
    if not thread_ts:
        return jsonify({'error': 'Failed to create order'}), 500
    
    # Test ALL payment statuses
    payment_statuses = [
        {'status': 'pending', 'details': {'Amount': '$199.99'}},
        {'status': 'authorized', 'details': {'Amount': '$199.99', 'Method': 'Credit Card'}},
        {'status': 'paid', 'details': {'Amount': '$199.99', 'Method': 'Credit Card'}},
        {'status': 'refunded', 'details': {'Amount': '$199.99', 'Method': 'Credit Card'}},
        {'status': 'voided', 'details': {'Amount': '$199.99', 'Reason': 'Customer Request'}}
    ]
    
    # Test ALL fulfillment statuses
    fulfillment_statuses = [
        {'status': 'unfulfilled', 'details': {}},
        {'status': 'in_progress', 'details': {'Note': 'Preparing for shipment'}},
        {'status': 'on_hold', 'details': {'Reason': 'Waiting for stock'}},
        {'status': 'partially_fulfilled', 'details': {'Fulfilled': '1 of 2 items'}},
        {'status': 'fulfilled', 'details': {'Tracking': 'TRK123456', 'Carrier': 'DHL'}}
    ]
    
    results = []
    
    # Send payment status updates
    for payment in payment_statuses:
        success = send_status_update(
            test_data['order_number'],
            'payment',
            payment['status'],
            payment['details']
        )
        results.append({'type': 'payment', 'status': payment['status'], 'success': success})
    
    # Send fulfillment status updates
    for fulfillment in fulfillment_statuses:
        success = send_status_update(
            test_data['order_number'],
            'fulfillment',
            fulfillment['status'],
            fulfillment['details']
        )
        results.append({'type': 'fulfillment', 'status': fulfillment['status'], 'success': success})
    
    return jsonify({
        'success': True,
        'order': test_data['order_number'],
        'results': results,
        'message': 'All status updates sent. Check thread in #shopify-slack'
    })

@app.route('/test-specific/<order_number>/<status_type>/<status>', methods=['GET'])
def test_specific(order_number, status_type, status):
    """Test specific status update"""
    details = {}
    
    if status_type == 'payment':
        details = {'Amount': '$149.99', 'Method': 'Credit Card'}
    elif status_type == 'fulfillment':
        if status == 'fulfilled':
            details = {'Tracking': 'ABC123XYZ', 'Carrier': 'DHL'}
        elif status == 'in_progress':
            details = {'Note': 'Processing in warehouse'}
    
    # First ensure order exists
    if order_number not in order_threads:
        test_data = {
            'order_number': order_number,
            'customer': {'name': 'Test Customer', 'phone': '+971501234567'},
            'line_items': [{'name': 'Test Item', 'quantity': 1}]
        }
        send_order_notification(test_data)
    
    success = send_status_update(order_number, status_type, status, details)
    
    if success:
        return jsonify({
            'success': True,
            'order': order_number,
            'status_type': status_type,
            'status': status,
            'message': f'{status_type} update sent for order #{order_number}'
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
            <h1>✅ Shopify → Slack (Payment & Fulfillment Status)</h1>
            <hr>
            <h3>Payment Status:</h3>
            <ul>
                <li>⏳ Payment Pending</li>
                <li>✅ Payment Received</li>
                <li>🔒 Payment Authorized</li>
                <li>↩️ Refund Processed</li>
                <li>❌ Payment Voided</li>
            </ul>
            
            <h3>Fulfillment Status:</h3>
            <ul>
                <li>📦 Order Not Fulfilled</li>
                <li>⚙️ Fulfillment In Progress</li>
                <li>⏸️ Fulfillment On Hold</li>
                <li>📤 Partially Fulfilled</li>
                <li>🚀 Order Fulfilled</li>
            </ul>
            <hr>
            <h3>Test Endpoints:</h3>
            <ul>
                <li><a href="/test-all-statuses">/test-all-statuses</a> - Test ALL statuses</li>
                <li><a href="/test-specific/TEST123/payment/paid">/test-specific/ORDER/STATUS_TYPE/STATUS</a> - Test specific</li>
                <li><a href="/health">/health</a> - Health check</li>
            </ul>
        </body>
    </html>
    '''

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)