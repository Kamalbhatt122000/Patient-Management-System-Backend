"""
Application configuration module.
Handles Firebase initialization and app-level settings.
"""
import os
import firebase_admin
from firebase_admin import credentials, firestore

# Path to Firebase service account key
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICE_ACCOUNT_KEY = os.path.join(
    BASE_DIR,
    "hospital-management-c603f-firebase-adminsdk-fbsvc-8ef915ee2b.json"
)

# Upload folder for medical reports
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads", "reports")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "gif", "bmp", "webp"}
MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB

def init_firebase():
    """Initialize Firebase Admin SDK (idempotent)."""
    if not firebase_admin._apps:
        cred = credentials.Certificate(SERVICE_ACCOUNT_KEY)
        firebase_admin.initialize_app(cred)
    return firestore.client()

def allowed_file(filename):
    """Check if a file has an allowed extension."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
