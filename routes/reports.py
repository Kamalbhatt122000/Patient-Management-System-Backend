"""
Medical reports routes – upload, listing, download, delete.
Bidirectional sync with Salesforce Files (ContentVersion / ContentDocumentLink).

Website → Salesforce:
  1. Upload file locally + Firebase
  2. Create ContentVersion in Salesforce (base64-encoded VersionData)
  3. Link the ContentDocument to Patient__c via ContentDocumentLink

Salesforce → Website:
  1. Query ContentDocumentLink for the patient's SF Id
  2. Fetch ContentVersion metadata + VersionData binary
  3. Return merged list (local + SF-only files)
"""
import os
import uuid
import base64
import requests
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, current_app, send_from_directory, Response
from werkzeug.utils import secure_filename
from config import allowed_file

reports_bp = Blueprint("reports", __name__)


# ═══════════════════════════════════════════════════════
#  Salesforce helpers
# ═══════════════════════════════════════════════════════

def _upload_file_to_salesforce(sf, file_bytes: bytes, filename: str, title: str) -> dict | None:
    """
    Create a ContentVersion in Salesforce.
    Returns {"content_version_id": ..., "content_document_id": ...} or None.
    """
    if not sf:
        return None
    try:
        encoded = base64.b64encode(file_bytes).decode("utf-8")
        result = sf.ContentVersion.create({
            "Title": title,
            "PathOnClient": filename,
            "VersionData": encoded,
        })
        cv_id = result.get("id")
        if not cv_id:
            return None

        # Query back to get the auto-generated ContentDocumentId
        cv = sf.query(
            f"SELECT ContentDocumentId FROM ContentVersion WHERE Id = '{cv_id}'"
        )
        cd_id = cv["records"][0]["ContentDocumentId"] if cv["records"] else None

        print(f"✅ File uploaded to Salesforce: CV={cv_id}, CD={cd_id}")
        return {"content_version_id": cv_id, "content_document_id": cd_id}
    except Exception as e:
        print(f"⚠️  Salesforce file upload failed: {e}")
        return None


def _link_file_to_patient(sf, content_document_id: str, patient_sf_id: str) -> bool:
    """
    Create a ContentDocumentLink so the file appears under the patient record
    in the 'Files' / 'Notes & Attachments' related list.
    """
    if not sf or not content_document_id or not patient_sf_id:
        return False
    try:
        sf.ContentDocumentLink.create({
            "ContentDocumentId": content_document_id,
            "LinkedEntityId": patient_sf_id,
            "ShareType": "V",        # V = Viewer
            "Visibility": "AllUsers",
        })
        print(f"✅ File linked to Patient {patient_sf_id}")
        return True
    except Exception as e:
        print(f"⚠️  ContentDocumentLink creation failed: {e}")
        return False


def _get_sf_files_for_patient(sf, patient_sf_id: str) -> list[dict]:
    """
    Query Salesforce for all files linked to a Patient__c record.
    Returns a list of report-like dicts for the frontend.
    """
    if not sf or not patient_sf_id:
        return []
    try:
        # Get all ContentDocumentLinks for this patient
        links = sf.query(
            f"SELECT ContentDocumentId FROM ContentDocumentLink "
            f"WHERE LinkedEntityId = '{patient_sf_id}'"
        )
        cd_ids = [r["ContentDocumentId"] for r in links.get("records", [])]
        if not cd_ids:
            return []

        # Get the latest ContentVersion for each ContentDocument
        id_list = "','".join(cd_ids)
        versions = sf.query(
            f"SELECT Id, Title, FileExtension, ContentSize, CreatedDate, "
            f"ContentDocumentId "
            f"FROM ContentVersion "
            f"WHERE ContentDocumentId IN ('{id_list}') AND IsLatest = true"
        )

        files = []
        for v in versions.get("records", []):
            ext = (v.get("FileExtension") or "").lower()
            files.append({
                "id": f"sf-{v['Id']}",
                "content_version_id": v["Id"],
                "content_document_id": v["ContentDocumentId"],
                "report_name": v.get("Title", "Untitled"),
                "original_filename": f"{v.get('Title', 'file')}.{ext}",
                "file_type": ext,
                "file_size": v.get("ContentSize", 0),
                "upload_date": v.get("CreatedDate", ""),
                "source": "salesforce",
            })
        return files
    except Exception as e:
        print(f"⚠️  Salesforce file query failed: {e}")
        return []


