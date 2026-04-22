"""
Appointment routes – booking, listing, duplicate prevention.
"""
import uuid
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, current_app

appointments_bp = Blueprint("appointments", __name__)

DOCTORS = [
    {"id": "dr-001", "name": "Dr. Sarah Johnson", "specialty": "General Medicine"},
    {"id": "dr-002", "name": "Dr. Michael Chen", "specialty": "Cardiology"},
    {"id": "dr-003", "name": "Dr. Emily Williams", "specialty": "Dermatology"},
    {"id": "dr-004", "name": "Dr. David Patel", "specialty": "Orthopedics"},
    {"id": "dr-005", "name": "Dr. Lisa Anderson", "specialty": "Pediatrics"},
    {"id": "dr-006", "name": "Dr. Robert Kim", "specialty": "Neurology"},
]

TIME_SLOTS = [
    "09:00 AM", "09:30 AM", "10:00 AM", "10:30 AM",
    "11:00 AM", "11:30 AM", "12:00 PM",
    "02:00 PM", "02:30 PM", "03:00 PM", "03:30 PM",
    "04:00 PM", "04:30 PM", "05:00 PM",
]

def _validate_appointment(data: dict) -> list[str]:
    errors = []
    required = ["patient_id", "doctor", "date", "time", "reason"]
    for field in required:
        if not data.get(field):
            errors.append(f"{field} is required.")
    return errors


@appointments_bp.route("/doctors", methods=["GET"])
def list_doctors():
    """Return available doctors."""
    return jsonify({"doctors": DOCTORS}), 200


@appointments_bp.route("/time-slots", methods=["GET"])
def list_time_slots():
    """Return available time slots."""
    return jsonify({"time_slots": TIME_SLOTS}), 200


@appointments_bp.route("", methods=["POST"])
def book_appointment():
    """Book a new appointment with duplicate prevention."""
    data = request.get_json(silent=True) or {}
    errors = _validate_appointment(data)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400

    db = current_app.config["FIRESTORE_DB"]

    # Check patient exists
    patient = db.collection("patients").document(data["patient_id"]).get()
    if not patient.exists:
        return jsonify({"error": "Patient not found. Register the patient first."}), 404

    # Prevent duplicate: same doctor + date + time
    duplicates = (
        db.collection("appointments")
        .where("doctor", "==", data["doctor"])
        .where("date", "==", data["date"])
        .where("time", "==", data["time"])
        .get()
    )
    if duplicates:
        return jsonify({
            "error": "This time slot is already booked for the selected doctor."
        }), 409

    # Prevent same patient booking same doctor on same date
    patient_dup = (
        db.collection("appointments")
        .where("patient_id", "==", data["patient_id"])
        .where("doctor", "==", data["doctor"])
        .where("date", "==", data["date"])
        .get()
    )
    if patient_dup:
        return jsonify({
            "error": "You already have an appointment with this doctor on the selected date."
        }), 409

    appointment_id = str(uuid.uuid4())
    patient_data = patient.to_dict()
    appointment = {
        "id": appointment_id,
        "patient_id": data["patient_id"],
        "patient_name": f"{patient_data['first_name']} {patient_data['last_name']}",
        "doctor": data["doctor"],
        "date": data["date"],
        "time": data["time"],
        "reason": data.get("reason", "").strip(),
        "status": "Scheduled",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    db.collection("appointments").document(appointment_id).set(appointment)
    return jsonify({"message": "Appointment booked successfully", "appointment": appointment}), 201


@appointments_bp.route("", methods=["GET"])
def list_appointments():
    """List appointments, optionally filtered by patient_id."""
    db = current_app.config["FIRESTORE_DB"]
    patient_id = request.args.get("patient_id")

    query = db.collection("appointments")
    if patient_id:
        query = query.where("patient_id", "==", patient_id)

    docs = query.order_by("created_at").stream()
    appointments = [doc.to_dict() for doc in docs]
    return jsonify({"appointments": appointments, "count": len(appointments)}), 200


@appointments_bp.route("/<appointment_id>", methods=["GET"])
def get_appointment(appointment_id):
    """Get a single appointment."""
    db = current_app.config["FIRESTORE_DB"]
    doc = db.collection("appointments").document(appointment_id).get()
    if not doc.exists:
        return jsonify({"error": "Appointment not found"}), 404
    return jsonify({"appointment": doc.to_dict()}), 200


@appointments_bp.route("/<appointment_id>", methods=["DELETE"])
def cancel_appointment(appointment_id):
    """Cancel an appointment."""
    db = current_app.config["FIRESTORE_DB"]
    doc = db.collection("appointments").document(appointment_id).get()
    if not doc.exists:
        return jsonify({"error": "Appointment not found"}), 404
    db.collection("appointments").document(appointment_id).delete()
    return jsonify({"message": "Appointment cancelled successfully"}), 200
