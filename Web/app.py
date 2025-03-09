from flask import Flask, render_template
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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM database_info")  # 替换 your_table_name 为你的实际表名
    data = cursor.fetchall()
    conn.close()

    return render_template('search.html', query=data)

if __name__ == "__main__":
    app.run(debug=True)
