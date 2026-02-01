"""
Configuration loader for the GitHub PR Review System.
Handles loading environment variables and YAML rule configurations.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from dotenv import load_dotenv


class Config:
    """Configuration manager for the PR review system."""
    
    def __init__(self, env_path: Optional[str] = None, rules_path: Optional[str] = None):
        """
        Initialize configuration.
        
        Args:
            env_path: Path to .env file. Defaults to project root.
            rules_path: Path to rules.yaml. Defaults to config/rules.yaml.
        """
        # Determine project root
        self.project_root = Path(__file__).parent.parent
        
        # Load environment variables
        if env_path:
            load_dotenv(env_path)
        else:
            load_dotenv(self.project_root / ".env")
        
        # Load rules configuration
        if rules_path:
            self.rules_path = Path(rules_path)
        else:
            self.rules_path = self.project_root / "config" / "rules.yaml"
        
        self._rules: Optional[Dict[str, Any]] = None
    
    @property
    def github_app_id(self) -> Optional[int]:
        """Get GitHub App ID from environment."""
        app_id = os.getenv("GITHUB_APP_ID")
        return int(app_id) if app_id else None

    @property
    def github_private_key(self) -> Optional[str]:
        """Get GitHub Private Key content."""
        # Try raw content first (better for Render/Cloud)
        raw_key = os.getenv("GITHUB_PRIVATE_KEY")
        if raw_key:
            return raw_key.replace("\\n", "\n")  # Handle escaped newlines

        # Try file path
        path = os.getenv("GITHUB_PRIVATE_KEY_PATH")
        if not path:
            return None
            
        key_path = Path(path)
        if not key_path.exists():
            # Try relative to project root
            key_path = self.project_root / path
            
        if not key_path.exists():
             raise FileNotFoundError(f"Private key not found at {path}")
             
        with open(key_path, "r", encoding="utf-8") as f:
            return f.read()

    @property
    def github_token(self) -> str:
        """Get GitHub token (legacy/PAT mode)."""
        token = os.getenv("GITHUB_TOKEN")
        # Token is optional now if using App mode
        return token or ""
    
    @property
    def github_webhook_secret(self) -> Optional[str]:
        """Get GitHub webhook secret from environment."""
        return os.getenv("GITHUB_WEBHOOK_SECRET")
    
    @property
    def groq_api_key(self) -> str:
        """Get Groq API key from environment."""
        key = os.getenv("GROQ_API_KEY")
        if not key:
            raise ValueError("GROQ_API_KEY environment variable is required")
        return key
    
    @property
    def groq_model(self) -> str:
        """Get default Groq model name from environment."""
        return os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    
    @property
    def groq_small_model(self) -> str:
        """Get small Groq model for minor changes."""
        return os.getenv("GROQ_SMALL_MODEL", "llama-3.1-8b-instant")
    
    @property
    def groq_large_model(self) -> str:
        """Get large Groq model for significant changes."""
        return os.getenv("GROQ_LARGE_MODEL", "llama-3.3-70b-versatile")
    
    @property
    def llm_change_threshold(self) -> int:
        """Get line change threshold for switching models."""
        return int(os.getenv("LLM_CHANGE_THRESHOLD", "150"))

    @property
    def enable_rag(self) -> bool:
        """Check if RAG (Deep Context) is enabled."""
        return os.getenv("ENABLE_RAG", "false").lower() == "true"
    
    @property
    def webhook_port(self) -> int:
        """Get webhook server port."""
        return int(os.getenv("WEBHOOK_PORT", "5000"))
    
    @property
    def langsmith_tracing(self) -> bool:
        """Check if LangSmith tracing is enabled."""
        return os.getenv("LANGCHAIN_TRACING_V2", "false").lower() == "true"

    @property
    def langsmith_project(self) -> str:
        """Get LangSmith project name."""
        return os.getenv("LANGCHAIN_PROJECT", "github-pr-reviewer")
    
    @property
    def rules(self) -> Dict[str, Any]:
        """Load and cache rules configuration."""
        if self._rules is None:
            self._rules = self._load_rules()
        return self._rules
    
    def _load_rules(self) -> Dict[str, Any]:
        """Load rules from YAML file."""
        if not self.rules_path.exists():
            raise FileNotFoundError(f"Rules file not found: {self.rules_path}")
        
        with open(self.rules_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    
    @property
    def settings(self) -> Dict[str, Any]:
        """Get settings section from rules."""
        return self.rules.get("settings", {})
    
    @property
    def strictness(self) -> str:
        """Get strictness level."""
        return self.settings.get("strictness", "moderate")
    
    @property
    def min_violations_to_fail(self) -> int:
        """Get minimum violations needed to fail a PR."""
        return self.settings.get("min_violations_to_fail", 3)
    
    @property
    def llm_severity_threshold(self) -> int:
        """Get LLM severity threshold to fail."""
        return self.settings.get("llm_severity_threshold", 7)
    
    @property
    def prohibited_patterns(self) -> List[Dict[str, Any]]:
        """Get prohibited patterns list."""
        return self.rules.get("prohibited_patterns", [])
    
    @property
    def required_patterns(self) -> List[Dict[str, Any]]:
        """Get required patterns list."""
        return self.rules.get("required_patterns", [])
    
    @property
    def file_rules(self) -> Dict[str, Any]:
        """Get file rules."""
        return self.rules.get("file_rules", {})
    
    @property
    def llm_review_focus(self) -> List[str]:
        """Get LLM review focus areas."""
        return self.rules.get("llm_review_focus", [])


# Global config instance
_config: Optional[Config] = None


def get_config(env_path: Optional[str] = None, rules_path: Optional[str] = None) -> Config:
    """Get or create global config instance."""
    global _config
    if _config is None:
        _config = Config(env_path, rules_path)
    return _config
