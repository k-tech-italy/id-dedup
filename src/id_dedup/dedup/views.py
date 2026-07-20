from __future__ import annotations

import json
import pathlib
import shutil
import tempfile
from typing import TYPE_CHECKING, Any, overload

from django.http import FileResponse, Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.middleware.csrf import get_token
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_POST, require_safe

from . import serializers
from .service import proposals, workflow

if TYPE_CHECKING:
    from .pipeline import ClusterResult

# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

SESSION_PREFIX = "wizard_"


@overload
def _get_from_session(request: HttpRequest, key: str) -> Any: ...  # noqa: ANN401


@overload
def _get_from_session[T](request: HttpRequest, key: str, default: T) -> T: ...


def _get_from_session(request, key, default=None):
    """Read a value from the wizard session, prepending ``wizard_`` to the key."""
    return request.session.get(f"{SESSION_PREFIX}{key}", default)


def _set_to_session(request: HttpRequest, key: str, value: object) -> None:
    """Write a value to the wizard session, prepending ``wizard_`` to the key."""
    request.session[f"{SESSION_PREFIX}{key}"] = value


def _clear_wizard(request: HttpRequest) -> None:
    """Remove every ``wizard_`` key from the session."""
    # NOTE: SessionBase lacks __iter__, so .keys() is required
    keys = [k for k in request.session.keys() if k.startswith(SESSION_PREFIX)]  # noqa: SIM118
    for k in keys:
        request.session.pop(k, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cluster_context(result: ClusterResult) -> tuple[list, list]:
    """Split ClusterResult into (groups, singletons) for template rendering."""
    groups: list = []
    singletons: list = []
    for label in sorted(result.clusters, key=lambda x: (x == -1, x)):
        members = result.clusters[label]
        if label == -1:
            singletons = members
        else:
            groups.append((label, members))
    return groups, singletons


def _now_str() -> str:
    """Return a human-readable batch name based on the current timestamp."""
    return timezone.now().strftime("Batch %y%m%d-%H%M")


def _bad_request(msg: str) -> HttpResponseBadRequest:
    """Return a plain-text 400 response."""
    return HttpResponseBadRequest(msg.encode())


def _hx_redirect(request: HttpRequest, to: str) -> HttpResponse:
    """Redirect via HX-Redirect header when HTMX is active, fall back to normal redirect."""
    if request.headers.get("HX-Request") == "true":
        resp = HttpResponse()
        resp["HX-Redirect"] = reverse(to)
        return resp
    return redirect(to)


def _adjudication_context(request: HttpRequest) -> dict | None:
    """Build context dict for the adjudication page."""
    raw = _get_from_session(request, "proposals")
    if raw is None:
        return None
    proposals_list = [serializers.deserialize_proposal(p) for p in raw]

    adj_index = _get_from_session(request, "adj_index", 0)
    if adj_index >= len(proposals_list):
        return None

    proposal = proposals_list[adj_index]
    total = len(proposals_list)
    pct = int(((adj_index + 1) / total) * 100) if total > 0 else 0

    assignments = _get_from_session(request, "assignments", {})
    cluster_label = f"Cluster {adj_index + 1}"

    return {
        "wizard_step": "adjudication",
        "batch_name": _get_from_session(request, "batch_name", ""),
        "proposals": proposals_list,
        "proposal": proposal,
        "adj_index": adj_index,
        "total": total,
        "progress_pct": pct,
        "cluster_label": cluster_label,
        "members": proposal.members,
        "matches": proposal.proposed_matches,
        "is_new_identity": proposal.is_new_identity,
        "is_current_assigned": str(adj_index) in assignments,
        "header_progress": f"{len(assignments)} of {total} assigned",
    }


# ---------------------------------------------------------------------------
# Step 1 — Upload
# ---------------------------------------------------------------------------


class Upload(View):
    """Step 1 — accept image uploads, run clustering, redirect to review."""

    def get(self, request: HttpRequest) -> HttpResponse:
        """Render the upload form."""
        return render(request, "wizard/upload.html", {"wizard_step": "upload"})

    def post(self, request: HttpRequest) -> HttpResponse:
        """Accept uploaded images, process them through the pipeline, and redirect to review."""
        files = request.FILES.getlist("images")
        if not files:
            return render(request, "wizard/upload.html", {"wizard_step": "upload", "error": "No files selected."})

        tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="id_dedup_"))

        try:
            result = workflow.process_uploads(files, tmpdir)
        except ValueError as exc:
            shutil.rmtree(tmpdir, ignore_errors=True)
            return render(request, "wizard/upload.html", {"wizard_step": "upload", "error": str(exc)})
        except Exception:
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise
        else:
            _clear_wizard(request)
            request.session["wizard_tmpdir"] = str(tmpdir)
            request.session["wizard_cluster_result"] = serializers.serialize_result(result)
            request.session["wizard_batch_name"] = _now_str()

        if request.headers.get("HX-Request") == "true":
            response = HttpResponse()
            response["HX-Redirect"] = reverse("wizard:review")
            return response
        return redirect("wizard:review")


