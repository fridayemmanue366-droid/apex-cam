"""Download the open-source AI models Apex Cam uses, into backend/models/.

    python scripts/download_models.py            # core models only
    python scripts/download_models.py --all      # core + optional extras (what the installer runs)

All the real work lives in app/core/model_downloader.py: resumable downloads,
retries, size verification, and the same code the in-app "Download missing
models" button uses. Safe to run repeatedly -- finished files are skipped and
half-finished ones resume. Always exits 0 so an installer never aborts on a
flaky network; anything unfinished is completed from inside the app.

Proprietary cloud models (Decart Lucy, ElevenLabs...) are not here; they only
run on the vendors' servers.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # backend/ -> `app` package

from app.core import model_downloader  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="also download optional extras")
    args = ap.parse_args()
    print("Apex Cam AI models -> " + str(model_downloader.MODELS))
    model_downloader.run(include_extra=args.all, printer=lambda s: print(s, flush=True))


if __name__ == "__main__":
    main()
