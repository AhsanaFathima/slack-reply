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
    try:
        phone = str(phone)
        phone = ''.join(filter(str.isdigit, phone))
        if phone.startswith('971') and len(phone) == 12:
            return f"+{phone[:3]} {phone[3:]}"
        return phone
    except:
        return "N/A"

def truncate_text(text, max_length=15):
    """Truncate text for horizontal display"""
    if not text:
        return "N/A"
    text = str(text)
    if len(text) <= max_length:
        return text
    return text[:max_length-2] + ".."

def create_horizontal_table(order_data):
    """Create HORIZONTAL table format (single line)"""
    try:
        order_number = order_data.get('order_number') or order_data.get('name', 'N/A')
        customer = order_data.get('customer', {})
        customer_name = customer.get('name', 'Customer')
        phone = format_phone(customer.get('phone', 'N/A'))
        
        # Get first item only
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
        
        # Truncate for horizontal display
        order_display = truncate_text(order_number, 18)
        customer_display = truncate_text(customer_name, 18)
        phone_display = truncate_text(phone, 17)
        item_display = truncate_text(item_info, 25)
        quantity_display = truncate_text(quantity, 8)
        
        # Create HORIZONTAL table (single line)
        message = "📦 *NEW ORDER*\n"
        message += "┌──────────┬────────────┬────────────┬────────────────────┬───────┐\n"
        message +=f"│ Order #  │ Customer   │ Phone      │ Item               │ Qty   │\n"
        message += "├──────────┼────────────┼────────────┼────────────────────┼───────┤\n"
        message +=f"│ {order_display:<8} │ {customer_display:<10} │ {phone_display:<10} │ {item_display:<18} │ {quantity_display:<5} │\n"
        message += "└──────────┴────────────┴────────────┴────────────────────┴───────┘"
        
        return message
    except Exception as e:
        print(f"❌ Error creating table: {e}")
        return "📦 *NEW ORDER*\nError creating table"

def send_order_notification(order_data):
    """Send order notification in HORIZONTAL table format"""
    if not SLACK_BOT_TOKEN:
        print("❌ No Slack bot token configured")
        return None
    
    headers = {
        'Authorization': f'Bearer {SLACK_BOT_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    try:
        order_number = order_data.get('order_number') or order_data.get('name', 'N/A')
        message_text = create_horizontal_table(order_data)
        
        print(f"📤 Sending order #{order_number} to Slack...")
        
        # Send the order notification
        message = {
            'channel': SLACK_CHANNEL_ID,
            'text': message_text
        }
        
        response = requests.post(
            'https://slack.com/api/chat.postMessage',
            headers=headers,
            json=message,
            timeout=10
        )
        
        if response.status_code == 200:
            result = response.json()
            if result.get('ok'):
                thread_ts = result['ts']
                order_threads[order_number] = thread_ts
                print(f"✅ Order #{order_number} notification sent")
                return thread_ts
            else:
                print(f"❌ Slack error: {result.get('error')}")
                
    except Exception as e:
        print(f"❌ Error sending to Slack: {e}")
    
    return None

def send_status_update(order_number, status_type, status, details=None):
    """Send status update as threaded reply"""
    if not SLACK_BOT_TOKEN:
        print("❌ No Slack bot token configured")
        return False
    
    if order_number not in order_threads:
        print(f"❌ Order #{order_number} not found in threads")
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
    
    try:
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
        
        # Get exact status
        if status:
            status_lower = status.lower()
            config = status_map.get(status_lower, {'emoji': '📝', 'text': status.title() if status else 'Unknown'})
        else:
            config = {'emoji': '❓', 'text': 'Unknown Status'}
        
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
        
        response = requests.post(
            'https://slack.com/api/chat.postMessage',
            headers=headers,
            json=message,
            timeout=10
        )
        
        if response.status_code == 200:
            result = response.json()
            if result.get('ok'):
                print(f"✅ {status_type.title()} update for #{order_number}: {status}")
                return True
            
    except Exception as e:
        print(f"❌ Error sending status update: {e}")
    
    return False

@app.route('/webhook/shopify', methods=['POST'])
def shopify_webhook():
    """Handle Shopify webhooks"""
    print("📩 Shopify webhook received")
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data received'}), 400
        
        webhook_topic = request.headers.get('X-Shopify-Topic', 'Unknown')
        
        # Extract order info
        order_number = data.get('order_number') or data.get('name') or f"ID-{data.get('id', 'unknown')}"
        financial_status = data.get('financial_status', 'pending')
        fulfillment_status = data.get('fulfillment_status', 'unfulfilled')
        total_price = data.get('total_price', '0.00')
        
        # Map Shopify status to our exact status names
        status_mapping = {
            'pending': 'payment pending',
            'partially_fulfilled': 'partially fulfilled',
            'partial': 'partially fulfilled'
        }
        
        payment_status = status_mapping.get(financial_status.lower() if financial_status else '', financial_status)
        fulfillment_status_mapped = status_mapping.get(fulfillment_status.lower() if fulfillment_status else '', fulfillment_status)
        
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
                return jsonify({'success': True, 'order': order_number}), 200
        
        elif webhook_topic == 'orders/updated':
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
                    fulfillment_status_mapped,
                    fulfillment_details
                )
            
            return jsonify({'success': True, 'order': order_number}), 200
        
        return jsonify({'success': True}), 200
        
    except Exception as e:
        print(f"❌ ERROR in webhook handler: {str(e)}")
        return jsonify({'error': str(e)}), 400