def _download_sf_file(sf, content_version_id: str) -> tuple[bytes, str] | None:
    """
    Download file binary from Salesforce ContentVersion.
    Returns (file_bytes, content_type) or None.
    """
    if not sf:
        return None
    try:
        # Build the download URL
        base_url = f"https://{sf.sf_instance}"
        url = f"{base_url}/services/data/v{sf.sf_version}/sobjects/ContentVersion/{content_version_id}/VersionData"
        headers = {"Authorization": f"Bearer {sf.session_id}"}
        resp = requests.get(url, headers=headers, stream=True)
        if resp.status_code == 200:
            content_type = resp.headers.get("Content-Type", "application/octet-stream")
            return resp.content, content_type
        else:
            print(f"⚠️  SF file download failed: {resp.status_code}")
            return None
    except Exception as e:
        print(f"⚠️  SF file download error: {e}")
        return None


def _delete_sf_content_document(sf, content_document_id: str) -> bool:
    """Delete a ContentDocument (and all its versions) from Salesforce."""
    if not sf or not content_document_id:
        return False
    try:
        sf.ContentDocument.delete(content_document_id)
        print(f"✅ Salesforce ContentDocument {content_document_id} deleted")
        return True
    except Exception as e:
        print(f"⚠️  Salesforce file delete failed: {e}")
        return False


# ═══════════════════════════════════════════════════════
#  Routes
# ═══════════════════════════════════════════════════════

@reports_bp.route("", methods=["POST"])
def upload_report():
    """
    Upload a medical report for a patient.
    Saves locally + Firebase, then syncs to Salesforce Files.
    """
    db = current_app.config["FIRESTORE_DB"]
    sf = current_app.config.get("SF_CLIENT")

    patient_id = request.form.get("patient_id")
    if not patient_id:
        return jsonify({"error": "patient_id is required."}), 400

    # Check patient exists
    patient_doc = db.collection("patients").document(patient_id).get()
    if not patient_doc.exists:
        return jsonify({"error": "Patient not found."}), 404

    if "file" not in request.files:
        return jsonify({"error": "No file provided."}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected."}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "File type not allowed. Use PDF, PNG, JPG, JPEG, GIF, BMP, or WEBP."}), 400

    # Read file bytes (we need them for both local save and SF upload)
    file_bytes = file.read()

    # --- Save file locally ---
    report_id = str(uuid.uuid4())
    ext = file.filename.rsplit(".", 1)[1].lower()
    filename = f"{report_id}.{ext}"
    filepath = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
    with open(filepath, "wb") as f:
        f.write(file_bytes)

    report_name = request.form.get("report_name", file.filename)

    report = {
        "id": report_id,
        "patient_id": patient_id,
        "report_name": report_name,
        "original_filename": secure_filename(file.filename),
        "file_path": filename,
        "file_type": ext,
        "upload_date": datetime.now(timezone.utc).isoformat(),
        "source": "website",
    }

    # --- Sync to Salesforce ---
    patient_data = patient_doc.to_dict()
    patient_sf_id = patient_data.get("salesforce_id")

    # Auto-create patient in Salesforce if they don't have an SF ID yet
    if sf and not patient_sf_id:
        try:
            from routes.patients import _sync_patient_to_salesforce
            patient_sf_id = _sync_patient_to_salesforce(sf, patient_data)
            if patient_sf_id:
                db.collection("patients").document(patient_id).update({
                    "salesforce_id": patient_sf_id
                })
                print(f"✅ Auto-synced patient {patient_id} to Salesforce: {patient_sf_id}")
        except Exception as e:
            print(f"⚠️  Auto-sync patient to SF failed: {e}")

    if sf and patient_sf_id:
        sf_result = _upload_file_to_salesforce(sf, file_bytes, secure_filename(file.filename), report_name)
        if sf_result:
            report["content_version_id"] = sf_result["content_version_id"]
            report["content_document_id"] = sf_result["content_document_id"]

            # Link file to Patient__c record
            _link_file_to_patient(sf, sf_result["content_document_id"], patient_sf_id)

            report["sf_synced"] = True
        else:
            report["sf_synced"] = False
    else:
        report["sf_synced"] = False
        if not patient_sf_id:
            print(f"⚠️  Patient {patient_id} could not be synced to Salesforce")

    # Save metadata to Firebase
    db.collection("reports").document(report_id).set(report)

    return jsonify({"message": "Report uploaded successfully", "report": report}), 201


@reports_bp.route("", methods=["GET"])
def list_reports():
    """
    List reports – merges local (Firebase) reports with Salesforce-only files.
    If patient_id is provided, fetches SF files for that patient and deduplicates.
    """
    db = current_app.config["FIRESTORE_DB"]
    sf = current_app.config.get("SF_CLIENT")
    patient_id = request.args.get("patient_id")

    # 1. Get local/Firebase reports
    query = db.collection("reports")
    if patient_id:
        query = query.where("patient_id", "==", patient_id)

    docs = query.order_by("upload_date").stream()
    local_reports = [doc.to_dict() for doc in docs]

    # 2. Get Salesforce files for this patient (bidirectional sync)
    sf_reports = []
    if sf and patient_id:
        patient_doc = db.collection("patients").document(patient_id).get()
        if patient_doc.exists:
            patient_sf_id = patient_doc.to_dict().get("salesforce_id")
            if patient_sf_id:
                sf_reports = _get_sf_files_for_patient(sf, patient_sf_id)

    # 3. Deduplicate: remove SF files that are already tracked locally
    local_cv_ids = {
        r.get("content_version_id") for r in local_reports if r.get("content_version_id")
    }
    sf_only = [r for r in sf_reports if r.get("content_version_id") not in local_cv_ids]

    # Mark local reports with their source
    for r in local_reports:
        if "source" not in r:
            r["source"] = "website"

    # 4. Merge: local first, then SF-only
    merged = local_reports + sf_only

    return jsonify({
        "reports": merged,
        "count": len(merged),
        "local_count": len(local_reports),
        "sf_count": len(sf_only),
    }), 200


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
    """Serve a locally-stored report file."""
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], filename)


