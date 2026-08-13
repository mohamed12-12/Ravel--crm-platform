from __future__ import annotations

import io
import os
import shutil
import sqlite3
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def _models():
    """Resolve model classes from the currently-loaded `app` package.

    These must NOT be imported at module scope. Other test files pop
    `app`/`app.*` out of sys.modules and re-import to get a fresh
    Flask-SQLAlchemy instance; a module-level class object would stay bound to
    the *previous* instance, so once this file ran after one of them every
    query raised "The current Flask app is not registered with this
    'SQLAlchemy' instance" -- these tests passed alone and failed in suite order.
    """
    from app.models.traveler import Traveler
    from app.models.trip import Trip

    return Traveler, Trip


def _make_app(tmpdir: Path):
    db_path = tmpdir / "crm.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.resolve().as_posix()}"
    os.environ["RAHMA_SYSTEM_DB_PATH"] = str(db_path)
    os.environ["TRAVELER_UPLOAD_ROOT"] = str(tmpdir / "uploads")
    # Pin auth off: app/__init__.py defaults CRM_AUTH_ENABLED to "true" when
    # unset, so inheriting it from the ambient environment made these
    # unauthenticated route tests 302-redirect in full-suite order.
    os.environ["CRM_AUTH_ENABLED"] = "false"
    from app import create_app
    from app.extensions import db as local_db
    Traveler, Trip = _models()
    app = create_app("development")
    app.config["TESTING"] = True
    app.config["TRAVELER_UPLOAD_ROOT"] = str(tmpdir / "uploads")
    with app.app_context():
        local_db.create_all()
        local_db.session.add_all([
            Traveler(
                traveler_id="TR900",
                full_name="Passport Traveler",
                status="Active",
                phone_code="20",
                whatsapp_raw="1012345678",
                normalized_whatsapp="+201012345678",
                integrated_whatsapp="+201012345678",
                phone_lookup_key="20:1012345678",
            ),
            Trip(
                trip_id="INT-001",
                trip_name="International Demo",
                type="International",
                sales_status="Open",
                single_total=2,
                double_total=2,
                triple_total=2,
                single_remaining=2,
                double_remaining=2,
                triple_remaining=2,
                draft_holds_single=0,
                draft_holds_double=0,
                draft_holds_triple=0,
            ),
            Trip(
                trip_id="LOC-001",
                trip_name="Local Demo",
                type="Local",
                sales_status="Open",
                single_total=2,
                double_total=2,
                triple_total=2,
                single_remaining=2,
                double_remaining=2,
                triple_remaining=2,
                draft_holds_single=0,
                draft_holds_double=0,
                draft_holds_triple=0,
            ),
        ])
        local_db.session.commit()
    return app.test_client(), app, db_path


class PassportAttachmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path('.tmp-test-workdirs') / f'phase6-passport-{uuid.uuid4().hex}'
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.original_env = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.original_env)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_passport_fields_save_on_traveler_edit(self) -> None:
        client, app, db_path = _make_app(self.tmpdir)
        response = client.put(
            '/travelers/TR900',
            json={
                'passport_name': 'Passport Traveler',
                'passport_number': 'A1234567',
                'passport_nationality': 'Egyptian',
                'passport_expiry': '2030-05-01',
            },
        )
        self.assertEqual(response.status_code, 200)
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT passport_name, passport_number, passport_expiry FROM travelers WHERE traveler_id = ?", ('TR900',)).fetchone()
            self.assertEqual(row[0], 'Passport Traveler')
            self.assertEqual(row[1], 'A1234567')
            self.assertEqual(row[2], '2030-05-01')

    def test_upload_accepts_supported_types_and_links_metadata(self) -> None:
        client, app, db_path = _make_app(self.tmpdir)
        samples = [
            (b'jpeg-bytes', 'passport.jpg', 'image/jpeg'),
            (b'png-bytes', 'passport.png', 'image/png'),
            (b'pdf-bytes', 'passport.pdf', 'application/pdf'),
        ]
        for idx, (payload, filename, mimetype) in enumerate(samples, start=1):
            resp = client.post(
                '/travelers/TR900/documents',
                data={
                    'category': 'passport',
                    'passport_full_name': 'Passport Traveler',
                    'passport_number': f'A12345{idx}',
                    'passport_nationality': 'Egyptian',
                    'passport_expiry': '2030-05-01',
                    'file': (io.BytesIO(payload), filename, mimetype),
                },
                content_type='multipart/form-data',
                follow_redirects=False,
            )
            self.assertEqual(resp.status_code, 302)
        with sqlite3.connect(db_path) as conn:
            docs = conn.execute("SELECT COUNT(*) FROM traveler_documents WHERE traveler_id = ?", ('TR900',)).fetchone()[0]
            self.assertEqual(docs, 3)
            traveler = conn.execute("SELECT passport_number FROM travelers WHERE traveler_id = ?", ('TR900',)).fetchone()
            self.assertEqual(traveler[0], 'A123453')

    def test_payment_screenshot_upload_does_not_update_passport_fields(self) -> None:
        client, app, db_path = _make_app(self.tmpdir)
        with app.app_context():
            from app.extensions import db as local_db

            Traveler, _Trip = _models()
            traveler = Traveler.query.filter_by(traveler_id="TR900").one()
            traveler.passport_name = "Original Passport Name"
            traveler.passport_number = "ORIGINAL123"
            traveler.passport_nationality = "Egyptian"
            traveler.passport_attachment_ref = "TR900/original-passport.pdf"
            local_db.session.commit()

        resp = client.post(
            '/travelers/TR900/documents',
            data={
                'category': 'payment_screenshot',
                'passport_full_name': 'Malicious Passport Name',
                'passport_number': 'OVERWRITE999',
                'passport_nationality': 'Overwrite Nationality',
                'passport_expiry': '2040-01-01',
                'notes': 'Deposit receipt',
                'file': (io.BytesIO(b'png-payment-screenshot'), 'payment.png', 'image/png'),
            },
            content_type='multipart/form-data',
            follow_redirects=False,
        )

        self.assertEqual(resp.status_code, 302)
        with sqlite3.connect(db_path) as conn:
            doc = conn.execute(
                """
                SELECT category, original_file_name, passport_full_name, passport_number, notes
                FROM traveler_documents
                WHERE traveler_id = ?
                """,
                ('TR900',),
            ).fetchone()
            self.assertEqual(doc[0], 'payment_screenshot')
            self.assertEqual(doc[1], 'payment.png')
            self.assertIsNone(doc[2])
            self.assertIsNone(doc[3])
            self.assertEqual(doc[4], 'Deposit receipt')

            traveler = conn.execute(
                """
                SELECT passport_name, passport_number, passport_nationality, passport_expiry, passport_attachment_ref
                FROM travelers
                WHERE traveler_id = ?
                """,
                ('TR900',),
            ).fetchone()
            self.assertEqual(traveler[0], 'Original Passport Name')
            self.assertEqual(traveler[1], 'ORIGINAL123')
            self.assertEqual(traveler[2], 'Egyptian')
            self.assertIsNone(traveler[3])
            self.assertEqual(traveler[4], 'TR900/original-passport.pdf')

    def test_uploaded_document_notes_render_on_traveler_profile(self) -> None:
        client, app, db_path = _make_app(self.tmpdir)
        resp = client.post(
            '/travelers/TR900/documents',
            data={
                'category': 'payment_screenshot',
                'notes': 'Deposit receipt from WhatsApp',
                'file': (io.BytesIO(b'png-payment-screenshot'), 'payment-note.png', 'image/png'),
            },
            content_type='multipart/form-data',
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)

        page = client.get('/travelers/TR900')
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn('Payment Screenshots', body)
        self.assertIn('payment-note.png', body)
        self.assertIn('Deposit receipt from WhatsApp', body)

    def test_upload_rejects_invalid_type_and_oversize(self) -> None:
        client, app, db_path = _make_app(self.tmpdir)
        bad = client.post(
            '/travelers/TR900/documents',
            data={'file': (io.BytesIO(b'bad'), 'passport.exe', 'application/octet-stream')},
            content_type='multipart/form-data',
            follow_redirects=False,
        )
        self.assertEqual(bad.status_code, 302)
        huge = client.post(
            '/travelers/TR900/documents',
            data={'file': (io.BytesIO(b'0' * (10 * 1024 * 1024 + 1)), 'passport.pdf', 'application/pdf')},
            content_type='multipart/form-data',
            follow_redirects=False,
        )
        self.assertEqual(huge.status_code, 413)
        with sqlite3.connect(db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM traveler_documents").fetchone()[0], 0)

    def test_international_booking_flags_passport_pending(self) -> None:
        client, app, db_path = _make_app(self.tmpdir)
        response = client.post(
            '/bookings/',
            data={
                'trip_id': 'INT-001',
                'traveler_id': 'TR900',
                'traveler_name': 'Passport Traveler',
                'room_type': 'Double',
                'currency': 'USD',
                'booking_source': 'Admin',
                'payment_status': 'Pending',
                'booking_notes': 'International booking',
            },
            follow_redirects=False,
        )
        self.assertIn(response.status_code, {302, 200})
        with sqlite3.connect(db_path) as conn:
            booking = conn.execute("SELECT passport_required, passport_status FROM trip_bookings LIMIT 1").fetchone()
            self.assertIsNotNone(booking)
            self.assertTrue(bool(booking[0]))
            self.assertEqual(booking[1], 'pending')

    def test_local_booking_does_not_require_passport(self) -> None:
        client, app, db_path = _make_app(self.tmpdir)
        response = client.post(
            '/bookings/',
            data={
                'trip_id': 'LOC-001',
                'traveler_id': 'TR900',
                'traveler_name': 'Passport Traveler',
                'room_type': 'Double',
                'currency': 'USD',
                'booking_source': 'Admin',
                'payment_status': 'Pending',
                'booking_notes': 'Local booking',
            },
            follow_redirects=False,
        )
        self.assertIn(response.status_code, {302, 200})
        with sqlite3.connect(db_path) as conn:
            booking = conn.execute("SELECT passport_required, COALESCE(passport_status, '') FROM trip_bookings LIMIT 1").fetchone()
            self.assertIsNotNone(booking)
            self.assertFalse(bool(booking[0]))
            self.assertIn(booking[1], ('',))


if __name__ == '__main__':
    unittest.main()
