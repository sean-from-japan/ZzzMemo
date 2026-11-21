"""dev.py — run the app locally on a copy of the production data.

    python dev.py pull    # copy the production DB and files (needs `fly auth login`)
    python dev.py         # serve the copy at http://localhost:5001 (no login on loopback)

The copy lives outside the repository (~/.cache/zzzmemo-dev), so data/ is never
touched. Each pull replaces the previous copy; changes made locally stay local.
"""

import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

DEV = Path.home() / ".cache" / "zzzmemo-dev"
APP = "zzzmemo"


def fly(*args: str) -> None:
    subprocess.run(["fly", *args, "--app", APP], check=True)


def pull() -> None:
    # the machine stops when idle and ssh needs it running; a request starts it
    urllib.request.urlopen(f"https://{APP}.fly.dev/login", timeout=60).read()
    time.sleep(2)
    # a new folder each time, so nothing on the server is deleted; the
    # machine's /tmp is reset when it next stops
    tmp = f"/tmp/dev-{int(time.time())}"
    fly("ssh", "console", "-C", f"mkdir {tmp}")
    # SQLite's backup API gives a consistent copy even while the app writes
    fly("ssh", "console", "-C", "python -c \"import sqlite3; s=sqlite3.connect('/app/data/qcatch.db'); "
        f"d=sqlite3.connect('{tmp}/qcatch.db'); s.backup(d); d.close()\"")
    fly("ssh", "console", "-C", f"tar czf {tmp}.tgz -C /app/data files -C {tmp} qcatch.db")
    DEV.parent.mkdir(parents=True, exist_ok=True)
    tgz = DEV.parent / "zzzmemo-dev.tgz"
    tgz.unlink(missing_ok=True)
    fly("ssh", "sftp", "get", f"{tmp}.tgz", str(tgz))
    shutil.rmtree(DEV, ignore_errors=True)
    DEV.mkdir()
    with tarfile.open(tgz) as t:
        t.extractall(DEV, filter="data")
    tgz.unlink()
    print(f"copied to {DEV}: qcatch.db and {len(list((DEV / 'files').glob('*')))} files")


def serve() -> None:
    if not (DEV / "qcatch.db").exists():
        sys.exit("no copy yet: run `python dev.py pull` first")
    os.environ["ZZZMEMO_DB"] = str(DEV / "qcatch.db")
    os.environ.setdefault("HOST", "127.0.0.1")
    from web.server import run

    run(port=int(os.environ.get("PORT", "5001")), open_browser=False)


if __name__ == "__main__":
    pull() if sys.argv[1:] == ["pull"] else serve()
