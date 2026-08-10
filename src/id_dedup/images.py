from __future__ import annotations

from typing import Protocol

_JPEG = b"\xff\xd8\xff"
_PNG = b"\x89PNG\r\n\x1a\n"


class UnsupportedImageType(Exception):
    """
    A submitted file is not a JPEG/PNG/WEBP image (or none could be read).

    Raised when magic-bytes detection rejects a file as an unsupported image
    type. The upload itself succeeded — it is the content that failed validation.
    """


class _ReadableSeekable(Protocol):
    """Anything we can read the head of and rewind — UploadedFile, BytesIO, mocks."""

    def read(self, n: int = -1, /) -> bytes: ...

    def seek(self, offset: int, whence: int = 0, /) -> int: ...


_validators = {
    "jpg": lambda header: header[:3] == _JPEG,
    "png": lambda header: header[:8] == _PNG,
    "webp": lambda header: header[:4] == b"RIFF" and header[8:12] == b"WEBP",
}


def validate_image(uploaded: _ReadableSeekable) -> None:
    """Raise :class:`UnsupportedImageType` unless `uploaded` is a JPEG/PNG/WEBP image."""
    header = uploaded.read(12)
    uploaded.seek(0)
    if any(validator(header) for validator in _validators.values()):
        return
    raise UnsupportedImageType("File is not a supported image type (JPG, PNG, or WEBP).")
