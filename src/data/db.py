import sqlite3
import json
import logging
from datetime import datetime, timezone

DB_NAME = "pr_reviews.db"
logger = logging.getLogger(__name__)

def init_db():
    """Initialize the database with the required table."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS review_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            repo_name TEXT,
            pr_number INTEGER,
            author TEXT,
            verdict TEXT,
            violations_count INTEGER,
            llm_severity INTEGER,
            comment TEXT,
            total_changes INTEGER DEFAULT 0,
            files_changed INTEGER DEFAULT 0
        )
    ''')
    
    # Migrations for existing DBs
    try:
        c.execute("ALTER TABLE review_logs ADD COLUMN total_changes INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass # Column likely exists
        
    try:
        c.execute("ALTER TABLE review_logs ADD COLUMN files_changed INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass # Column likely exists
        
    conn.commit()
    conn.close()
    logger.info("Database initialized.")

def log_review(repo_name, pr_number, author, verdict, violations_count, llm_severity, comment, total_changes=0, files_changed=0):
    """Log a PR review result."""
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('''
            INSERT INTO review_logs 
            (timestamp, repo_name, pr_number, author, verdict, violations_count, llm_severity, comment, total_changes, files_changed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            datetime.now(timezone.utc).isoformat(),
            repo_name,
            pr_number,
            author,
            verdict,
            violations_count,
            llm_severity,
            comment,
            total_changes,
            files_changed
        ))
        conn.commit()
        conn.close()
        logger.info(f"Logged review for PR #{pr_number}")
    except Exception as e:
        logger.error(f"Failed to log review: {e}")

def get_recent_reviews(limit=50, repo=None, author=None):
    """Get most recent reviews with optional filters."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    query = 'SELECT * FROM review_logs'
    params = []
    conditions = []
    
    if repo:
        conditions.append("repo_name = ?")
        params.append(repo)
    if author:
        conditions.append("author = ?")
        params.append(author)
        
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
        
    query += ' ORDER BY id DESC LIMIT ?'
    params.append(limit)
    
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_stats(repo=None, author=None):
    """Get statistics for the dashboard with optional filters."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    where_clause = ""
    params = []
    conditions = []
    
    if repo:
        conditions.append("repo_name = ?")
        params.append(repo)
    if author:
        conditions.append("author = ?")
        params.append(author)
    
    if conditions:
        where_clause = " WHERE " + " AND ".join(conditions)
    
    # Total reviews
    c.execute(f'SELECT COUNT(*) FROM review_logs{where_clause}', params)
    total_reviews = c.fetchone()[0]
    
    # Verdict counts
    c.execute(f'SELECT verdict, COUNT(*) FROM review_logs{where_clause} GROUP BY verdict', params)
    verdict_counts = dict(c.fetchall())
    
    # Author stats (for chart)
    c.execute(f'SELECT author, COUNT(*) as count FROM review_logs{where_clause} GROUP BY author ORDER BY count DESC LIMIT 10', params)
    author_stats = dict(c.fetchall())

    conn.close()
    
    return {
        "total_reviews": total_reviews,
        "verdict_counts": verdict_counts,
        "author_stats": author_stats
    }

def get_filter_options():
    """Get unique repos and authors for filters."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute("SELECT DISTINCT repo_name FROM review_logs ORDER BY repo_name")
    repos = [row[0] for row in c.fetchall() if row[0]]
    
    c.execute("SELECT DISTINCT author FROM review_logs ORDER BY author")
    authors = [row[0] for row in c.fetchall() if row[0]]
    
    conn.close()
    return {"repos": repos, "authors": authors}