@reports_bp.route("/sf-file/<content_version_id>", methods=["GET"])
def serve_sf_file(content_version_id):
    """
    Proxy-download a file from Salesforce ContentVersion.
    Used for files uploaded directly in Salesforce (SF → Website flow).
    """
    sf = current_app.config.get("SF_CLIENT")
    if not sf:
        return jsonify({"error": "Salesforce not connected"}), 503

    result = _download_sf_file(sf, content_version_id)
    if not result:
        return jsonify({"error": "File not found in Salesforce"}), 404

    file_bytes, content_type = result

    # Try to get the filename from ContentVersion
    try:
        cv = sf.ContentVersion.get(content_version_id)
        filename = cv.get("PathOnClient", f"download.{cv.get('FileExtension', 'bin')}")
    except Exception:
        filename = "download.bin"

    return Response(
        file_bytes,
        mimetype=content_type,
        headers={
            "Content-Disposition": f"inline; filename=\"{filename}\"",
            "Content-Length": str(len(file_bytes)),
        },
    )


@reports_bp.route("/<report_id>", methods=["DELETE"])
def delete_report(report_id):
    """
    Delete a report from Firebase, local disk, and Salesforce.
    Handles both locally-uploaded and SF-only reports.
    """
    db = current_app.config["FIRESTORE_DB"]
    sf = current_app.config.get("SF_CLIENT")

    # Handle SF-only reports (id starts with 'sf-')
    if report_id.startswith("sf-"):
        cv_id = report_id[3:]  # strip 'sf-' prefix
        if sf:
            try:
                cv = sf.ContentVersion.get(cv_id)
                cd_id = cv.get("ContentDocumentId")
                if cd_id:
                    _delete_sf_content_document(sf, cd_id)
            except Exception as e:
                return jsonify({"error": f"Failed to delete from Salesforce: {str(e)}"}), 500
        return jsonify({"message": "Report deleted from Salesforce successfully"}), 200

    # Handle locally-tracked reports
    doc = db.collection("reports").document(report_id).get()
    if not doc.exists:
        return jsonify({"error": "Report not found"}), 404

    report = doc.to_dict()

    # Delete local file
    if report.get("file_path"):
        filepath = os.path.join(current_app.config["UPLOAD_FOLDER"], report["file_path"])
        if os.path.exists(filepath):
            os.remove(filepath)

    # Delete from Salesforce
    cd_id = report.get("content_document_id")
    if sf and cd_id:
        _delete_sf_content_document(sf, cd_id)

    # Delete from Firebase
    db.collection("reports").document(report_id).delete()
    return jsonify({"message": "Report deleted successfully"}), 200
