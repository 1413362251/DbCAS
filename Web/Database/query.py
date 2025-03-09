import sqlite3

# 连接到数据库
db_path = f"D:\File\DbCAS\Web\data.db"  # 替换为你的 db 文件路径
connection = sqlite3.connect(db_path)
cursor = connection.cursor()

# 查询数据
cursor.execute('SELECT * FROM data_table')
rows = cursor.fetchall()
print("查询结果：")
for row in rows:
    print(row)


# 关闭连接
cursor.close()
connection.close()