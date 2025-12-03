import os
import requests
from flask import Flask, request, jsonify
from datetime import datetime
import hmac
import hashlib

app = Flask(__name__)

# Configuration
SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN')
SHOPIFY_ACCESS_TOKEN = os.getenv('SHOPIFY_ACCESS_TOKEN')
SHOPIFY_SHOP_NAME = os.getenv('SHOPIFY_SHOP_NAME', 'fragrantsouq.com')
SLACK_CHANNEL_ID = "C0A068PHZMY"  # Your #shopify-slack channel for testing

# Store thread timestamps for orders
order_threads = {}

def verify_shopify_webhook(data, hmac_header):
    """Verify Shopify webhook signature"""
    shopify_secret = os.getenv('SHOPIFY_WEBHOOK_SECRET', '')
    if not shopify_secret:
        return True  # Skip verification if no secret
    
    calculated_hmac = hmac.new(
        shopify_secret.encode('utf-8'),
        data,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(calculated_hmac, hmac_header)

def get_shopify_order_details(order_id):
    """Fetch real order details from Shopify API"""
    headers = {
        'X-Shopify-Access-Token': SHOPIFY_ACCESS_TOKEN,
        'Content-Type': 'application/json'
    }
    
    try:
        response = requests.get(
            f'https://{SHOPIFY_SHOP_NAME}.myshopify.com/admin/api/2024-01/orders/{order_id}.json',
            headers=headers
        )
        
        if response.status_code == 200:
            return response.json().get('order', {})
    except Exception as e:
        print(f"Error fetching order details: {e}")
    
    return {}

def find_or_create_order_thread(order_data):
    """Find existing order message or create one in #shopify-slack"""
    order_id = order_data.get('id')
    order_number = order_data.get('order_number', f"#{order_id}")
    
    # Check if we already have a thread for this order
    if order_number in order_threads:
        return order_threads[order_number]
    
    # Extract real customer data
    customer = order_data.get('customer', {})
    customer_name = customer.get('name', 'Customer')
    phone = customer.get('phone', 'N/A')
    
    # Get first item
    line_items = order_data.get('line_items', [])
    item_info = "N/A"
    quantity = "N/A"
    
    if line_items:
        first_item = line_items[0]
        item_name = first_item.get('name', 'Item')
        variant = first_item.get('variant_title', '')
        item_info = f"{item_name} {variant}".strip()
        quantity = str(first_item.get('quantity', 1))
    
    # Create the order message (like your Shopify notification)
    headers = {
        'Authorization': f'Bearer {SLACK_BOT_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    # Format exactly like your Shopify notifications
    message_text = f"New Shopify Order Received!\n\n#{order_number} | {customer_name} | {phone} | {item_info} | {quantity}"
    
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
                print(f"✅ Order #{order_number} message created in #shopify-slack")
                return thread_ts
    except Exception as e:
        print(f"Error creating order message: {e}")
    
    return None

def send_payment_update(order_number, status, order_data=None):
    """Send real payment update as threaded reply"""
    if order_number not in order_threads:
        print(f"Order #{order_number} not found in threads")
        return False
    
    thread_ts = order_threads[order_number]
    headers = {
        'Authorization': f'Bearer {SLACK_BOT_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    # Get real status from Shopify
    if order_data:
        financial_status = order_data.get('financial_status', 'pending')
        total_price = order_data.get('total_price', '0.00')
        gateway = order_data.get('gateway', '')
    else:
        financial_status = status
        total_price = ""
        gateway = ""
    
    # Status mapping
    status_config = {
        'pending': {'emoji': '⏳', 'text': 'Payment Pending'},
        'paid': {'emoji': '✅', 'text': 'Payment Received'},
        'authorized': {'emoji': '🔒', 'text': 'Payment Authorized'},
        'refunded': {'emoji': '↩️', 'text': 'Refund Processed'},
        'partially_paid': {'emoji': '💰', 'text': 'Partially Paid'},
        'voided': {'emoji': '❌', 'text': 'Payment Voided'}
    }
    
    config = status_config.get(financial_status, {'emoji': '📝', 'text': financial_status.title()})
    time_now = datetime.now().strftime("%I:%M %p")
    
    # Create update message with real data
    message_text = f"{config['emoji']} *{config['text']}* • {time_now}"
    
    if total_price:
        message_text += f"\nAmount: ${total_price}"
    if gateway:
        message_text += f"\nMethod: {gateway}"
    
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
                print(f"✅ Payment update sent for order #{order_number}: {financial_status}")
                return True
    except Exception as e:
        print(f"Error sending payment update: {e}")
    
    return False

@app.route('/webhook/shopify', methods=['POST'])
def shopify_webhook():
    """Handle REAL Shopify webhooks"""
    # Verify webhook signature
    hmac_header = request.headers.get('X-Shopify-Hmac-Sha256', '')
    if not verify_shopify_webhook(request.data, hmac_header):
        return jsonify({'error': 'Invalid signature'}), 401
    
    try:
        data = request.json
        webhook_topic = request.headers.get('X-Shopify-Topic', '')
        
        print(f"📦 Real Shopify webhook received: {webhook_topic}")
        
        order_id = data.get('id')
        order_number = data.get('order_number', f"#{order_id}")
        financial_status = data.get('financial_status', 'pending')
        
        if webhook_topic == 'orders/create':
            # New order - create message in #shopify-slack
            thread_ts = find_or_create_order_thread(data)
            if thread_ts:
                # Send initial payment status
                send_payment_update(order_number, financial_status, data)
                return jsonify({'success': True}), 200
        
        elif webhook_topic == 'orders/updated':
            # Order updated - check if payment status changed
            old_status = "unknown"  # In production, you'd compare with previous status
            thread_ts = find_or_create_order_thread(data)
            
            if thread_ts:
                # Send payment update
                send_payment_update(order_number, financial_status, data)
                return jsonify({'success': True}), 200
        
        return jsonify({'success': True}), 200
        
    except Exception as e:
        print(f"Error processing webhook: {str(e)}")
        return jsonify({'error': str(e)}), 400

@app.route('/real-order/<order_id>', methods=['GET'])
def fetch_real_order(order_id):
    """Fetch and display a REAL Shopify order"""
    order_data = get_shopify_order_details(order_id)
    
    if not order_data:
        return jsonify({'error': 'Order not found'}), 404
    
    order_number = order_data.get('order_number', f"#{order_id}")
    customer = order_data.get('customer', {})
    
    # Create message in #shopify-slack
    thread_ts = find_or_create_order_thread(order_data)
    
    if thread_ts:
        # Send current payment status
        send_payment_update(order_number, order_data.get('financial_status', 'pending'), order_data)
        
        return jsonify({
            'success': True,
            'order': order_number,
            'customer': customer.get('name'),
            'status': order_data.get('financial_status'),
            'amount': order_data.get('total_price'),
            'message': f'Real order #{order_number} posted to #shopify-slack'
        })
    
    return jsonify({'error': 'Failed to create order message'}), 500

@app.route('/update-real-order/<order_id>', methods=['GET'])
def update_real_order_status(order_id):
    """Manually trigger update for a real order"""
    order_data = get_shopify_order_details(order_id)
    
    if not order_data:
        return jsonify({'error': 'Order not found'}), 404
    
    order_number = order_data.get('order_number', f"#{order_id}")
    financial_status = order_data.get('financial_status', 'pending')
    
    # Find existing thread or create one
    thread_ts = find_or_create_order_thread(order_data)
    
    if thread_ts:
        success = send_payment_update(order_number, financial_status, order_data)
        
        if success:
            return jsonify({
                'success': True,
                'order': order_number,
                'status': financial_status,
                'message': f'Payment status updated for order #{order_number}'
            })
    
    return jsonify({'error': 'Failed to update order'}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'healthy',
        'shop': SHOPIFY_SHOP_NAME,
        'slack_channel': 'shopify-slack',
        'mode': 'production'
    }), 200

@app.route('/debug/orders', methods=['GET'])
def debug_orders():
    """Show currently tracked orders"""
    return jsonify({
        'tracked_orders': list(order_threads.keys()),
        'total_orders': len(order_threads)
    })

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)