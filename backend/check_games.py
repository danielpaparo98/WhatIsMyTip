#!/usr/bin/env python3
"""Check games in database by season."""
import sqlite3

conn = sqlite3.connect('whatismytip.db')
cursor = conn.cursor()

print("Games by season:")
cursor.execute('SELECT season, COUNT(*) FROM games GROUP BY season ORDER BY season')
for row in cursor.fetchall():
    print(f'  {row[0]}: {row[1]} games')

print("\nTotal games:")
cursor.execute('SELECT COUNT(*) FROM games')
print(f'  {cursor.fetchone()[0]} total games')

print("\nModel predictions:")
cursor.execute('SELECT COUNT(*) FROM model_predictions')
print(f'  {cursor.fetchone()[0]} predictions')

print("\nTips:")
cursor.execute('SELECT COUNT(*) FROM tips')
print(f'  {cursor.fetchone()[0]} tips')

conn.close()
