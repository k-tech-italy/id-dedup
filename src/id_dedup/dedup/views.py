from __future__ import annotations

import json
import pathlib
import shutil
import tempfile
import uuid
from datetime import datetime

import numpy as np
from django.core.files import File
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.middleware.csrf import get_token
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_safe

from .models import Identity, Image
from .pipeline import ClusterMember, ClusterResult, process_images
from .services import ClusterProposal, IdentityMatch, propose_matches

# ---------------------------------------------------------------------------
# Serialization — store ClusterResult / proposals as JSON-safe dicts
# ---------------------------------------------------------------------------


def _serialize_member(m: ClusterMember) -> dict:
    return {"file": str(m.file), "embedding": m.embedding.tolist()}


def _deserialize_member(d: dict) -> ClusterMember:
    return ClusterMember(
        file=pathlib.Path(d["file"]),
        embedding=np.array(d["embedding"], dtype=np.float32),
    )


def _serialize_result(result: ClusterResult) -> dict:
    return {
        "clusters": {str(label): [_serialize_member(m) for m in members] for label, members in result.clusters.items()},
        "failed": [str(f) for f in result.failed],
    }


def _deserialize_result(data: dict) -> ClusterResult:
    result = ClusterResult()
    for label_str, members in data.get("clusters", {}).items():
        label = int(label_str)
        result.clusters[label] = [_deserialize_member(m) for m in members]
    result.failed = [pathlib.Path(f) for f in data.get("failed", [])]
    return result


def _serialize_identity_match(m: IdentityMatch) -> dict:
    return {
        "identity_id": str(m.identity_id),
        "display_name": m.display_name,
        "similarity": m.similarity,
        "matched_image_count": m.matched_image_count,
        "image_url": m.image_url,
    }


def _deserialize_identity_match(d: dict) -> IdentityMatch:
    return IdentityMatch(
        identity_id=d["identity_id"],
        display_name=d["display_name"],
        similarity=d["similarity"],
        matched_image_count=d["matched_image_count"],
    )


def _serialize_proposal(p: ClusterProposal) -> dict:
    return {
        "members": [_serialize_member(m) for m in p.members],
        "centroid": p.centroid.tolist(),
        "proposed_matches": [_serialize_identity_match(m) for m in p.proposed_matches],
    }


def _deserialize_proposal(d: dict) -> ClusterProposal:
    return ClusterProposal(
        members=[_deserialize_member(m) for m in d["members"]],
        centroid=np.array(d["centroid"], dtype=np.float32),
        proposed_matches=[_deserialize_identity_match(m) for m in d.get("proposed_matches", [])],
    )


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


def _get_result(request: HttpRequest) -> ClusterResult | None:
    raw = request.session.get("wizard_cluster_result")
    if raw is None:
        return None
    return _deserialize_result(raw)


def _set_result(request: HttpRequest, result: ClusterResult) -> None:
    request.session["wizard_cluster_result"] = _serialize_result(result)


def _get_proposals(request: HttpRequest) -> list[ClusterProposal] | None:
    raw = request.session.get("wizard_proposals")
    if raw is None:
        return None
    return [_deserialize_proposal(p) for p in raw]


def _set_proposals(request: HttpRequest, proposals: list[ClusterProposal]) -> None:
    request.session["wizard_proposals"] = [_serialize_proposal(p) for p in proposals]


def _get_adj_index(request: HttpRequest) -> int:
    return request.session.get("wizard_adj_index", 0)


def _set_adj_index(request: HttpRequest, index: int) -> None:
    request.session["wizard_adj_index"] = index


def _get_assignments(request: HttpRequest) -> dict:
    return request.session.get("wizard_assignments", {})


def _set_assignments(request: HttpRequest, assignments: dict) -> None:
    request.session["wizard_assignments"] = assignments


def _get_batch_name(request: HttpRequest) -> str:
    return request.session.get("wizard_batch_name", "")


