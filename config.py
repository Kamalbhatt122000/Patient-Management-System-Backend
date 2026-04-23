"""
Application configuration module.
Handles Firebase initialization and app-level settings.
"""
import os
import json
import base64
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


def init_salesforce():
    """Initialize and return the Salesforce client."""
    from salesforce_client import get_salesforce_client
    return get_salesforce_client()

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)

CRED_FILENAME = "hospital-management-c603f-firebase-adminsdk-fbsvc-8ef915ee2b.json"

def _find_credentials():
    """Find Firebase credentials from env var, current dir, or parent dir."""
    # 1. Environment variable (supports raw JSON or base64-encoded JSON)
    cred_json = os.environ.get("FIREBASE_CREDENTIALS")
    if cred_json:
        # Try base64 first
        try:
            decoded = base64.b64decode(cred_json).decode("utf-8")
            return json.loads(decoded)
        except Exception:
            pass
        # Try raw JSON
        try:
            return json.loads(cred_json)
        except json.JSONDecodeError:
            pass
    # 2. Environment variable with file path
    cred_path = os.environ.get("FIREBASE_CREDENTIALS_PATH")
    if cred_path and os.path.exists(cred_path):
        return cred_path
    # 3. Same directory as this file (backend/)
    local = os.path.join(CURRENT_DIR, CRED_FILENAME)
    if os.path.exists(local):
        return local
    # 4. Parent directory
    parent = os.path.join(PARENT_DIR, CRED_FILENAME)
    if os.path.exists(parent):
        return parent
    raise FileNotFoundError(
        f"Firebase credentials not found. Set FIREBASE_CREDENTIALS env var "
        f"or place {CRED_FILENAME} in the backend directory."
    )

# Upload folder for medical reports
UPLOAD_FOLDER = os.path.join(CURRENT_DIR, "uploads", "reports")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "gif", "bmp", "webp"}
MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB

def init_firebase():
    """Initialize Firebase Admin SDK (idempotent)."""
    if not firebase_admin._apps:
        cred_source = _find_credentials()
        if isinstance(cred_source, dict):
            cred = credentials.Certificate(cred_source)
        else:
            cred = credentials.Certificate(cred_source)
        firebase_admin.initialize_app(cred)
    return firestore.client()

def allowed_file(filename):
    """Check if a file has an allowed extension."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
