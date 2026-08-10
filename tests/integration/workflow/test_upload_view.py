from __future__ import annotations

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError

from id_dedup.workflow.models import Batch, Image, OutboxMessage
from id_dedup.workflow.service import EmptyBatch, register_upload

PROCESS_BATCH_TASK = "id_dedup.workflow.tasks.process_batch"

_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
_JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 100


def _png(name: str = "test.png") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, _PNG_BYTES, content_type="image/png")


@pytest.mark.django_db
class TestUploadView:
    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def test_anonymous_post_redirects_to_login(self, client):
        response = client.post("/workflow/upload/", data={}, follow=False)
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def test_anonymous_get_redirects_to_login(self, client):
        response = client.get("/workflow/upload/")
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    # ------------------------------------------------------------------
    # GET
    # ------------------------------------------------------------------

    def test_get_renders_form(self, logged_in_client):
        response = logged_in_client.get("/workflow/upload/")
        assert response.status_code == 200
        assert b'name="images"' in response.content

    # ------------------------------------------------------------------
    # POST — happy path
    # ------------------------------------------------------------------

    def test_post_valid_png_creates_batch_image_outbox(self, logged_in_client, settings, tmp_path):
        response = logged_in_client.post("/workflow/upload/", data={"images": [_png()]})

        assert response.status_code == 302
        assert response["Location"].endswith("/")

        assert Batch.objects.count() == 1
        assert Image.objects.count() == 1
        batch = Batch.objects.get()
        outbox = OutboxMessage.objects.get()
        assert outbox.task_name == PROCESS_BATCH_TASK
        assert outbox.payload["batch_id"] == str(batch.pk)
        assert outbox.payload["user_id"] == User.objects.get(username="testuser").pk

    def test_post_valid_jpeg_creates_batch(self, logged_in_client):
        jpeg = SimpleUploadedFile("photo.jpg", _JPEG_BYTES, content_type="image/jpeg")
        logged_in_client.post("/workflow/upload/", data={"images": [jpeg]})

        assert Batch.objects.count() == 1
        assert Image.objects.count() == 1

    # ------------------------------------------------------------------
    # POST — duplicate filenames
    # ------------------------------------------------------------------

    def test_post_duplicate_filenames_distinct_storage(self, logged_in_client, tmp_path):
        same_name = "photo.jpg"
        file_a = SimpleUploadedFile(same_name, _JPEG_BYTES, content_type="image/jpeg")
        file_b = SimpleUploadedFile(same_name, _JPEG_BYTES, content_type="image/jpeg")

        logged_in_client.post("/workflow/upload/", data={"images": [file_a, file_b]})

        assert Image.objects.count() == 2
        names = [img.source_image.name for img in Image.objects.all()]
        assert len(set(names)) == 2
        for name in names:
            assert (settings.MEDIA_ROOT / name).exists()

    # ------------------------------------------------------------------
    # POST — validation errors (no Batch created)
    # ------------------------------------------------------------------

    def test_post_no_files_shows_error(self, logged_in_client):
        response = logged_in_client.post("/workflow/upload/", data={})

        assert response.status_code == 200
        assert b"No files selected." in response.content
        assert Batch.objects.count() == 0

    def test_post_text_file_shows_error(self, logged_in_client):
        bad = SimpleUploadedFile("notes.txt", b"not an image", content_type="text/plain")
        response = logged_in_client.post("/workflow/upload/", data={"images": [bad]})

        assert response.status_code == 200
        assert b"None of the uploaded files were valid images." in response.content
        assert Batch.objects.count() == 0

    def test_post_pdf_file_shows_error(self, logged_in_client):
        bad = SimpleUploadedFile("doc.pdf", b"%PDF-1.4 garbage", content_type="application/pdf")
        response = logged_in_client.post("/workflow/upload/", data={"images": [bad]})

        assert response.status_code == 200
        assert b"None of the uploaded files were valid images." in response.content
        assert Batch.objects.count() == 0

    def test_post_mixed_valid_and_invalid_keeps_valid(self, logged_in_client):
        good = _png("good.png")
        bad = SimpleUploadedFile("bad.txt", b"hello", content_type="text/plain")
        response = logged_in_client.post("/workflow/upload/", data={"images": [good, bad]})

        assert response.status_code == 302
        assert Batch.objects.count() == 1
        assert Image.objects.count() == 1
        batch = Batch.objects.get()
        assert batch.skipped_files == ["bad.txt"]
        assert OutboxMessage.objects.count() == 1

    def test_post_mixed_upload_warns_about_skipped(self, logged_in_client):
        good = _png("good.png")
        bad = SimpleUploadedFile("bad.txt", b"hello", content_type="text/plain")
        response = logged_in_client.post(
            "/workflow/upload/",
            data={"images": [good, bad]},
            follow=True,
        )

        assert b"Skipped 1 file" in response.content
        assert b"Upload received" in response.content

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------

    def test_duplicate_source_image_rejected(self):
        batch = Batch.objects.create()
        Image.objects.create(batch=batch, source_image="images/collision.jpg")
        with pytest.raises(IntegrityError):
            Image.objects.create(batch=batch, source_image="images/collision.jpg")