def _clear_wizard(request: HttpRequest) -> None:
    keys = [
        "wizard_cluster_result",
        "wizard_proposals",
        "wizard_adj_index",
        "wizard_assignments",
        "wizard_batch_name",
    ]
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
    return datetime.now().strftime("Batch %y%m%d-%H%M")


def _bad_request(msg: str) -> HttpResponseBadRequest:
    return HttpResponseBadRequest(msg.encode())


def _hx_redirect(request: HttpRequest, to: str) -> HttpResponse:
    if request.headers.get("HX-Request") == "true":
        resp = HttpResponse()
        resp["HX-Redirect"] = reverse(to)
        return resp
    return redirect(to)


# ---------------------------------------------------------------------------
# Step 1 — Upload
# ---------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def upload(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        files = request.FILES.getlist("images")
        if not files:
            return render(request, "wizard/upload.html", {"error": "No files selected."})

        saved: list[pathlib.Path] = []
        tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="id_dedup_"))
        for f in files:
            p = pathlib.Path(f.name)
            unique_name = f"{p.stem}_{uuid.uuid4().hex[:8]}{p.suffix}"
            dest = tmpdir / unique_name
            with open(dest, "wb") as out:
                out.writelines(f.chunks())
            saved.append(dest)

        result = process_images(saved)

        # Track temp dir for cleanup — store in session separately
        request.session["wizard_tmpdir"] = str(tmpdir)

        _clear_wizard(request)
        _set_result(request, result)
        request.session["wizard_batch_name"] = _now_str()

        if request.headers.get("HX-Request") == "true":
            response = HttpResponse()
            response["HX-Redirect"] = reverse("wizard:review")
            return response
        return redirect("wizard:review")

    return render(request, "wizard/upload.html")


# ---------------------------------------------------------------------------
# Step 2 — Review Clusters
# ---------------------------------------------------------------------------


@require_http_methods(["GET"])
def review(request: HttpRequest) -> HttpResponse:
    result = _get_result(request)
    if result is None:
        return redirect("wizard:upload")

    groups, singletons = _cluster_context(result)
    return render(
        request,
        "wizard/review.html",
        {
            "wizard_step": "review",
            "batch_name": _get_batch_name(request),
            "groups": groups,
            "singletons": singletons,
            "group_count": len(groups),
        },
    )


@require_http_methods(["POST"])
def split(request: HttpRequest) -> HttpResponse:
    """Handle both single drag-drop moves and multi-file split-drawer moves."""
    result = _get_result(request)
    if result is None:
        return _bad_request("No active session")

    cluster_label_str = request.POST.get("cluster_label")
    if cluster_label_str is None:
        return _bad_request("Missing cluster_label")

    cluster_label = int(cluster_label_str)

    files_raw = request.POST.get("files")
    if files_raw:
        try:
            filenames = json.loads(files_raw)
        except (json.JSONDecodeError, TypeError):
            return _bad_request("Invalid files payload")
        file_set = set()
        for fname in filenames:
            for m in result.clusters.get(cluster_label, []):
                if m.file.name == fname or str(m.file) == fname:
                    file_set.add(m.file)
                    break
    else:
        file_path_str = request.POST.get("file", "")
        file_set = set()
        file_path = pathlib.Path(file_path_str)
        for m in result.clusters.get(cluster_label, []):
            if m.file == file_path or m.file.name == file_path_str:
                file_set.add(m.file)
                break

    if not file_set:
        return _bad_request("No matching files found in cluster")

    to_cluster_str = request.POST.get("to_cluster")
    result.split(cluster_label, file_set, to_cluster=int(to_cluster_str) if to_cluster_str else None)
    _set_result(request, result)

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

    if not str(filepath).startswith(str(tmpdir)):
        raise Http404("Invalid path")
    if not filepath.is_file():
        raise Http404("File not found")

    return FileResponse(open(filepath, "rb"))


