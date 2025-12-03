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
        return f"+{phone[:3]} {phone[3:]}"
    return phone

def create_simple_table(order_data):
    """Create SIMPLE table with only required fields"""
    order_number = order_data.get('order_number', 'N/A')
    customer = order_data.get('customer', {})
    customer_name = customer.get('name', 'Customer')
    phone = format_phone(customer.get('phone', 'N/A'))
    
    # Get first item only (as per your example)
    line_items = order_data.get('line_items', [])
    item_info = "N/A"
    quantity = "N/A"
    
    if line_items:
        first_item = line_items[0]
        item_name = first_item.get('name', 'Item')
        variant = first_item.get('variant_title', '')
        item_info = f"{item_name}"
        if variant:
            item_info += f" {variant}"
        quantity = str(first_item.get('quantity', 1))
    
    # Create SIMPLE table format
    message = "📦 *NEW ORDER*\n"
    message += "┌─────────────────────────────────────┐\n"
    message += f"│ {'Order #:':<15} {order_number:<20} │\n"
    message += f"│ {'Customer:':<15} {customer_name:<20} │\n"
    message += f"│ {'Phone:':<15} {phone:<20} │\n"
    message += f"│ {'Item:':<15} {item_info:<20} │\n"
    message += f"│ {'Quantity:':<15} {quantity:<20} │\n"
    message += "└─────────────────────────────────────┘"
    
    return message

