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

# Store order threads (in memory)
order_threads = {}


def send_to_slack(
    order_number,
    status,
    customer_name,
    amount,
    customer_email="",
    phone="",
    item_name="",
    quantity=""
):
    """
    Send message to Slack.

    - If status == 'created'  -> create parent message like the screenshot
    - Else                    -> send status message as a reply in that thread
    """
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
        'created': '📦',
        'voided': '❌'
    }

    status = (status or '').lower()
    emoji = emojis.get(status, '📦')
    time_now = datetime.now().strftime("%I:%M %p")

    # -----------------------------
    # 1) NEW ORDER MESSAGE (PARENT)
    # -----------------------------
    if status == 'created':
        # Format exactly like your screenshot
        # Line 1
        message_text = "🛒 New Shopify Order Received!\n"

        # Line 2: #1256 | Name | Phone | Item | Qty
        line2_parts = [
            f"#{order_number}",
            customer_name or "Customer",
            phone or "No phone",
            item_name or "Item",
            str(quantity or "1")
        ]
        message_text += " | ".join(line2_parts)

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
                    # Save ts for future thread replies
                    order_threads[order_number] = result['ts']
                    print(f"✅ Created parent message & thread for order #{order_number}")
                    return True
                else:
                    print(f"❌ Slack error (parent): {result.get('error')}")
                    return False
            else:
                print(f"❌ Slack HTTP error (parent): {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ Error creating parent message: {e}")
            return False

    # --------------------------------
    # 2) STATUS UPDATE (THREAD REPLY)
    # --------------------------------
    # If we don't yet know this order's thread (app restarted, etc),
    # create a parent message first so we have something to reply under.
    if order_number not in order_threads:
        parent_text = "🛒 New Shopify Order Received!\n"
        parent_line2 = " | ".join([
            f"#{order_number}",
            customer_name or "Customer",
            phone or "No phone",
            item_name or "Item",
            str(quantity or "1")
        ])
        parent_text += parent_line2

        parent_data = {
            'channel': SLACK_CHANNEL_ID,
            'text': parent_text
        }

        try:
            parent_res = requests.post(
                'https://slack.com/api/chat.postMessage',
                headers=headers,
                json=parent_data
            )
            if parent_res.status_code == 200:
                parent_result = parent_res.json()
                if parent_result.get('ok'):
                    order_threads[order_number] = parent_result['ts']
                    print(f"ℹ️ Parent message auto-created for order #{order_number}")
                else:
                    print(f"❌ Slack error (auto-parent): {parent_result.get('error')}")
                    return False
            else:
                print(f"❌ Slack HTTP error (auto-parent): {parent_res.status_code}")
                return False
        except Exception as e:
            print(f"❌ Error auto-creating parent: {e}")
            return False

    thread_ts = order_threads.get(order_number)
    if not thread_ts:
        print(f"❌ No thread_ts for order #{order_number}")
        return False

    # Build status message in thread
    # 🔴 as you requested: NO customer name, NO email in thread
    message_text = f"{emoji} *{status.upper()}* • {time_now}\n"
    message_text += f"Order: #{order_number}\n"
    message_text += f"Amount: ${amount}"

    data = {
        'channel': SLACK_CHANNEL_ID,
        'thread_ts': thread_ts,
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
                print(f"✅ Thread reply sent for order #{order_number} ({status})")
                return True
            else:
                print(f"❌ Slack error (thread): {result.get('error')}")
                return False
        else:
            print(f"❌ Slack HTTP error (thread): {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Error sending thread reply: {e}")
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

        # Extract basic order info
        order_id = data.get('id')
        order_number = str(data.get('order_number', f"{order_id}")).lstrip("#")
        financial_status = data.get('financial_status', 'pending')
        total_price = data.get('total_price', '0')

        # Get customer info
        customer = data.get('customer', {}) or {}
        customer_name = customer.get('name') or (
            (customer.get('first_name', '') + " " + customer.get('last_name', '')).strip()
        ) or 'Customer'
        customer_email = data.get('contact_email') or customer.get('email', '')

        # Phone: shipping -> billing -> customer
        shipping_address = data.get('shipping_address') or {}
        billing_address = data.get('billing_address') or {}
        phone = (
            shipping_address.get('phone') or
            billing_address.get('phone') or
            customer.get('phone') or
            ""
        )

        # Item name & quantity (like screenshot uses first item)
        line_items = data.get('line_items', []) or []
        if line_items:
            first_item = line_items[0]
            item_name = first_item.get('title') or first_item.get('name', 'Item')
            quantity = first_item.get('quantity', 1)
        else:
            item_name = ""
            quantity = ""

        # Check webhook type
        webhook_topic = request.headers.get('X-Shopify-Topic', '')
        print(f"📝 Webhook topic: {webhook_topic}")

        # For new orders, use 'created' status (parent message)
        if webhook_topic == 'orders/create':
            status = 'created'
        else:
            status = financial_status or 'pending'

        # Send to Slack
        success = send_to_slack(
            order_number=order_number,
            status=status,
            customer_name=customer_name,
            amount=total_price,
            customer_email=customer_email,
            phone=phone,
            item_name=item_name,
            quantity=quantity
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
    # Simulate "created" parent message
    success_created = send_to_slack(
        order_number='TEST-001',
        status='created',
        customer_name='Test Customer',
        amount='149.99',
        customer_email='test@example.com',
        phone='0500000000',
        item_name='Demo Perfume 100ml',
        quantity=1
    )

    # Simulate a later "paid" update in same thread
    success_paid = send_to_slack(
        order_number='TEST-001',
        status='paid',
        customer_name='Test Customer',
        amount='149.99',
        customer_email='test@example.com',
        phone='0500000000',
        item_name='Demo Perfume 100ml',
        quantity=1
    )

    if success_created and success_paid:
        return jsonify({
            'success': True,
            'message': 'Test messages sent. Check #shopify-slack channel.'
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
