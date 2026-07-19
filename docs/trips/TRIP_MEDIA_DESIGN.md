# Trip Media Design

## Purpose

Rahma Traveler stores official trip images inside the CRM so employees and the AI assistant use the same verified source of truth.

The AI agent is read-only for trip media. Only authenticated CRM employees can upload or update trip images.

## Data Model

Trip media is stored in `trip_media`.

Fields:

1) `trip_id`
2) `storage_key`
3) `public_url`
4) `image_type`: `cover` or `gallery`
5) `alt_text`
6) `display_order`
7) `uploaded_by_user_id`
8) `uploaded_by_name`
9) `created_at`
10) `is_active`
11) `verification_status`

The relation is `Trip.media`. A trip may have one active cover image and many active gallery images.

## Storage

Files are stored below `TRIP_MEDIA_ROOT`.

Default:

```text
instance/uploads/trips
```

Each trip receives a hashed folder name. Stored file names use generated UUIDs, not the uploaded filename.

The original filename is kept as metadata only.

## Upload Security

Uploads are accepted only from employee browser sessions.

Validation rules:

1) Extension must be JPG, JPEG, PNG, or WEBP.
2) MIME type must match the extension.
3) File size must be lower than `TRIP_MEDIA_MAX_BYTES`.
4) File bytes must match a known image signature.
5) Executable signatures are rejected.
6) Original filename must not include path separators.
7) Resolved storage path must stay under `TRIP_MEDIA_ROOT`.

## Agent Access

The agent uses `get_trip_media(trip_id)`.

The tool returns only active, verified CRM media for the selected trip.

The agent must not upload, edit, delete, invent, or reuse unrelated images. Instagram or ad images are not official unless an employee uploads or maps them to the CRM trip.
