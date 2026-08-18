-- Rahma Traveler CRM PostgreSQL schema.
-- Generated from the live SQLite catalog and SQLAlchemy models.
-- This file contains schema only. Do not add production data here.

BEGIN;

CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    username VARCHAR(80) NOT NULL UNIQUE,
    password_hash VARCHAR(200) NOT NULL,
    email VARCHAR(120) UNIQUE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT now(),
    full_name VARCHAR(200),
    role VARCHAR(50) NOT NULL DEFAULT 'agent',
    updated_at TIMESTAMPTZ DEFAULT now(),
    last_login_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_users_role ON users (role);

CREATE TABLE IF NOT EXISTS travelers (
    traveler_id VARCHAR(20) PRIMARY KEY,
    status VARCHAR(50),
    full_name VARCHAR(200) NOT NULL,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    birthday DATE,
    gender VARCHAR(20),
    nationality VARCHAR(100),
    phone_code VARCHAR(10),
    whatsapp_raw VARCHAR(50),
    email VARCHAR(150),
    community_whatsapp VARCHAR(100),
    residence VARCHAR(150),
    local_trips_count INTEGER DEFAULT 0,
    international_trips_count INTEGER DEFAULT 0,
    total_trips INTEGER DEFAULT 0,
    community_events_count INTEGER DEFAULT 0,
    lifetime_revenue DOUBLE PRECISION DEFAULT 0,
    notes TEXT,
    introduce_yourself TEXT,
    emergency_contact VARCHAR(200),
    emergency_phone VARCHAR(50),
    medical_notes TEXT,
    room_preference VARCHAR(100),
    rating DOUBLE PRECISION,
    integrated_whatsapp VARCHAR(50),
    normalized_whatsapp VARCHAR(50),
    phone_lookup_key VARCHAR(50),
    lead_source VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT now(),
    last_contacted_at TIMESTAMPTZ,
    agent_notes TEXT,
    data_audit TEXT,
    primary_language VARCHAR(20),
    current_flow_key VARCHAR(100),
    current_step VARCHAR(100),
    last_lead_id VARCHAR(50),
    last_booking_id VARCHAR(50),
    passport_name VARCHAR(200),
    passport_number VARCHAR(50),
    passport_expiry DATE,
    passport_nationality VARCHAR(100),
    passport_attachment_ref VARCHAR(300),
    preferred_currency VARCHAR(20)
);

CREATE INDEX IF NOT EXISTS ix_travelers_phone_lookup_key ON travelers (phone_lookup_key);
CREATE INDEX IF NOT EXISTS ix_travelers_normalized_whatsapp ON travelers (normalized_whatsapp);

