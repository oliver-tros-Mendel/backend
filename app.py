import os
import secrets
import sqlite3
from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__, static_folder="static", static_url_path="")

BASE_DIR = os.path.dirname(__file__)
DB_FILE = os.path.join(BASE_DIR, "database.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB

# Security settings
TOKEN_DAYS = 7           # how long a login lasts
MAX_FAILED_LOGINS = 5    # wrong passwords allowed...
LOCKOUT_MINUTES = 15     # ...within this many minutes

MAX_COMMENT_LENGTH = 200


def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tokens (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS post_likes (
            user_id INTEGER NOT NULL,
            post_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, post_id)
        );

        CREATE TABLE IF NOT EXISTS login_attempts (
            username TEXT NOT NULL,
            attempted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS follows (
            follower_id INTEGER NOT NULL,
            followed_id INTEGER NOT NULL,
            PRIMARY KEY (follower_id, followed_id)
        );
        """
    )

    # Add the image column if the posts table doesn't have it yet
    post_columns = [row["name"] for row in conn.execute("PRAGMA table_info(posts)")]
    if "image" not in post_columns:
        conn.execute("ALTER TABLE posts ADD COLUMN image TEXT")

    # Add created_at to tokens if the table doesn't have it yet
    token_columns = [row["name"] for row in conn.execute("PRAGMA table_info(tokens)")]
    if "created_at" not in token_columns:
        conn.execute("ALTER TABLE tokens ADD COLUMN created_at TEXT")
    # Old tokens get "now" as their start time, so they expire in 7 days
    conn.execute("UPDATE tokens SET created_at = datetime('now') WHERE created_at IS NULL")

    conn.commit()
    conn.close()


init_db()


@app.errorhandler(RequestEntityTooLarge)
def too_large(error):
    return jsonify({"error": "Image is too large (max 5 MB)"}), 413


def create_token(conn, user_id):
    # Clean out expired tokens so the table doesn't grow forever
    conn.execute(
        "DELETE FROM tokens WHERE created_at < datetime('now', ?)",
        (f"-{TOKEN_DAYS} days",),
    )

    token = secrets.token_hex(32)
    conn.execute(
        "INSERT INTO tokens (token, user_id, created_at) VALUES (?, ?, datetime('now'))",
        (token, user_id),
    )
    conn.commit()
    return token


def get_current_user(conn):
    """Returns the logged-in user row, or None."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    token = header[len("Bearer "):]
    return conn.execute(
        "SELECT users.id, users.username FROM tokens "
        "JOIN users ON users.id = tokens.user_id "
        "WHERE tokens.token = ? AND tokens.created_at > datetime('now', ?)",
        (token, f"-{TOKEN_DAYS} days"),
    ).fetchone()


@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not 3 <= len(username) <= 20:
        return jsonify({"error": "Username must be 3-20 characters"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400

    conn = get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, generate_password_hash(password)),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "Username already taken"}), 409

    token = create_token(conn, cursor.lastrowid)
    conn.close()
    return jsonify({"token": token, "username": username}), 201


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "")
    key = username.lower()  # "Bob" and "bob" share the same attempt counter

    conn = get_db()

    # Forget old attempts, then count the recent failed ones
    conn.execute(
        "DELETE FROM login_attempts WHERE attempted_at < datetime('now', ?)",
        (f"-{LOCKOUT_MINUTES} minutes",),
    )
    failed = conn.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE username = ?", (key,)
    ).fetchone()[0]

    if failed >= MAX_FAILED_LOGINS:
        conn.commit()
        conn.close()
        return jsonify(
            {"error": f"Too many failed attempts. Try again in {LOCKOUT_MINUTES} minutes."}
        ), 429

    user = conn.execute(
        "SELECT id, username, password_hash FROM users WHERE username = ?",
        (username,),
    ).fetchone()

    if user is None or not check_password_hash(user["password_hash"], password):
        conn.execute("INSERT INTO login_attempts (username) VALUES (?)", (key,))
        conn.commit()
        conn.close()
        return jsonify({"error": "Wrong username or password"}), 401

    # Correct password: reset the counter
    conn.execute("DELETE FROM login_attempts WHERE username = ?", (key,))
    conn.commit()

    token = create_token(conn, user["id"])
    conn.close()
    return jsonify({"token": token, "username": user["username"]})


@app.route("/api/logout", methods=["POST"])
def logout():
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        conn = get_db()
        conn.execute("DELETE FROM tokens WHERE token = ?", (header[len("Bearer "):],))
        conn.commit()
        conn.close()
    return jsonify({"ok": True})


