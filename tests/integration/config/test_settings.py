from id_dedup.config import settings as app_settings


def test_beat_schedule_uses_outbox_sweep_seconds():
    assert app_settings.CELERY_BEAT_SCHEDULE["dispatch-outbox"]["schedule"] == app_settings.OUTBOX_SWEEP_SECONDS
