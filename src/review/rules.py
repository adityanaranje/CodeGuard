"""
Rule checker for validating code against company standards.
Uses pattern matching and file analysis to detect violations.
"""

import re
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from enum import Enum

from src.review.parser import FileDiff, ChangeType
from src.config import Config


class Severity(Enum):
    """Severity levels for rule violations."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"
    
    @property
    def emoji(self) -> str:
        """Get emoji for severity level."""
        emojis = {
            "info": "ℹ️",
            "warning": "⚠️",
            "error": "❌",
            "critical": "🚨"
        }
        return emojis.get(self.value, "❓")
    
    @property
    def weight(self) -> int:
        """Get numeric weight for calculations."""
        weights = {
            "info": 1,
            "warning": 2,
            "error": 5,
            "critical": 10
        }
        return weights.get(self.value, 1)


@dataclass
class Violation:
    """Represents a rule violation."""
    rule_name: str
    severity: Severity
    file: str
    line: Optional[int]
    message: str
    matched_content: Optional[str] = None
    
    @property
    def severity_emoji(self) -> str:
        return self.severity.emoji
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_name": self.rule_name,
            "severity": self.severity.value,
            "severity_emoji": self.severity_emoji,
            "file": self.file,
            "line": self.line,
            "message": self.message,
            "matched_content": self.matched_content
        }


class RuleChecker:
    """Checks code against company rules and patterns."""
    
    def __init__(self, config: Config):
        """
        Initialize rule checker.
        
        Args:
            config: Configuration object with rules.
        """
        self.config = config
        self._compiled_patterns: Dict[str, re.Pattern] = {}
    
    def _compile_pattern(self, pattern: str) -> Optional[re.Pattern]:
        """Compile and cache a regex pattern."""
        if pattern not in self._compiled_patterns:
            try:
                self._compiled_patterns[pattern] = re.compile(pattern, re.MULTILINE | re.IGNORECASE)
            except re.error:
                return None
        return self._compiled_patterns[pattern]
    
    def _parse_severity(self, severity_str: str) -> Severity:
        """Parse severity string to enum."""
        try:
            return Severity(severity_str.lower())
        except ValueError:
            return Severity.WARNING
    
    def check_prohibited_patterns(self, diff: FileDiff) -> List[Violation]:
        """
        Check for prohibited patterns in added lines.
        
        Args:
            diff: File diff to check.
            
        Returns:
            List of violations found.
        """
        violations = []
        
        for rule in self.config.prohibited_patterns:
            pattern = self._compile_pattern(rule["pattern"])
            if not pattern:
                continue
            
            severity = self._parse_severity(rule.get("severity", "warning"))
            
            # Check only added lines
            for line_num, content in diff.added_lines:
                match = pattern.search(content)
                if match:
                    violations.append(Violation(
                        rule_name=rule["name"],
                        severity=severity,
                        file=diff.filename,
                        line=line_num,
                        message=rule.get("message", f"Prohibited pattern found: {rule['name']}"),
                        matched_content=match.group(0)[:50] if match else None
                    ))
        
        return violations
    
    def check_file_rules(self, diff: FileDiff) -> List[Violation]:
        """
        Check file-level rules (naming, size, etc.).
        
        Args:
            diff: File diff to check.
            
        Returns:
            List of violations found.
        """
        violations = []
        file_rules = self.config.file_rules
        
        # Check max line length
        max_line_length = file_rules.get("max_line_length", 120)
        for line_num, content in diff.added_lines:
            if len(content) > max_line_length:
                violations.append(Violation(
                    rule_name="Line Length",
                    severity=Severity.WARNING,
                    file=diff.filename,
                    line=line_num,
                    message=f"Line exceeds {max_line_length} characters ({len(content)} chars)"
                ))
        
        # Check naming patterns
        naming_patterns = file_rules.get("naming_patterns", {})
        ext = diff.filename.rsplit(".", 1)[-1] if "." in diff.filename else ""
        
        lang_pattern = None
        if ext == "py":
            lang_pattern = naming_patterns.get("python")
        elif ext in ("js", "jsx", "ts", "tsx"):
            lang_pattern = naming_patterns.get("javascript")
        
        if lang_pattern:
            filename_only = diff.filename.split("/")[-1]
            pattern = self._compile_pattern(lang_pattern)
            if pattern and not pattern.match(filename_only):
                violations.append(Violation(
                    rule_name="File Naming",
                    severity=Severity.INFO,
                    file=diff.filename,
                    line=None,
                    message=f"Filename doesn't match pattern: {lang_pattern}"
                ))
        
        return violations
    
    def check_all_rules(self, diffs: List[FileDiff]) -> List[Violation]:
        """
        Check all rules against all file diffs.
        
        Args:
            diffs: List of file diffs to check.
            
        Returns:
            List of all violations found.
        """
        all_violations = []
        
        for diff in diffs:
            # Skip deleted files
            if diff.status == "removed":
                continue
            
            # Check prohibited patterns
            all_violations.extend(self.check_prohibited_patterns(diff))
            
            # Check file rules
            all_violations.extend(self.check_file_rules(diff))
        
        # Sort by severity (critical first) then file
        all_violations.sort(key=lambda v: (-v.severity.weight, v.file, v.line or 0))
        
        return all_violations
    
    def calculate_violation_score(self, violations: List[Violation]) -> int:
        """
        Calculate total violation score.
        
        Args:
            violations: List of violations.
            
        Returns:
            Total weighted score.
        """
        return sum(v.severity.weight for v in violations)
    
    def has_critical_violations(self, violations: List[Violation]) -> bool:
        """Check if there are any critical violations."""
        return any(v.severity == Severity.CRITICAL for v in violations)
    
    def get_violation_summary(self, violations: List[Violation]) -> Dict[str, int]:
        """
        Get count of violations by severity.
        
        Returns:
            Dict with severity counts.
        """
        summary = {s.value: 0 for s in Severity}
        for v in violations:
            summary[v.severity.value] += 1
        return summary
