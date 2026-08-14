# Deduplication flow (id_dedup.dedup)

> **Scope:** This document describes the `id_dedup.dedup` app — the original session-based 4-step wizard, kept for demos. The ticket-based async design lives in `id_dedup.workflow` and is covered in [`architecture.md`](architecture.md).

## Overview

```
POST /upload       → process_uploads()  → ClusterResult  → session
                                                ↓
                               user edits clusters (split / drag-drop)
                                                ↓
POST /review/save  → propose_matches()  → [ClusterProposal] → session
                                                ↓
                               user adjudicates each proposal
                                                ↓
POST adjudication/assign|new_identity|next → persist Identity + Image records → clear session
                                                ↓
GET  /complete     → render summary (read-only) → clear session
```

---

## Stage 1: pipeline.py

**`extract_embedding(image_path)`**
Reads the image with OpenCV, runs `FaceAnalysis` (InsightFace, lazy singleton `_app` loaded once on first call with CPUExecutionProvider), selects the largest face by bounding-box area, and returns its raw 512-d float32 embedding. Returns `None` if the file can't be read or no face is detected. The embedding is *not yet L2-normalised* at this point.

**`cluster_dbscan(embeddings, eps=0.4, min_samples=2)`**
L2-normalises the embedding matrix (creates a new array, does not modify the original), then runs `DBSCAN` with `metric="cosine"`, `algorithm="brute"`, and `n_jobs=-1`. Returns `(labels, normalised_embeddings)`. Label `-1` = noise/singletons. The normalised embeddings are ready to store directly in `Image.embedding` (the pgvector cosine index expects unit vectors).

**`process_images(image_paths, eps=0.4, min_samples=2)`**
Orchestrates the above. Images that fail extraction are collected in `ClusterResult.failed`. The rest are stacked, clustered, and stored in `ClusterResult.clusters`.

**`ClusterResult`**
Container for the stage-1 result. `clusters` is a `dict[int, list[ClusterMember]]`. Key `-1` holds singletons; keys `>= 0` are confirmed groups. `.groups` and `.singletons` are convenience properties.

**`ClusterResult.split(label, move, to_cluster=None)`**
Pure Python pointer move — no embeddings are recomputed and no DB queries are made. Contract:

- `to_cluster == label` → no-op, returns `label` immediately.
- `to_cluster` is an existing label → moves files into that cluster.
- `to_cluster` is `None`, `len(move) == 1` → moves to the `-1` (singletons) bucket.
- `to_cluster` is `None`, `len(move) >= 2` → assigns a new positive label (computed as `max(keys >= 0) + 1` *before* any deletion to prevent label recycling).
- If the source cluster becomes empty after the move, it is deleted from `clusters`.
- Raises `ValueError` if `label` doesn't exist or none of the specified files are in it.

---

## Stage 2: service/proposals.py

**`_centroid(members)`**
Mean of the L2-normalised embeddings, then re-normalised. For a single member, returns its embedding directly (no computation needed). Uses `normalised_mean()` from `pipeline.py` for multi-member clusters.

**`_query_candidates(centroid, top_k, min_similarity, similarity_band)`**
The only ORM call in the services layer. Queries `Identity.centroid` directly — O(M identities) with a DB-level LIMIT, not O(N images).

1. Converts `min_similarity` → `max_distance = 1.0 - min_similarity`.
2. Queries `Identity.objects.filter(centroid__isnull=False)` annotated with `CosineDistance("centroid", centroid)`.
3. Filters to `distance <= max_distance`, orders by distance, slices to `top_k`.
4. Fetches one representative image URL per matched identity via `DISTINCT ON (identity_id)`.
5. Builds `IdentityMatch` list with `similarity = 1.0 - distance` and `matched_image_count = row.image_count`.
6. Applies `similarity_band`: drops entries where `similarity < best.similarity - similarity_band`. Set `similarity_band=0` to disable and always return up to `top_k`.

**`IdentityMatch`**
Dataclass holding per-identity match info: `identity_id`, `display_name`, `similarity` (cosine similarity in [0, 1]), `matched_image_count` (total images for this identity), and `image_url` (representative image path or `None`).

