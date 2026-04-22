"""
Patient routes – CRUD operations with triage logic.
"""
import re
import uuid
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, current_app

patients_bp = Blueprint("patients", __name__)

# --------------- helpers ---------------

HIGH_PRIORITY_KEYWORDS = [
    "chest pain", "difficulty breathing", "shortness of breath",
    "severe bleeding", "unconscious", "stroke", "heart attack",
    "seizure", "anaphylaxis",
]

def _calculate_priority(symptoms: str, duration_days: int | None = None) -> str:
    """Basic triage logic."""
    symptoms_lower = symptoms.lower() if symptoms else ""
    for keyword in HIGH_PRIORITY_KEYWORDS:
        if keyword in symptoms_lower:
            return "HIGH"
    if duration_days is not None and duration_days > 3:
        return "MEDIUM"
    return "LOW"

def _validate_patient(data: dict) -> list[str]:
    """Return list of validation errors (empty = valid)."""
    errors = []
    required = ["first_name", "last_name", "age", "gender", "email", "phone"]
    for field in required:
        if not data.get(field):
            errors.append(f"{field} is required.")
    if data.get("email") and not re.match(r"[^@]+@[^@]+\.[^@]+", data["email"]):
        errors.append("Invalid email format.")
    if data.get("age"):
        try:
            age = int(data["age"])
            if age < 0 or age > 150:
                errors.append("Age must be between 0 and 150.")
        except (ValueError, TypeError):
            errors.append("Age must be a number.")
    if data.get("phone") and not re.match(r"^[\d\s\+\-\(\)]{7,20}$", data["phone"]):
        errors.append("Invalid phone number format.")
    return errors

# --------------- routes ---------------

@patients_bp.route("", methods=["POST"])
def register_patient():
    """Register a new patient."""
    data = request.get_json(silent=True) or {}
    errors = _validate_patient(data)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400

    db = current_app.config["FIRESTORE_DB"]

    # Check duplicate email
    existing = db.collection("patients").where("email", "==", data["email"]).get()
    if existing:
        return jsonify({"error": "A patient with this email already exists."}), 409

    priority = _calculate_priority(
        data.get("symptoms", ""),
        data.get("duration_days"),
    )

    patient_id = str(uuid.uuid4())
    patient = {
        "id": patient_id,
        "first_name": data["first_name"].strip(),
        "last_name": data["last_name"].strip(),
        "age": int(data["age"]),
        "gender": data["gender"],
        "email": data["email"].strip().lower(),
        "phone": data["phone"].strip(),
        "address": data.get("address", "").strip(),
        "symptoms": data.get("symptoms", "").strip(),
        "duration_days": data.get("duration_days"),
        "priority": priority,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    db.collection("patients").document(patient_id).set(patient)
    return jsonify({"message": "Patient registered successfully", "patient": patient}), 201


@patients_bp.route("", methods=["GET"])
def list_patients():
    """List all patients."""
    db = current_app.config["FIRESTORE_DB"]
    docs = db.collection("patients").order_by("created_at").stream()
    patients = [doc.to_dict() for doc in docs]
    return jsonify({"patients": patients, "count": len(patients)}), 200


@patients_bp.route("/<patient_id>", methods=["GET"])
def get_patient(patient_id):
    """Get a single patient by ID."""
    db = current_app.config["FIRESTORE_DB"]
    doc = db.collection("patients").document(patient_id).get()
    if not doc.exists:
        return jsonify({"error": "Patient not found"}), 404
    return jsonify({"patient": doc.to_dict()}), 200


@patients_bp.route("/<patient_id>", methods=["PUT"])
def update_patient(patient_id):
    """Update patient details."""
    db = current_app.config["FIRESTORE_DB"]
    doc_ref = db.collection("patients").document(patient_id)
    doc = doc_ref.get()
    if not doc.exists:
        return jsonify({"error": "Patient not found"}), 404

    data = request.get_json(silent=True) or {}
    allowed_fields = [
        "first_name", "last_name", "age", "gender", "email",
        "phone", "address", "symptoms", "duration_days",
    ]
    updates = {k: v for k, v in data.items() if k in allowed_fields and v is not None}

    if "symptoms" in updates or "duration_days" in updates:
        current = doc.to_dict()
        symptoms = updates.get("symptoms", current.get("symptoms", ""))
        duration = updates.get("duration_days", current.get("duration_days"))
        updates["priority"] = _calculate_priority(symptoms, duration)

    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    doc_ref.update(updates)

    updated = doc_ref.get().to_dict()
    return jsonify({"message": "Patient updated", "patient": updated}), 200


@patients_bp.route("/<patient_id>", methods=["DELETE"])
def delete_patient(patient_id):
    """Delete a patient."""
    db = current_app.config["FIRESTORE_DB"]
    doc = db.collection("patients").document(patient_id).get()
    if not doc.exists:
        return jsonify({"error": "Patient not found"}), 404
    db.collection("patients").document(patient_id).delete()
    return jsonify({"message": "Patient deleted successfully"}), 200