@require_http_methods(["GET"])
def review_save(request: HttpRequest) -> HttpResponse:
    """
    Finalize clusters, compute proposals, redirect to adjudication.

    Temp dir is kept alive — it's cleaned up in complete() after persisting.
    """
    result = _get_result(request)
    if result is None:
        return redirect("wizard:upload")

    proposals = propose_matches(result)
    _set_proposals(request, proposals)
    _set_adj_index(request, 0)
    _set_assignments(request, {})

    return redirect("wizard:adjudication")


# ---------------------------------------------------------------------------
# Step 3 — Adjudication
# ---------------------------------------------------------------------------


def _adjudication_context(request: HttpRequest) -> dict | None:
    """Build context dict for the adjudication page."""
    proposals = _get_proposals(request)
    if proposals is None:
        return None

    adj_index = _get_adj_index(request)
    if adj_index >= len(proposals):
        return None

    proposal = proposals[adj_index]
    total = len(proposals)
    pct = int(((adj_index + 1) / total) * 100) if total > 0 else 0

    assignments = _get_assignments(request)

    cluster_label = f"Cluster {adj_index + 1}"

    return {
        "wizard_step": "adjudication",
        "batch_name": _get_batch_name(request),
        "proposals": proposals,
        "proposal": proposal,
        "adj_index": adj_index,
        "total": total,
        "progress_pct": pct,
        "cluster_label": cluster_label,
        "members": proposal.members,
        "matches": proposal.proposed_matches,
        "is_new_identity": proposal.is_new_identity,
        "header_progress": f"{len(assignments)} of {total} assigned",
    }


@require_http_methods(["GET"])
def adjudication(request: HttpRequest) -> HttpResponse:
    context = _adjudication_context(request)
    if context is None:
        proposals = _get_proposals(request)
        if proposals is None:
            return redirect("wizard:upload")
        return redirect("wizard:complete")

    return render(request, "wizard/adjudication.html", context)


@require_http_methods(["GET"])
def adjudication_next(request: HttpRequest) -> HttpResponse:
    adj_index = _get_adj_index(request)
    proposals = _get_proposals(request)
    if proposals is None:
        return redirect("wizard:upload")

    if adj_index + 1 >= len(proposals):
        return redirect("wizard:complete")

    _set_adj_index(request, adj_index + 1)
    return redirect("wizard:adjudication")


@require_http_methods(["GET"])
def adjudication_prev(request: HttpRequest) -> HttpResponse:
    adj_index = _get_adj_index(request)
    if adj_index <= 0:
        return redirect("wizard:adjudication")

    _set_adj_index(request, adj_index - 1)
    return redirect("wizard:adjudication")


@require_http_methods(["POST"])
def assign(request: HttpRequest) -> HttpResponse:
    """Assign current cluster to an existing identity and advance."""
    identity_id_str = request.POST.get("identity_id")
    if not identity_id_str:
        return _hx_redirect(request, "wizard:adjudication")

    proposals = _get_proposals(request)
    if proposals is None:
        return _hx_redirect(request, "wizard:upload")

    adj_index = _get_adj_index(request)
    if adj_index >= len(proposals):
        return _hx_redirect(request, "wizard:complete")

    proposal = proposals[adj_index]
    assignments = _get_assignments(request)
    assignments[str(adj_index)] = {
        "identity_id": identity_id_str,
        "display_name": next(
            (m.display_name for m in proposal.proposed_matches if str(m.identity_id) == identity_id_str),
            "Unknown",
        ),
        "is_new": False,
    }
    _set_assignments(request, assignments)

    next_index = adj_index + 1
    if next_index >= len(proposals):
        return _hx_redirect(request, "wizard:complete")

    _set_adj_index(request, next_index)
    return _hx_redirect(request, "wizard:adjudication")


