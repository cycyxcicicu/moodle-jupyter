import sqlite3
import sys

conn = sqlite3.connect('/app/assignment_service.db')
cursor = conn.cursor()

print("--- ACTIVE SESSIONS ---")
cursor.execute("SELECT * FROM active_sessions")
for row in cursor.fetchall():
    print(row)

print("--- SUBMISSIONS ---")
cursor.execute("SELECT * FROM submissions")
for row in cursor.fetchall():
    print(row)
