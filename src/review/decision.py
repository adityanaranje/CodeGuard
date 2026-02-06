"""
Decision engine that combines rule checks and LLM review to make final verdicts.
"""

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional
from pathlib import Path

from jinja2 import Template

from src.config import Config
from src.review.rules import Violation, Severity, RuleChecker
from src.review.service import LLMReview


class Verdict(Enum):
    """Final PR verdict."""
    PASS = "PASS"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAIL = "FAIL"
    
    @property
    def emoji(self) -> str:
        emojis = {
            "PASS": "✅",
            "NEEDS_REVIEW": "⚠️",
            "FAIL": "❌"
        }
        return emojis.get(self.value, "❓")
    
    @property
    def github_status(self) -> str:
        """Get corresponding GitHub commit status."""
        statuses = {
            "PASS": "success",
            "NEEDS_REVIEW": "pending",
            "FAIL": "failure"
        }
        return statuses.get(self.value, "pending")


@dataclass
class ReviewDecision:
    """Complete review decision with all information."""
    verdict: Verdict
    rule_violations: List[Violation]
    llm_review: LLMReview
    violation_score: int
    reasons: List[str]


class DecisionEngine:
    """Makes final pass/fail decisions based on all review data."""
    
    def __init__(self, config: Config):
        """
        Initialize decision engine.
        
        Args:
            config: Configuration object with thresholds.
        """
        self.config = config
        self._template: Optional[Template] = None
    
    def decide(
        self,
        violations: List[Violation],
        llm_review: LLMReview,
        rule_checker: RuleChecker
    ) -> ReviewDecision:
        """
        Make final decision based on rule violations and LLM review.
        
        Args:
            violations: List of rule violations.
            llm_review: LLM review result.
            rule_checker: Rule checker for scoring.
            
        Returns:
            ReviewDecision with verdict and reasons.
        """
        reasons = []
        violation_score = rule_checker.calculate_violation_score(violations)
        has_critical = rule_checker.has_critical_violations(violations)
        
        # Determine verdict based on rules and LLM
        verdict = Verdict.PASS
        
        # Check for any AI-detected bugs
        has_bugs = any(i.get("type", "").lower() == "bug" for i in llm_review.issues)
        
        # Check for high-severity issues from LLM
        high_severity_issues = [
            i for i in llm_review.issues
            if i.get("severity", "").lower() in ("high", "critical")
        ]

        # 1. FAIL - Highest Priority
        if has_critical:
            verdict = Verdict.FAIL
            critical_count = sum(1 for v in violations if v.severity == Severity.CRITICAL)
            reasons.append(f"Found {critical_count} critical violation(s)")
        
        elif len(violations) >= self.config.min_violations_to_fail:
            verdict = Verdict.FAIL
            reasons.append(f"Exceeded violation threshold ({len(violations)} >= {self.config.min_violations_to_fail})")
        
        elif llm_review.severity_score >= self.config.llm_severity_threshold:
            verdict = Verdict.FAIL
            reasons.append(f"LLM severity score too high ({llm_review.severity_score}/10)")
            
        elif any(i.get("severity", "").lower() == "critical" for i in llm_review.issues):
            verdict = Verdict.FAIL
            reasons.append("LLM found critical severity issues")

        # 2. NEEDS_REVIEW - Second Priority
        if verdict != Verdict.FAIL:
            if llm_review.security_concerns:
                verdict = Verdict.NEEDS_REVIEW
                reasons.append(f"LLM flagged {len(llm_review.security_concerns)} security concern(s)")
            
            elif high_severity_issues:
                verdict = Verdict.NEEDS_REVIEW
                reasons.append(f"LLM found {len(high_severity_issues)} high-severity issue(s)")
            
            elif has_bugs:
                verdict = Verdict.NEEDS_REVIEW
                reasons.append("LLM detected potential bugs in the logic")
        
        # 3. TECHNICAL FAILURE - Upgrade to NEEDS_REVIEW if LLM failed
        if "Review failed" in llm_review.summary:
            if verdict == Verdict.PASS: # Only upgrade if it was going to PASS
                verdict = Verdict.NEEDS_REVIEW
                reasons.append("LLM review failed to process; manual verification recommended")
        
        # If still passing but has warnings
        if verdict == Verdict.PASS and violations:
            reasons.append(f"Passed with {len(violations)} minor warning(s)")
        elif verdict == Verdict.PASS and not violations:
            reasons.append("All checks passed")
        
        return ReviewDecision(
            verdict=verdict,
            rule_violations=violations,
            llm_review=llm_review,
            violation_score=violation_score,
            reasons=reasons
        )
    
    def format_comment(
        self,
        decision: ReviewDecision,
        repo_name: str,
        pr_number: int,
        pr_title: str,
        pr_author: str,
        generated_description: Optional[str] = None
    ) -> str:
        """
        Format the complete review as a PR comment.
        
        Args:
            decision: Review decision object.
            repo_name: Repository name.
            pr_number: PR number.
            pr_title: PR title.
            pr_author: PR author username.
            
        Returns:
            Formatted markdown comment.
        """
        # Build comment sections
        lines = []
        
        # Header
        lines.append("# 🔍 Automated Code Review\n")
        lines.append(f"**Repository:** {repo_name}")
        lines.append(f"**Pull Request:** #{pr_number} - {pr_title}")
        lines.append(f"**Author:** @{pr_author}\n")
        lines.append("---\n")
        
        # Generated PR Description (if applicable)
        if generated_description:
            lines.append("## 📝 Generated PR Description\n")
            lines.append("> **Note:** The PR body was empty, so we generated this description for you:\n")
            lines.append(generated_description)
            lines.append("\n---\n")
        
        # Rule Check Results
        lines.append("## 📋 Rule Check Results\n")
        
        if decision.rule_violations:
            lines.append(f"### ⚠️ Violations Found ({len(decision.rule_violations)})\n")
            lines.append("| Severity | Rule | File | Line | Message |")
            lines.append("|----------|------|------|------|---------|")
            
            for v in decision.rule_violations[:20]:  # Limit to 20 violations
                line_str = str(v.line) if v.line else "-"
                lines.append(
                    f"| {v.severity_emoji} {v.severity.value.upper()} | {v.rule_name} | "
                    f"`{v.file}` | {line_str} | {v.message} |"
                )
            
            if len(decision.rule_violations) > 20:
                lines.append(f"\n*...and {len(decision.rule_violations) - 20} more violations*")
            lines.append("")
        else:
            lines.append("### ✅ No Rule Violations\n")
            lines.append("All company coding standards have been met!\n")
        
        lines.append("---\n")
        
        # LLM Review
        lines.append("## 🤖 LLM Code Review\n")
        from src.review.service import LLMReviewer
        llm_markdown = LLMReviewer(self.config).format_review_markdown(decision.llm_review)
        lines.append(llm_markdown)
        
        lines.append("---\n")
        
        # Final Verdict
        lines.append("## 📊 Final Verdict\n")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Rule Violations | {len(decision.rule_violations)} |")
        lines.append(f"| LLM Severity Score | {decision.llm_review.severity_score}/10 |")
        lines.append(f"| **Status** | {decision.verdict.emoji} **{decision.verdict.value}** |\n")
        
        # Verdict explanation
        if decision.reasons:
            lines.append("**Reasons:**")
            for reason in decision.reasons:
                lines.append(f"- {reason}")
            lines.append("")
        
        # Verdict-specific message
        if decision.verdict == Verdict.FAIL:
            lines.append("> ⛔ **This PR requires changes before it can be merged.**\n")
        elif decision.verdict == Verdict.NEEDS_REVIEW:
            lines.append("> ⚠️ **This PR requires manual review before merging.**\n")
        else:
            lines.append("> ✅ **This PR is approved for merging.**\n")
        
        # Add action buttons
        lines.append("### 🎯 Quick Actions\n")
        lines.append("**Maintainers:** Use these buttons to take action on this PR:\n")
        lines.append("")
        lines.append("| Action | Command |")
        lines.append("|--------|---------|")
        lines.append("| ✅ Approve & Merge | `/approve` |")
        lines.append("| ❌ Reject PR | `/reject` |")
        lines.append("| 🔄 Request Changes | `/request-changes` |")
        lines.append("| 💬 Comment Only | Reply normally |")
        lines.append("")
        lines.append("*Note: These commands can be used in a comment to trigger the corresponding action.*\n")
        
        lines.append("---\n")
        lines.append("<sub>🤖 Automated review by GitHub PR Review Bot | Powered by Groq LLM</sub>")
        
        return "\n".join(lines)
