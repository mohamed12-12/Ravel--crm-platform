from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TravelerDocument(db.Model):
    __tablename__ = "traveler_documents"

    document_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    traveler_id = db.Column(db.String(20), db.ForeignKey("travelers.traveler_id"), nullable=False, index=True)
    category = db.Column(db.String(50), nullable=False, default="document")
    file_name = db.Column(db.String(255), nullable=False)
    original_file_name = db.Column(db.String(255))
    mime_type = db.Column(db.String(100))
    file_extension = db.Column(db.String(20))
    file_size = db.Column(db.Integer)
    storage_path = db.Column(db.String(500), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=_utc_now, nullable=False)
    uploaded_by = db.Column(db.String(100))
    passport_full_name = db.Column(db.String(200))
    passport_number = db.Column(db.String(50))
    passport_nationality = db.Column(db.String(100))
    passport_expiry = db.Column(db.Date)
    verification_status = db.Column(db.String(50), default="pending")
    notes = db.Column(db.Text)

    def to_dict(self):
        return {
            "document_id": self.document_id,
            "traveler_id": self.traveler_id,
            "category": self.category,
            "file_name": self.file_name,
            "original_file_name": self.original_file_name,
            "mime_type": self.mime_type,
            "file_extension": self.file_extension,
            "file_size": self.file_size,
            "storage_path": self.storage_path,
            "uploaded_at": self.uploaded_at.isoformat() if self.uploaded_at else None,
            "uploaded_by": self.uploaded_by,
            "passport_full_name": self.passport_full_name,
            "passport_number": self.passport_number,
            "passport_nationality": self.passport_nationality,
            "passport_expiry": self.passport_expiry.isoformat() if self.passport_expiry else None,
            "verification_status": self.verification_status,
            "notes": self.notes,
        }
