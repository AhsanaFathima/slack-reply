import os
import requests
from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)

# Configuration
SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN')
# Channel for NEW order notifications (main messages)
ORDER_CHANNEL = "C0A068PHZMY"  # Your #shopify-slack channel
# If you want different channel for new orders, change this

# Store thread IDs for each order
order_threads = {}

def format_phone_number(phone):
    """Format phone number"""
    if not phone:
        return "N/A"
    # Remove any non-digit characters
    digits = ''.join(filter(str.isdigit, phone))
    if len(digits) == 12 and digits.startswith('971'):  # UAE format
        return f"+{digits[:3]} {digits[3:]}"
    return phone

def send_new_order_notification(order_data):
    """Send NEW ORDER notification to channel"""
    headers = {
        'Authorization': f'Bearer {SLACK_BOT_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    order_number = order_data.get('order_number', 'N/A')
    customer = order_data.get('customer', {})
    customer_name = customer.get('name', 'Customer')
    customer_email = order_data.get('contact_email', customer.get('email', 'N/A'))
    customer_phone = format_phone_number(customer.get('phone', 'N/A'))
    total_price = order_data.get('total_price', '0.00')
    
    # Get first item details
    line_items = order_data.get('line_items', [])
    item_details = "N/A"
    quantity = "N/A"
    
    if line_items:
        first_item = line_items[0]
        item_details = f"{first_item.get('name', 'Item')} {first_item.get('variant_title', '')}".strip()
        quantity = str(first_item.get('quantity', 1))
    
    # Create the NEW ORDER message
    message = {
        'channel': ORDER_CHANNEL,
        'text': f'📦 NEW ORDER #{order_number}',
        'blocks': [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"📦 NEW ORDER #{order_number}",
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
                        "text": f"*Phone:*\n{customer_phone}"
                    }
                ]
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Email:*\n{customer_email}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Amount:*\n${total_price}"
                    }
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Item:*\n{item_details}"
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Quantity:*\n{quantity}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Items Count:*\n{len(line_items)}"
                    }
                ]
            },
            {
                "type": "divider"
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "_Payment updates will appear as replies to this message_"
                    }
                ]
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
                # Store thread timestamp for this order
                order_threads[order_number] = thread_ts
                print(f"✅ New order notification sent for #{order_number}")
                return thread_ts
            else:
                print(f"❌ Slack error: {result.get('error')}")
                return None
    except Exception as e:
        print(f"❌ Error: {e}")
        return None

def send_order_update(order_number, status, details):
    """Send order update as thread reply"""
    if order_number not in order_threads:
        print(f"❌ No thread found for order #{order_number}")
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
        'partially_paid': {'emoji': '💰', 'text': 'Partially Paid'},
        'voided': {'emoji': '❌', 'text': 'Payment Voided'},
        'expired': {'emoji': '⌛', 'text': 'Payment Expired'}
    }
    
    config = status_config.get(status, {'emoji': '📝', 'text': status.title()})
    time_now = datetime.now().strftime("%I:%M %p")
    
    # Create update message
    message_text = f"{config['emoji']} *{config['text']}* • {time_now}"
    
    # Add details if provided
    if details:
        message_text += "\n"
        for key, value in details.items():
            if value:  # Only add if value exists
                message_text += f"{key}: {value}\n"
    
    message = {
        'channel': ORDER_CHANNEL,
        'thread_ts': thread_ts,  # This makes it a threaded reply
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
                print(f"✅ Update sent for order #{order_number}: {status}")
                return True
            else:
                print(f"❌ Slack error: {result.get('error')}")
                return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

@app.route('/webhook/shopify', methods=['POST'])
def shopify_webhook():
    """Receive Shopify webhooks"""
    try:
        data = request.json
        webhook_topic = request.headers.get('X-Shopify-Topic', '')
        
        print(f"📩 Received: {webhook_topic}")
        
        # Extract order info
        order_id = data.get('id')
        order_number = data.get('order_number', f"#{order_id}")
        financial_status = data.get('financial_status', 'pending')
        fulfillment_status = data.get('fulfillment_status')
        total_price = data.get('total_price', '0.00')
        
        if webhook_topic == 'orders/create':
            # New order - send main notification
            thread_ts = send_new_order_notification(data)
            if thread_ts:
                # Also send initial status as first reply
                initial_status = 'created' if financial_status == 'pending' else financial_status
                send_order_update(order_number, initial_status, {
                    'Initial Status': initial_status.title(),
                    'Amount': f"${total_price}"
                })
                return jsonify({'success': True}), 200
            else:
                return jsonify({'error': 'Failed to send notification'}), 500
        
        elif webhook_topic == 'orders/updated':
            # Order update - send threaded reply
            details = {}
            
            # Check what changed
            if financial_status:
                details['Payment Status'] = financial_status.title()
            
            if fulfillment_status and fulfillment_status != 'unfulfilled':
                details['Fulfillment'] = fulfillment_status.title()
            
            if data.get('note'):
                details['Note'] = data.get('note')[:100]
            
            # Determine which status to show
            if fulfillment_status and fulfillment_status != 'unfulfilled':
                status = fulfillment_status
            else:
                status = financial_status
            
            success = send_order_update(order_number, status, details)
            
            if success:
                return jsonify({'success': True}), 200
            else:
                return jsonify({'error': 'Failed to send update'}), 500
        
        return jsonify({'success': True}), 200
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return jsonify({'error': str(e)}), 400

@app.route('/test/new-order', methods=['GET'])
def test_new_order():
    """Test new order notification"""
    test_data = {
        'order_number': 'TEST-' + str(datetime.now().strftime("%H%M")),
        'customer': {
            'name': 'Ahsana',
            'phone': '+971545982212',
            'email': 'ahsana@example.com'
        },
        'contact_email': 'ahsana@example.com',
        'total_price': '149.99',
        'line_items': [
            {
                'name': 'Abercrombie & Fitch Authentic Night 100 ml',
                'variant_title': 'EDP Women Perfume',
                'quantity': 1
            }
        ]
    }
    
    thread_ts = send_new_order_notification(test_data)
    
    if thread_ts:
        # Simulate updates
        updates = [
            {'status': 'pending', 'details': {'Amount': '$149.99'}},
            {'status': 'paid', 'details': {'Amount': '$149.99', 'Method': 'Credit Card'}},
            {'status': 'fulfilled', 'details': {'Tracking': 'ABC123XYZ', 'Carrier': 'DHL'}}
        ]
        
        for update in updates:
            send_order_update(test_data['order_number'], update['status'], update['details'])
        
        return jsonify({
            'success': True,
            'order': test_data['order_number'],
            'message': 'Check #shopify-slack for new order notification and threaded updates'
        })
    
    return jsonify({'error': 'Test failed'}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy'}), 200

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)