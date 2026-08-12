from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.utils import timezone
from model_bakery import baker

from id_dedup.workflow.models import Batch, Image, OutboxMessage
from id_dedup.workflow.service import register_upload
from id_dedup.workflow.tasks import dispatch_outbox

_PROCESS_BATCH_TASK = "id_dedup.workflow.tasks.process_batch"
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


def _outbox(task_name: str = "id_dedup.workflow.tasks.process_batch", **kwargs) -> OutboxMessage:
    return baker.make(OutboxMessage, task_name=task_name, payload={}, **kwargs)


def _patch_send_task(monkeypatch, send):
    """Replace current_app.send_task so the reaper never touches the broker."""
    monkeypatch.setattr("id_dedup.workflow.tasks.current_app.send_task", send)


@pytest.mark.django_db
class TestRegisterUploadOutbox:
    def test_register_upload_creates_exactly_one_outbox(self):
        upload = SimpleUploadedFile("img.png", _PNG_BYTES, content_type="image/png")
        batch = register_upload([upload])

        assert Batch.objects.count() == 1
        assert Image.objects.filter(batch=batch).count() == 1
        outbox = OutboxMessage.objects.get()
        assert outbox.task_name == _PROCESS_BATCH_TASK
        assert outbox.payload["batch_id"] == str(batch.pk)
        assert outbox.dispatched_at is None
        assert outbox.attempts == 0
        assert outbox.dead_lettered_at is None

    def test_upload_view_creates_exactly_one_outbox(self, logged_in_client):
        upload = SimpleUploadedFile("img.png", _PNG_BYTES, content_type="image/png")
        logged_in_client.post("/workflow/upload/", data={"images": [upload]})

        assert OutboxMessage.objects.count() == 1
        assert Batch.objects.count() == 1


@pytest.mark.django_db
class TestDispatchOutbox:
    def test_dispatches_pending_row_and_marks_dispatched_at(self, monkeypatch):
        msg = _outbox()
        sent = []

        def _send(task_name, kwargs):
            sent.append((task_name, kwargs))

        _patch_send_task(monkeypatch, _send)

        assert dispatch_outbox() == 1
        assert sent == [(_PROCESS_BATCH_TASK, {})]
        msg.refresh_from_db()
        assert msg.dispatched_at is not None
        assert msg.attempts == 1

    def test_success_counts_attempt_after_prior_failure(self, monkeypatch):
        msg = _outbox(max_attempts=5)
        calls = {"n": 0}

        def _send(task_name, kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("broker down")

        _patch_send_task(monkeypatch, _send)

        assert dispatch_outbox() == 0
        assert dispatch_outbox() == 1
        msg.refresh_from_db()
        assert msg.dispatched_at is not None
        assert msg.attempts == 2
        assert "broker down" in msg.last_error

    def test_already_dispatched_rows_are_not_resent(self, monkeypatch):
        dispatched = _outbox()
        dispatched.dispatched_at = timezone.now()
        dispatched.save(update_fields=["dispatched_at"])
        _patch_send_task(monkeypatch, lambda task_name, kwargs: None)

        assert dispatch_outbox() == 0
        dispatched.refresh_from_db()
        assert dispatched.attempts == 0

    def test_send_failure_records_attempt_and_continues(self, monkeypatch):
        good = _outbox(task_name="good")
        bad = _outbox(task_name="bad")

        def _send(task_name, kwargs):
            if task_name == "bad":
                raise RuntimeError("broker down")

        _patch_send_task(monkeypatch, _send)

        assert dispatch_outbox() == 1
        good.refresh_from_db()
        bad.refresh_from_db()
        assert good.dispatched_at is not None
        assert bad.dispatched_at is None
        assert bad.attempts == 1
        assert "broker down" in bad.last_error

    def test_row_dead_lettered_after_max_attempts(self, monkeypatch):
        msg = _outbox(max_attempts=2)
        _patch_send_task(monkeypatch, lambda task_name, kwargs: (_ for _ in ()).throw(RuntimeError("down")))

        assert dispatch_outbox() == 0
        msg.refresh_from_db()
        assert msg.attempts == 1
        assert msg.dead_lettered_at is None

        assert dispatch_outbox() == 0
        msg.refresh_from_db()
        assert msg.attempts == 2
        assert msg.dead_lettered_at is not None

    def test_dead_lettered_row_is_never_swept_again(self, monkeypatch):
        msg = _outbox(max_attempts=2)
        _patch_send_task(monkeypatch, lambda task_name, kwargs: (_ for _ in ()).throw(RuntimeError("down")))

        dispatch_outbox()
        dispatch_outbox()
        msg.refresh_from_db()
        assert msg.dead_lettered_at is not None

        # The dead-lettered row must not be swept again, even with the broker back.
        _patch_send_task(monkeypatch, lambda task_name, kwargs: None)
        assert dispatch_outbox() == 0
        msg.refresh_from_db()
        assert msg.attempts == 2

    def test_empty_queue_returns_zero(self, monkeypatch):
        _patch_send_task(monkeypatch, lambda task_name, kwargs: None)
        assert dispatch_outbox() == 0


@pytest.mark.django_db
class TestDispatchOutboxCommand:
    def test_command_dispatches_and_reports_count(self, monkeypatch, capsys):
        _outbox()
        _patch_send_task(monkeypatch, lambda task_name, kwargs: None)

        call_command("dispatch_outbox")

        out = capsys.readouterr().out
        assert "Dispatched 1 message(s)" in out

    def test_command_dead_lists_dead_rows(self, capsys):
        dead = _outbox(max_attempts=1)
        dead.dead_lettered_at = timezone.now()
        dead.last_error = "boom"
        dead.attempts = 1
        dead.save(update_fields=["dead_lettered_at", "last_error", "attempts"])

        call_command("dispatch_outbox", "--dead")

        out = capsys.readouterr().out
        assert str(dead.pk) in out
        assert "boom" in out

    def test_command_requeue_dead_resets_dead_rows(self):
        dead = _outbox(max_attempts=1)
        dead.dead_lettered_at = timezone.now()
        dead.last_error = "boom"
        dead.attempts = 1
        dead.save(update_fields=["dead_lettered_at", "last_error", "attempts"])

        call_command("dispatch_outbox", "--requeue-dead")

        dead.refresh_from_db()
        assert dead.dead_lettered_at is None
        assert dead.attempts == 0
        assert dead.last_error == ""
