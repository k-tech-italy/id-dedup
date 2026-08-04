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


def is_valid_image(uploaded: _ReadableSeekable) -> bool:
    """
    Return True if *uploaded* claims to be a JPEG/PNG/WEBP image (magic bytes).

    This is the wizard's existing hand-rolled detection, moved to a shared module.
    A decode-based validator (Pillow verify/load, dimension caps) is a future
    ticket; do not extend this function beyond the current behavior.
    """
    header = uploaded.read(12)
    uploaded.seek(0)
    if header[:3] == _JPEG:
        return True
    if header[:8] == _PNG:
        return True
    return header[:4] == b"RIFF" and header[8:12] == b"WEBP"
