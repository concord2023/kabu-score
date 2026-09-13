"""Run the full daily pipeline for the watchlist.
1) update_data.py -> data/stocks.json
2) rank_decisions.py -> data/decision_ranking.json
"""
import os, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
if not os.environ.get('IRBANK_API_KEY'):
    raise SystemExit('IRBANK_API_KEY is not set. Set it only in your local environment; never paste it into chat.')
subprocess.run([sys.executable, str(ROOT/'update_data.py')], cwd=ROOT, check=True)
subprocess.run([sys.executable, str(ROOT/'rank_decisions.py')], cwd=ROOT, check=True)
print('Daily pipeline completed.')
