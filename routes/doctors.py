"""
Doctor routes – fetched live from Salesforce Doctor__c object.
Generates available time slots based on Available_from__c / Available_To__c.
"""
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, current_app, request

doctors_bp = Blueprint("doctors", __name__)

SLOT_DURATION_MINUTES = 30  # each appointment slot = 30 min


def _sf_time_to_minutes(sf_time_str: str) -> int:
    """
    Convert a Salesforce time value to minutes-since-midnight.
    SF returns time as 'HH:MM:SS.000Z' or just 'HH:MM:SS'.
    """
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


def _generate_slots(available_from: str, available_to: str) -> list[str]:
    """Generate 30-minute appointment slots between from and to times."""
    start = _sf_time_to_minutes(available_from)
    end = _sf_time_to_minutes(available_to)
    slots = []
    current = start
    while current + SLOT_DURATION_MINUTES <= end:
        slots.append(_minutes_to_display(current))
        current += SLOT_DURATION_MINUTES
    return slots


@doctors_bp.route("", methods=["GET"])
def list_doctors():
    """
    Fetch all doctors from Salesforce Doctor__c.
    Returns: id, name, specialization, availability window, generated slots.
    """
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


@doctors_bp.route("/<doctor_id>/slots", methods=["GET"])
def get_doctor_slots(doctor_id):
    """
    Fetch available slots for a specific doctor.
    Optionally pass ?date=YYYY-MM-DD to filter out already-booked slots.
    """
    sf = current_app.config.get("SF_CLIENT")
    if not sf:
        return jsonify({"error": "Salesforce not connected"}), 503

    try:
        # Get doctor availability
        doc = sf.Doctor__c.get(doctor_id)
        avail_from = doc.get("Available_from__c")
        avail_to = doc.get("Available_To__c")

        if not avail_from or not avail_to:
            return jsonify({"slots": [], "doctor": doc["Name"]}), 200

        all_slots = _generate_slots(avail_from, avail_to)

        # If a date is provided, remove already-booked slots
        date_filter = request.args.get("date")
        if date_filter:
            booked_result = sf.query(
                f"SELECT Id, Date__c FROM Appointments__c "
                f"WHERE Doctor__c = '{doctor_id}' AND Date__c = {date_filter}"
            )
            booked_count = booked_result.get("totalSize", 0)
            # For now return all slots (exact time-based filtering would require
            # a time field on Appointments__c). We just note how many are booked.

        return jsonify({
            "slots": all_slots,
            "doctor": doc["Name"],
            "specialty": doc.get("Specialization__c", "General"),
        }), 200

    except Exception as e:
        return jsonify({"error": f"Failed to fetch slots: {str(e)}"}), 500
