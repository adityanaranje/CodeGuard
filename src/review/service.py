"""
LLM-powered code reviewer using Groq.
Analyzes code changes and provides intelligent feedback.
"""

from dataclasses import dataclass
from typing import List, Dict, Any, Optional
import json
# from groq import Groq # removed

from src.config import Config
from src.review.parser import FileDiff, DiffParser
from src.agent.graph import Agent


@dataclass
class LLMReview:
    """Represents the LLM review result."""
    summary: str
    severity_score: int  # 1-10
    issues: List[Dict[str, Any]]
    suggestions: List[str]
    security_concerns: List[str]
    positive_feedback: List[str]
    raw_response: str
    generated_tests: Optional[str] = None
    dependency_warnings: List[str] = None


class LLMReviewer:
    """LLM-powered code review using Groq Agent."""
    
    def __init__(self, config: Config):
        """
        Initialize LLM reviewer.
        
        Args:
            config: Configuration object with API keys.
        """
        self.config = config
        # Initialize the LangGraph Agent
        self.agent = Agent(config) 
        self.diff_parser = DiffParser()
    
    def generate_pr_description(self, diffs: List[FileDiff], pr_title: str) -> str:
        """
        Generate a PR description from the diff when the body is empty.
        
        Args:
            diffs: List of file diffs.
            pr_title: The PR title.
            
        Returns:
            Generated description string.
        """
        from langchain_groq import ChatGroq
        from langchain_core.messages import SystemMessage, HumanMessage
        
        # Format diff for analysis
        diff_text = self.diff_parser.format_for_review(diffs, max_lines=500)
        
        # Use a simple model for this task
        model = ChatGroq(
            api_key=self.config.groq_api_key,
            model_name=self.config.groq_small_model,
            temperature=0.3
        )
        
        system_prompt = """You are a technical writer helping developers write clear PR descriptions.
        
        Given a PR title and the code diff, generate a concise PR description that includes:
        1. A brief summary (1-2 sentences)
        2. Key changes (bullet points)
        3. Impact/rationale (if evident from the changes)
        
        Keep it professional and concise. Use markdown formatting."""
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"PR Title: {pr_title}\n\nCode Changes:\n{diff_text}")
        ]
        
        response = model.invoke(messages)
        return response.content
    
    def review(
        self,
        diffs: List[FileDiff],
        pr_title: str,
        pr_body: Optional[str] = None
    ) -> LLMReview:
        """
        Review code changes using Agent.
        """
        # Filter out non-code files
        code_diffs = [d for d in diffs if self._is_code_file(d.filename)]
        
        if not code_diffs:
            return LLMReview(
                summary="No code files to review.",
                severity_score=1,
                issues=[],
                suggestions=[],
                security_concerns=[],
                positive_feedback=[],
                raw_response="",
                generated_tests=None
            )
        
        # Run dependency analysis
        from src.analysis.dependencies import DependencyAnalyzer
        dependency_warnings = []
        try:
            analyzer = DependencyAnalyzer(repo_path=".")
            dependency_warnings = analyzer.analyze_impact(code_diffs)
        except Exception as e:
            print(f"Dependency analysis failed: {e}")
        
        # Format the diff for the agent
        diff_text = self.diff_parser.format_for_review(code_diffs, max_lines=1000)
        
        # Use '.' as repo path for now (current working directory)
        # In a real deployment, we might need a more dynamic path if processing multiple repos locally
        repo_path = "."
        
        try:
            # 5. Determine which Groq model to use based on PR size
            total_changes = sum(d.additions + d.deletions for d in code_diffs)
            model_to_use = self.config.groq_large_model
            
            if total_changes < self.config.llm_change_threshold:
                model_to_use = self.config.groq_small_model
                print(f"⚡ Small PR detected ({total_changes} lines). Using fast model: {model_to_use}")
            else:
                print(f"🧠 Large PR detected ({total_changes} lines). Using powerful model: {model_to_use}")

            # Call Agent Orchestrator
            raw_response = self.agent.review_pr(
                pr_diff=diff_text, 
                repo_path=repo_path,
                model_name=model_to_use,
                task_description=pr_body
            )
            
            # Parse the response (now returns JSON with code_review and generated_tests)
            agent_result = json.loads(raw_response)
            code_review_str = agent_result.get("code_review", "")
            generated_tests_str = agent_result.get("generated_tests", "")
            
            # Clean up potential markdown JSON from code_review
            clean_response = code_review_str
            if "```json" in clean_response:
                clean_response = clean_response.split("```json")[1].split("```")[0].strip()
            elif "```" in clean_response:
                 clean_response = clean_response.split("```")[1].strip()

            review_data = json.loads(clean_response)
            
            return LLMReview(
                summary=review_data.get("summary", "Review completed."),
                severity_score=min(10, max(1, review_data.get("severity_score", 5))),
                issues=review_data.get("issues", []),
                suggestions=review_data.get("suggestions", []),
                security_concerns=review_data.get("security_concerns", []),
                positive_feedback=review_data.get("positive_feedback", []),
                raw_response=raw_response,
                generated_tests=generated_tests_str,
                dependency_warnings=dependency_warnings
            )
            
        except Exception as e:
            return LLMReview(
                summary=f"Review failed: {str(e)}",
                severity_score=5,
                issues=[],
                suggestions=[],
                security_concerns=[],
                positive_feedback=[],
                raw_response=""
            )
    
    def _is_code_file(self, filename: str) -> bool:
        """Check if a file is a code file worth reviewing."""
        code_extensions = {
            "py", "js", "jsx", "ts", "tsx", "java", "go", "rs", "rb",
            "php", "c", "cpp", "h", "hpp", "cs", "swift", "kt", "scala",
            "sql", "sh", "bash"
        }
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        return ext in code_extensions
    
    def format_review_markdown(self, review: LLMReview) -> str:
        """
        Format the LLM review as markdown for PR comment.
        
        Args:
            review: LLMReview object.
            
        Returns:
            Formatted markdown string.
        """
        lines = []
        
        # Summary
        lines.append(f"### Summary\n{review.summary}\n")
        
        # Security concerns (priority)
        if review.security_concerns:
            lines.append("### 🔒 Security Concerns")
            for concern in review.security_concerns:
                lines.append(f"- {concern}")
            lines.append("")
        
        # Issues
        if review.issues:
            lines.append("### 🔍 Issues Found")
            for issue in review.issues:
                severity_emoji = {"low": "🟡", "medium": "🟠", "high": "🔴", "critical": "🚨"}.get(
                    issue.get("severity", "medium").lower(), "🟡"
                )
                file_line = ""
                if issue.get("file"):
                    file_line = f" in `{issue['file']}`"
                    if issue.get("line"):
                        file_line += f" (line {issue['line']})"
                
                lines.append(f"- {severity_emoji} **{issue.get('type', 'issue').title()}**{file_line}")
                lines.append(f"  - {issue.get('description', 'No description')}")
                if issue.get("suggestion"):
                    lines.append(f"  - 💡 *Suggestion:* {issue['suggestion']}")
            lines.append("")
        
        # Suggestions
        if review.suggestions:
            lines.append("### 💡 Suggestions")
            for suggestion in review.suggestions:
                lines.append(f"- {suggestion}")
            lines.append("")
        
        # Dependency Warnings
        if review.dependency_warnings:
            lines.append("### 🔗 Dependency Impact Analysis")
            lines.append("The following files may be affected by your changes:")
            for warning in review.dependency_warnings:
                lines.append(f"{warning}")
            lines.append("")
        
        # Positive feedback
        if review.positive_feedback:
            lines.append("### ✅ What's Good")
            for feedback in review.positive_feedback:
                lines.append(f"- {feedback}")
            lines.append("")

        # AI Fix Prompt
        if review.issues:
            lines.append("### 🛠️ AI Fix Prompt")
            lines.append("Use this prompt with your preferred AI assistant to fix the identified issues:")
            lines.append("```text")
            lines.append("I have the following code issues identified in a PR review. Please provide the corrected code based on these findings.")
            lines.append("")
            lines.append("Issues:")
            for issue in review.issues:
                file_info = f" in {issue.get('file', 'unknown')}"
                if issue.get('line'):
                    file_info += f":{issue['line']}"
                
                lines.append(f"- {issue.get('type', 'issue').title()}{file_info}: {issue.get('description', '')}")
                if issue.get('suggestion'):
                    lines.append(f"  Suggestion: {issue['suggestion']}")
            lines.append("```")
            lines.append("")
        
        # Generated Tests
        if review.generated_tests and review.generated_tests.strip() and review.generated_tests != "NO_TESTS_NEEDED":
            lines.append("### 🧪 Generated Unit Tests")
            lines.append("The AI has generated the following tests for your changes:")
            lines.append("<details>")
            lines.append("<summary>Click to expand test code</summary>")
            lines.append("")
            lines.append("```python")
            lines.append(review.generated_tests)
            lines.append("```")
            lines.append("</details>")
            lines.append("")
        
        return "\n".join(lines)
