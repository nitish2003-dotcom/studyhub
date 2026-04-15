import sqlite3

conn = sqlite3.connect("studyhub.db")
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

rows = cursor.execute("SELECT * FROM study_sessions").fetchall()

print("Total rows:", len(rows))

if len(rows) == 0:
    print("No study sessions found")
else:
    for row in rows:
        print(dict(row))

conn.close()