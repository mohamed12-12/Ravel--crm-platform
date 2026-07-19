from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from flask import current_app
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models.trip_media import TripMedia


ALLOWED_IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


class TripMediaValidationError(ValueError):
    pass


@dataclass(frozen=True)
class StoredTripImage:
    storage_key: str
    original_filename: str
    mime_type: str
    file_extension: str
    file_size: int


def configured_max_bytes() -> int:
    return int(current_app.config.get("TRIP_MEDIA_MAX_BYTES") or 5 * 1024 * 1024)


def storage_root() -> Path:
    root = Path(current_app.config["TRIP_MEDIA_ROOT"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _has_file(file: FileStorage | None) -> bool:
    return bool(file and str(file.filename or "").strip())


def has_trip_media_upload(files) -> bool:
    if _has_file(files.get("cover_image")):
        return True
    return any(_has_file(file) for file in files.getlist("gallery_images"))


def _safe_original_filename(filename: str) -> str:
    raw = str(filename or "").strip()
    if not raw:
        raise TripMediaValidationError("Image filename is required.")
    if "\x00" in raw or "/" in raw or "\\" in raw:
        raise TripMediaValidationError("Unsafe image filename.")
    safe = secure_filename(raw)
    if not safe:
        raise TripMediaValidationError("Unsafe image filename.")
    if safe != Path(safe).name:
        raise TripMediaValidationError("Unsafe image filename.")
    return safe


def _validate_signature(data: bytes, mime_type: str) -> None:
    if data.startswith(b"MZ") or data.startswith(b"\x7fELF") or data.startswith(b"#!"):
        raise TripMediaValidationError("Executable files are not allowed.")
    if mime_type == "image/jpeg" and not data.startswith(b"\xff\xd8\xff"):
        raise TripMediaValidationError("Invalid JPEG image signature.")
    if mime_type == "image/png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise TripMediaValidationError("Invalid PNG image signature.")
    if mime_type == "image/webp" and not (len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        raise TripMediaValidationError("Invalid WEBP image signature.")


def _store_file(trip_id: str, file: FileStorage) -> StoredTripImage:
    original = _safe_original_filename(file.filename or "")
    suffix = Path(original).suffix.lower()
    expected_mime = ALLOWED_IMAGE_TYPES.get(suffix)
    if expected_mime is None:
        raise TripMediaValidationError("Trip images must be JPG, JPEG, PNG, or WEBP.")

    claimed_mime = str(file.mimetype or "").strip().lower()
    if claimed_mime != expected_mime:
        raise TripMediaValidationError("Image MIME type does not match the file extension.")

    max_bytes = configured_max_bytes()
    data = file.stream.read(max_bytes + 1)
    if not data:
        raise TripMediaValidationError("Image file is empty.")
    if len(data) > max_bytes:
        raise TripMediaValidationError(f"Trip image exceeds the {max_bytes} byte limit.")
    _validate_signature(data, expected_mime)

    root = storage_root()
    trip_hash = hashlib.sha256(str(trip_id).encode("utf-8")).hexdigest()[:20]
    generated_name = f"{uuid.uuid4().hex}{suffix}"
    storage_key = PurePosixPath(trip_hash, generated_name).as_posix()
    target = (root / trip_hash / generated_name).resolve()
    if root not in target.parents:
        raise TripMediaValidationError("Unsafe image storage path.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as handle:
        handle.write(data)

    return StoredTripImage(
        storage_key=storage_key,
        original_filename=original,
        mime_type=expected_mime,
        file_extension=suffix.lstrip("."),
        file_size=len(data),
    )


def resolve_storage_key(storage_key: str) -> Path:
    root = storage_root()
    relative = PurePosixPath(str(storage_key or ""))
    if relative.is_absolute() or ".." in relative.parts:
        raise TripMediaValidationError("Unsafe image storage path.")
    path = (root / Path(*relative.parts)).resolve()
    if root not in path.parents:
        raise TripMediaValidationError("Unsafe image storage path.")
    return path


def verified_trip_media(trip_id: str) -> list[TripMedia]:
    return (
        TripMedia.query.filter_by(trip_id=trip_id, is_active=True, verification_status="verified")
        .order_by(
            db.case((TripMedia.image_type == "cover", 0), else_=1),
            TripMedia.display_order.asc(),
            TripMedia.media_id.asc(),
        )
        .all()
    )


def cover_media_by_trip_ids(trip_ids: Iterable[str]) -> dict[str, TripMedia]:
    ids = [str(trip_id) for trip_id in trip_ids if trip_id]
    if not ids:
        return {}
    rows = (
        TripMedia.query.filter(
            TripMedia.trip_id.in_(ids),
            TripMedia.is_active.is_(True),
            TripMedia.verification_status == "verified",
            TripMedia.image_type == "cover",
        )
        .order_by(TripMedia.display_order.asc(), TripMedia.media_id.asc())
        .all()
    )
    covers: dict[str, TripMedia] = {}
    for row in rows:
        covers.setdefault(row.trip_id, row)
    return covers


def save_trip_media_uploads(
    *,
    trip_id: str,
    files,
    form,
    uploaded_by_user_id: int | None,
    uploaded_by_name: str = "",
) -> list[TripMedia]:
    created: list[TripMedia] = []
    written_paths: list[Path] = []
    try:
        cover = files.get("cover_image")
        if _has_file(cover):
            stored = _store_file(trip_id, cover)
            TripMedia.query.filter_by(trip_id=trip_id, image_type="cover", is_active=True).update(
                {"is_active": False},
                synchronize_session=False,
            )
            media = TripMedia(
                public_id=str(uuid.uuid4()),
                trip_id=trip_id,
                storage_key=stored.storage_key,
                public_url="",
                image_type="cover",
                alt_text=str(form.get("cover_alt_text") or "").strip()[:255],
                display_order=0,
                original_filename=stored.original_filename,
                mime_type=stored.mime_type,
                file_extension=stored.file_extension,
                file_size=stored.file_size,
                uploaded_by_user_id=uploaded_by_user_id,
                uploaded_by_name=uploaded_by_name,
                verification_status="verified",
                is_active=True,
            )
            media.public_url = f"/trips/media/{media.public_id}"
            db.session.add(media)
            created.append(media)
            written_paths.append(resolve_storage_key(stored.storage_key))

        next_order = (
            db.session.query(db.func.max(TripMedia.display_order))
            .filter_by(trip_id=trip_id, image_type="gallery")
            .scalar()
            or 0
        )
        gallery_alt = str(form.get("gallery_alt_text") or "").strip()[:255]
        for file in files.getlist("gallery_images"):
            if not _has_file(file):
                continue
            next_order += 1
            stored = _store_file(trip_id, file)
            media = TripMedia(
                public_id=str(uuid.uuid4()),
                trip_id=trip_id,
                storage_key=stored.storage_key,
                public_url="",
                image_type="gallery",
                alt_text=gallery_alt,
                display_order=next_order,
                original_filename=stored.original_filename,
                mime_type=stored.mime_type,
                file_extension=stored.file_extension,
                file_size=stored.file_size,
                uploaded_by_user_id=uploaded_by_user_id,
                uploaded_by_name=uploaded_by_name,
                verification_status="verified",
                is_active=True,
            )
            media.public_url = f"/trips/media/{media.public_id}"
            db.session.add(media)
            created.append(media)
            written_paths.append(resolve_storage_key(stored.storage_key))
    except Exception:
        for path in written_paths:
            try:
                os.remove(path)
            except OSError:
                pass
        raise
    return created
