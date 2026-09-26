import subprocess, sys
for script in ('update_data.py','rank_decisions.py'):
    r=subprocess.run([sys.executable,script],check=False)
    if r.returncode:
        raise SystemExit(r.returncode)

# External daily attention is supplementary: a source outage must not block
# the core stock-score update.
r=subprocess.run([sys.executable,'daily_attention.py'],check=False)
if r.returncode:
    print('daily_attention.py failed; core daily update remains valid')
