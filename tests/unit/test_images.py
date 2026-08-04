from __future__ import annotations

from io import BytesIO

from id_dedup.images import UnsupportedImageType, is_valid_image


def test_unsupported_image_type_is_raised_for_rejected_content():
    assert issubclass(UnsupportedImageType, Exception)


def test_is_valid_image_accepts_jpeg_magic_bytes():
    assert is_valid_image(BytesIO(b"\xff\xd8\xff\xe0" + b"\x00" * 100))


def test_is_valid_image_accepts_png_signature():
    assert is_valid_image(BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100))


def test_is_valid_image_accepts_webp_signature():
    assert is_valid_image(BytesIO(b"RIFF" + b"\x00" * 4 + b"WEBP"))


def test_is_valid_image_rejects_pdf():
    assert not is_valid_image(BytesIO(b"%PDF-1.4 garbage"))


def test_is_valid_image_rejects_plain_text():
    assert not is_valid_image(BytesIO(b"plain text"))


def test_is_valid_image_magic_bytes_win_over_extension():
    assert is_valid_image(BytesIO(b"\xff\xd8\xff\xe0" + b"\x00" * 100))


def test_is_valid_image_rewinds_stream():
    stream = BytesIO(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
    is_valid_image(stream)
    assert stream.tell() == 0
