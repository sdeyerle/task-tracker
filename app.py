import os
import sqlite3
from flask import Flask, jsonify, request, send_from_directory

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "tasks.db")

app = Flask(__name__)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            progress INTEGER NOT NULL DEFAULT 0,
            done INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS subtasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()
    conn.close()


init_db()


def migrate():
    """Add newer columns to existing databases; backfill sensibly."""
    conn = get_db()
    task_cols = [r["name"] for r in conn.execute("PRAGMA table_info(tasks)")]
    if "forced" not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN forced INTEGER NOT NULL DEFAULT 0")
    sub_cols = [r["name"] for r in conn.execute("PRAGMA table_info(subtasks)")]
    if "progress" not in sub_cols:
        conn.execute(
            "ALTER TABLE subtasks ADD COLUMN progress INTEGER NOT NULL DEFAULT 0"
        )
        conn.execute("UPDATE subtasks SET progress = 100 WHERE done = 1")
    conn.commit()
    # Recompute parents that have subtasks (manual tasks keep their value).
    for (tid,) in conn.execute("SELECT DISTINCT task_id FROM subtasks"):
        recalc_progress(conn, tid)
    conn.commit()
    conn.close()


def task_to_dict(row):
    conn = get_db()
    subs = conn.execute(
        "SELECT id, title, progress FROM subtasks WHERE task_id = ? ORDER BY id",
        (row["id"],),
    ).fetchall()
    conn.close()
    return {
        "id": row["id"],
        "title": row["title"],
        "progress": row["progress"],
        "done": bool(row["done"]),
        "forced": bool(row["forced"]),
        "subtasks": [
            {
                "id": s["id"],
                "title": s["title"],
                "progress": s["progress"],
                "done": s["progress"] == 100,
            }
            for s in subs
        ],
    }


def recalc_progress(conn, task_id):
    """Recompute a task's % from its subtasks' progress (average).

    A forced task stays at 100. Tasks without subtasks keep their
    manually-set progress.
    """
    task = conn.execute(
        "SELECT forced FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    if not task:
        return
    subs = conn.execute(
        "SELECT progress FROM subtasks WHERE task_id = ?", (task_id,)
    ).fetchall()
    if task["forced"]:
        progress, done = 100, 1
    elif subs:
        progress = round(sum(s["progress"] for s in subs) / len(subs))
        done = 1 if progress == 100 else 0
    else:
        return
    conn.execute(
        "UPDATE tasks SET progress = ?, done = ? WHERE id = ?",
        (progress, done, task_id),
    )


migrate()


@app.route("/")
def index():
    return send_from_directory(os.path.join(APP_DIR, "static"), "index.html")


@app.route("/api/tasks", methods=["GET"])
def list_tasks():
    conn = get_db()
    rows = conn.execute("SELECT * FROM tasks ORDER BY done, id DESC").fetchall()
    conn.close()
    return jsonify([task_to_dict(r) for r in rows])


@app.route("/api/tasks", methods=["POST"])
def create_task():
    data = request.get_json(force=True)
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    conn = get_db()
    cur = conn.execute("INSERT INTO tasks (title) VALUES (?)", (title,))
    conn.commit()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (cur.lastrowid,)).fetchone()
    result = task_to_dict(row)
    conn.close()
    return jsonify(result), 201


@app.route("/api/tasks/<int:task_id>", methods=["PATCH"])
def update_task(task_id):
    data = request.get_json(force=True)
    conn = get_db()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "not found"}), 404
    if "title" in data and (data["title"] or "").strip():
        conn.execute(
            "UPDATE tasks SET title = ? WHERE id = ?", (data["title"].strip(), task_id)
        )
    has_subs = conn.execute(
        "SELECT 1 FROM subtasks WHERE task_id = ? LIMIT 1", (task_id,)
    ).fetchone()
    if "forced" in data:
        f = 1 if data["forced"] else 0
        conn.execute("UPDATE tasks SET forced = ? WHERE id = ?", (f, task_id))
        recalc_progress(conn, task_id)
    if "progress" in data and not has_subs:
        # Manual progress only applies to tasks without subtasks; parents
        # with subtasks get their % from the subtasks.
        p = max(0, min(100, int(data["progress"])))
        conn.execute(
            "UPDATE tasks SET progress = ?, done = ? WHERE id = ?",
            (p, 1 if p == 100 else 0, task_id),
        )
    if "done" in data:
        if has_subs:
            # The head checkbox on a parent task is the "force to 100%" switch.
            f = 1 if data["done"] else 0
            conn.execute("UPDATE tasks SET forced = ? WHERE id = ?", (f, task_id))
            recalc_progress(conn, task_id)
        else:
            d = 1 if data["done"] else 0
            conn.execute(
                "UPDATE tasks SET done = ?, progress = ? WHERE id = ?",
                (d, 100 if d else row["progress"], task_id),
            )
    conn.commit()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    result = task_to_dict(row)
    conn.close()
    return jsonify(result)


@app.route("/api/tasks/<int:task_id>", methods=["DELETE"])
def delete_task(task_id):
    conn = get_db()
    conn.execute("DELETE FROM subtasks WHERE task_id = ?", (task_id,))
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/tasks/<int:task_id>/subtasks", methods=["POST"])
def create_subtask(task_id):
    data = request.get_json(force=True)
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    conn = get_db()
    if not conn.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone():
        conn.close()
        return jsonify({"error": "task not found"}), 404
    cur = conn.execute(
        "INSERT INTO subtasks (task_id, title) VALUES (?, ?)", (task_id, title)
    )
    recalc_progress(conn, task_id)
    conn.commit()
    sub = conn.execute("SELECT * FROM subtasks WHERE id = ?", (cur.lastrowid,)).fetchone()
    conn.close()
    return jsonify(
        {"id": sub["id"], "title": sub["title"], "done": False, "progress": 0}
    ), 201


@app.route("/api/subtasks/<int:sub_id>", methods=["PATCH"])
def update_subtask(sub_id):
    data = request.get_json(force=True)
    conn = get_db()
    sub = conn.execute("SELECT * FROM subtasks WHERE id = ?", (sub_id,)).fetchone()
    if not sub:
        conn.close()
        return jsonify({"error": "not found"}), 404
    if "title" in data and (data["title"] or "").strip():
        conn.execute(
            "UPDATE subtasks SET title = ? WHERE id = ?", (data["title"].strip(), sub_id)
        )
    if "progress" in data:
        p = max(0, min(100, int(data["progress"])))
        conn.execute(
            "UPDATE subtasks SET progress = ?, done = ? WHERE id = ?",
            (p, 1 if p == 100 else 0, sub_id),
        )
    elif "done" in data:
        # Legacy clients that still send done get it mapped onto progress.
        p = 100 if data["done"] else 0
        conn.execute(
            "UPDATE subtasks SET progress = ?, done = ? WHERE id = ?",
            (p, 1 if p == 100 else 0, sub_id),
        )
    recalc_progress(conn, sub["task_id"])
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/subtasks/<int:sub_id>", methods=["DELETE"])
def delete_subtask(sub_id):
    conn = get_db()
    sub = conn.execute("SELECT task_id FROM subtasks WHERE id = ?", (sub_id,)).fetchone()
    if sub:
        conn.execute("DELETE FROM subtasks WHERE id = ?", (sub_id,))
        recalc_progress(conn, sub["task_id"])
        conn.commit()
    conn.close()
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
