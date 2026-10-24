"""Billing, refund and uploaded-document tools. Owner: Person 1."""

from app import config, db


class RecordsBillingTools:
    def get_billing_summary(self, patient_id):
        return self._run("get_billing_summary", patient_id, {}, lambda: db.query(
            "SELECT * FROM billing WHERE patient_id = ? AND bill_type != 'voided' ORDER BY bill_date DESC", (patient_id,)))

    def get_refund_status(self, patient_id):
        return self._run("get_refund_status", patient_id, {}, lambda: db.query(
            "SELECT * FROM refunds WHERE patient_id = ? ORDER BY initiated_date DESC", (patient_id,)))

    def read_uploaded_document(self, patient_id, upload_id=None):
        def run():
            sql, params = "SELECT * FROM uploaded_documents WHERE patient_id = ?", [patient_id]
            if upload_id:
                sql += " AND upload_id = ?"
                params.append(upload_id)
            rows = db.query(sql + " ORDER BY uploaded_date DESC", params)
            for r in rows:
                path = config.UPLOADS_DIR / r["file_name"]
                r["raw_text"] = path.read_text(encoding="utf-8") if path.exists() else ""
            return rows
        return self._run("read_uploaded_document", patient_id, {"upload_id": upload_id or ""}, run)