# ---------------------------------------------------------------------------
# Step 2 — Review Clusters
# ---------------------------------------------------------------------------


@require_safe
def review(request: HttpRequest) -> HttpResponse:
    """Step 2 — show clustered groups for manual split/merge before matching."""
    raw = _get_from_session(request, "cluster_result")
    if raw is None:
        return redirect("wizard:upload")
    result = serializers.deserialize_result(raw)

    groups, singletons = _cluster_context(result)
    return render(
        request,
        "wizard/review.html",
        {
            "wizard_step": "review",
            "batch_name": _get_from_session(request, "batch_name", ""),
            "groups": groups,
            "singletons": singletons,
            "group_count": len(groups),
        },
    )


@require_POST
def split(request: HttpRequest) -> HttpResponse:
    """Move files out of one cluster into another (or singletons) during review."""
    raw = _get_from_session(request, "cluster_result")
    if raw is None:
        return _bad_request("No active session")
    result = serializers.deserialize_result(raw)

    try:
        cluster_label = int(request.POST["cluster_label"])
    except (KeyError, ValueError):
        return _bad_request("Missing cluster_label")

    filenames = None
    if files_raw := request.POST.get("files"):
        try:
            filenames = json.loads(files_raw)
        except (json.JSONDecodeError, TypeError):
            return _bad_request("Invalid files payload")

    file_path = request.POST.get("file")
    to_cluster_str = request.POST.get("to_cluster")
    to_cluster = int(to_cluster_str) if to_cluster_str else None

    try:
        result = workflow.apply_split(
            result,
            cluster_label,
            filenames=filenames,
            file_path=file_path,
            to_cluster=to_cluster,
        )
    except ValueError as e:
        return _bad_request(str(e))

    request.session["wizard_cluster_result"] = serializers.serialize_result(result)

    groups, singletons = _cluster_context(result)
    return render(
        request,
        "wizard/_cluster_grid.html",
        {
            "groups": groups,
            "singletons": singletons,
        },
    )


@require_safe
def review_image(request: HttpRequest, path: str) -> FileResponse:
    """Serve an uploaded image from the wizard temp directory."""
    tmpdir_name = request.session.get("wizard_tmpdir")
    if not tmpdir_name:
        raise Http404("No upload session")

    tmpdir = pathlib.Path(tmpdir_name).resolve()
    filepath = (tmpdir / path).resolve()

    if not filepath.is_relative_to(tmpdir):
        raise Http404("Invalid path")
    if not filepath.is_file():
        raise Http404("File not found")

    return FileResponse(filepath.open("rb"))


@require_POST
def review_save(request: HttpRequest) -> HttpResponse:
    """Finalize clusters, compute identity-match proposals, redirect to adjudication."""
    raw = _get_from_session(request, "cluster_result")
    if raw is None:
        return redirect("wizard:upload")
    result = serializers.deserialize_result(raw)

    proposals_list = proposals.propose_matches(result)
    request.session.pop("wizard_cluster_result", None)
    request.session["wizard_proposals"] = [serializers.serialize_proposal(p) for p in proposals_list]
    request.session["wizard_adj_index"] = 0
    request.session["wizard_assignments"] = {}
    request.session["wizard_new_identities"] = {}

    return redirect("wizard:adjudication")


# ---------------------------------------------------------------------------
# Step 3 — Adjudication
# ---------------------------------------------------------------------------


@require_safe
def adjudication(request: HttpRequest) -> HttpResponse:
    """Step 3 — review identity-match proposals and assign each cluster."""
    context = _adjudication_context(request)
    if context is None:
        raw = _get_from_session(request, "proposals")
        if raw is None:
            return redirect("wizard:upload")
        return redirect("wizard:complete")

    return render(request, "wizard/adjudication.html", context)


@require_POST
def adjudication_next(request: HttpRequest) -> HttpResponse:
    """Advance to the next unassigned cluster or persist and finish."""
    adj_index = _get_from_session(request, "adj_index", 0)
    raw = _get_from_session(request, "proposals")
    if raw is None:
        return redirect("wizard:upload")
    proposals_list = [serializers.deserialize_proposal(p) for p in raw]

    if str(adj_index) not in _get_from_session(request, "assignments", {}):
        return redirect("wizard:adjudication")

    if adj_index + 1 >= len(proposals_list):
        assignments = _get_from_session(request, "assignments", {})
        summary = workflow.persist_assignments(
            assignments,
            proposals_list,
            request.session.pop("wizard_tmpdir", None),
        )
        _clear_wizard(request)
        request.session["wizard_summary"] = summary
        return redirect("wizard:complete")

    _set_to_session(request, "adj_index", adj_index + 1)
    return redirect("wizard:adjudication")


