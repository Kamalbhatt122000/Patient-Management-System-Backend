"""
Main Flask application entry point.
Registers all blueprints and configures middleware.
"""
from flask import Flask
from flask_cors import CORS
from config import init_firebase, init_salesforce, UPLOAD_FOLDER, MAX_CONTENT_LENGTH

# Import route blueprints
from routes.patients import patients_bp
from routes.appointments import appointments_bp
from routes.reports import reports_bp
from routes.doctors import doctors_bp

def create_app():
    """Application factory."""
    app = Flask(__name__)
    
    # --- Configuration ---
    app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
    
    # --- CORS ---
    CORS(app, resources={r"/api/*": {"origins": "*"}})
    
    # --- Firebase ---
    db = init_firebase()
    app.config["FIRESTORE_DB"] = db
    
    # --- Salesforce ---
    try:
        sf = init_salesforce()
        app.config["SF_CLIENT"] = sf
        print("✅ Salesforce integration enabled")
    except Exception as e:
        app.config["SF_CLIENT"] = None
        print(f"⚠️  Salesforce init failed (app will still run): {e}")
    
    # --- Register Blueprints ---
    app.register_blueprint(patients_bp, url_prefix="/api/patients")
    app.register_blueprint(appointments_bp, url_prefix="/api/appointments")
    app.register_blueprint(reports_bp, url_prefix="/api/reports")
    app.register_blueprint(doctors_bp, url_prefix="/api/doctors")
    
    # --- Health Check ---
    @app.route("/api/health")
    def health():
        sf_status = "connected" if app.config.get("SF_CLIENT") else "disconnected"
        return {
            "status": "healthy",
            "service": "Patient Management System API",
            "salesforce": sf_status,
        }
    
    # --- Error Handlers ---
    @app.errorhandler(400)
    def bad_request(e):
        return {"error": "Bad Request", "message": str(e.description)}, 400
    
    @app.errorhandler(404)
    def not_found(e):
        return {"error": "Not Found", "message": str(e.description)}, 404
    
    @app.errorhandler(413)
    def payload_too_large(e):
        return {"error": "Payload Too Large", "message": "File size exceeds 16 MB limit."}, 413
    
    @app.errorhandler(500)
    def internal_error(e):
        return {"error": "Internal Server Error", "message": "An unexpected error occurred."}, 500
    
    return app


# Module-level app instance for Gunicorn (gunicorn app:app)
app = create_app()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
