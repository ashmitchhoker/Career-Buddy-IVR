# make_call.py
import os
from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv()

account_sid = os.getenv("TWILIO_ACCOUNT_SID")
auth_token = os.getenv("TWILIO_AUTH_TOKEN")
twilio_number = os.getenv("TWILIO_PHONE_NUMBER")

client = Client(account_sid, auth_token)

# ✅ Replace this with your verified Indian number (must start with +91)
to_number = "+91XXXXXXXXXX"

# Your ngrok URL (same one you used in Twilio webhook)
ngrok_url = "https://nicki-grizzled-trinh.ngrok-free.dev"

call = client.calls.create(
    to=to_number,
    from_=twilio_number,
    url=f"{ngrok_url}/voice"  # Twilio will fetch this to start conversation
)

print(f"Call initiated! SID: {call.sid}")
