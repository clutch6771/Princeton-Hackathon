"""Generate local synthetic speech for the Judge sample suite (macOS).

This never records a screen, microphone or other application.
"""

import argparse
import subprocess  # nosec B404 - fixed local speech synthesizer, no shell
import sys
from pathlib import Path

WATCHER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WATCHER.parent))
sys.path.insert(0, str(WATCHER))

from tests.support.guardian_samples import demo_samples, read_speech  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for sample in demo_samples():
        if not sample["transcript"]:
            continue
        output = args.output_dir / f"{sample['id']}.aiff"
        # All arguments are separate, and speech is an invented repo fixture.
        subprocess.run(  # noqa: S603  # nosec B603
            [
                "/usr/bin/say",
                "-v",
                "Samantha",
                "-r",
                "190",
                "-o",
                str(output),
                sample["transcript"],
            ],
            check=True,
            timeout=30,
            capture_output=True,
        )
        pcm = read_speech(str(output))
        print(f"{output.name}: {len(pcm) / 16000:.1f} seconds of synthetic speech")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
