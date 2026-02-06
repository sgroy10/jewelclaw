"""Test Twilio WhatsApp integration."""

import os
from dotenv import load_dotenv
from twilio.rest import Client

load_dotenv()

# Load credentials
account_sid = os.getenv("TWILIO_ACCOUNT_SID")
auth_token = os.getenv("TWILIO_AUTH_TOKEN")
from_number = os.getenv("TWILIO_WHATSAPP_NUMBER")
test_phone = os.getenv("TEST_PHONE_NUMBER")

print("=" * 50)
print("Twilio WhatsApp Test")
print("=" * 50)
print(f"Account SID: {account_sid[:10]}...{account_sid[-4:]}")
print(f"From Number: {from_number}")
print(f"Test Phone:  {test_phone}")
print("=" * 50)

if not test_phone or test_phone == "+91XXXXXXXXXX":
    print("\n[!] Please set TEST_PHONE_NUMBER in .env file")
    print("    Format: +91XXXXXXXXXX (your WhatsApp number)")
    exit(1)

# Initialize client
client = Client(account_sid, auth_token)

# Test message
message_body = """🙏 *JewelClaw Test Message*

Your WhatsApp integration is working!

Today's Gold Rates (Mumbai):
💰 24K: Rs.15,442/gram
💰 22K: Rs.14,155/gram
💎 Silver: Rs.300/gram

Reply with "gold rate" to get live rates."""

try:
    print("\nSending test message...")

    message = client.messages.create(
        body=message_body,
        from_=from_number,
        to=f"whatsapp:{test_phone}" if not test_phone.startswith("whatsapp:") else test_phone
    )

    print(f"\n[OK] Message sent!")
    print(f"     SID: {message.sid}")
    print(f"     Status: {message.status}")
    print(f"\nCheck your WhatsApp for the message.")

except Exception as e:
    print(f"\n[ERROR] {e}")
    print("\nTroubleshooting:")
    print("1. Make sure you've joined the Twilio sandbox")
    print("   Send 'join <your-code>' to +1 415 523 8886")
    print("2. Check your credentials in .env")
    print("3. Verify your phone number format (+91XXXXXXXXXX)")
