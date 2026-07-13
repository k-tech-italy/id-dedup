# Deduplication flow

## Overview

```
POST /upload       → process_images()   → ClusterResult  → session
                                               ↓
                              user edits clusters (split / drag-drop)
                                               ↓
GET  /review/save  → propose_matches()  → [ClusterProposal] → session
                                               ↓
                              user adjudicates each proposal
                                               ↓
GET  /complete     → persist Identity + Image records → clear session
```

---

## Stage 1: pipeline.py

**`extract_embedding(path)`**
Reads the image with OpenCV, runs `FaceAnalysis` (InsightFace, lazy singleton `_app` loaded once on first call with CPUExecutionProvider), selects the largest face by bounding-box area, and returns its raw 512-d float32 embedding. Returns `None` if the file can't be read or no face is detected. The embedding is *not yet L2-normalised* at this point.

**`cluster_dbscan(embeddings, eps=0.4, min_samples=2)`**
L2-normalises the embedding matrix (in-place normalisation per row), then runs `DBSCAN` with `metric="cosine"` and `algorithm="brute"`. Returns `(labels, normalised_embeddings)`. Label `-1` = noise/singletons. The normalised embeddings are ready to store directly in `Image.embedding` (the pgvector cosine index expects unit vectors).

**`process_images(paths)`**
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
Mean of the L2-normalised embeddings, then re-normalised. For a single member, returns its embedding directly (no computation needed).

**`_query_candidates(centroid, top_k, min_similarity, similarity_band)`**
The only ORM call in the services layer.

1. Converts `min_similarity` → `max_distance = 1.0 - min_similarity`.
2. Queries `Image.objects.filter(identity__isnull=False)` annotated with `CosineDistance("embedding", centroid)`.
3. Iterates results, collapsing to one `IdentityMatch` per identity:
   - First row for an identity sets `display_name` and `image_url`.
   - `similarity` = running maximum of `1.0 - distance`.
   - `matched_image_count` increments for every row for that identity.
4. Sorts descending by similarity, slices to `top_k`.
5. Applies `similarity_band`: drops entries where `similarity < best.similarity - similarity_band`. Set `similarity_band=0` to disable and always return up to `top_k`.

**`propose_for_members(members, top_k=5, min_similarity=0.6, similarity_band=0.1)`**
The public primitive. Accepts any list of `ClusterMember` objects — both confirmed groups and individual singletons call this. Returns a `ClusterProposal`.

**`propose_matches(result)`**
Wraps `propose_for_members` over an entire `ClusterResult`. Emits proposals for groups first (ascending by label), then singletons (each queried individually). Called exactly once, from `review_save`.

**`ClusterProposal`**
`members`, `centroid`, `proposed_matches` (ranked descending). `is_new_identity` is `True` when `proposed_matches` is empty (no DB identity crossed `min_similarity`). `best_match` returns the first entry or `None`.

---

## Orchestration: service/workflow.py

`workflow.py` bridges the ML pipeline and ORM persistence. It imports both `pipeline` and `models` — the only module that does so.

**`process_uploads(uploads, tmpdir, default_file_name="image")`**
Saves uploaded files to a temp directory with unique names, then calls `pipeline.process_images()`. Returns the `ClusterResult`.

**`apply_split(result, cluster_label, filenames=None, file_path=None, to_cluster=None)`**
Resolves file paths from filenames or a direct path, then delegates to `result.split()`. Returns the mutated `ClusterResult`.

**`create_assignment(proposal, identity_id, assignments, registry, adj_index)`**
Resolves the display name from the proposal's matches, the session registry, or the DB. Handles garbage-collection of previous `is_new` assignments when a cluster is reassigned.

**`create_new_identity_assignment(display_name, registry, assignments, adj_index)`**
Generates a UUID, stores the identity in the session registry, records the assignment. Includes GC logic for previous `is_new` entries.

**`search_identities(query, registry, limit=10)`**
Queries the DB (`display_name__icontains`) and merges with session-only identities from the registry. Deduplicates by case-insensitive display name.

