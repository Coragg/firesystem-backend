"""[AI] Entry point for `python -m src.ingest daily|backfill`; the exit code is what `main` returns."""

import sys

from src.ingest.run import main

sys.exit(main())
