"""投稿待ちリストと投稿の記録。同じ商品を二度出さないために使う。"""
import json
import sqlite3
from datetime import datetime, timedelta

from .config import DB_PATH


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_code TEXT NOT NULL,
            item_name TEXT,
            genre_id TEXT,
            price INTEGER,
            image_url TEXT,
            comment TEXT,
            status TEXT NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL
        )"""
    )
    # 投稿待ちリスト。item には楽天APIから取った商品情報を JSON で丸ごと入れる
    conn.execute(
        """CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_code TEXT NOT NULL UNIQUE,
            item TEXT NOT NULL,
            comment TEXT NOT NULL,
            comment_source TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    # あとから足した列。古いデータベースにもなければ足す
    cols = {r[1] for r in conn.execute("PRAGMA table_info(queue)")}
    if "images" not in cols:
        conn.execute("ALTER TABLE queue ADD COLUMN images TEXT NOT NULL DEFAULT '[]'")
    return conn


def already_used(item_code: str) -> bool:
    """投稿待ちに入れたことがある、または投稿済みの商品か。"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM queue WHERE item_code = ?"
            " UNION SELECT 1 FROM posts WHERE item_code = ? AND status = 'posted' LIMIT 1",
            (item_code, item_code),
        ).fetchone()
    return row is not None


def queue_add(item: dict, comment: str, source: str) -> int | None:
    """投稿待ちに入れる。入れた候補の番号を返す（すでにあれば None）。"""
    with _conn() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO queue (item_code, item, comment, comment_source, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (item["code"], json.dumps(item, ensure_ascii=False), comment, source, _now(), _now()),
        )
        return cur.lastrowid if cur.rowcount else None


def _queue_row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["item"] = json.loads(d["item"])
    d["images"] = json.loads(d.get("images") or "[]")
    return d


def queue_list(status: str = "pending") -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM queue WHERE status = ? ORDER BY id DESC", (status,)).fetchall()
    return [_queue_row(r) for r in rows]


def queue_get(queue_id: int) -> dict | None:
    with _conn() as conn:
        r = conn.execute("SELECT * FROM queue WHERE id = ?", (queue_id,)).fetchone()
    return _queue_row(r) if r else None


def queue_update(queue_id: int, **fields) -> None:
    allowed = {k: v for k, v in fields.items() if k in ("comment", "status", "images")}
    if not allowed:
        return
    sets = ", ".join(f"{k} = ?" for k in allowed)
    with _conn() as conn:
        conn.execute(f"UPDATE queue SET {sets}, updated_at = ? WHERE id = ?", (*allowed.values(), _now(), queue_id))


def queue_find_recent(item_code: str, minutes: int = 60) -> dict | None:
    """拡張機能用。投稿待ちか、少し前に投稿済みにした候補を探す。"""
    since = (datetime.now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    with _conn() as conn:
        r = conn.execute(
            "SELECT * FROM queue WHERE item_code = ?"
            " AND (status = 'pending' OR (status = 'posted' AND updated_at >= ?)) ORDER BY id DESC LIMIT 1",
            (item_code, since),
        ).fetchone()
    return _queue_row(r) if r else None


def unrecord_posted(item_code: str) -> None:
    """「元に戻す」用。いちばん新しい投稿の記録を消す。"""
    with _conn() as conn:
        conn.execute(
            "DELETE FROM posts WHERE id = (SELECT MAX(id) FROM posts WHERE item_code = ? AND status = 'posted')",
            (item_code,),
        )


def queued_today() -> int:
    today = datetime.now().date().isoformat()
    with _conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM queue WHERE created_at >= ?", (today,)).fetchone()[0]


def pending_count() -> int:
    with _conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM queue WHERE status = 'pending'").fetchone()[0]


def record(item: dict, comment: str | None, status: str, error: str | None = None) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO posts (item_code, item_name, genre_id, price, image_url, comment, status, error, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item["code"], item["name"], item["genre_id"], item["price"], item["image_url"],
                comment, status, error, _now(),
            ),
        )


def posted_today() -> int:
    today = datetime.now().date().isoformat()
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM posts WHERE status = 'posted' AND created_at >= ?", (today,)
        ).fetchone()
    return row[0]


def daily_counts(days: int = 7) -> list[dict]:
    """直近 days 日分の、日ごとの投稿数と失敗数。投稿がない日も0で返す。"""
    today = datetime.now().date()
    start = today - timedelta(days=days - 1)
    with _conn() as conn:
        rows = conn.execute(
            "SELECT substr(created_at, 1, 10) AS day,"
            " SUM(status = 'posted') AS posted, SUM(status != 'posted') AS failed"
            " FROM posts WHERE created_at >= ? GROUP BY day",
            (start.isoformat(),),
        ).fetchall()
    by_day = {r["day"]: r for r in rows}
    result = []
    for i in range(days):
        day = (start + timedelta(days=i)).isoformat()
        r = by_day.get(day)
        result.append({"date": day, "posted": r["posted"] if r else 0, "failed": r["failed"] if r else 0})
    return result


def recent(limit: int = 50) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM posts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
