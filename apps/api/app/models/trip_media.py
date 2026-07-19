from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db


class TripMedia(db.Model):
    __tablename__ = "trip_media"

    media_id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(36), nullable=False, unique=True, index=True)
    trip_id = db.Column(db.String(50), db.ForeignKey("trips.trip_id"), nullable=False, index=True)
    storage_key = db.Column(db.String(500), nullable=False, unique=True)
    public_url = db.Column(db.String(500), nullable=False)
    image_type = db.Column(db.String(20), nullable=False, default="gallery", index=True)
    alt_text = db.Column(db.String(255))
    display_order = db.Column(db.Integer, nullable=False, default=0)
    original_filename = db.Column(db.String(255))
    mime_type = db.Column(db.String(100), nullable=False)
    file_extension = db.Column(db.String(10), nullable=False)
    file_size = db.Column(db.Integer, nullable=False, default=0)
    uploaded_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    uploaded_by_name = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    verification_status = db.Column(db.String(50), nullable=False, default="verified", index=True)

    trip = db.relationship("Trip", back_populates="media")
    uploaded_by_user = db.relationship("User", foreign_keys=[uploaded_by_user_id])

    def to_public_dict(self) -> dict:
        return {
            "media_id": self.media_id,
            "public_id": self.public_id,
            "trip_id": self.trip_id,
            "url": self.public_url,
            "public_url": self.public_url,
            "image_type": self.image_type,
            "alt_text": self.alt_text or "",
            "display_order": self.display_order,
            "mime_type": self.mime_type,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
