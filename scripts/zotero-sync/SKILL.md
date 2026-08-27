# Zotero peer sync — preserve identity, preserve both peers

Self-contained operating skill for this repo. Read it before synchronising or
recovering Zotero peers, changing `sync_library.sh`, or reconciling Zotero with a
literature worktree. Run commands from the research-stack root and use
`./.venv/bin/python`.

## Non-negotiable invariants

- A Zotero item/note/attachment/collection key is immutable identity. Storage
  directories, citations, manifests, exports, parents and relations depend on it.
- Never merge peers by recreating or replaying objects through MCP. Creation
  assigns new keys. Never make a key map to paper over that damage.
- Sync a winning SQLite snapshot whole. `merge_replica.py` classifies state; it
  does not merge rows.
- `.sync/base/zotero.sqlite` is the last snapshot accepted by both peers. Unequal
  non-empty peers without a trustworthy base are a conflict, not a direction
  guess.
- Permit only `equal`, `origin_fast_forward`, or `replica_fast_forward`. If both
  peers contain independent changes, stop before mutation and keep the plan.
- Touch only the exact `zotero` container. Never use `docker compose up/down` in
  this workflow and never create or wake Open WebUI or sibling services.
- Record whether Zotero was initially running on each peer and restore that
  state. Leave a peer stopped only when its post-swap check fails.
- Keep site-specific hosts/jumps in ignored `.sync/config.env`, never tracked
  scripts. Prefer the configured jump and fall back to ordinary SSH config.
- PDFs in `literature/` are intentionally gitignored corpus payloads. Sync them
  out of band; never `git add -f`, weaken `*.pdf`, or commit them. Text/capture
  sources and `ZOTERO.md` are committed only when explicitly requested.

## Preflight

1. Read `AGENTS.md`; for corpus files also read `READING.md` and the literature
   repo's `AGENTS.md`.
2. Gather SSH, exact-container Docker and scoped write permissions up front.
3. Check local and remote worktrees. Replica changes are inputs, not debris:

   ```bash
   git status --short
   ssh peer 'cd research-stack && git status --short'
   ssh peer 'cd literature && git status --short'
   ```

4. Check running containers and remember Zotero's state. Do not touch unrelated
   running containers.
5. Confirm `.sync/config.env` is ignored and holds local routing, for example:

   ```bash
   SYNC_REPLICA=user@peer
   SYNC_SSH_JUMP=preferred-jump
   SYNC_SSH_FALLBACK_JUMP=config
   SYNC_SSH_PERSIST=20m
   ```

6. Inventory replica-only source files before any origin-to-replica write. Test
   paths recorded in Zotero `extra` explicitly: `git status` and `rg --files`
   omit ignored PDFs.

## Normal sync

Use the persistent connection assembled by `sync_library.sh`: one
`ControlPath=.sync/ssh-%C`, `ControlMaster=auto`, a long `ControlPersist`, server
keepalives, and the same chosen jump route for SSH and rsync. Keep a long
transfer in the same authorized execution context; do not remove an active
socket. Quiet rsync over a slow jump is not failure—check the process and let
keepalives decide before restarting it.

Run on the origin:

```bash
SYNC_REPLICA=user@peer ./scripts/sync_library.sh --dry-run
SYNC_REPLICA=user@peer ./scripts/sync_library.sh
```

The real run must, in order:

1. lock `.sync` and select preferred/fallback SSH once;
2. stop only replica Zotero if it was running and fetch its DB consistently;
3. create and integrity-check the origin snapshot;
4. compare both snapshots with the last common base;
5. stop on conflict before either library changes;
6. when replica wins, pull storage without deleting local cache and install its
   exact DB on the origin;
7. push the winning storage/profile/snapshot with partial rsync, install it with
   `place_snapshot.sh`, and check integrity plus total item count;
8. advance `.sync/base` only after both peers pass;
9. restore the recorded Zotero running states.

First-fill is valid only when one peer truly has no DB. A nearly empty DB created
by starting Zotero too early must never win against a full library.

## If keys were already reassigned

Stop the normal workflow. Preserve, with timestamps and SHA-256:

- the last pre-reassignment authoritative snapshot;
- current origin and replica snapshots plus any hot journal;
- the last common base, if trustworthy;
- affected attachment storage;
- logical plans/manifests for both peers.

Compare logical payloads by Zotero keys, not SQLite integer IDs or DB file hashes.
Prove the old-only keys, generated replacement keys, changed shared payloads,
parents, memberships, and attachment directories. Equal item counts do not prove
identity.

Take a fresh replica checkpoint immediately before its final stop: users may add
documents while recovery is being investigated. Compare it to the earlier bad
checkpoint to isolate genuine replica-only additions.

Build the candidate from the whole last authoritative snapshot, retaining all
original keys. Reintroduce only proven later additions while preserving their
replica keys. Prefer Zotero native sync for complex objects. A one-time direct
SQLite transplant is acceptable only on disposable same-schema copies for a
simple object whose entire dependency graph is known; reject children or special
state. Do not generalise it into a row merger—creators, values, tags, notes,
attachments, annotations, relations, publications, retractions, collections and
memberships all have dependent/shared tables.

Before installing the candidate, require:

- `PRAGMA integrity_check` = `ok`;
- every authoritative key present and every generated replacement key absent;
- delta from the authoritative snapshot equals only the enumerated genuine
  additions/relations;
- added payloads match the final replica checkpoint exactly;
- every attachment directory uses the authoritative attachment key and is
  non-empty/hash-verified;
- `merge_replica.py` reports the expected fast-forward.

Transfer storage without delete, install the exact candidate on each stopped
peer with `place_snapshot.sh`, compare stopped snapshot hashes/counts, then set
`.sync/base`. If placement fails, leave that peer stopped and restore from
`zotero.sqlite.prev` or the timestamped backup.

## Literature is a separate sync surface

Zotero DB/profile sync does not move the literature worktree.

- Pull replica-only files to the origin before pushing a manifest; copy explicit
  paths only and never use a directory-wide delete.
- Verify copied files by SHA-256 on both peers.
- For every Zotero `extra` path, check readable text/capture and ignored original
  PDF separately. Copy the PDF out of band and keep it ignored/uncommitted.
- Preserve remote untracked files. Never interpret them as cleanup targets.
- Copy final `ZOTERO.md` to the other peer only after library manifests match.
- Do not commit in either repo unless the user explicitly requests it.

## Post-sync recheck and cleanup

A finished run is not a finished job. After any sync or recovery:

1. Run the verification gate below (stopped snapshots, live API spot-check,
   manifests on both peers diffed clean).
2. Only after the gate passes, remove transient transfer artefacts:
   `.sync/replica/` (fetched DB copies), `.sync/candidates/` (base candidates
   being tested), stray `.sync/ssh-*` control sockets, `/tmp` plans and
   manifests from the session, plus superseded `.sync/snap*.txt`. Do **not**
   delete `.sync/base/`, `.sync/pre-fast-forward/`, `zotero.sqlite.prev`,
   `zotero.sqlite.bak*` or `.sync/storage/` — those are rollback evidence;
   prune them only by explicit decision, noting SHA-256 first.
3. Report container end states, unresolved findings, and every file removed.

## Verification gate

Verify from three independent views:

1. **Stopped snapshot:** identical SHA-256 where an exact snapshot was installed,
   SQLite integrity, and total `items` count.
2. **Live API:** query known old keys and every replica-only key; check metadata,
   tags, parents, collection memberships and children. A live DB may be locked or
   legitimately change after startup, so do not require its file hash.
3. **Manifest:** on both peers run

   ```bash
   ./scripts/library_manifest.sh /tmp/zotero-manifest.md
   ```

   Compare content exactly. `items excluding trash + trash` must equal the DB
   item count. Refresh literature `ZOTERO.md` from the verified manifest. The
   renderer bypasses inherited proxies for localhost and compacts large note
   bodies before rendering.

Finish with `bash -n` for sync scripts, merge-classifier unit tests, Python
compile checks, `git diff --check`, and status in every touched repo. Report
container end states, unresolved conflicts, modified/untracked files, backups,
and whether any commit was made.
