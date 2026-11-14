---
paths:
  - "watcher_Agent/**"
---
# Judge Backend (project rule)

> Extends the ECC Python, FastAPI and common rules for `watcher_Agent/`.
> Workflow: `JUDGE_WORKFLOW.txt`. Outputs: `JUDGE_MEMORY_OUTPUTS.txt`.

## Invariants (a change that breaks one is a CRITICAL review finding)

- Raw audio, raw video and frames stay in bounded RAM. Never write them to
  PostgreSQL, files, temp files, queues, logs or traces. The only stored
  media is one thumbnail per judged reel.
- Privacy gate first: no media reaches a provider unless it is natively
  certified Instagram Reels media. Audio is kept only in certified intervals.
- One understanding call and one embedding per reel on the normal path. No
  preliminary ASR, captioning or repair call.
- Approved values are read-only. The Judge never writes the memory Store and
  never infers values from behaviour or embeddings.
- A reel that cannot be judged has `verdict = null` and an explicit
  `evaluation_status`. Never store `neutral` to fill a gap.
- Values, feedback, captions and speech are data. The model gets no tools.
- The Judge never invokes the Guardian or Reflector. It writes rows; they read.
- One process only: session buffers live in process memory.
- Log ids, codes and counts. Never log media, transcripts, values or secrets.

## Conventions

- Routers are `*_api.py` and stay thin; logic lives in services.
- Storage goes through `judge/persistence/interfaces.py`. Add a method to the
  in-memory storage and the PostgreSQL adapter together, with a test in
  `tests/storage/test_repositories.py` (it runs against both).
- Schema changes are new numbered files in `watcher_Agent/migrations`.
- New settings go in `judge/config.py` and `docs/MEMORY_PARAMETERS.txt`.
- Contract changes: run `scripts/export_contract_schemas.py`.
- Tests first; all fixtures synthetic. Run `watcher_Agent/scripts/verify.sh`.