def send_order_notification(order_data):
    """Send order notification in SIMPLE table format"""
    headers = {
        'Authorization': f'Bearer {SLACK_BOT_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    order_number = order_data.get('order_number', 'N/A')
    message_text = create_simple_table(order_data)
    
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
    
    # EXACT STATUS NAMES as specified
    payment_status = {
        'paid': {'emoji': '✅', 'text': 'Payment Paid'},
        'payment pending': {'emoji': '⏳', 'text': 'Payment Pending'},
        'authorized': {'emoji': '🔒', 'text': 'Payment Authorized'},
        'refunded': {'emoji': '↩️', 'text': 'Payment Refunded'},
        'voided': {'emoji': '❌', 'text': 'Payment Voided'},
    }
    
    fulfillment_status = {
        'fulfilled': {'emoji': '🚀', 'text': 'Fulfilled'},
        'unfulfilled': {'emoji': '📦', 'text': 'Unfulfilled'},
        'partially fulfilled': {'emoji': '📤', 'text': 'Partially Fulfilled'},
        'in progress': {'emoji': '⚙️', 'text': 'In Progress'},
        'on hold': {'emoji': '⏸️', 'text': 'On Hold'},
    }
    
    # Get config based on status type
    if status_type == 'payment':
        status_map = payment_status
        prefix = '💳'
    elif status_type == 'fulfillment':
        status_map = fulfillment_status
        prefix = '📦'
    else:
        status_map = {}
        prefix = '📝'
    
    # Get exact status (case-insensitive)
    status_lower = status.lower()
    config = status_map.get(status_lower, {'emoji': '📝', 'text': status.title()})
    
    time_now = datetime.now().strftime("%I:%M %p")
    
    # Create message
    message_text = f"{prefix} {config['emoji']} *{config['text']}* • {time_now}"
    
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
        financial_status = data.get('financial_status', 'pending').lower()
        fulfillment_status = data.get('fulfillment_status', 'unfulfilled').lower()
        total_price = data.get('total_price', '0.00')
        
        # Map Shopify status to our exact status names
        status_mapping = {
            'pending': 'payment pending',
            'partially_fulfilled': 'partially fulfilled',
            'partial': 'partially fulfilled'
        }
        
        payment_status = status_mapping.get(financial_status, financial_status)
        fulfillment_status = status_mapping.get(fulfillment_status, fulfillment_status)
        
        if webhook_topic == 'orders/create':
            # New order - send notification
            thread_ts = send_order_notification(data)
            if thread_ts:
                # Send initial payment status
                send_status_update(
                    order_number, 
                    'payment',
                    payment_status,
                    {'Amount': f"${total_price}"}
                )
                return jsonify({'success': True}), 200
        
        elif webhook_topic == 'orders/updated':
            # Order update - check what changed
            
            # First, ensure we have a thread for this order
            if order_number not in order_threads:
                send_order_notification(data)
            
            # Check if payment status changed
            if financial_status and financial_status != 'pending':
                details = {'Amount': f"${total_price}"}
                if data.get('gateway'):
                    details['Method'] = data.get('gateway')
                
                send_status_update(
                    order_number,
                    'payment',
                    payment_status,
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

@app.route('/test-simple-table', methods=['GET'])
def test_simple_table():
    """Test SIMPLE table format"""
    test_data = {
        'order_number': '1257',
        'customer': {
            'name': 'Ahsana',
            'phone': '+971545982212'
        },
        'total_price': '149.99',
        'line_items': [
            {
                'name': 'Ahmed Al Maghribi Bidun Esam 12 ml',
                'variant_title': 'CPO Unisex Perfume',
                'quantity': 1
            }
        ],
        'financial_status': 'pending'
    }
    
    # Send order notification in SIMPLE table format
    thread_ts = send_order_notification(test_data)
    
    if not thread_ts:
        return jsonify({'error': 'Failed to create order'}), 500
    
    # Test status updates
    updates = [
        {'type': 'payment', 'status': 'payment pending', 'details': {'Amount': '$149.99'}},
        {'type': 'payment', 'status': 'paid', 'details': {'Amount': '$149.99', 'Method': 'Credit Card'}},
        {'type': 'fulfillment', 'status': 'in progress', 'details': {}},
        {'type': 'fulfillment', 'status': 'fulfilled', 'details': {'Tracking': 'TRK789012'}}
    ]
    
    for update in updates:
        send_status_update(
            test_data['order_number'],
            update['type'],
            update['status'],
            update['details']
        )
    
    return jsonify({
        'success': True,
        'order': test_data['order_number'],
        'message': 'Simple table format sent to #shopify-slack'
    })

@app.route('/test-multiple-items', methods=['GET'])
def test_multiple_items():
    """Test with multiple items (shows only first item)"""
    test_data = {
        'order_number': '1254',
        'customer': {
            'name': 'Test Customer',
            'phone': '+971501234567'
        },
        'line_items': [
            {
                'name': 'Abercrombie & Fitch Authentic Night 100 ml',
                'variant_title': 'EDP Women Perfume',
                'quantity': 1
            },
            {
                'name': 'Abercrombie & Fitch Authentic Night 100 ml',
                'variant_title': 'EDT Men Perfume',
                'quantity': 1
            }
        ]
    }
    
    thread_ts = send_order_notification(test_data)
    
    if thread_ts:
        send_status_update(
            test_data['order_number'],
            'payment',
            'paid',
            {'Amount': '$299.98'}
        )
        
        return jsonify({
            'success': True,
            'order': test_data['order_number'],
            'note': 'Only first item shown in table'
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
            <h1>✅ Shopify → Slack (Simple Table)</h1>
            <hr>
            <h3>Table Format:</h3>
            <pre>
📦 NEW ORDER
┌─────────────────────────────────────┐
│ Order #:          1257              │
│ Customer:         Ahsana            │
│ Phone:            +971 545982212    │
│ Item:             Ahmed Al Maghribi │
│                   Bidun Esam 12 ml  │
│                   CPO Unisex Perfume│
│ Quantity:         1                 │
└─────────────────────────────────────┘
            </pre>
            
            <h3>Status Updates (Threaded):</h3>
            <ul>
                <li>💳 ⏳ Payment Pending • 6:48 PM</li>
                <li>💳 ✅ Payment Paid • 6:49 PM</li>
                <li>📦 ⚙️ In Progress • 6:50 PM</li>
                <li>📦 🚀 Fulfilled • 6:51 PM</li>
            </ul>
            <hr>
            <h3>Test:</h3>
            <ul>
                <li><a href="/test-simple-table">/test-simple-table</a> - Test simple table</li>
                <li><a href="/health">/health</a> - Health check</li>
            </ul>
        </body>
    </html>
    '''

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)