@require_http_methods(["POST"])
def new_identity(request: HttpRequest) -> HttpResponse:
    """Create a new identity, assign current cluster, and advance."""
    display_name = request.POST.get("display_name", "").strip()
    if not display_name:
        return _hx_redirect(request, "wizard:adjudication")

    proposals = _get_proposals(request)
    if proposals is None:
        return _hx_redirect(request, "wizard:upload")

    adj_index = _get_adj_index(request)
    if adj_index >= len(proposals):
        return _hx_redirect(request, "wizard:complete")

    identity = Identity.objects.create(display_name=display_name)

    assignments = _get_assignments(request)
    assignments[str(adj_index)] = {
        "identity_id": str(identity.pk),
        "display_name": display_name,
        "is_new": True,
    }
    _set_assignments(request, assignments)

    next_index = adj_index + 1
    if next_index >= len(proposals):
        return _hx_redirect(request, "wizard:complete")

    _set_adj_index(request, next_index)
    return _hx_redirect(request, "wizard:adjudication")


@require_http_methods(["GET"])
def search(request: HttpRequest) -> HttpResponse:
    """Search identities — returns HTML fragment for HTMX."""
    q = request.GET.get("q", "").strip()
    if not q:
        content = "<div class='text-xs text-gray-500 py-2'>Type to search identities...</div>"
        return HttpResponse(content.encode())

    identities = list(Identity.objects.filter(display_name__icontains=q)[:10])
    if not identities:
        content = "<div class='text-xs text-gray-500 py-2'>No identities found.</div>"
        return HttpResponse(content.encode())

    url = reverse("wizard:assign")
    csrf_token = get_token(request)
    lines = [
        "<form method='post'"
        f" hx-post='{url}' hx-target='#wizard-content' hx-swap='innerHTML'"
        " class='block'>"
        f"<input type='hidden' name='csrfmiddlewaretoken' value='{csrf_token}'>"
        f"<input type='hidden' name='identity_id' value='{identity.pk}'>"
        "<button type='submit'"
        " class='w-full text-left block text-sm text-gray-300 hover:text-white hover:bg-gray-800 rounded-lg px-3 py-2 transition-colors'>"
        f"{identity.display_name}</button></form>"
        for identity in identities
    ]
    html = "<div class='space-y-1 mt-2'>" + "".join(lines) + "</div>"
    return HttpResponse(html.encode())


# ---------------------------------------------------------------------------
# Step 4 — Complete
# ---------------------------------------------------------------------------


@require_http_methods(["GET"])
def complete(request: HttpRequest) -> HttpResponse:
    assignments = _get_assignments(request)
    proposals = _get_proposals(request) or []
    total = len(proposals)
    assigned = len(assignments)
    new_ids = 0

    for adj_index_str, assignment in assignments.items():
        adj_index = int(adj_index_str)
        if assignment.get("is_new"):
            new_ids += 1

        if adj_index >= len(proposals):
            continue

        try:
            identity = Identity.objects.get(pk=assignment["identity_id"])
        except Identity.DoesNotExist:
            continue

        proposal = proposals[adj_index]
        for member in proposal.members:
            if not member.file.exists():
                continue
            ext = "".join(member.file.suffixes) or ".jpg"
            dest_name = f"{uuid.uuid4()}{ext}"
            with open(member.file, "rb") as f:
                Image.objects.create(
                    identity=identity,
                    embedding=member.embedding,
                    source_image=File(f, name=dest_name),
                )

    # Clean up temp dir
    tmpdir_name = request.session.pop("wizard_tmpdir", None)
    if tmpdir_name:
        tmpdir_path = pathlib.Path(tmpdir_name)
        if tmpdir_path.exists():
            shutil.rmtree(tmpdir_path, ignore_errors=True)

    _clear_wizard(request)

    return render(
        request,
        "wizard/complete.html",
        {
            "wizard_step": "complete",
            "total_clusters": total,
            "assigned": assigned,
            "new_identities": new_ids,
        },
    )
