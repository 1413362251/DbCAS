from flask import Flask, render_template
from flask import request
import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "Database", "data.db")

app = Flask(__name__)
import os

@app.route("/")
def home():
    return render_template("home.html")

@app.route('/search')
def search():
    # 连接数据库并获取数据
    keyword = request.args.get('q', '').strip()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if keyword:
        search_columns = [
            'Name', 'Public_Date', 'Data Type', 'Data Format', 'Data Volume',
            'Species', 'Tissues', 'Diseases', 'Source', 'DOI', 'Url', 'Citation',
            'Description'
        ]
        where_clause = " OR ".join([f'"{col}" LIKE ?' for col in search_columns])
        sql = f"SELECT * FROM database_info WHERE {where_clause}"
        params = tuple(['%' + keyword + '%'] * len(search_columns))
        cursor.execute(sql, params)
    else:
        cursor.execute("SELECT * FROM database_info")

    data = cursor.fetchall()
    conn.close()

    return render_template('search.html', query=data,keyword=keyword)

if __name__ == "__main__":
    app.run(debug=True)