@pytest.mark.django_db
class TestRegisterUploadService:
    """Service-layer tests for register_upload — no HTTP layer involved."""

    def test_empty_upload_raises_empty_batch(self):
        with pytest.raises(EmptyBatch, match="No files selected."):
            register_upload([])

    def test_none_upload_raises_empty_batch(self):
        with pytest.raises(EmptyBatch):
            register_upload(None)

    def test_valid_upload_creates_batch_and_outbox(self):
        upload = SimpleUploadedFile("img.png", _PNG_BYTES, content_type="image/png")
        batch = register_upload([upload])

        assert batch.pk is not None
        assert Image.objects.filter(batch=batch).count() == 1
        outbox = OutboxMessage.objects.get()
        assert outbox.task_name == PROCESS_BATCH_TASK
        assert outbox.payload["batch_id"] == str(batch.pk)
        assert outbox.payload["user_id"] is None

    def test_valid_upload_with_user_id(self):
        upload = SimpleUploadedFile("img.png", _PNG_BYTES, content_type="image/png")
        batch = register_upload([upload], user_id=42)

        outbox = OutboxMessage.objects.get()
        assert outbox.payload["batch_id"] == str(batch.pk)
        assert outbox.payload["user_id"] == 42

    def test_valid_upload_records_no_skipped_files(self):
        upload = SimpleUploadedFile("img.png", _PNG_BYTES, content_type="image/png")
        batch = register_upload([upload])

        assert batch.skipped_files == []

    def test_mixed_upload_registers_valid_and_records_skipped(self):
        good = SimpleUploadedFile("good.png", _PNG_BYTES, content_type="image/png")
        bad = SimpleUploadedFile("bad.txt", b"hello", content_type="text/plain")
        batch = register_upload([good, bad])

        assert Image.objects.filter(batch=batch).count() == 1
        assert batch.skipped_files == ["bad.txt"]
        outbox = OutboxMessage.objects.get()
        assert outbox.task_name == PROCESS_BATCH_TASK
        assert outbox.payload["batch_id"] == str(batch.pk)

    def test_invalid_file_type_raises_empty_batch(self):
        bad = SimpleUploadedFile("notes.txt", b"not an image", content_type="text/plain")
        with pytest.raises(EmptyBatch, match="None of the uploaded files were valid images."):
            register_upload([bad])

    def test_duplicate_filenames_uniquified(self, settings):
        file_a = SimpleUploadedFile("photo.jpg", _JPEG_BYTES, content_type="image/jpeg")
        file_b = SimpleUploadedFile("photo.jpg", _JPEG_BYTES, content_type="image/jpeg")

        batch = register_upload([file_a, file_b])

        images = list(Image.objects.filter(batch=batch))
        assert len(images) == 2
        names = {img.source_image.name for img in images}
        assert len(names) == 2
        for name in names:
            assert (settings.MEDIA_ROOT / name).exists()

    def test_no_outbox_on_validation_error(self):
        bad = SimpleUploadedFile("bad.txt", b"nope", content_type="text/plain")
        with pytest.raises(EmptyBatch):
            register_upload([bad])
        assert OutboxMessage.objects.count() == 0
