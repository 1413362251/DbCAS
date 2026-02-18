from flask import Flask, render_template, request
from pathlib import Path
import sqlite3

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
DB_PATH = PROJECT_ROOT / "database" / "data.db"

app = Flask(__name__)

def split_text(value, sep=";"):
    if value is None:
        return []
    return [part for part in str(value).split(sep)]

app.jinja_env.filters["split"] = split_text

def load_display_config():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='display_columns'"
    )
    has_display_table = cursor.fetchone() is not None
    if has_display_table:
        cursor.execute("PRAGMA table_info(display_columns)")
        display_cols = {row[1] for row in cursor.fetchall()}
        has_data_type = "data_type" in display_cols
        cursor.execute(
            """
            SELECT column_name, display_name, display_group, order_index, is_access
            {data_type_col}
            FROM display_columns
            ORDER BY order_index
            """.format(
                data_type_col=", data_type" if has_data_type else ""
            )
        )
        rows = cursor.fetchall()
        columns = [
            {
                "name": row["column_name"],
                "label": row["display_name"],
                "group": row["display_group"],
                "is_access": row["is_access"],
                "data_type": row["data_type"] if has_data_type else None,
            }
            for row in rows
        ]
    else:
        cursor.execute("PRAGMA table_info(database_info)")
        rows = cursor.fetchall()
        columns = [
            {
                "name": row[1],
                "label": row[1],
                "group": "main",
            }
            for row in rows
        ]
    conn.close()
    for col in columns:
        data_type = (col.get("data_type") or "").lower()
        if data_type == "t-word-url":
            col["render"] = "url"
        elif data_type == "t-word-doi":
            col["render"] = "doi"
        elif data_type == "t-bool-access":
            col["render"] = "access"
        else:
            col["render"] = "text"
    main_cols = [col for col in columns if col["group"] == "main"]
    expand_cols = [col for col in columns if col["group"] == "expand"]
    all_cols = [col["name"] for col in columns]
    return main_cols, expand_cols, all_cols

@app.route("/")
def home():
    return render_template("home.html")

@app.route('/search')
def search():
    # Connect to database and fetch data.
    keyword = request.args.get('q', '').strip()
    main_cols, expand_cols, all_cols = load_display_config()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    if keyword:
        where_clause = " OR ".join([f'"{col}" LIKE ?' for col in all_cols])
        sql = f"SELECT {', '.join([f'\"{col}\"' for col in all_cols])} FROM database_info WHERE {where_clause}"
        params = tuple(['%' + keyword + '%'] * len(all_cols))
        cursor.execute(sql, params)
    else:
        cursor.execute(f"SELECT {', '.join([f'\"{col}\"' for col in all_cols])} FROM database_info")

    data = cursor.fetchall()
    conn.close()

    tag_columns = [
        col["name"]
        for col in main_cols + expand_cols
        if (col.get("data_type") or "").lower() == "t-word-tag"
    ]
    tag_options = {col: set() for col in tag_columns}
    for row in data:
        for col in tag_columns:
            value = row[col]
            if value is None:
                continue
            for tag in str(value).split(";"):
                tag_text = tag.strip()
                if tag_text:
                    tag_options[col].add(tag_text)
    tag_options = {
        col: sorted(tags, key=str.lower)[:50]
        for col, tags in tag_options.items()
    }

    return render_template(
        'search.html',
        query=data,
        keyword=keyword,
        main_columns=main_cols,
        expand_columns=expand_cols,
        tag_options=tag_options,
        main_colspan=len(main_cols) + 1,
    )

if __name__ == "__main__":
    app.run(debug=True)
