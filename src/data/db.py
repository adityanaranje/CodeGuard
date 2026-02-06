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

    try:
        c.execute("ALTER TABLE review_logs ADD COLUMN merged_at TEXT")
    except sqlite3.OperationalError:
        pass # Column likely exists

    try:
        c.execute("ALTER TABLE review_logs ADD COLUMN merged_by TEXT")
    except sqlite3.OperationalError:
        pass # Column likely exists

    try:
        c.execute("ALTER TABLE review_logs ADD COLUMN was_overridden INTEGER DEFAULT 0")
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

def get_recent_reviews(limit=50, repo=None, author=None, deduplicate=False):
    """Get most recent reviews with optional filters."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    if deduplicate:
        # Get only the latest review for each PR
        query = '''
            SELECT * FROM review_logs 
            WHERE id IN (
                SELECT MAX(id) FROM review_logs 
                GROUP BY repo_name, pr_number
            )
        '''
    else:
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
        if deduplicate:
            query += " AND " + " AND ".join(conditions)
        else:
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

    # Calculate Activity Change (Last 7 days vs previous 7 days)
    # Using UTC now for consistency
    now = datetime.now(timezone.utc)
    
    # Last 7 days
    c.execute(f'''
        SELECT COUNT(*) FROM review_logs 
        {where_clause} {" AND " if where_clause else " WHERE "}
        timestamp >= datetime('now', '-7 days')
    ''', params)
    last_7_days = c.fetchone()[0]
    
    # Previous 7 days
    c.execute(f'''
        SELECT COUNT(*) FROM review_logs 
        {where_clause} {" AND " if where_clause else " WHERE "}
        timestamp >= datetime('now', '-14 days') AND timestamp < datetime('now', '-7 days')
    ''', params)
    prev_7_days = c.fetchone()[0]
    
    activity_change = 0
    if prev_7_days > 0:
        activity_change = round(((last_7_days - prev_7_days) / prev_7_days) * 100)
    elif last_7_days > 0:
        activity_change = 100 # New activity

    conn.close()
    
    return {
        "total_reviews": total_reviews,
        "verdict_counts": verdict_counts,
        "author_stats": author_stats,
        "activity_change": activity_change
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

def get_daily_stats(repo=None, author=None):
    """Get daily statistics for trend chart."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
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
        
    # SQLite uses strftime for date extraction
    # We want to group by YYYY-MM-DD
    query = f'''
        SELECT 
            strftime('%Y-%m-%d', timestamp) as date,
            COUNT(*) as total,
            SUM(CASE WHEN verdict = 'PASS' THEN 1 ELSE 0 END) as pass_count,
            SUM(CASE WHEN verdict = 'FAIL' THEN 1 ELSE 0 END) as fail_count,
            SUM(CASE WHEN verdict = 'NEEDS_REVIEW' THEN 1 ELSE 0 END) as needs_review_count
        FROM review_logs
        {where_clause}
        GROUP BY 1
        ORDER BY 1 ASC
        LIMIT 30
    '''
    
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]
    return [dict(row) for row in rows]

def track_merge(repo_name, pr_number, merged_by):
    """
    Track when a PR is merged.
    Returns True if this PR was previously failed by the bot.
    """
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        # Migrations handled in init_db


        # Find the latest review for this PR
        c.execute('''
            SELECT id, verdict FROM review_logs 
            WHERE repo_name = ? AND pr_number = ? 
            ORDER BY id DESC LIMIT 1
        ''', (repo_name, pr_number))
        
        last_review = c.fetchone()
        was_overridden = False

        if last_review:
            # Check if bot failed it (FAIL or CHANGES_REQUESTED)
            if last_review['verdict'] in ('FAIL', 'CHANGES_REQUESTED', 'NEEDS_REVIEW'):
                was_overridden = True
            
            # Update the log
            c.execute('''
                UPDATE review_logs 
                SET merged_at = ?, merged_by = ?, was_overridden = ?
                WHERE id = ?
            ''', (
                datetime.now(timezone.utc).isoformat(),
                merged_by,
                1 if was_overridden else 0,
                last_review['id']
            ))
            
            conn.commit()
            logger.info(f"Tracked merge for PR #{pr_number} (Overridden: {was_overridden})")
            return was_overridden
            
        conn.close()
    except Exception as e:
        logger.error(f"Failed to track merge: {e}")
    return False

def get_override_stats(repo=None):
    """Get statistics on ignored/overridden reviews."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    where_clause = "WHERE was_overridden = 1"
    params = []
    
    if repo:
        where_clause += " AND repo_name = ?"
        params.append(repo)
        
    c.execute(f'SELECT COUNT(*) FROM review_logs {where_clause}', params)
    count = c.fetchone()[0]
    
    # Get details of recent overrides
    c.execute(f'''
        SELECT repo_name, pr_number, author, merged_by, timestamp 
        FROM review_logs {where_clause} 
        ORDER BY id DESC LIMIT 10
    ''', params)
    
    recent_overrides = [dict(zip(['repo_name', 'pr_number', 'author', 'merged_by', 'timestamp'], row)) for row in c.fetchall()]
    
    conn.close()
    return {"count": count, "recent": recent_overrides}
