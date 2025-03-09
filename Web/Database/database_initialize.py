import sqlite3
import random

# Connect to SQLite database (or create it if it doesn't exist)
conn = sqlite3.connect('data.db')
cursor = conn.cursor()

# Create table with the given schema
cursor.execute('''
CREATE TABLE IF NOT EXISTS data_table (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    Analysis TEXT NOT NULL,
    Web_Interface TEXT NOT NULL,
    Reference TEXT NOT NULL,
    Species TEXT NOT NULL,
    Age TEXT NOT NULL,
    Sample TEXT NOT NULL,
    Method TEXT NOT NULL,
    Isoform TEXT NOT NULL,
    Accession TEXT NOT NULL
)
''')

# Commit the table creation
conn.commit()

# Insert 3 random rows of data
random_data = [
    ("Analysis A", "Interface 1", "Ref 1", "Human", f"{random.randint(1, 100)} years", "Sample X", "Method 1", "Isoform 1", f"Acc-{random.randint(1000, 9999)}"),
    ("Analysis B", "Interface 2", "Ref 2", "Mouse", f"{random.randint(1, 100)} weeks", "Sample Y", "Method 2", "Isoform 2", f"Acc-{random.randint(1000, 9999)}"),
    ("Analysis C", "Interface 3", "Ref 3", "Rat", f"{random.randint(1, 100)} months", "Sample Z", "Method 3", "Isoform 3", f"Acc-{random.randint(1000, 9999)}")
]

cursor.executemany('''
INSERT INTO data_table (Analysis, Web_Interface, Reference, Species, Age, Sample, Method, Isoform, Accession)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
''', random_data)

# Commit the data insertion
conn.commit()

# Query the inserted data to verify
cursor.execute('SELECT * FROM data_table')
rows = cursor.fetchall()

# Close the connection
cursor.close()
conn.close()
