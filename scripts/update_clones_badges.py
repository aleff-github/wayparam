#!/usr/bin/env python3
import json
import os
import pathlib
import urllib.request
from datetime import datetime

API_VERSION = "2022-11-28"


def gh_request(url: str, token: str) -> tuple[int, str]:
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("X-GitHub-Api-Version", API_VERSION)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, body


def write_json(path: pathlib.Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main():
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("TRAFFIC_TOKEN", "")
    if not repo or not token:
        raise SystemExit("Missing GITHUB_REPOSITORY or TRAFFIC_TOKEN")

    owner, name = repo.split("/", 1)
    url = f"https://api.github.com/repos/{owner}/{name}/traffic/clones?per=day"

    status, text = gh_request(url, token)

    # 202 = dati in preparazione (capita). In quel caso non tocchiamo i file.
    if status == 202:
        print("GitHub returned 202 Accepted (traffic data being generated). Skipping update.")
        return

    if status != 200:
        print(f"GitHub API error {status}: {text}")
        raise SystemExit(1)

    data = json.loads(text)
    count_14d = int(data.get("count", 0))
    uniques_14d = int(data.get("uniques", 0))
    points = data.get("clones", [])  # lista con timestamp/count/uniques (ultimi 14 giorni)

    out_dir = pathlib.Path(".github/traffic")
    history_path = out_dir / "clones-history.json"

    # Storico: dizionario timestamp -> {count, uniques}
    if history_path.exists():
        history = json.loads(history_path.read_text(encoding="utf-8"))
    else:
        history = {}

    for p in points:
        ts = p.get("timestamp")
        if ts:
            history[ts] = {"count": int(p.get("count", 0)), "uniques": int(p.get("uniques", 0))}

    total_tracked = sum(v.get("count", 0) for v in history.values())

    badge_total = {
        "schemaVersion": 1,
        "label": "clones (tracked)",
        "message": str(total_tracked),
    }

    write_json(history_path, dict(sorted(history.items())))
    write_json(out_dir / "clones-total.json", badge_total)

    # Log utile
    print(
        f"Updated: 14d={count_14d} uniques={uniques_14d} total_tracked={total_tracked} points={len(points)} at {datetime.utcnow().isoformat()}Z"
    )


if __name__ == "__main__":
    main()