@require_POST
def adjudication_prev(request: HttpRequest) -> HttpResponse:
    """Go back to the previous cluster proposal."""
    adj_index = _get_from_session(request, "adj_index", 0)
    if adj_index <= 0:
        return redirect("wizard:adjudication")

    _set_to_session(request, "adj_index", adj_index - 1)
    return redirect("wizard:adjudication")


@require_POST
def assign(request: HttpRequest) -> HttpResponse:
    """Assign the current cluster to an existing identity and advance."""
    identity_id = request.POST.get("identity_id")
    if not identity_id:
        return _hx_redirect(request, "wizard:adjudication")

    raw = _get_from_session(request, "proposals")
    if raw is None:
        return _hx_redirect(request, "wizard:upload")
    proposals_list = [serializers.deserialize_proposal(p) for p in raw]

    adj_index = _get_from_session(request, "adj_index", 0)
    if adj_index >= len(proposals_list):
        return _hx_redirect(request, "wizard:complete")

    assignments = _get_from_session(request, "assignments", {})
    registry = _get_from_session(request, "new_identities", {})

    assignments, registry = workflow.create_assignment(
        proposals_list[adj_index],
        identity_id,
        assignments,
        registry,
        adj_index,
    )
    _set_to_session(request, "assignments", assignments)
    _set_to_session(request, "new_identities", registry)

    next_index = adj_index + 1
    if next_index >= len(proposals_list):
        summary = workflow.persist_assignments(
            assignments,
            proposals_list,
            request.session.pop("wizard_tmpdir", None),
        )
        _clear_wizard(request)
        request.session["wizard_summary"] = summary
        return _hx_redirect(request, "wizard:complete")

    _set_to_session(request, "adj_index", next_index)
    return _hx_redirect(request, "wizard:adjudication")


@require_POST
def new_identity(request: HttpRequest) -> HttpResponse:
    """Create a new identity for the current cluster and advance."""
    display_name = request.POST.get("display_name", "").strip()
    if not display_name:
        return _hx_redirect(request, "wizard:adjudication")

    raw = _get_from_session(request, "proposals")
    if raw is None:
        return _hx_redirect(request, "wizard:upload")
    proposals_list = [serializers.deserialize_proposal(p) for p in raw]

    adj_index = _get_from_session(request, "adj_index", 0)
    if adj_index >= len(proposals_list):
        return _hx_redirect(request, "wizard:complete")

    registry = _get_from_session(request, "new_identities", {})
    assignments = _get_from_session(request, "assignments", {})

    _identity_id, registry, assignments = workflow.create_new_identity_assignment(
        display_name,
        registry,
        assignments,
        adj_index,
    )
    _set_to_session(request, "new_identities", registry)
    _set_to_session(request, "assignments", assignments)

    next_index = adj_index + 1
    if next_index >= len(proposals_list):
        summary = workflow.persist_assignments(
            assignments,
            proposals_list,
            request.session.pop("wizard_tmpdir", None),
        )
        _clear_wizard(request)
        request.session["wizard_summary"] = summary
        return _hx_redirect(request, "wizard:complete")

    _set_to_session(request, "adj_index", next_index)
    return _hx_redirect(request, "wizard:adjudication")


@require_safe
def search(request: HttpRequest) -> HttpResponse:
    """Search identities — returns HTML fragment for HTMX."""
    query = request.GET.get("q", "").strip()
    if not query:
        return render(request, "wizard/_search_results.html", {"results": [], "q": query})

    registry = _get_from_session(request, "new_identities", {})
    results = workflow.search_identities(query, registry)

    url = reverse("wizard:assign")
    csrf_token = get_token(request)
    return render(
        request,
        "wizard/_search_results.html",
        {"results": results, "q": query, "url": url, "csrf_token": csrf_token},
    )


# ---------------------------------------------------------------------------
# Step 4 — Complete
# ---------------------------------------------------------------------------


@require_safe
def complete(request: HttpRequest) -> HttpResponse:
    """Step 4 — show the persistence summary after all clusters are assigned."""
    summary = _get_from_session(request, "summary")
    if summary is None:
        return redirect("wizard:upload")

    return render(request, "wizard/complete.html", summary)
