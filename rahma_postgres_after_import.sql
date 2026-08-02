-- Rahma Traveler PostgreSQL after-import constraints and sequence reset
-- Run after loading SQLite data into PostgreSQL.
-- Important: current SQLite audit found 12 orphan booking_status_history rows referencing missing trip_bookings.
-- If this script fails on fk_booking_status_history_0_trip_bookings, audit/fix those rows before re-running constraints.

-- Audit known issue before applying FKs:
-- SELECT bsh.*
-- FROM booking_status_history bsh
-- LEFT JOIN trip_bookings tb ON tb.booking_id = bsh.booking_id
-- WHERE bsh.booking_id IS NOT NULL AND tb.booking_id IS NULL;

BEGIN;

ALTER TABLE "assignment_history" ADD CONSTRAINT "fk_assignment_history_0_users" FOREIGN KEY ("assigned_by_user_id") REFERENCES "users" ("id");
ALTER TABLE "assignment_history" ADD CONSTRAINT "fk_assignment_history_1_users" FOREIGN KEY ("new_user_id") REFERENCES "users" ("id");
ALTER TABLE "assignment_history" ADD CONSTRAINT "fk_assignment_history_2_users" FOREIGN KEY ("previous_user_id") REFERENCES "users" ("id");
ALTER TABLE "booking_event_trail" ADD CONSTRAINT "fk_booking_event_trail_0_interactions" FOREIGN KEY ("interaction_id") REFERENCES "interactions" ("interaction_id");
ALTER TABLE "booking_event_trail" ADD CONSTRAINT "fk_booking_event_trail_1_trips" FOREIGN KEY ("trip_id") REFERENCES "trips" ("trip_id");
ALTER TABLE "booking_event_trail" ADD CONSTRAINT "fk_booking_event_trail_2_trip_bookings" FOREIGN KEY ("booking_id") REFERENCES "trip_bookings" ("booking_id");
ALTER TABLE "booking_event_trail" ADD CONSTRAINT "fk_booking_event_trail_3_leads" FOREIGN KEY ("lead_id") REFERENCES "leads" ("lead_id");
ALTER TABLE "booking_event_trail" ADD CONSTRAINT "fk_booking_event_trail_4_travelers" FOREIGN KEY ("traveler_id") REFERENCES "travelers" ("traveler_id");
ALTER TABLE "booking_status_history" ADD CONSTRAINT "fk_booking_status_history_0_trip_bookings" FOREIGN KEY ("booking_id") REFERENCES "trip_bookings" ("booking_id");
ALTER TABLE "ce_bookings" ADD CONSTRAINT "fk_ce_bookings_0_travelers" FOREIGN KEY ("traveler_id") REFERENCES "travelers" ("traveler_id");
ALTER TABLE "ce_bookings" ADD CONSTRAINT "fk_ce_bookings_1_community_events" FOREIGN KEY ("event_id") REFERENCES "community_events" ("event_id");
ALTER TABLE "handoff_queue" ADD CONSTRAINT "fk_handoff_queue_0_trips" FOREIGN KEY ("trip_id") REFERENCES "trips" ("trip_id");
ALTER TABLE "handoff_queue" ADD CONSTRAINT "fk_handoff_queue_1_travelers" FOREIGN KEY ("traveler_id") REFERENCES "travelers" ("traveler_id");
ALTER TABLE "handoff_queue" ADD CONSTRAINT "fk_handoff_queue_2_leads" FOREIGN KEY ("lead_id") REFERENCES "leads" ("lead_id");
ALTER TABLE "interactions" ADD CONSTRAINT "fk_interactions_0_travelers" FOREIGN KEY ("traveler_id") REFERENCES "travelers" ("traveler_id");
ALTER TABLE "leads" ADD CONSTRAINT "fk_leads_0_travelers" FOREIGN KEY ("traveler_id") REFERENCES "travelers" ("traveler_id");
ALTER TABLE "traveler_documents" ADD CONSTRAINT "fk_traveler_documents_0_travelers" FOREIGN KEY ("traveler_id") REFERENCES "travelers" ("traveler_id");
ALTER TABLE "trip_bookings" ADD CONSTRAINT "fk_trip_bookings_0_travelers" FOREIGN KEY ("traveler_id") REFERENCES "travelers" ("traveler_id");
ALTER TABLE "trip_bookings" ADD CONSTRAINT "fk_trip_bookings_1_trips" FOREIGN KEY ("trip_id") REFERENCES "trips" ("trip_id");
ALTER TABLE "trip_media" ADD CONSTRAINT "fk_trip_media_0_users" FOREIGN KEY ("uploaded_by_user_id") REFERENCES "users" ("id");
ALTER TABLE "trip_media" ADD CONSTRAINT "fk_trip_media_1_trips" FOREIGN KEY ("trip_id") REFERENCES "trips" ("trip_id");
ALTER TABLE "user_audit_log" ADD CONSTRAINT "fk_user_audit_log_0_users" FOREIGN KEY ("target_user_id") REFERENCES "users" ("id");
ALTER TABLE "user_audit_log" ADD CONSTRAINT "fk_user_audit_log_1_users" FOREIGN KEY ("actor_user_id") REFERENCES "users" ("id");

-- Reset identity sequences after importing explicit IDs.
SELECT setval(pg_get_serial_sequence('assignment_history', 'id'), COALESCE((SELECT MAX("id") FROM "assignment_history"), 0) + 1, false);
SELECT setval(pg_get_serial_sequence('booking_status_history', 'history_id'), COALESCE((SELECT MAX("history_id") FROM "booking_status_history"), 0) + 1, false);
SELECT setval(pg_get_serial_sequence('language_templates', 'id'), COALESCE((SELECT MAX("id") FROM "language_templates"), 0) + 1, false);
SELECT setval(pg_get_serial_sequence('traveler_documents', 'document_id'), COALESCE((SELECT MAX("document_id") FROM "traveler_documents"), 0) + 1, false);
SELECT setval(pg_get_serial_sequence('trip_media', 'media_id'), COALESCE((SELECT MAX("media_id") FROM "trip_media"), 0) + 1, false);
SELECT setval(pg_get_serial_sequence('user_audit_log', 'id'), COALESCE((SELECT MAX("id") FROM "user_audit_log"), 0) + 1, false);
SELECT setval(pg_get_serial_sequence('users', 'id'), COALESCE((SELECT MAX("id") FROM "users"), 0) + 1, false);

COMMIT;