@app.route('/test-horizontal', methods=['GET'])
def test_horizontal():
    """Test HORIZONTAL table format"""
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
        ]
    }
    
    thread_ts = send_order_notification(test_data)
    
    if thread_ts:
        # Send test updates
        send_status_update(test_data['order_number'], 'payment', 'payment pending', {'Amount': '$149.99'})
        send_status_update(test_data['order_number'], 'payment', 'paid', {'Amount': '$149.99', 'Method': 'Credit Card'})
        
        return jsonify({
            'success': True,
            'order': test_data['order_number'],
            'message': 'HORIZONTAL table sent. Check #shopify-slack!'
        })
    
    return jsonify({'error': 'Failed to send'}), 500

@app.route('/test-multiple', methods=['GET'])
def test_multiple():
    """Test with longer item name"""
    test_data = {
        'order_number': '1258',
        'customer': {
            'name': 'John Smith',
            'phone': '+971501234567'
        },
        'line_items': [
            {
                'name': 'Abercrombie & Fitch Authentic Night 100 ml EDP Women Perfume Long Name Test',
                'variant_title': 'Premium Edition',
                'quantity': 2
            }
        ]
    }
    
    thread_ts = send_order_notification(test_data)
    
    if thread_ts:
        return jsonify({
            'success': True,
            'order': test_data['order_number'],
            'note': 'Long item name truncated in horizontal table'
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
            <h1>✅ Shopify → Slack (Horizontal Table)</h1>
            <p><strong>Horizontal Table Format:</strong></p>
            <pre style="background: #f5f5f5; padding: 10px; border-radius: 5px;">
📦 NEW ORDER
┌──────────┬────────────┬────────────┬────────────────────┬───────┐
│ Order #  │ Customer   │ Phone      │ Item               │ Qty   │
├──────────┼────────────┼────────────┼────────────────────┼───────┤
│ 1257     │ Ahsana     │ +971 5459..│ Ahmed Al Maghribi..│ 1     │
└──────────┴────────────┴────────────┴────────────────────┴───────┘
            </pre>
            <hr>
            <p><a href="/test-horizontal">/test-horizontal</a> - Test horizontal table</p>
            <p><a href="/test-multiple">/test-multiple</a> - Test with long item name</p>
            <p><a href="/health">/health</a> - Health check</p>
        </body>
    </html>
    '''

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)