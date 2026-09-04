"""The documentation site must build cleanly."""

import pathlib
import shutil
import subprocess
import sys
import tempfile

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = REPO_ROOT / "properdocs.yml"


def _find_properdocs() -> str | None:
    """
    Locate the `properdocs` executable.

    Look next to the running interpreter first: `pytest` is routinely invoked as
    `.venv/bin/python -m pytest`, which does not put the venv's scripts on PATH.
    """
    candidate = pathlib.Path(sys.executable).parent / "properdocs"
    if candidate.is_file():
        return str(candidate)
    return shutil.which("properdocs")


PROPERDOCS = _find_properdocs()


@pytest.mark.skipif(PROPERDOCS is None, reason="docs dependency group not installed")
def test_docs_build_is_clean():
    """
    Build the site with `--strict`, so a broken internal link or a page missing
    from a `.pages` nav file fails here rather than in someone's browser.

    The build must not need a database: autodoc is limited to the Django-free
    modules for exactly this reason (see docs/dev-guide/index.md).
    """
    with tempfile.TemporaryDirectory() as site_dir:
        result = subprocess.run(  # noqa: S603
            [PROPERDOCS, "build", "--strict", "-f", str(CONFIG), "-d", site_dir],
            capture_output=True,
            check=False,
            cwd=REPO_ROOT,
            text=True,
        )
        assert result.returncode == 0, f"properdocs build failed:\n{result.stdout}\n{result.stderr}"
        assert (pathlib.Path(site_dir) / "index.html").exists()
