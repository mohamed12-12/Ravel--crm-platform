from __future__ import annotations

import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfeA\xe2&\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"


class _Service:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()


class TripMediaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_env = os.environ.copy()
        self.tmpdir = tempfile.mkdtemp(prefix="rahma-trip-media-")
        self.db_path = Path(self.tmpdir) / "test.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{self.db_path.as_posix()}"
        os.environ["CRM_AUTH_ENABLED"] = "true"
        os.environ["DATA_AUTHORITY"] = "crm"
        os.environ["DEMO_DATA_MODE"] = "true"

        from app import create_app
        from app.extensions import db
        from app.models.trip import Trip
        from app.models.trip_media import TripMedia
        from app.models.user import User

        self.app = create_app("development")
        self.app.config.update(
            TESTING=True,
            TRIP_MEDIA_ROOT=str(Path(self.tmpdir) / "trip-media"),
            TRIP_MEDIA_MAX_BYTES=1024 * 1024,
        )
        self.db = db
        self.Trip = Trip
        self.TripMedia = TripMedia
        self.User = User
        with self.app.app_context():
            self.db.drop_all()
            self.db.create_all()
            self.db.session.add(
                self.Trip(
                    trip_id="RT-IMG-1",
                    trip_name="Image Test Trip",
                    type="Local",
                    sales_status="Open",
                    single_total=4,
                    single_remaining=4,
                    public_price="1000 EGP",
                )
            )
            self.db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.original_env)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _login(self, role: str = "agent") -> str:
        token = f"csrf-{uuid.uuid4().hex}"
        with self.app.app_context():
            user = self.User(username=f"user-{uuid.uuid4().hex[:8]}", full_name="Media Tester", password_hash="x", role=role, is_active=True)
            self.db.session.add(user)
            self.db.session.commit()
            user_id = user.id
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["username"] = user.username
            sess["user_id"] = user_id
            sess["csrf_token"] = token
        return token

    def _upload(self, filename: str, content: bytes = PNG_BYTES, mimetype: str = "image/png", *, token: str | None = None):
        data = {
            "csrf_token": token or self._login(),
            "cover_image": (io.BytesIO(content), filename),
        }
        return self.client.post(
            "/trips/RT-IMG-1/media",
            data=data,
            content_type="multipart/form-data",
            headers={"Accept": "application/json"},
        )

    def test_authorized_cover_upload_stores_verified_metadata(self) -> None:
        response = self._upload("cover.png")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        with self.app.app_context():
            media = self.TripMedia.query.filter_by(trip_id="RT-IMG-1", image_type="cover", is_active=True).one()
            self.assertEqual(media.mime_type, "image/png")
            self.assertTrue(media.public_url.startswith("/trips/media/"))
            self.assertTrue((Path(self.app.config["TRIP_MEDIA_ROOT"]) / media.storage_key).resolve().exists())

    def test_unauthorized_upload_is_rejected(self) -> None:
        response = self.client.post(
            "/trips/RT-IMG-1/media",
            data={"cover_image": (io.BytesIO(PNG_BYTES), "cover.png")},
            content_type="multipart/form-data",
            headers={"Accept": "application/json"},
        )
        self.assertEqual(response.status_code, 401)

    def test_invalid_extension_is_rejected(self) -> None:
        response = self._upload("cover.gif")
        self.assertEqual(response.status_code, 400)
        self.assertIn("JPG, JPEG, PNG, or WEBP", response.get_data(as_text=True))

    def test_invalid_mime_type_is_rejected(self) -> None:
        response = self.client.post(
            "/trips/RT-IMG-1/media",
            data={
                "csrf_token": self._login(),
                "cover_image": (io.BytesIO(PNG_BYTES), "cover.png", "application/octet-stream"),
            },
            content_type="multipart/form-data",
            headers={"Accept": "application/json"},
        )
        self.assertEqual(response.status_code, 400)

    def test_excessive_file_size_is_rejected(self) -> None:
        self.app.config["TRIP_MEDIA_MAX_BYTES"] = 16
        response = self._upload("cover.png", PNG_BYTES + b"x" * 100)
        self.assertEqual(response.status_code, 400)
        self.assertIn("exceeds", response.get_data(as_text=True))

    def test_unsafe_path_filename_is_rejected_by_storage_service(self) -> None:
        from werkzeug.datastructures import FileStorage
        from app.services.trip_media import TripMediaValidationError, save_trip_media_uploads

        with self.app.app_context():
            upload = FileStorage(stream=io.BytesIO(PNG_BYTES), filename="../evil.png", content_type="image/png")
            with self.assertRaises(TripMediaValidationError):
                save_trip_media_uploads(
                    trip_id="RT-IMG-1",
                    files={"cover_image": upload},
                    form={},
                    uploaded_by_user_id=None,
                    uploaded_by_name="Tester",
                )

    def test_cover_image_is_displayed_on_trip_list(self) -> None:
        self._upload("cover.png")
        response = self.client.get("/trips/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("/trips/media/", html)
        self.assertIn("Image Test Trip", html)

    def test_gallery_images_keep_upload_order(self) -> None:
        token = self._login()
        response = self.client.post(
            "/trips/RT-IMG-1/media",
            data={
                "csrf_token": token,
                "gallery_images": [
                    (io.BytesIO(PNG_BYTES), "one.png"),
                    (io.BytesIO(JPEG_BYTES), "two.jpg"),
                ],
            },
            content_type="multipart/form-data",
            headers={"Accept": "application/json"},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        with self.app.app_context():
            orders = [
                row.display_order
                for row in self.TripMedia.query.filter_by(trip_id="RT-IMG-1", image_type="gallery").order_by(self.TripMedia.display_order).all()
            ]
            self.assertEqual(orders, [1, 2])

    def test_agent_retrieves_correct_trip_image(self) -> None:
        self._upload("cover.png")
        from dataclasses import replace
        from services.ai_agent.ai_agent_app.agent.read_only_tools import ReadOnlyCRMTools
        from services.ai_agent.ai_agent_app.config import load_settings

        settings = replace(load_settings(), crm_api_base_url="http://crm.example")
        tools = ReadOnlyCRMTools(settings, service=_Service(self.db_path))
        result = tools.get_trip_media(trip_id="RT-IMG-1")
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["cover"]["trip_id"], "RT-IMG-1")
        self.assertTrue(result["cover"]["public_url"].startswith("http://crm.example/trips/media/"))

    def test_missing_trip_image_returns_safe_response(self) -> None:
        from dataclasses import replace
        from services.ai_agent.ai_agent_app.agent.read_only_tools import ReadOnlyCRMTools
        from services.ai_agent.ai_agent_app.config import load_settings

        settings = replace(load_settings(), crm_api_base_url="http://crm.example")
        tools = ReadOnlyCRMTools(settings, service=_Service(self.db_path))
        result = tools.get_trip_media(trip_id="RT-IMG-1")
        self.assertEqual(result["status"], "not_found")
        self.assertIn("No verified CRM trip image", result["message"])

    def test_pic_wording_is_recognized_as_trip_media_request(self) -> None:
        from services.ai_agent.ai_agent_app.agent.tool_calling_runtime import ToolCallingSessionRuntime

        self.assertTrue(ToolCallingSessionRuntime._is_trip_media_request("give me pic of trip"))
        self.assertTrue(ToolCallingSessionRuntime._is_trip_media_request("send pics for this hotel"))


if __name__ == "__main__":
    unittest.main()
