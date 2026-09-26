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


def task_to_dict(row):
    conn = get_db()
    subs = conn.execute(
        "SELECT id, title, done FROM subtasks WHERE task_id = ? ORDER BY id",
        (row["id"],),
    ).fetchall()
    conn.close()
    return {
        "id": row["id"],
        "title": row["title"],
        "progress": row["progress"],
        "done": bool(row["done"]),
        "subtasks": [
            {"id": s["id"], "title": s["title"], "done": bool(s["done"])} for s in subs
        ],
    }


def recalc_progress(conn, task_id):
    """Recompute a task's % from its subtasks (done / total)."""
    subs = conn.execute(
        "SELECT done FROM subtasks WHERE task_id = ?", (task_id,)
    ).fetchall()
    if subs:
        done_count = sum(1 for s in subs if s["done"])
        progress = round(done_count / len(subs) * 100)
        conn.execute(
            "UPDATE tasks SET progress = ?, done = ? WHERE id = ?",
            (progress, 1 if progress == 100 else 0, task_id),
        )


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
    if "progress" in data:
        p = max(0, min(100, int(data["progress"])))
        conn.execute(
            "UPDATE tasks SET progress = ?, done = ? WHERE id = ?",
            (p, 1 if p == 100 else 0, task_id),
        )
    if "done" in data:
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
    return jsonify({"id": sub["id"], "title": sub["title"], "done": False}), 201


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
    if "done" in data:
        conn.execute(
            "UPDATE subtasks SET done = ? WHERE id = ?", (1 if data["done"] else 0, sub_id)
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