@app.route("/api/posts", methods=["GET"])
def get_posts():
    conn = get_db()
    user = get_current_user(conn)

    # Optional filters:
    #   /api/posts?user=alice        -> only alice's posts
    #   /api/posts?feed=following    -> posts from people I follow (and my own)
    username_filter = request.args.get("user")
    following_only = request.args.get("feed") == "following"

    sql = """
        SELECT posts.id, posts.user_id, posts.content, posts.created_at,
               posts.image, users.username,
               (SELECT COUNT(*) FROM post_likes
                WHERE post_likes.post_id = posts.id) AS likes,
               (SELECT COUNT(*) FROM comments
                WHERE comments.post_id = posts.id) AS comment_count
        FROM posts
        JOIN users ON users.id = posts.user_id
    """
    conditions = []
    params = []

    if username_filter:
        conditions.append("users.username = ?")
        params.append(username_filter)

    if following_only:
        if user is None:
            conn.close()
            return jsonify([])
        conditions.append(
            "(posts.user_id IN (SELECT followed_id FROM follows WHERE follower_id = ?)"
            " OR posts.user_id = ?)"
        )
        params.extend([user["id"], user["id"]])

    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY posts.id"

    rows = conn.execute(sql, params).fetchall()

    liked_ids = set()
    if user:
        liked_ids = {
            r["post_id"]
            for r in conn.execute(
                "SELECT post_id FROM post_likes WHERE user_id = ?", (user["id"],)
            )
        }
    conn.close()

    return jsonify(
        [
            {
                "id": row["id"],
                "username": row["username"],
                "content": row["content"],
                "image": "/uploads/" + row["image"] if row["image"] else None,
                "created_at": row["created_at"].replace(" ", "T") + "Z",
                "likes": row["likes"],
                "comments": row["comment_count"],
                "mine": user is not None and row["user_id"] == user["id"],
                "liked_by_me": row["id"] in liked_ids,
            }
            for row in rows
        ]
    )


@app.route("/api/posts", methods=["POST"])
def add_post():
    conn = get_db()
    user = get_current_user(conn)
    if user is None:
        conn.close()
        return jsonify({"error": "Please log in"}), 401

    # Posts arrive as form data (text + optional file), not JSON
    content = request.form.get("content", "").strip()
    image = request.files.get("image")

    filename = None
    if image and image.filename:
        extension = image.filename.rsplit(".", 1)[-1].lower() if "." in image.filename else ""
        if extension not in ALLOWED_EXTENSIONS:
            conn.close()
            return jsonify({"error": "Only PNG, JPG, GIF and WEBP images are allowed"}), 400
        # Random name: avoids clashes and ignores whatever name the user's file had
        filename = secrets.token_hex(16) + "." + extension

    if not content and not filename:
        conn.close()
        return jsonify({"error": "Write something or add a picture"}), 400
    if len(content) > 280:
        conn.close()
        return jsonify({"error": "Post is too long (max 280 characters)"}), 400

    if filename:
        image.save(os.path.join(UPLOAD_DIR, filename))

    conn.execute(
        "INSERT INTO posts (user_id, content, image) VALUES (?, ?, ?)",
        (user["id"], content, filename),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True}), 201


@app.route("/api/posts/<int:post_id>", methods=["DELETE"])
def delete_post(post_id):
    conn = get_db()
    user = get_current_user(conn)
    if user is None:
        conn.close()
        return jsonify({"error": "Please log in"}), 401

    post = conn.execute(
        "SELECT user_id, image FROM posts WHERE id = ?", (post_id,)
    ).fetchone()
    if post is None:
        conn.close()
        return jsonify({"error": "Post not found"}), 404
    if post["user_id"] != user["id"]:
        conn.close()
        return jsonify({"error": "You can only delete your own posts"}), 403

    conn.execute("DELETE FROM post_likes WHERE post_id = ?", (post_id,))
    conn.execute("DELETE FROM comments WHERE post_id = ?", (post_id,))
    conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    conn.commit()
    conn.close()

    # Also remove the picture file so the uploads folder doesn't fill up
    if post["image"]:
        try:
            os.remove(os.path.join(UPLOAD_DIR, post["image"]))
        except OSError:
            pass

    return jsonify({"deleted": post_id})


@app.route("/api/posts/<int:post_id>/like", methods=["POST"])
def toggle_like(post_id):
    conn = get_db()
    user = get_current_user(conn)
    if user is None:
        conn.close()
        return jsonify({"error": "Please log in"}), 401

    post = conn.execute("SELECT id FROM posts WHERE id = ?", (post_id,)).fetchone()
    if post is None:
        conn.close()
        return jsonify({"error": "Post not found"}), 404

    existing = conn.execute(
        "SELECT 1 FROM post_likes WHERE user_id = ? AND post_id = ?",
        (user["id"], post_id),
    ).fetchone()

    if existing:
        conn.execute(
            "DELETE FROM post_likes WHERE user_id = ? AND post_id = ?",
            (user["id"], post_id),
        )
    else:
        conn.execute(
            "INSERT INTO post_likes (user_id, post_id) VALUES (?, ?)",
            (user["id"], post_id),
        )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/posts/<int:post_id>/comments", methods=["GET"])
