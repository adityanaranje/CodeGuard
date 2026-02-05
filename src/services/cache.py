"""
Caching service for PR reviews to reduce redundant LLM calls.
Uses SQLite to store reviews by diff hash.
"""

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime


class ReviewCache:
    """Cache for storing and retrieving PR reviews."""
    
    def __init__(self, db_path: str = "pr_reviews.db"):
        """
        Initialize the review cache.
        
        Args:
            db_path: Path to SQLite database file.
        """
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize database schema if it doesn't exist."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS review_cache (
                diff_hash TEXT PRIMARY KEY,
                review_data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                access_count INTEGER DEFAULT 1
            )
        """)
        
        conn.commit()
        conn.close()
    
    def hash_diff(self, diff_text: str) -> str:
        """
        Create SHA256 hash of diff text.
        
        Args:
            diff_text: The diff content to hash.
            
        Returns:
            Hexadecimal hash string.
        """
        return hashlib.sha256(diff_text.encode('utf-8')).hexdigest()
    
    def get(self, diff_hash: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve cached review by diff hash.
        
        Args:
            diff_hash: Hash of the diff.
            
        Returns:
            Review data dict if found, None otherwise.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT review_data FROM review_cache WHERE diff_hash = ?",
            (diff_hash,)
        )
        
        result = cursor.fetchone()
        
        if result:
            # Update access stats
            cursor.execute("""
                UPDATE review_cache 
                SET accessed_at = CURRENT_TIMESTAMP, 
                    access_count = access_count + 1
                WHERE diff_hash = ?
            """, (diff_hash,))
            conn.commit()
            
            conn.close()
            return json.loads(result[0])
        
        conn.close()
        return None
    
    def set(self, diff_hash: str, review_data: Dict[str, Any]):
        """
        Store review in cache.
        
        Args:
            diff_hash: Hash of the diff.
            review_data: Review data to store.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO review_cache (diff_hash, review_data, created_at, accessed_at)
            VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """, (diff_hash, json.dumps(review_data)))
        
        conn.commit()
        conn.close()
    
    def clear_old_entries(self, days: int = 30):
        """
        Clear cache entries older than specified days.
        
        Args:
            days: Number of days to keep.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            DELETE FROM review_cache 
            WHERE accessed_at < datetime('now', '-' || ? || ' days')
        """, (days,))
        
        conn.commit()
        conn.close()
