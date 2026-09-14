import subprocess, sys
for script in ('update_data.py','rank_decisions.py'):
    r=subprocess.run([sys.executable,script],check=False)
    if r.returncode:
        raise SystemExit(r.returncode)