def get_comments(post_id):
    conn = get_db()
    user = get_current_user(conn)

    rows = conn.execute(
        """
        SELECT comments.id, comments.user_id, comments.content,
               comments.created_at, users.username
        FROM comments
        JOIN users ON users.id = comments.user_id
        WHERE comments.post_id = ?
        ORDER BY comments.id
        """,
        (post_id,),
    ).fetchall()
    conn.close()

    return jsonify(
        [
            {
                "id": row["id"],
                "username": row["username"],
                "content": row["content"],
                "created_at": row["created_at"].replace(" ", "T") + "Z",
                "mine": user is not None and row["user_id"] == user["id"],
            }
            for row in rows
        ]
    )


@app.route("/api/posts/<int:post_id>/comments", methods=["POST"])
def add_comment(post_id):
    conn = get_db()
    user = get_current_user(conn)
    if user is None:
        conn.close()
        return jsonify({"error": "Please log in"}), 401

    post = conn.execute("SELECT id FROM posts WHERE id = ?", (post_id,)).fetchone()
    if post is None:
        conn.close()
        return jsonify({"error": "Post not found"}), 404

    data = request.get_json()
    content = data.get("content", "").strip()
    if not content:
        conn.close()
        return jsonify({"error": "Comment can't be empty"}), 400
    if len(content) > MAX_COMMENT_LENGTH:
        conn.close()
        return jsonify(
            {"error": f"Comment is too long (max {MAX_COMMENT_LENGTH} characters)"}
        ), 400

    conn.execute(
        "INSERT INTO comments (post_id, user_id, content) VALUES (?, ?, ?)",
        (post_id, user["id"], content),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True}), 201


@app.route("/api/comments/<int:comment_id>", methods=["DELETE"])
def delete_comment(comment_id):
    conn = get_db()
    user = get_current_user(conn)
    if user is None:
        conn.close()
        return jsonify({"error": "Please log in"}), 401

    comment = conn.execute(
        "SELECT user_id FROM comments WHERE id = ?", (comment_id,)
    ).fetchone()
    if comment is None:
        conn.close()
        return jsonify({"error": "Comment not found"}), 404
    if comment["user_id"] != user["id"]:
        conn.close()
        return jsonify({"error": "You can only delete your own comments"}), 403

    conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
    conn.commit()
    conn.close()
    return jsonify({"deleted": comment_id})

@app.route("/api/users/<username>", methods=["GET"])
def get_profile(username):
    conn = get_db()
    me = get_current_user(conn)

    target = conn.execute(
        "SELECT id, username FROM users WHERE username = ?", (username,)
    ).fetchone()
    if target is None:
        conn.close()
        return jsonify({"error": "User not found"}), 404

    followers = conn.execute(
        "SELECT COUNT(*) FROM follows WHERE followed_id = ?", (target["id"],)
    ).fetchone()[0]
    following = conn.execute(
        "SELECT COUNT(*) FROM follows WHERE follower_id = ?", (target["id"],)
    ).fetchone()[0]

    followed_by_me = False
    if me:
        followed_by_me = conn.execute(
            "SELECT 1 FROM follows WHERE follower_id = ? AND followed_id = ?",
            (me["id"], target["id"]),
        ).fetchone() is not None
    conn.close()

    return jsonify(
        {
            "username": target["username"],
            "followers": followers,
            "following": following,
            "followed_by_me": followed_by_me,
            "is_me": me is not None and me["id"] == target["id"],
        }
    )


@app.route("/api/users/<username>/follow", methods=["POST"])
def toggle_follow(username):
    conn = get_db()
    me = get_current_user(conn)
    if me is None:
        conn.close()
        return jsonify({"error": "Please log in"}), 401

    target = conn.execute(
        "SELECT id FROM users WHERE username = ?", (username,)
    ).fetchone()
    if target is None:
        conn.close()
        return jsonify({"error": "User not found"}), 404
    if target["id"] == me["id"]:
        conn.close()
        return jsonify({"error": "You can't follow yourself"}), 400

    existing = conn.execute(
        "SELECT 1 FROM follows WHERE follower_id = ? AND followed_id = ?",
        (me["id"], target["id"]),
    ).fetchone()

    if existing:
        conn.execute(
            "DELETE FROM follows WHERE follower_id = ? AND followed_id = ?",
            (me["id"], target["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO follows (follower_id, followed_id) VALUES (?, ?)",
            (me["id"], target["id"]),
        )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(debug=True, port=5001)