**`propose_for_members(members, top_k=5, min_similarity=0.6, similarity_band=0.1)`**
The public primitive. Accepts any list of `ClusterMember` objects — both confirmed groups and individual singletons call this. Returns a `ClusterProposal`.

**`propose_matches(result, top_k=5, min_similarity=0.6, similarity_band=0.1)`**
Wraps `propose_for_members` over an entire `ClusterResult`. Emits proposals for groups first (ascending by label), then singletons (each queried individually). Called exactly once, from `review_save`.

**`ClusterProposal`**
`members`, `centroid`, `proposed_matches` (ranked descending). `is_new_identity` is `True` when `proposed_matches` is empty (no DB identity crossed `min_similarity`). `best_match` returns the first entry or `None`.

---

## Orchestration: service/workflow.py

`workflow.py` bridges the ML pipeline and ORM persistence. It imports both `pipeline` and `models` — the only module that does so.

**`validate_image(uploaded)`** (from `id_dedup/images.py`, shared with the workflow app)
Validates an uploaded file by reading its first 12 bytes and checking magic numbers for JPEG (`FF D8 FF`), PNG (`89 50 4E 47`), and WEBP (`RIFF....WEBP`); rewinds the stream before returning. Raises `UnsupportedImageType` for unsupported types. `process_uploads` raises `UnsupportedImageType` if any file is rejected.

**`process_uploads(uploads, tmpdir, default_file_name="image")`**
Validates uploaded file types via `validate_image` (from `id_dedup/images.py`), raises `UnsupportedImageType` listing rejected names if any file fails, saves files to a temp directory with unique names, then calls `pipeline.process_images()`. Returns the `ClusterResult`.

**`apply_split(result, cluster_label, filenames=None, file_path=None, to_cluster=None)`**
Resolves file paths from filenames or a direct path, then delegates to `result.split()`. Returns the mutated `ClusterResult`.

**`create_assignment(proposal, identity_id, assignments, registry, adj_index)`**
Resolves the display name from the proposal's matches, the session registry, or the DB. Handles garbage-collection of previous `is_new` assignments when a cluster is reassigned.

**`create_new_identity_assignment(display_name, registry, assignments, adj_index)`**
Generates a UUID, stores the identity in the session registry, records the assignment. Includes GC logic for previous `is_new` entries.

**`search_identities(query, registry, limit=10)`**
Queries the DB (`display_name__icontains`) and merges with session-only identities from the registry. Deduplicates by case-insensitive display name.

**`persist_assignments(assignments, proposals, tmpdir_name=None)`**
The single write path. Runs inside `@transaction.atomic`. For each assignment: `get_or_create` for `is_new=True` identities, `get` for existing ones (silently skips if `Identity.DoesNotExist`). Creates `Image` records with embeddings and uploaded files (skips if source file no longer exists on disk). Calls `identity.update_centroid()` after each identity's images are assigned. Cleans up the temp directory. Returns a summary dict.

---

## Serialization: serializers.py

Session serialization is handled by a dedicated module rather than inline in views. Functions:

- `serialize_member` / `deserialize_member` — `ClusterMember` (file path + base64-encoded embedding)
- `serialize_result` / `deserialize_result` — `ClusterResult` (dict keys cast `str → int` on deserialise)
- `serialize_identity_match` / `deserialize_identity_match` — `IdentityMatch`
- `serialize_proposal` / `deserialize_proposal` — `ClusterProposal` (members + base64-encoded centroid + matches)

NumPy arrays are serialised via `base64.b64encode(array.tobytes())` and deserialised via `np.frombuffer(base64.b64decode(...), dtype=np.float32)`. This is more compact than `.tolist()` for large float arrays.

---

## Models: Identity and Image

**`Identity`** — UUID primary key, `display_name`, `centroid` (512-d VectorField, null until first image), `image_count` (denormalised count), timestamps. Has an HNSW cosine index on `centroid` for the stage-2 query.

**`Image`** — UUID primary key, nullable FK to `Identity` (null = unassigned), `embedding` (512-d VectorField), `source_image` (FileField), timestamps. Has an HNSW cosine index on `embedding`.

