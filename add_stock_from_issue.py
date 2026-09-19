import json
import os
import re
from pathlib import Path

CODE_RE = re.compile(r"^[0-9A-Z]{4,5}$")
TITLE_PREFIX = "[株スコア追加]"


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
    body = str(issue.get("body") or "").strip()

    # Only accept requests created by the repository owner and by our exact
    # app-generated issue format. This prevents arbitrary public issues from
    # modifying the watchlist.
    if not owner or author.lower() != owner.lower():
        raise SystemExit(f"Unauthorized issue author: {author}")
    if not title.startswith(TITLE_PREFIX):
        raise SystemExit("Not a kabu-score stock-add request")
    if not CODE_RE.fullmatch(body.upper()):
        raise SystemExit("Issue body must contain exactly one 4-5 character security code")

    code = body.upper()
    with open(os.environ.get("GITHUB_OUTPUT", "/dev/stdout"), "a", encoding="utf-8") as f:
        f.write(f"query={code}\n")
    print(f"Validated stock-add request: {code}")


if __name__ == "__main__":
    main()
