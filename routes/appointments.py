"""
Appointment routes – booking, listing, duplicate prevention.
Doctors fetched from Salesforce; appointments synced to Salesforce Appointments__c.
"""
import uuid
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, current_app

appointments_bp = Blueprint("appointments", __name__)


def _validate_appointment(data: dict) -> list[str]:
    errors = []
    required = ["patient_id", "doctor_sf_id", "date", "time", "reason"]
    for field in required:
        if not data.get(field):
            errors.append(f"{field} is required.")
    return errors


def _sync_appointment_to_salesforce(sf, appt_data: dict, patient_sf_id: str) -> str | None:
    """Create an Appointments__c record in Salesforce."""
    if not sf:
        return None
    try:
        result = sf.Appointments__c.create({
            "Doctor__c": appt_data["doctor_sf_id"],
            "Patient__c": patient_sf_id,
            "Date__c": appt_data["date"],
            "Reason_for_visit__c": appt_data.get("reason", ""),
        })
        sf_id = result.get("id")
        print(f"✅ Appointment synced to Salesforce: {sf_id}")
        return sf_id
    except Exception as e:
        print(f"⚠️  Salesforce appointment sync failed: {e}")
        return None


@appointments_bp.route("/doctors", methods=["GET"])
def list_doctors():
    """Fetch doctors from Salesforce Doctor__c with generated time slots."""
    sf = current_app.config.get("SF_CLIENT")
    if not sf:
        return jsonify({"error": "Salesforce not connected"}), 503

    try:
        result = sf.query(
            "SELECT Id, Name, Specialization__c, Available_from__c, Available_To__c "
            "FROM Doctor__c ORDER BY Name"
        )
        doctors = []
        for rec in result.get("records", []):
            avail_from = rec.get("Available_from__c")
            avail_to = rec.get("Available_To__c")
            slots = _generate_slots(avail_from, avail_to) if avail_from and avail_to else []

            doctors.append({
                "id": rec["Id"],
                "name": rec["Name"],
                "specialty": rec.get("Specialization__c", "General"),
                "available_from": avail_from,
                "available_to": avail_to,
                "slots": slots,
            })
        return jsonify({"doctors": doctors}), 200
    except Exception as e:
        return jsonify({"error": f"Failed to fetch doctors: {str(e)}"}), 500


def _sf_time_to_minutes(sf_time_str: str) -> int:
    """Convert Salesforce time (HH:MM:SS.000Z) to minutes since midnight."""
    clean = sf_time_str.replace("Z", "").split(".")[0]
    parts = clean.split(":")
    return int(parts[0]) * 60 + int(parts[1])


def _minutes_to_display(minutes: int) -> str:
    """Convert minutes-since-midnight to '10:00 AM' display format."""
    h = minutes // 60
    m = minutes % 60
    period = "AM" if h < 12 else "PM"
    display_h = h if 1 <= h <= 12 else (12 if h == 0 or h == 12 else h - 12)
    return f"{display_h}:{m:02d} {period}"


def _generate_slots(available_from: str, available_to: str, duration: int = 30) -> list[str]:
    """Generate appointment slots between from and to times."""
    start = _sf_time_to_minutes(available_from)
    end = _sf_time_to_minutes(available_to)
    slots = []
    current = start
    while current + duration <= end:
        slots.append(_minutes_to_display(current))
        current += duration
    return slots


@appointments_bp.route("/time-slots", methods=["GET"])
def get_time_slots():
    """
    Return time slots for a specific doctor.
    Query: ?doctor_id=<SF_Doctor_Id>
    If no doctor_id, returns generic slots.
    """
    sf = current_app.config.get("SF_CLIENT")
    doctor_id = request.args.get("doctor_id")

    if sf and doctor_id:
        try:
            doc = sf.Doctor__c.get(doctor_id)
            avail_from = doc.get("Available_from__c")
            avail_to = doc.get("Available_To__c")
            if avail_from and avail_to:
                slots = _generate_slots(avail_from, avail_to)
                return jsonify({"time_slots": slots}), 200
        except Exception as e:
            print(f"⚠️  Slot fetch error: {e}")

    # Fallback generic slots
    fallback = [
        "09:00 AM", "09:30 AM", "10:00 AM", "10:30 AM",
        "11:00 AM", "11:30 AM", "12:00 PM",
        "02:00 PM", "02:30 PM", "03:00 PM", "03:30 PM",
        "04:00 PM", "04:30 PM", "05:00 PM",
    ]
    return jsonify({"time_slots": fallback}), 200


@appointments_bp.route("", methods=["POST"])
def book_appointment():
    """Book a new appointment (Firebase + Salesforce)."""
    data = request.get_json(silent=True) or {}
    errors = _validate_appointment(data)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400

    db = current_app.config["FIRESTORE_DB"]
    sf = current_app.config.get("SF_CLIENT")

    # Check patient exists in Firebase
    patient = db.collection("patients").document(data["patient_id"]).get()
    if not patient.exists:
        return jsonify({"error": "Patient not found. Register the patient first."}), 404

    # Prevent duplicate: same doctor + date + time in Firebase
    duplicates = (
        db.collection("appointments")
        .where("doctor_sf_id", "==", data["doctor_sf_id"])
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
        .where("doctor_sf_id", "==", data["doctor_sf_id"])
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
        "doctor_sf_id": data["doctor_sf_id"],
        "doctor": data.get("doctor_name", "Doctor"),
        "date": data["date"],
        "time": data["time"],
        "reason": data.get("reason", "").strip(),
        "status": "Scheduled",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # 1. Save to Firebase
    db.collection("appointments").document(appointment_id).set(appointment)

    # 2. Sync to Salesforce
    patient_sf_id = patient_data.get("salesforce_id")
    if sf and patient_sf_id:
        sf_appt_id = _sync_appointment_to_salesforce(sf, {
            "doctor_sf_id": data["doctor_sf_id"],
            "date": data["date"],
            "reason": data.get("reason", ""),
        }, patient_sf_id)
        if sf_appt_id:
            appointment["salesforce_id"] = sf_appt_id
            db.collection("appointments").document(appointment_id).update({
                "salesforce_id": sf_appt_id
            })

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
    """Cancel an appointment (Firebase + Salesforce)."""
    db = current_app.config["FIRESTORE_DB"]
    sf = current_app.config.get("SF_CLIENT")
    doc = db.collection("appointments").document(appointment_id).get()
    if not doc.exists:
        return jsonify({"error": "Appointment not found"}), 404

    appt_data = doc.to_dict()

    # Delete from Salesforce first
    sf_id = appt_data.get("salesforce_id")
    if sf and sf_id:
        try:
            sf.Appointments__c.delete(sf_id)
            print(f"✅ Salesforce appointment {sf_id} deleted")
        except Exception as e:
            print(f"⚠️  Salesforce appointment delete failed: {e}")

    db.collection("appointments").document(appointment_id).delete()
    return jsonify({"message": "Appointment cancelled successfully"}), 200
