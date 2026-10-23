
from flask import Flask, render_template, request, jsonify
import sqlite3
import requests
from datetime import datetime

app = Flask(__name__)

DATABASE = "nova_chat.db"
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "llama3.2:3b"


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL DEFAULT 'New Chat',
            created_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (chat_id) REFERENCES chats(id)
                ON DELETE CASCADE
        )
    """)

    conn.commit()
    conn.close()


def now():
    return datetime.now().isoformat(timespec="seconds")


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/chats", methods=["GET"])
def get_chats():
    search = request.args.get("search", "").strip()
    conn = get_db()

    if search:
        chats = conn.execute("""
            SELECT DISTINCT c.*
            FROM chats c
            LEFT JOIN messages m ON m.chat_id = c.id
            WHERE c.title LIKE ? OR m.content LIKE ?
            ORDER BY c.id DESC
        """, (f"%{search}%", f"%{search}%")).fetchall()
    else:
        chats = conn.execute("""
            SELECT * FROM chats ORDER BY id DESC
        """).fetchall()

    result = [dict(chat) for chat in chats]
    conn.close()
    return jsonify(result)


@app.route("/api/chats", methods=["POST"])
def create_chat():
    conn = get_db()
    cursor = conn.execute("""
        INSERT INTO chats (title, created_at)
        VALUES (?, ?)
    """, ("New Chat", now()))

    chat_id = cursor.lastrowid
    conn.commit()

    chat = conn.execute(
        "SELECT * FROM chats WHERE id = ?", (chat_id,)
    ).fetchone()

    conn.close()
    return jsonify(dict(chat)), 201


@app.route("/api/chat/<int:chat_id>/messages", methods=["GET"])
def get_messages(chat_id):
    conn = get_db()

    chat = conn.execute(
        "SELECT id FROM chats WHERE id = ?", (chat_id,)
    ).fetchone()

    if not chat:
        conn.close()
        return jsonify({"error": "Chat not found"}), 404

    messages = conn.execute("""
        SELECT id, role, content, created_at
        FROM messages
        WHERE chat_id = ?
        ORDER BY id ASC
    """, (chat_id,)).fetchall()

    conn.close()
    return jsonify([dict(message) for message in messages])


@app.route("/api/chat/<int:chat_id>", methods=["POST"])
def send_message(chat_id):
    data = request.get_json(silent=True) or {}
    user_message = data.get("message", "").strip()

    if not user_message:
        return jsonify({"error": "Please enter a message"}), 400

    conn = get_db()
    chat = conn.execute(
        "SELECT * FROM chats WHERE id = ?", (chat_id,)
    ).fetchone()

    if not chat:
        conn.close()
        return jsonify({"error": "Chat not found"}), 404

    # Save the user's message.
    conn.execute("""
        INSERT INTO messages (chat_id, role, content, created_at)
        VALUES (?, 'user', ?, ?)
    """, (chat_id, user_message, now()))

    # Set the title from the first user message.
    previous_user_message = conn.execute("""
        SELECT COUNT(*) AS total FROM messages
        WHERE chat_id = ? AND role = 'user'
    """, (chat_id,)).fetchone()["total"]

    if previous_user_message == 1 and chat["title"] == "New Chat":
        title = user_message[:40]
        conn.execute(
            "UPDATE chats SET title = ? WHERE id = ?",
            (title, chat_id)
        )

    history = conn.execute("""
        SELECT role, content FROM messages
        WHERE chat_id = ?
        ORDER BY id ASC
    """, (chat_id,)).fetchall()

    ollama_messages = [
        {
            "role": "system",
            "content": "You are ADHU AI, a helpful assistant. "
                       "Give clear, friendly answers."
        }
    ]

    ollama_messages.extend([
        {"role": row["role"], "content": row["content"]}
        for row in history
    ])

    conn.commit()
    conn.close()

    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "messages": ollama_messages,
                "stream": False
            },
            timeout=180
        )
        response.raise_for_status()
        answer = response.json()["message"]["content"].strip()

    except requests.RequestException:
        return jsonify({
            "error": (
                "Could not connect to Ollama. Make sure Ollama is running "
                "and llama3.2:3b is installed."
            )
        }), 503
    except (KeyError, ValueError):
        return jsonify({
            "error": "Ollama returned an unexpected response."
        }), 502

    # Save the assistant's response.
    conn = get_db()
    conn.execute("""
        INSERT INTO messages (chat_id, role, content, created_at)
        VALUES (?, 'assistant', ?, ?)
    """, (chat_id, answer, now()))
    conn.commit()
    conn.close()

    return jsonify({"reply": answer, "chat_id": chat_id})


@app.route("/api/chat/<int:chat_id>/rename", methods=["POST"])
def rename_chat(chat_id):
    data = request.get_json(silent=True) or {}
    title = data.get("title", "").strip()

    if not title:
        return jsonify({"error": "Enter a chat name"}), 400

    conn = get_db()
    cursor = conn.execute(
        "UPDATE chats SET title = ? WHERE id = ?",
        (title[:100], chat_id)
    )
    conn.commit()
    updated = cursor.rowcount
    conn.close()

    if not updated:
        return jsonify({"error": "Chat not found"}), 404

    return jsonify({"success": True, "title": title[:100]})


@app.route("/api/chat/<int:chat_id>", methods=["DELETE"])
def delete_chat(chat_id):
    conn = get_db()
    conn.execute("PRAGMA foreign_keys = ON")

    cursor = conn.execute(
        "DELETE FROM chats WHERE id = ?", (chat_id,)
    )

    conn.commit()
    deleted = cursor.rowcount
    conn.close()

    if not deleted:
        return jsonify({"error": "Chat not found"}), 404

    return jsonify({"success": True})


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
