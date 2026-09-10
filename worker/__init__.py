"""Paper Radar worker package.

Deployed as three Render components sharing this codebase:
  - Cron Job   : `python -m worker.run`      (daily pipeline)
  - Web Service : `uvicorn worker.app:app`   (dashboard-triggered term backfill + manual run)
  - one-off Job : `python -m worker.backfill` (initial 12-16 week bootstrap)

All arXiv access from any component is serialized through one Postgres advisory
lock (worker.arxiv_lock) to honour arXiv's one-connection / >=3s rule.
"""

__version__ = "0.1.0"
