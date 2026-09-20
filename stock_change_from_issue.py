import json
import os
import re
from pathlib import Path

CODE_RE = re.compile(r"^[0-9A-Z]{4,5}$")
ADD_PREFIX = "[株スコア追加]"
DELETE_PREFIX = "[株スコア削除]"


def main():
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        raise SystemExit("GITHUB_EVENT_PATH is not set")
    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    issue = event.get("issue") or {}
    repo = event.get("repository") or {}
    author = str((issue.get("user") or {}).get("login") or "")
    owner = str((repo.get("owner") or {}).get("login") or "")
    title = str(issue.get("title") or "").strip()
    body = str(issue.get("body") or "").strip().upper()
    if not owner or author.lower() != owner.lower():
        raise SystemExit(f"Unauthorized issue author: {author}")
    if title.startswith(ADD_PREFIX):
        action = "add"
    elif title.startswith(DELETE_PREFIX):
        action = "delete"
    else:
        raise SystemExit("Not a kabu-score stock change request")
    if not CODE_RE.fullmatch(body):
        raise SystemExit("Issue body must contain exactly one 4-5 character security code")
    with open(os.environ.get("GITHUB_OUTPUT", "/dev/stdout"), "a", encoding="utf-8") as f:
        f.write(f"action={action}\nquery={body}\n")
    print(f"Validated stock-{action} request: {body}")

if __name__ == "__main__":
    main()
