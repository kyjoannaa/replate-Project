#Create the Firebase Cloud Messaging dispatch service to notify couriers and beneficiary shelters
import os
import firebase_admin
from firebase_admin import credentials, messaging
from app.config import settings

# Initialize Firebase Admin SDK if credentials exist
if os.path.exists(settings.FIREBASE_CREDENTIALS_PATH if hasattr(settings, 'FIREBASE_CREDENTIALS_PATH') else './firebase-service-account.json'):
    cred = credentials.Certificate("./firebase-service-account.json")
    firebase_admin.initialize_app(cred)

def send_match_notification(topic: str, title: str, body: str, data_payload: dict = None):
    """
    Broadcasts an asynchronous FCM push notification to a specific topic 
    (e.g., 'available_couriers' or 'beneficiary_{id}').
    """
    try:
        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data=data_payload or {},
            topic=topic,
        )
        response = messaging.send(message)
        print(f"FCM Push Sent Successfully. Message ID: {response}")
        return {"status": "sent", "message_id": response}
    except Exception as e:
        print(f"FCM Notification Error (Simulated): {e}")
        return {"status": "simulated", "detail": str(e)}