**`persist_assignments(assignments, proposals, tmpdir_name=None)`**
The single write path. Runs inside `@transaction.atomic`. For each assignment: `get_or_create` for `is_new=True` identities, `get` for existing ones. Creates `Image` records with embeddings and uploaded files. Cleans up the temp directory. Returns a summary dict.

---

## Serialization: serializers.py

Session serialization is handled by a dedicated module rather than inline in views. Functions:

- `serialize_member` / `deserialize_member` — `ClusterMember` (file path + embedding via `.tolist()`)
- `serialize_result` / `deserialize_result` — `ClusterResult` (dict keys cast `str → int` on deserialise)
- `serialize_identity_match` / `deserialize_identity_match` — `IdentityMatch`
- `serialize_proposal` / `deserialize_proposal` — `ClusterProposal`

NumPy arrays are serialised via `.tolist()` and deserialised via `np.array(..., dtype=np.float32)`.

---

## Session state

All wizard state lives in the Django session under `wizard_` prefixed keys. `_clear_wizard()` removes all of them except `wizard_tmpdir` (which is popped separately in `complete()`).

| Key | Type | Purpose |
|---|---|---|
| `wizard_cluster_result` | serialised `ClusterResult` | Stage-1 output; mutated by split operations |
| `wizard_proposals` | serialised `list[ClusterProposal]` | Stage-2 output; indexed by `adj_index` |
| `wizard_adj_index` | `int` | Which proposal is currently on screen |
| `wizard_assignments` | `dict[str(int), {identity_id, display_name, is_new}]` | User's adjudication decisions, keyed by adj_index as string |
| `wizard_new_identities` | `dict[str(uuid), display_name]` | Session-only identity registry; not in DB until `complete()` |
| `wizard_batch_name` | `str` | Display label for the batch (e.g. `"Batch 250707-1042"`) |
| `wizard_tmpdir` | `str` | Path to the temp directory holding uploaded files |

NumPy arrays are serialised via `.tolist()` and deserialised via `np.array(..., dtype=np.float32)`. `ClusterResult.clusters` dict keys are stored as strings in JSON and cast back to `int` on deserialisation.

---

## Wizard steps

**Step 1 — upload**
POST saves files to `tempfile.mkdtemp()`, calls `process_images()`, stores `ClusterResult`. Clears any existing wizard session state before setting new keys.

**Step 2 — review**
GET renders the cluster grid (groups + singletons). Users can split clusters via a split drawer (checkbox-based) or by dragging images between clusters (SortableJS). Both POST to `split`, which calls `ClusterResult.split()` and returns the `_cluster_grid.html` partial via HTMX. `review_image` serves uploaded images from the temp directory with a path-traversal guard.

**Transition 2→3 — review_save**
GET that calls `propose_matches(result)`, stores the proposals, resets `wizard_adj_index` to `0`, then redirects to adjudication.

**Step 3 — adjudication**
Shows one proposal at a time (`wizard_proposals[adj_index]`).

- `assign` POST: records `{identity_id, display_name, is_new=False}` in assignments and advances `adj_index`. If the cluster was previously assigned to a session-only identity, that identity is garbage-collected from `wizard_new_identities` if no other cluster references it.
- `new_identity` POST: generates a UUID, stores `display_name` in `wizard_new_identities` (not in DB), records `{identity_id, display_name, is_new=True}` in assignments. Same garbage-collection logic applies.
- `search` GET: returns an HTML fragment of matching DB identities plus session registry entries. Used for HTMX typeahead.

**Step 4 — complete**
Guards: redirects back to the first unassigned cluster if any exist.

Delegates to `workflow.persist_assignments()`, which handles the `@transaction.atomic` block, Identity/Image creation, and temp directory cleanup. Then clears the wizard session.

---

## Hard constraint: no auto-assignment

Nothing in `pipeline.py`, `service/proposals.py`, `service/workflow.py`, or `views.py` ever automatically assigns a cluster to an identity. Every `identity_id` in `wizard_assignments` originates from an explicit user POST to `assign` or `new_identity`. Do not change this.