**`Identity.update_centroid()`** — Computes the L2-normalised mean of all assigned image embeddings via pgvector `Avg` aggregate, updates `centroid` and `image_count`, then saves. Called by `persist_assignments()` after each identity's images are written, and automatically by the `post_delete` signal on `Image` (via `_refresh_centroid_on_image_delete`).

---

## Session state

All wizard state lives in the Django session under `wizard_` prefixed keys. `_clear_wizard()` removes all of them.

| Key | Type | Purpose |
|---|---|---|
| `wizard_cluster_result` | serialised `ClusterResult` | Stage-1 output; mutated by split operations |
| `wizard_proposals` | serialised `list[ClusterProposal]` | Stage-2 output; indexed by `adj_index` |
| `wizard_adj_index` | `int` | Which proposal is currently on screen |
| `wizard_assignments` | `dict[str, {identity_id, display_name, is_new}]` | User's adjudication decisions, keyed by adj_index as string |
| `wizard_new_identities` | `dict[str, str]` | Session-only identity registry; not in DB until `persist_assignments()` |
| `wizard_batch_name` | `str` | Display label for the batch (e.g. `"Batch 250707-1042"`, format `%y%m%d-%H%M`) |
| `wizard_tmpdir` | `str` | Path to the temp directory holding uploaded files |
| `wizard_summary` | `dict` | Persistence summary set after `persist_assignments()`: `{wizard_step, total_clusters, assigned, new_identities}` |

NumPy arrays are serialised via base64 encoding and deserialised via `np.frombuffer()`. `ClusterResult.clusters` dict keys are stored as strings in JSON and cast back to `int` on deserialisation.

---

## Wizard steps

**Step 1 — upload**
POST saves files to `tempfile.mkdtemp()`, validates MIME types via magic bytes (JPEG, PNG, WEBP only), calls `workflow.process_uploads()`, stores `ClusterResult`. Clears any existing wizard session state before setting new keys.

**Step 2 — review**
GET renders the cluster grid (groups + singletons). Users can split clusters via a split drawer (checkbox-based) or by dragging images between clusters (SortableJS). Both POST to `split`, which calls `ClusterResult.split()` and returns the `_cluster_grid.html` partial via HTMX. `review_image` serves uploaded images from the temp directory with a path-traversal guard.

**Transition 2→3 — review_save**
POST that calls `propose_matches(result)`, stores the proposals, resets `wizard_adj_index` to `0`, then redirects to adjudication.

**Step 3 — adjudication**
Shows one proposal at a time (`wizard_proposals[adj_index]`). Users can navigate forward (`adjudication_next`) and backward (`adjudication_prev`) between proposals.

- `assign` POST: records `{identity_id, display_name, is_new=False}` in assignments and advances `adj_index`. If the cluster was previously assigned to a session-only identity, that identity is garbage-collected from `wizard_new_identities` if no other cluster references it.
- `new_identity` POST: generates a UUID, stores `display_name` in `wizard_new_identities` (not in DB), records `{identity_id, display_name, is_new=True}` in assignments. Same garbage-collection logic applies.
- `search` GET: returns an HTML fragment of matching DB identities plus session registry entries. Used for HTMX typeahead.

When the last proposal is processed (by `assign`, `new_identity`, or `adjudication_next`), persistence is triggered immediately: `workflow.persist_assignments()` creates `Identity` and `Image` records, recomputes centroids, cleans up the temp directory, and stores the summary in the session. Then redirects to `complete`.

**Step 4 — complete**
Guards: redirects back to the first unassigned cluster if any exist (or to upload if no proposals exist). Pure read-only — renders the persistence summary already stored in the session as `wizard_summary`.

---

## Hard constraint: no auto-assignment (dedup app)

Nothing in `pipeline.py`, `service/proposals.py`, `service/workflow.py`, or `views.py` ever automatically assigns a cluster to an identity. Every `identity_id` in `wizard_assignments` originates from an explicit user POST to `assign` or `new_identity`. Do not change this.
