"""Run synthetic Guardian -> Judge examples; replay or opt-in real Grok.

python watcher_Agent/scripts/run_guardian_judge_samples.py --report /tmp/results.json
Add --provider grok --speech-dir /tmp/judge-speech for real recognition.
Add --postgres with TEST_DATABASE_URL for a disposable PostgreSQL database.
No model response is treated as proof of its own correctness: compare it
against independent demo labels, including deliberately wrong decisions.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

WATCHER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WATCHER.parent))
sys.path.insert(0, str(WATCHER))

from langgraph.store.postgres import PostgresStore  # noqa: E402

from judge.config import load_settings  # noqa: E402
from judge.contracts.understanding import Verdict  # noqa: E402
from judge.evaluation import assess_decision, classification_metrics  # noqa: E402
from judge.persistence import migrator  # noqa: E402
from judge.persistence.postgres import PostgresStorage  # noqa: E402
from judge.understanding.grok import GrokUnderstandingProvider  # noqa: E402
from judge.values.postgres_source import PostgresMemoryItemSource  # noqa: E402
from judge.values.reader import StoreValuesReader  # noqa: E402
from tests.support.guardian_samples import (  # noqa: E402
    OWNER,
    approve_guardian_values,
    demo_samples,
    execute_stream_sample,
    read_speech,
    reference_for,
    replay_judgment,
)
from tests.support.harness import Harness  # noqa: E402
from tests.support.media import sine  # noqa: E402


async def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = load_settings()
    samples = demo_samples()
    if args.provider == "grok":
        if (
            settings.xai_api_key is None
            or not settings.xai_api_key.get_secret_value().strip()
        ):
            raise ValueError(
                "Put XAI_API_KEY in watcher_Agent/.env before live testing"
            )
        if args.speech_dir is None:
            raise ValueError(
                "Live audio recognition needs --speech-dir with generated AIFF files"
            )
    provider = (
        GrokUnderstandingProvider(
            api_key=settings.xai_api_key.get_secret_value(),
            model_id=settings.grok_model_id,
            transcription_model=settings.grok_transcription_model,
            timeout_seconds=30,
        )
        if args.provider == "grok" and settings.xai_api_key is not None
        else None
    )
    storage = None
    if args.postgres:
        url = os.environ.get("TEST_DATABASE_URL", "")
        if not url:
            raise ValueError(
                "--postgres requires TEST_DATABASE_URL for a disposable database"
            )
        migrator.apply(url, without_vector=True)
        migrator.setup_memory_store(url)
        with PostgresStore.from_conn_string(url) as store:
            text, values = approve_guardian_values(store)
        storage = await PostgresStorage.connect(url, vector_enabled=False)
    else:
        text, values = approve_guardian_values()
    h = await Harness.create(
        values=values,
        storage=storage,
        provider=provider,
        embedding_indexing_enabled=False,
        provider_timeout_seconds=30,
        job_deadline_seconds=90,
        provider_max_attempts=1,
    )
    h.owner = OWNER
    if storage is not None:
        h.runtime.pipeline.values_gate.reader = StoreValuesReader(
            PostgresMemoryItemSource(storage.pool),
            max_bytes=16_384,
            require_approval=True,
            clock=h.clock,
        )
    rows = []
    try:
        for sample in samples:
            if args.provider == "replay":
                h.script = lambda request, sample=sample: replay_judgment(
                    sample, audio=request.has_audio
                )
            pcm = (
                read_speech(str(args.speech_dir / f"{sample['id']}.aiff"))
                if args.speech_dir is not None and sample["transcript"]
                else sine(3.0)
            )
            record = await execute_stream_sample(h, sample, pcm)
            reference = reference_for(sample, values)
            arbitrary = {}
            for verdict in Verdict:
                ids = (
                    ()
                    if verdict is Verdict.NEUTRAL
                    else tuple(reference.value_ids or {"V2"})
                )
                candidate = record.model_copy(
                    update={"verdict": verdict, "value_ids": ids}
                )
                arbitrary[verdict.value] = assess_decision(
                    candidate, reference, values
                ).confirmed
            async with h.runtime.storage.transaction() as tx:
                reread = await tx.reels.get(OWNER, record.id)
            if reread is None:
                raise ValueError("stored decision could not be read back")
            row = {
                **asdict(assess_decision(record, reference, values)),
                "description": sample["description"],
                "reason": record.reason,
                "value_ids": list(record.value_ids),
                "transcript": record.transcript,
                "evaluation_status": record.evaluation_status.value,
                "audio_status": (
                    record.audio_status.value if record.audio_status else None
                ),
                "video_frames_selected": (
                    record.coverage.selected_frame_count if record.coverage else 0
                ),
                "model_id": record.model_id,
                "stored_reel_id": str(record.id),
                "database_roundtrip": reread == record,
                "arbitrary_decision_checks": arbitrary,
            }
            rows.append(row)
            print(
                f"{sample['id']}: expected={sample['verdict']} "
                f"actual={row['actual_verdict']} confirmed={row['confirmed']}"
            )
        return {
            "provider": args.provider,
            "recognition_test": args.provider == "grok",
            "limitation": (
                "Replay verifies plumbing/scoring, not model recognition."
                if args.provider == "replay"
                else "Seven synthetic examples, not a general accuracy estimate."
            ),
            "storage": "postgres" if storage else "memory",
            "guardian_values": text,
            "confirmed": sum(r["confirmed"] is True for r in rows),
            "denied": sum(r["confirmed"] is False for r in rows),
            "inconclusive": sum(r["confirmed"] is None for r in rows),
            "arbitrary_decisions_confirmed": sum(
                result is True
                for row in rows
                for result in row["arbitrary_decision_checks"].values()
            ),
            "deliberately_wrong_decisions_denied": sum(
                result is False
                for row in rows
                for result in row["arbitrary_decision_checks"].values()
            ),
            "executions": rows,
            "metrics": classification_metrics(rows),
        }
    finally:
        await h.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("replay", "grok"), default="replay")
    parser.add_argument("--postgres", action="store_true")
    parser.add_argument("--speech-dir", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = asyncio.run(run(args))
    except (ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 1 if report["denied"] or report["inconclusive"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
