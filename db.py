"""One storage interface over two engines.

SQLite when running as a normal process; Postgres when DATABASE_URL is set,
which is what serverless hosting needs -- Vercel's filesystem is read-only
apart from a per-invocation /tmp, so a SQLite file there would lose every
claim between requests.

The SQL in store.py is written once, in the subset both engines share, and
this module papers over the three places they differ:

  * placeholders      ?              -> %s
  * insert-or-ignore  INSERT OR IGNORE -> INSERT ... ON CONFLICT DO NOTHING
  * rows              sqlite3.Row / tuple -> plain dicts, both engines

Everything else -- ON CONFLICT ... DO UPDATE SET x = excluded.x, COALESCE,
CREATE TABLE IF NOT EXISTS -- is the same on both.
"""
import re

import config

IS_POSTGRES = bool(config.DATABASE_URL)


# --------------------------------------------------------------- dialect --

_IGNORE_RE = re.compile(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", re.I)


def translate(sql):
    """Rewrite SQLite-flavoured SQL for Postgres. A no-op on SQLite.

    The `?` -> `%s` swap is safe here because none of our SQL contains a
    question mark or a percent sign inside a string literal; `_assert_clean`
    keeps that true.
    """
    if not IS_POSTGRES:
        return sql
    if _IGNORE_RE.search(sql):
        sql = _IGNORE_RE.sub("INSERT INTO", sql)
        sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return sql.replace("?", "%s")


def _assert_clean(sql):
    """Guard the assumption `translate` relies on."""
    if "'" in sql or '"' in sql:
        for lit in re.findall(r"'[^']*'|\"[^\"]*\"", sql):
            if "?" in lit or "%" in lit:
                raise ValueError("SQL literal contains ? or %%: %s" % lit)


# ------------------------------------------------------------ connection --

class Cursor(object):
    """Uniform result access: rows are dicts on both engines."""

    def __init__(self, raw):
        self._raw = raw

    def _row(self, row):
        if row is None:
            return None
        if isinstance(row, dict):
            return row
        # sqlite3.Row is mapping-like but is not a dict subclass, so it needs
        # converting rather than passing through.
        return dict(row)

    def fetchone(self):
        return self._row(self._raw.fetchone())

    def fetchall(self):
        return [self._row(r) for r in self._raw.fetchall()]

    def __iter__(self):
        for r in self._raw:
            yield self._row(r)

    @property
    def rowcount(self):
        return self._raw.rowcount


class Connection(object):
    def __init__(self):
        self._closed = False
        if IS_POSTGRES:
            import psycopg2
            import psycopg2.extras
            self._pg = True
            self._con = psycopg2.connect(config.DATABASE_URL,
                                         connect_timeout=10)
            self._con.autocommit = True
            self._factory = psycopg2.extras.RealDictCursor
        else:
            import sqlite3
            self._pg = False
            config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            self._con = sqlite3.connect(str(config.DB_PATH), timeout=10)
            self._con.row_factory = sqlite3.Row
            self._con.execute("PRAGMA busy_timeout=8000")
            # WAL survives a crash; NORMAL drops the fsync on every commit.
            self._con.execute("PRAGMA synchronous=NORMAL")

    # -- statements --------------------------------------------------------
    def execute(self, sql, params=()):
        _assert_clean(sql)
        sql = translate(sql)
        if self._pg:
            cur = self._con.cursor(cursor_factory=self._factory)
            cur.execute(sql, tuple(params))
            return Cursor(cur)
        return Cursor(self._con.execute(sql, tuple(params)))

    def executemany(self, sql, seq):
        _assert_clean(sql)
        sql = translate(sql)
        rows = [tuple(r) for r in seq]
        if not rows:
            return
        if self._pg:
            cur = self._con.cursor()
            cur.executemany(sql, rows)
            return
        self._con.executemany(sql, rows)

    def executescript(self, sql):
        """Schema only: several statements, no parameters."""
        if self._pg:
            cur = self._con.cursor()
            for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
                cur.execute(stmt)
            return
        self._con.executescript(sql)

    def pragma(self, text):
        """SQLite tuning. Silently skipped on Postgres, which has no pragmas."""
        if self._pg:
            return None
        row = self._con.execute("PRAGMA " + text).fetchone()
        return row[0] if row else None

    # -- transactions ------------------------------------------------------
    # `with con:` commits on success and rolls back on error, on both engines.
    def __enter__(self):
        if self._pg:
            self._con.autocommit = False
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._pg:
            if exc_type is None:
                self._con.commit()
            else:
                self._con.rollback()
            self._con.autocommit = True
            return False
        if exc_type is None:
            self._con.commit()
        else:
            self._con.rollback()
        return False

    def close(self):
        if not self._closed:
            self._closed = True
            self._con.close()


def connect():
    return Connection()