CREATE TABLE IF NOT EXISTS trips (
    trip_id VARCHAR(50) PRIMARY KEY,
    trip_name VARCHAR(200) NOT NULL,
    type VARCHAR(50),
    year INTEGER,
    trip_leader VARCHAR(100),
    start_date DATE,
    end_date DATE,
    sales_status VARCHAR(50),
    data_audit TEXT,
    trip_window_status VARCHAR(50),
    trip_availability_note TEXT,
    next_reengage_date DATE,
    single_total INTEGER DEFAULT 0,
    double_total INTEGER DEFAULT 0,
    triple_total INTEGER DEFAULT 0,
    single_remaining INTEGER DEFAULT 0,
    double_remaining INTEGER DEFAULT 0,
    triple_remaining INTEGER DEFAULT 0,
    draft_holds_single INTEGER DEFAULT 0,
    draft_holds_double INTEGER DEFAULT 0,
    draft_holds_triple INTEGER DEFAULT 0,
    boys_double INTEGER DEFAULT 0,
    girls_double INTEGER DEFAULT 0,
    boys_triple INTEGER DEFAULT 0,
    girls_triple INTEGER DEFAULT 0,
    public_price VARCHAR(200),
    room_prices_json TEXT,
    public_description TEXT,
    itinerary TEXT,
    inclusions TEXT,
    exclusions TEXT,
    sales_notes TEXT,
    draft_holds_boys_double INTEGER DEFAULT 0,
    draft_holds_girls_double INTEGER DEFAULT 0,
    draft_holds_boys_triple INTEGER DEFAULT 0,
    draft_holds_girls_triple INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_trips_type_status ON trips (type, sales_status);

CREATE TABLE IF NOT EXISTS community_events (
    event_id VARCHAR(50) PRIMARY KEY,
    event_name VARCHAR(200) NOT NULL,
    date DATE,
    location VARCHAR(200),
    status VARCHAR(50),
    description TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS dm_copy_library (
    message_key VARCHAR(100) PRIMARY KEY,
    flow_key VARCHAR(100),
    step_key VARCHAR(100),
    channel VARCHAR(50),
    arabic_copy TEXT,
    english_copy TEXT,
    buttons_arabic TEXT,
    buttons_english TEXT,
    variables VARCHAR(255),
    source_sheet VARCHAR(100),
    active BOOLEAN,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS language_templates (
    id BIGSERIAL PRIMARY KEY,
    template_key VARCHAR(100),
    language VARCHAR(20),
    use_case VARCHAR(100),
    approved_copy TEXT,
    variables VARCHAR(255),
    status VARCHAR(50),
    notes TEXT
);

CREATE TABLE IF NOT EXISTS leads (
    lead_id VARCHAR(50) PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    customer_name VARCHAR(200),
    raw_phone VARCHAR(50),
    integrated_whatsapp VARCHAR(50),
    phone_lookup_key VARCHAR(50),
    traveler_id VARCHAR(20),
    traveler_status VARCHAR(50),
    customer_tier VARCHAR(50),
    match_status VARCHAR(50),
    lead_stage VARCHAR(100),
    lead_source VARCHAR(100),
    channel VARCHAR(50),
    preferred_trip_type VARCHAR(50),
    interested_trip_ids TEXT,
    suggested_trip_ids TEXT,
    priority VARCHAR(50),
    follow_up_status VARCHAR(100),
    follow_up_due_date DATE,
    last_interaction_id VARCHAR(50),
    interaction_count INTEGER DEFAULT 0,
    handoff_required BOOLEAN DEFAULT FALSE,
    handoff_reason TEXT,
    notes TEXT,
    flow_key VARCHAR(100),
    current_step VARCHAR(100),
    language VARCHAR(20),
    trigger_keyword VARCHAR(100),
    waitlist_id VARCHAR(50),
    handoff_id VARCHAR(50),
    booking_id VARCHAR(50),
    source_sheet VARCHAR(100),
    source_row INTEGER,
    group_size INTEGER DEFAULT 1,
    passport_attachment_ref TEXT,
    passport_status TEXT,
    assigned_to VARCHAR(100),
    last_contact_at TIMESTAMPTZ,
    customer_response_status VARCHAR(100),
    assigned_to_user_id BIGINT,
    assigned_at TIMESTAMPTZ,
    assigned_by_user_id BIGINT,
    idempotency_key TEXT,
    CONSTRAINT fk_leads_traveler FOREIGN KEY (traveler_id) REFERENCES travelers(traveler_id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_leads_assigned_to_user FOREIGN KEY (assigned_to_user_id) REFERENCES users(id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_leads_assigned_by_user FOREIGN KEY (assigned_by_user_id) REFERENCES users(id) DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX IF NOT EXISTS idx_leads_idempotency_key ON leads (idempotency_key);
CREATE INDEX IF NOT EXISTS ix_leads_assigned_to_user_id ON leads (assigned_to_user_id);
CREATE INDEX IF NOT EXISTS ix_leads_phone_lookup_key ON leads (phone_lookup_key);
CREATE INDEX IF NOT EXISTS ix_leads_stage_followup ON leads (lead_stage, follow_up_due_date);

CREATE TABLE IF NOT EXISTS interactions (
    interaction_id VARCHAR(50) PRIMARY KEY,
    timestamp TIMESTAMPTZ DEFAULT now(),
    channel VARCHAR(50),
    customer_name VARCHAR(200),
    raw_phone VARCHAR(50),
    integrated_whatsapp VARCHAR(50),
    phone_lookup_key VARCHAR(50),
    traveler_id VARCHAR(20),
    matched_row INTEGER,
    status_snapshot VARCHAR(100),
    intent VARCHAR(100),
    trip_type VARCHAR(50),
    suggested_trips TEXT,
    action_taken TEXT,
    handoff_required BOOLEAN DEFAULT FALSE,
    handoff_reason TEXT,
    agent_notes TEXT,
    flow_key VARCHAR(100),
    step_key VARCHAR(100),
    message_key VARCHAR(100),
    language VARCHAR(20),
    source_sheet VARCHAR(100),
    source_row INTEGER,
    outcome TEXT,
    CONSTRAINT fk_interactions_traveler FOREIGN KEY (traveler_id) REFERENCES travelers(traveler_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX IF NOT EXISTS ix_interactions_traveler_id ON interactions (traveler_id);
CREATE INDEX IF NOT EXISTS ix_interactions_phone_lookup_key ON interactions (phone_lookup_key);
CREATE UNIQUE INDEX IF NOT EXISTS ux_interactions_channel_message_key
    ON interactions (channel, message_key)
    WHERE message_key IS NOT NULL AND message_key <> '';

CREATE TABLE IF NOT EXISTS ai_agent_sessions (
    session_id VARCHAR(64) PRIMARY KEY,
    schema_version INTEGER NOT NULL DEFAULT 1,
    payload TEXT NOT NULL,
    agent_state TEXT,
    version INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    locked_until TIMESTAMPTZ,
    lock_owner VARCHAR(80),
    last_message_key VARCHAR(160),
    traveler_id VARCHAR(20),
    lead_id VARCHAR(50),
    raw_phone VARCHAR(32)
);

CREATE INDEX IF NOT EXISTS ix_ai_agent_sessions_locked_until
    ON ai_agent_sessions (locked_until);
CREATE INDEX IF NOT EXISTS ix_ai_agent_sessions_traveler_id
    ON ai_agent_sessions (traveler_id);
CREATE INDEX IF NOT EXISTS ix_ai_agent_sessions_lead_id
    ON ai_agent_sessions (lead_id);

CREATE TABLE IF NOT EXISTS trip_bookings (
    booking_id VARCHAR(50) PRIMARY KEY,
    trip_id VARCHAR(50),
    trip_name VARCHAR(200),
    traveler_id VARCHAR(20),
    traveler_name VARCHAR(200),
    room_type VARCHAR(50),
    flight_option VARCHAR(50),
    date_option VARCHAR(50),
    currency VARCHAR(20),
    booking_status VARCHAR(50),
    draft_created_at TIMESTAMPTZ DEFAULT now(),
    booking_source VARCHAR(100),
    lead_id VARCHAR(50),
    interaction_id VARCHAR(50),
    alert_id VARCHAR(50),
    payment_status VARCHAR(100),
    refund_amount DOUBLE PRECISION,
    booking_notes TEXT,
    passport_required BOOLEAN DEFAULT FALSE,
    passport_status TEXT,
    group_size INTEGER DEFAULT 1,
    assigned_to VARCHAR(100),
    priority VARCHAR(50),
    next_follow_up_at TIMESTAMPTZ,
    next_action VARCHAR(200),
    last_contact_at TIMESTAMPTZ,
    customer_response_status VARCHAR(100),
    assigned_to_user_id BIGINT,
    assigned_at TIMESTAMPTZ,
    assigned_by_user_id BIGINT,
    room_group VARCHAR(50),
    boys_rooms_requested INTEGER DEFAULT 0,
    girls_rooms_requested INTEGER DEFAULT 0,
    room_requirements_json TEXT,
    idempotency_key TEXT,
    CONSTRAINT fk_trip_bookings_trip FOREIGN KEY (trip_id) REFERENCES trips(trip_id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_trip_bookings_traveler FOREIGN KEY (traveler_id) REFERENCES travelers(traveler_id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_trip_bookings_lead FOREIGN KEY (lead_id) REFERENCES leads(lead_id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_trip_bookings_interaction FOREIGN KEY (interaction_id) REFERENCES interactions(interaction_id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_trip_bookings_assigned_to_user FOREIGN KEY (assigned_to_user_id) REFERENCES users(id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_trip_bookings_assigned_by_user FOREIGN KEY (assigned_by_user_id) REFERENCES users(id) DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX IF NOT EXISTS idx_trip_bookings_active_traveler_trip ON trip_bookings (traveler_id, trip_id, booking_status);
CREATE INDEX IF NOT EXISTS idx_trip_bookings_idempotency_key ON trip_bookings (idempotency_key);
CREATE INDEX IF NOT EXISTS ix_trip_bookings_assigned_to_user_id ON trip_bookings (assigned_to_user_id);
CREATE INDEX IF NOT EXISTS ix_trip_bookings_lead_id ON trip_bookings (lead_id);

CREATE TABLE IF NOT EXISTS ce_bookings (
    booking_id VARCHAR(50) PRIMARY KEY,
    event_id VARCHAR(50),
    event_name VARCHAR(200),
    traveler_id VARCHAR(20),
    traveler_name VARCHAR(200),
    status VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT now(),
    notes TEXT,
    CONSTRAINT fk_ce_bookings_event FOREIGN KEY (event_id) REFERENCES community_events(event_id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_ce_bookings_traveler FOREIGN KEY (traveler_id) REFERENCES travelers(traveler_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS booking_status_history (
    history_id BIGSERIAL PRIMARY KEY,
    booking_id VARCHAR(50) NOT NULL,
    old_status VARCHAR(50),
    new_status VARCHAR(50),
    changed_at TIMESTAMPTZ DEFAULT now(),
    changed_by VARCHAR(50),
    change_source VARCHAR(50),
    notes TEXT,
    CONSTRAINT fk_booking_status_history_booking FOREIGN KEY (booking_id) REFERENCES trip_bookings(booking_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX IF NOT EXISTS ix_booking_status_history_booking_id ON booking_status_history (booking_id);

CREATE TABLE IF NOT EXISTS legacy_booking_status_history_orphans (
    history_id BIGINT PRIMARY KEY,
    booking_id VARCHAR(50),
    original_booking_id VARCHAR(50) NOT NULL,
    old_status VARCHAR(50),
    new_status VARCHAR(50),
    changed_at TIMESTAMPTZ,
    changed_by VARCHAR(50),
    change_source VARCHAR(50),
    notes TEXT,
    source_table VARCHAR(100) NOT NULL DEFAULT 'booking_status_history',
    archived_reason TEXT NOT NULL DEFAULT 'missing_trip_booking_parent',
    archived_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_legacy_booking_status_history_original_booking_id
    ON legacy_booking_status_history_orphans (original_booking_id);
CREATE INDEX IF NOT EXISTS ix_legacy_booking_status_history_archived_reason
    ON legacy_booking_status_history_orphans (archived_reason);

CREATE TABLE IF NOT EXISTS handoff_queue (
    handoff_id VARCHAR(50) PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT now(),
    lead_id VARCHAR(50),
    traveler_id VARCHAR(20),
    trip_id VARCHAR(50),
    flow_key VARCHAR(100),
    reason TEXT,
    priority VARCHAR(50),
    channel VARCHAR(50),
    status VARCHAR(50) DEFAULT 'Pending',
    owner VARCHAR(100),
    assigned_to VARCHAR(100),
    notes TEXT,
    idempotency_key TEXT,
    CONSTRAINT fk_handoff_queue_lead FOREIGN KEY (lead_id) REFERENCES leads(lead_id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_handoff_queue_traveler FOREIGN KEY (traveler_id) REFERENCES travelers(traveler_id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_handoff_queue_trip FOREIGN KEY (trip_id) REFERENCES trips(trip_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX IF NOT EXISTS idx_handoff_queue_idempotency_key ON handoff_queue (idempotency_key);
CREATE INDEX IF NOT EXISTS idx_handoff_queue_lead_status ON handoff_queue (lead_id, status);
CREATE INDEX IF NOT EXISTS idx_handoff_queue_status_created ON handoff_queue (status, created_at);
CREATE INDEX IF NOT EXISTS idx_handoff_queue_traveler_status ON handoff_queue (traveler_id, status);

CREATE TABLE IF NOT EXISTS booking_event_trail (
    event_id VARCHAR(50) PRIMARY KEY,
    occurred_at TIMESTAMPTZ DEFAULT now(),
    event_type VARCHAR(100),
    event_label VARCHAR(150),
    traveler_id VARCHAR(20),
    lead_id VARCHAR(50),
    booking_id VARCHAR(50),
    trip_id VARCHAR(50),
    interaction_id VARCHAR(50),
    channel VARCHAR(50),
    actor VARCHAR(50),
    notes TEXT,
    metadata_json TEXT,
    CONSTRAINT fk_booking_event_trail_traveler FOREIGN KEY (traveler_id) REFERENCES travelers(traveler_id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_booking_event_trail_lead FOREIGN KEY (lead_id) REFERENCES leads(lead_id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_booking_event_trail_booking FOREIGN KEY (booking_id) REFERENCES trip_bookings(booking_id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_booking_event_trail_trip FOREIGN KEY (trip_id) REFERENCES trips(trip_id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_booking_event_trail_interaction FOREIGN KEY (interaction_id) REFERENCES interactions(interaction_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX IF NOT EXISTS ix_booking_event_trail_booking_id ON booking_event_trail (booking_id);
CREATE INDEX IF NOT EXISTS ix_booking_event_trail_lead_id ON booking_event_trail (lead_id);
CREATE INDEX IF NOT EXISTS ix_booking_event_trail_traveler_id ON booking_event_trail (traveler_id);

CREATE TABLE IF NOT EXISTS traveler_documents (
    document_id BIGSERIAL PRIMARY KEY,
    traveler_id VARCHAR(20) NOT NULL,
    category VARCHAR(50) NOT NULL DEFAULT 'document',
    file_name VARCHAR(255) NOT NULL,
    original_file_name VARCHAR(255),
    mime_type VARCHAR(100),
    file_extension VARCHAR(20),
    file_size INTEGER,
    storage_path VARCHAR(500) NOT NULL,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    uploaded_by VARCHAR(100),
    passport_full_name VARCHAR(200),
    passport_number VARCHAR(50),
    passport_nationality VARCHAR(100),
    passport_expiry DATE,
    verification_status VARCHAR(50) DEFAULT 'pending',
    notes TEXT,
    idempotency_key TEXT,
    CONSTRAINT fk_traveler_documents_traveler FOREIGN KEY (traveler_id) REFERENCES travelers(traveler_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX IF NOT EXISTS idx_traveler_documents_idempotency_key ON traveler_documents (idempotency_key);
CREATE INDEX IF NOT EXISTS ix_traveler_documents_traveler_id ON traveler_documents (traveler_id);

CREATE TABLE IF NOT EXISTS trip_media (
    media_id BIGSERIAL PRIMARY KEY,
    public_id VARCHAR(36) NOT NULL UNIQUE,
    trip_id VARCHAR(50) NOT NULL,
    storage_key VARCHAR(500) NOT NULL UNIQUE,
    public_url VARCHAR(500) NOT NULL,
    image_type VARCHAR(20) NOT NULL DEFAULT 'gallery',
    alt_text VARCHAR(255),
    display_order INTEGER NOT NULL DEFAULT 0,
    original_filename VARCHAR(255),
    mime_type VARCHAR(100) NOT NULL,
    file_extension VARCHAR(10) NOT NULL,
    file_size INTEGER NOT NULL DEFAULT 0,
    uploaded_by_user_id BIGINT,
    uploaded_by_name VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    verification_status VARCHAR(50) NOT NULL DEFAULT 'verified',
    CONSTRAINT fk_trip_media_trip FOREIGN KEY (trip_id) REFERENCES trips(trip_id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_trip_media_uploaded_by FOREIGN KEY (uploaded_by_user_id) REFERENCES users(id) DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX IF NOT EXISTS ix_trip_media_image_type ON trip_media (image_type);
CREATE INDEX IF NOT EXISTS ix_trip_media_is_active ON trip_media (is_active);
CREATE INDEX IF NOT EXISTS ix_trip_media_public_id ON trip_media (public_id);
CREATE INDEX IF NOT EXISTS ix_trip_media_trip_id ON trip_media (trip_id);
CREATE INDEX IF NOT EXISTS ix_trip_media_verification_status ON trip_media (verification_status);

CREATE TABLE IF NOT EXISTS assignment_history (
    id BIGSERIAL PRIMARY KEY,
    resource_type VARCHAR(30) NOT NULL,
    resource_id VARCHAR(80) NOT NULL,
    previous_user_id BIGINT,
    new_user_id BIGINT,
    assigned_by_user_id BIGINT,
    reason TEXT,
    request_id VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_assignment_history_previous_user FOREIGN KEY (previous_user_id) REFERENCES users(id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_assignment_history_new_user FOREIGN KEY (new_user_id) REFERENCES users(id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_assignment_history_assigned_by_user FOREIGN KEY (assigned_by_user_id) REFERENCES users(id) DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX IF NOT EXISTS ix_assignment_history_resource ON assignment_history (resource_id);

CREATE TABLE IF NOT EXISTS user_audit_log (
    id BIGSERIAL PRIMARY KEY,
    actor_user_id BIGINT,
    target_user_id BIGINT,
    action VARCHAR(80) NOT NULL,
    details TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_user_audit_actor FOREIGN KEY (actor_user_id) REFERENCES users(id) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_user_audit_target FOREIGN KEY (target_user_id) REFERENCES users(id) DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX IF NOT EXISTS ix_user_audit_log_actor_user_id ON user_audit_log (actor_user_id);
CREATE INDEX IF NOT EXISTS ix_user_audit_log_target_user_id ON user_audit_log (target_user_id);
CREATE INDEX IF NOT EXISTS ix_user_audit_log_created_at ON user_audit_log (created_at);

CREATE TABLE IF NOT EXISTS sync_queue (
    mapping_name TEXT NOT NULL,
    record_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (mapping_name, record_id)
);

COMMIT;
