#!/usr/bin/env python
"""Script to clean duplicate records from backtest_results and tips tables."""
import sqlite3
from app.config import settings

def clean_backtest_duplicates():
    """Clean duplicate records from backtest_results table."""
    db_url = settings.database_url.replace('sqlite+aiosqlite:///', '')
    conn = sqlite3.connect(db_url)
    cursor = conn.cursor()

    # Check duplicates
    cursor.execute('''
        SELECT season, round_id, heuristic, COUNT(*) as cnt 
        FROM backtest_results 
        GROUP BY season, round_id, heuristic 
        HAVING cnt > 1
    ''')
    duplicates = cursor.fetchall()
    print(f'Found {len(duplicates)} duplicate groups in backtest_results')

    if duplicates:
        # Show some examples
        print('Sample duplicates:', duplicates[:3])

        # Delete duplicates keeping the one with highest id
        cursor.execute('''
            DELETE FROM backtest_results 
            WHERE id NOT IN (
                SELECT MAX(id) 
                FROM backtest_results 
                GROUP BY season, round_id, heuristic
            )
        ''')
        deleted = cursor.rowcount
        conn.commit()
        print(f'Deleted {deleted} duplicate records from backtest_results')

        # Verify
        cursor.execute('''
            SELECT season, round_id, heuristic, COUNT(*) as cnt 
            FROM backtest_results 
            GROUP BY season, round_id, heuristic 
            HAVING cnt > 1
        ''')
        remaining = cursor.fetchall()
        print(f'Remaining duplicates: {len(remaining)}')

    conn.close()
    return len(duplicates)

def clean_tips_duplicates():
    """Clean duplicate records from tips table."""
    db_url = settings.database_url.replace('sqlite+aiosqlite:///', '')
    conn = sqlite3.connect(db_url)
    cursor = conn.cursor()

    # Check duplicates
    cursor.execute('''
        SELECT game_id, heuristic, COUNT(*) as cnt 
        FROM tips 
        GROUP BY game_id, heuristic 
        HAVING cnt > 1
    ''')
    duplicates = cursor.fetchall()
    print(f'Found {len(duplicates)} duplicate groups in tips')

    if duplicates:
        # Show some examples
        print('Sample duplicates:', duplicates[:3])

        # Delete duplicates keeping the one with highest id
        cursor.execute('''
            DELETE FROM tips 
            WHERE id NOT IN (
                SELECT MAX(id) 
                FROM tips 
                GROUP BY game_id, heuristic
            )
        ''')
        deleted = cursor.rowcount
        conn.commit()
        print(f'Deleted {deleted} duplicate records from tips')

        # Verify
        cursor.execute('''
            SELECT game_id, heuristic, COUNT(*) as cnt 
            FROM tips 
            GROUP BY game_id, heuristic 
            HAVING cnt > 1
        ''')
        remaining = cursor.fetchall()
        print(f'Remaining duplicates: {len(remaining)}')

    conn.close()
    return len(duplicates)

if __name__ == '__main__':
    print('Cleaning duplicate records...')
    print('=' * 50)
    
    backtest_count = clean_backtest_duplicates()
    print()
    tips_count = clean_tips_duplicates()
    
    print('=' * 50)
    print(f'Total duplicate groups cleaned: {backtest_count + tips_count}')
    print('Done!')
