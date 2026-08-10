from __future__ import annotations

from io import BytesIO

import pytest

from id_dedup.images import UnsupportedImageType, validate_image


def test_unsupported_image_type_is_raised_for_rejected_content():
    assert issubclass(UnsupportedImageType, Exception)


def test_validate_image_accepts_jpeg_magic_bytes():
    validate_image(BytesIO(b"\xff\xd8\xff\xe0" + b"\x00" * 100))


def test_validate_image_accepts_png_signature():
    validate_image(BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100))


def test_validate_image_accepts_webp_signature():
    validate_image(BytesIO(b"RIFF" + b"\x00" * 4 + b"WEBP"))


def test_validate_image_rejects_pdf():
    with pytest.raises(UnsupportedImageType):
        validate_image(BytesIO(b"%PDF-1.4 garbage"))


def test_validate_image_rejects_plain_text():
    with pytest.raises(UnsupportedImageType):
        validate_image(BytesIO(b"plain text"))


def test_validate_image_magic_bytes_win_over_extension():
    validate_image(BytesIO(b"\xff\xd8\xff\xe0" + b"\x00" * 100))


def test_validate_image_rewinds_stream():
    stream = BytesIO(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
    validate_image(stream)
    assert stream.tell() == 0


def test_validate_image_rewinds_stream_on_rejection():
    stream = BytesIO(b"%PDF-1.4 garbage")
    with pytest.raises(UnsupportedImageType):
        validate_image(stream)
    assert stream.tell() == 0
