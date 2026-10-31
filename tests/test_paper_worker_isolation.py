import sqlite3
import subprocess
import sys


def test_isolation_allows_only_exact_readonly_sqlite_uri(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    source_db = tmp_path / "native.sqlite"
    connection = sqlite3.connect(source_db)
    connection.execute("CREATE TABLE frames (value TEXT)")
    connection.execute("INSERT INTO frames VALUES ('ok')")
    connection.commit()
    connection.close()
    script = r'''import sqlite3, sys
from pathlib import Path
from tools.paper_worker_isolation import install
root, db = Path(sys.argv[1]), Path(sys.argv[2])
install(root, readonly_sqlite_paths=[db])
uri = db.as_uri() + "?mode=ro"
assert sqlite3.connect(uri, uri=True).execute("select value from frames").fetchone()[0] == "ok"
for target in (db.as_uri() + "?mode=rw", (db.parent / "other.sqlite").as_uri() + "?mode=ro", str(db)):
    try:
        sqlite3.connect(target, uri=target.startswith("file:"))
    except PermissionError:
        pass
    else:
        raise AssertionError("unapproved SQLite open was allowed: " + target)
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(root), str(source_db)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
