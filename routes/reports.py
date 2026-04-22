"""
Medical reports routes – upload and listing.
"""
import os
import uuid
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, current_app, send_from_directory
from werkzeug.utils import secure_filename
from config import allowed_file

reports_bp = Blueprint("reports", __name__)


@reports_bp.route("", methods=["POST"])
def upload_report():
    """Upload a medical report for a patient."""
    db = current_app.config["FIRESTORE_DB"]

    patient_id = request.form.get("patient_id")
    if not patient_id:
        return jsonify({"error": "patient_id is required."}), 400

    # Check patient exists
    patient = db.collection("patients").document(patient_id).get()
    if not patient.exists:
        return jsonify({"error": "Patient not found."}), 404

    if "file" not in request.files:
        return jsonify({"error": "No file provided."}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected."}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "File type not allowed. Use PDF, PNG, JPG, JPEG, GIF, BMP, or WEBP."}), 400

    # Save file
    report_id = str(uuid.uuid4())
    ext = file.filename.rsplit(".", 1)[1].lower()
    filename = f"{report_id}.{ext}"
    filepath = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
    file.save(filepath)

    report_name = request.form.get("report_name", file.filename)

    report = {
        "id": report_id,
        "patient_id": patient_id,
        "report_name": report_name,
        "original_filename": secure_filename(file.filename),
        "file_path": filename,
        "file_type": ext,
        "upload_date": datetime.now(timezone.utc).isoformat(),
    }

    db.collection("reports").document(report_id).set(report)
    return jsonify({"message": "Report uploaded successfully", "report": report}), 201


@reports_bp.route("", methods=["GET"])
def list_reports():
    """List reports, optionally filtered by patient_id."""
    db = current_app.config["FIRESTORE_DB"]
    patient_id = request.args.get("patient_id")

    query = db.collection("reports")
    if patient_id:
        query = query.where("patient_id", "==", patient_id)

    docs = query.order_by("upload_date").stream()
    reports = [doc.to_dict() for doc in docs]
    return jsonify({"reports": reports, "count": len(reports)}), 200


@reports_bp.route("/<report_id>", methods=["GET"])
def get_report(report_id):
    """Get report metadata."""
    db = current_app.config["FIRESTORE_DB"]
    doc = db.collection("reports").document(report_id).get()
    if not doc.exists:
        return jsonify({"error": "Report not found"}), 404
    return jsonify({"report": doc.to_dict()}), 200


@reports_bp.route("/file/<filename>", methods=["GET"])
def serve_report_file(filename):
    """Serve a report file."""
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], filename)


@reports_bp.route("/<report_id>", methods=["DELETE"])
def delete_report(report_id):
    """Delete a report."""
    db = current_app.config["FIRESTORE_DB"]
    doc = db.collection("reports").document(report_id).get()
    if not doc.exists:
        return jsonify({"error": "Report not found"}), 404

    report = doc.to_dict()
    filepath = os.path.join(current_app.config["UPLOAD_FOLDER"], report["file_path"])
    if os.path.exists(filepath):
        os.remove(filepath)

    db.collection("reports").document(report_id).delete()
    return jsonify({"message": "Report deleted successfully"}), 200
