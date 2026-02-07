"""
LLM-powered code reviewer using Groq.
Analyzes code changes and provides intelligent feedback.
"""

from dataclasses import dataclass
from typing import List, Dict, Any, Optional
import json
import re
import logging
# from groq import Groq # removed

logger = logging.getLogger(__name__)

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
    primary_language: Optional[str] = None


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
        
        # Phase 4: Initialize cache
        from src.services.cache import ReviewCache
        self.cache = ReviewCache()
    
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
    
    def _is_auto_generated(self, filename: str) -> bool:
        """Check if file is auto-generated and should be skipped."""
        auto_generated_patterns = [
            'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml',
            'Gemfile.lock', 'Cargo.lock', 'composer.lock',
            '.min.js', '.min.css',
            'dist/', 'build/', 'node_modules/',
            '.pyc', '__pycache__/',
            'go.sum'
        ]
        
        filename_lower = filename.lower()
        return any(pattern in filename_lower for pattern in auto_generated_patterns)
    
    def _is_excluded_file(self, filename: str) -> bool:
        """Check if file should be excluded (images, videos, fonts, binaries)."""
        excluded_extensions = {
            # Images
            'png', 'jpg', 'jpeg', 'gif', 'svg', 'ico', 'webp', 'bmp',
            # Videos
            'mp4', 'avi', 'mov', 'wmv', 'flv', 'webm',
            # Audio
            'mp3', 'wav', 'ogg', 'flac',
            # Fonts
            'ttf', 'otf', 'woff', 'woff2', 'eot',
            # Binaries
            'exe', 'dll', 'so', 'dylib', 'bin',
            # Archives
            'zip', 'tar', 'gz', 'rar', '7z',
            # Documents
            'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx'
        }
        
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        return ext in excluded_extensions
    
    def _is_trivial_change(self, diffs: List[FileDiff]) -> bool:
        """Check if PR contains only trivial changes (whitespace, version bumps)."""
        # Version-only changes
        if len(diffs) == 1:
            filename = diffs[0].filename.lower()
            if filename in ['package.json', 'pyproject.toml', 'cargo.toml', 'pom.xml']:
                total_changes = diffs[0].additions + diffs[0].deletions
                if total_changes <= 2:  # Likely just version bump
                    return True
        
        # Check if all changes are whitespace only
        all_whitespace = True
        for diff in diffs:
            for line_num, content in diff.added_lines:
                if content.strip():  # Non-whitespace content
                    all_whitespace = False
                    break
            if not all_whitespace:
                break
        
        return all_whitespace
    
    def _has_merge_conflicts(self, diff_text: str) -> bool:
        """Check if the diff text contains merge conflict markers."""
        conflict_markers = ['<<<<<<<', '=======', '>>>>>>>']
        return any(marker in diff_text for marker in conflict_markers)
    
    def _is_documentation_only(self, diffs: List[FileDiff]) -> bool:
        """Check if PR contains only documentation changes."""
        doc_extensions = {'md', 'txt', 'rst', 'adoc', 'pdf'}
        
        for diff in diffs:
            ext = diff.filename.rsplit('.', 1)[-1].lower() if '.' in diff.filename else ''
            if ext not in doc_extensions:
                return False
        
        return len(diffs) > 0
    
    def review(
        self,
        diffs: List[FileDiff],
        pr_title: str,
        pr_body: Optional[str] = None
    ) -> LLMReview:
        """
        Review code changes using Agent.
        """
        # Early return for trivial PRs (Phase 3)
        if self._is_trivial_change(diffs):
            print("Skipping review: Trivial changes detected (version bump or whitespace only)")
            return LLMReview(
                summary="Trivial changes detected. No review needed.",
                severity_score=1,
                issues=[],
                suggestions=[],
                security_concerns=[],
                positive_feedback=["Changes appear to be trivial (version bump or formatting)."],
                raw_response="",
                generated_tests=None
            )
        
        # Filter out non-code files
        code_diffs = [d for d in diffs if self._is_code_file(d.filename)]
        
        # Phase 1: Apply smart filtering
        if self.config.skip_auto_generated:
            code_diffs = [d for d in code_diffs if not self._is_auto_generated(d.filename)]
            code_diffs = [d for d in code_diffs if not self._is_excluded_file(d.filename)]
        
        # Filter by file size
        max_size_bytes = self.config.max_file_size_kb * 1024
        code_diffs = [d for d in code_diffs if len(d.full_diff_text) <= max_size_bytes]

        # Filter out files with merge conflicts
        code_diffs = [d for d in code_diffs if not self._has_merge_conflicts(d.full_diff_text)]
        
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
        
        # Phase 4: Check cache before making LLM call
        diff_hash = self.cache.hash_diff(diff_text)
        cached_review = self.cache.get(diff_hash)
        
        if cached_review:
            print(f"Cache hit! Returning cached review (hash: {diff_hash[:8]}...)")
            return LLMReview(
                summary=cached_review.get("summary", ""),
                severity_score=cached_review.get("severity_score", 5),
                issues=cached_review.get("issues", []),
                suggestions=cached_review.get("suggestions", []),
                security_concerns=cached_review.get("security_concerns", []),
                positive_feedback=cached_review.get("positive_feedback", []),
                raw_response=cached_review.get("raw_response", ""),
                generated_tests=cached_review.get("generated_tests"),
                dependency_warnings=cached_review.get("dependency_warnings", []),
                primary_language=cached_review.get("primary_language")
            )
        
        print(f"Cache miss. Proceeding with LLM review (hash: {diff_hash[:8]}...)")
        
        # Use '.' as repo path for now (current working directory)
        # In a real deployment, we might need a more dynamic path if processing multiple repos locally
        repo_path = "."
        
        try:
            # Calculate PR stats (needed for both progressive and original logic)
            total_lines = sum(d.additions + d.deletions for d in code_diffs)
            total_files = len(code_diffs)
            total_tokens = sum(len(d.full_diff_text) for d in code_diffs) // 4
            
            # Phase 5: Progressive Review Strategy
            # Start with small model, escalate to large only if needed
            if self.config.enable_progressive_review:
                # Always start with small model
                model_to_use = self.config.groq_small_model
                print(f"Progressive review: Starting with small model: {model_to_use}")
                print(f"   Stats: {total_lines} lines, {total_files} files, ~{total_tokens} tokens")
            else:
                # Original logic: Choose model based on PR size
                use_large_model = False
                reasons = []

                # Check thresholds
                if total_lines > self.config.llm_change_threshold:
                    use_large_model = True
                    reasons.append(f"Lines ({total_lines} > {self.config.llm_change_threshold})")
                
                if total_files > self.config.llm_file_limit:
                    use_large_model = True
                    reasons.append(f"Files ({total_files} > {self.config.llm_file_limit})")

                if total_tokens > self.config.llm_token_limit:
                    use_large_model = True
                    reasons.append(f"Tokens (~{total_tokens} > {self.config.llm_token_limit})")

                if use_large_model:
                    model_to_use = self.config.groq_large_model
                    print(f"Large PR detected. Using powerful model: {model_to_use}")
                    print(f"   Reason: {', '.join(reasons)}")
                else:
                    model_to_use = self.config.groq_small_model
                    print(f"Small PR detected. Using fast model: {model_to_use}")
                    print(f"   Stats: {total_lines} lines, {total_files} files, ~{total_tokens} tokens")

            # Detect primary language for test generation
            primary_language = self._detect_primary_language(code_diffs)
            print(f"Detected primary language: {primary_language}")
            
            # Phase 2: Determine if we should generate tests
            should_generate_tests = self.config.enable_test_generation
            if should_generate_tests:
                # Skip tests for documentation-only PRs
                if self._is_documentation_only(code_diffs):
                    should_generate_tests = False
                    print("Skipping test generation: Documentation-only PR")
                
                # Skip tests for small PRs
                elif total_lines < self.config.test_generation_min_lines:
                    should_generate_tests = False
                    print(f"Skipping test generation: PR too small ({total_lines} < {self.config.test_generation_min_lines} lines)")

            # Call Agent Orchestrator
            raw_response = self.agent.review_pr(
                pr_diff=diff_text, 
                repo_path=repo_path,
                model_name=model_to_use,
                task_description=pr_body,
                primary_language=primary_language,
                skip_test_generation=not should_generate_tests
            )
            
            # Parse the response (now returns JSON with code_review and generated_tests)
            agent_result = json.loads(raw_response)
            code_review_str = agent_result.get("code_review", "")
            generated_tests_str = agent_result.get("generated_tests", "")
            
            # Clean up potential markdown JSON from code_review
            clean_response = code_review_str
            try:
                review_data = self._fix_json_robustly(clean_response)
            except Exception as e:
                logger.error(f"Failed to parse or repair JSON: {e}")
                # Fallback handled by parent try-except
                raise

            severity_score = min(10, max(1, review_data.get("severity_score", 5)))
            
            # Phase 5: Check if we need to escalate to large model
            if (self.config.enable_progressive_review and 
                model_to_use == self.config.groq_small_model and 
                severity_score > self.config.progressive_review_threshold):
                
                print(f"Escalating to large model: Severity {severity_score} > threshold {self.config.progressive_review_threshold}")
                
                # Re-run with large model
                raw_response = self.agent.review_pr(
                    pr_diff=diff_text, 
                    repo_path=repo_path,
                    model_name=self.config.groq_large_model,
                    task_description=pr_body,
                    primary_language=primary_language,
                    skip_test_generation=not should_generate_tests
                )
                
                # Re-parse with large model results
                agent_result = json.loads(raw_response)
                code_review_str = agent_result.get("code_review", "")
                generated_tests_str = agent_result.get("generated_tests", "")
                
                try:
                    review_data = self._fix_json_robustly(code_review_str)
                except Exception as e:
                    logger.error(f"Failed to parse or repair escalated JSON: {e}")
                    raise

                severity_score = min(10, max(1, review_data.get("severity_score", 5)))
                print(f"Large model review complete. Final severity: {severity_score}")
            
            # Phase 4: Store review in cache for future use
            cache_data = {
                "summary": review_data.get("summary", "Review completed."),
                "severity_score": severity_score,
                "issues": review_data.get("issues", []),
                "suggestions": review_data.get("suggestions", []),
                "security_concerns": review_data.get("security_concerns", []),
                "positive_feedback": review_data.get("positive_feedback", []),
                "raw_response": raw_response,
                "generated_tests": generated_tests_str,
                "dependency_warnings": dependency_warnings,
                "primary_language": primary_language
            }
            self.cache.set(diff_hash, cache_data)
            print(f"Review cached (hash: {diff_hash[:8]}...)")
            
            return LLMReview(
                summary=review_data.get("summary", "Review completed."),
                severity_score=min(10, max(1, review_data.get("severity_score", 5))),
                issues=review_data.get("issues", []),
                suggestions=review_data.get("suggestions", []),
                security_concerns=review_data.get("security_concerns", []),
                positive_feedback=review_data.get("positive_feedback", []),
                raw_response=raw_response,
                generated_tests=generated_tests_str,
                dependency_warnings=dependency_warnings,
                primary_language=primary_language
            )
            
        except Exception as e:
            logger.error(f"LLM Review failed: {e}")
            return LLMReview(
                summary=f"Review failed: {str(e)}",
                severity_score=8,  # Higher fallback to trigger intervention
                issues=[],
                suggestions=[],
                security_concerns=[],
                positive_feedback=[],
                raw_response=""
            )
    
    def _detect_primary_language(self, diffs: List[FileDiff]) -> str:
        """
        Detect the primary programming language from the diffs.
        Returns the most common language across all changed files.
        """
        language_counts = {}
        
        for diff in diffs:
            lang = self.diff_parser.get_language(diff.filename)
            if lang != "unknown":
                language_counts[lang] = language_counts.get(lang, 0) + 1
        
        if not language_counts:
            return "python"  # Default fallback
        
        # Return most common language
        return max(language_counts, key=language_counts.get)
    
    def _is_code_file(self, filename: str) -> bool:
        """Check if a file is a code file worth reviewing."""
        code_extensions = {
            "py", "js", "jsx", "ts", "tsx", "java", "go", "rs", "rb",
            "php", "c", "cpp", "h", "hpp", "cs", "swift", "kt", "scala",
            "sql", "sh", "bash"
        }
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        return ext in code_extensions
    
    def _fix_json_robustly(self, text: str) -> Dict[str, Any]:
        """
        Attempts to fix common LLM JSON errors and parse the result.
        """
        if not text:
            raise ValueError("Empty response")
            
        # 1. Try standard parse first (with strict=False)
        try:
            return json.loads(text, strict=False)
        except json.JSONDecodeError:
            pass

        # 2. Extract largest JSON-like block (Greedy matching is safer for nested objects)
        # Try markdown blocks first
        json_match = re.search(r'```json\s*(\{.*\})\s*```', text, re.DOTALL)
        if not json_match:
            json_match = re.search(r'```\s*(\{.*\})\s*```', text, re.DOTALL)
        if not json_match:
            json_match = re.search(r'(\{.*\})', text, re.DOTALL)
        
        if json_match:
            text = json_match.group(1)
            # Remove trailing triple backticks if greedy matching caught them
            text = re.sub(r'```.*$', '', text, flags=re.DOTALL).strip()

        # 3. Handle Python-isms (None, True, False) that LLMs often leak
        text = re.sub(r'\bNone\b', 'null', text)
        text = re.sub(r'\bTrue\b', 'true', text)
        text = re.sub(r'\bFalse\b', 'false', text)

        # 4. Handle single quotes (Convert to double quotes)
        # Handle single quoted keys: 'key': -> "key":
        text = re.sub(r"'\s*([^'\s\"]+)\s*':", r'"\1":', text)
        # Handle single quoted values (heuristic): : 'value' -> : "value"
        text = re.sub(r":\s*'([^']*)'", r': "\1"', text)
        # Handle single quoted array items
        text = re.sub(r"'\s*,\s*'", r'", "', text)
        text = re.sub(r"\[\s*'([^']*)'", r'["\1"', text)
        text = re.sub(r"'([^']*)'\s*\]", r'"\1"]', text)

        # 5. Quote common unquoted keys (Schema specific)
        common_keys = [
            "summary", "severity_score", "issues", "suggestions", "security_concerns", 
            "positive_feedback", "type", "file", "line", "description", "suggestion", 
            "fixed_code", "confidence", "evidence", "attack_vector", "severity",
            "start_line", "generated_tests", "review_data", "code_review"
        ]
        for key in common_keys:
            # Match unquoted key followed by colon, ensuring it's not already quoted or part of a path
            text = re.sub(rf'(?<!["/])\b{key}\b(?<!["/])\s*:', rf'"{key}":', text)

        # 6. Fix common structural issues
        # Remove trailing commas in lists/objects
        text = re.sub(r',\s*([\]}])', r'\1', text)
        
        # 7. Try parsing again
        try:
            return json.loads(text, strict=False)
        except json.JSONDecodeError:
            pass

        # 8. Fix missing commas between elements (Common Groq/LLM issue)
        # Between objects: } { -> }, {
        text = re.sub(r'\}\s*\{', '}, {', text)
        # Between arrays: ] [ -> ], [
        text = re.sub(r'\]\s*\[', '], [', text)
        
        # Between properties: value "next_key": -> value, "next_key":
        # Handle various value endings (quotes, digits, booleans, objects, arrays)
        text = re.sub(r'("\s*)("\w+":)', r'\1, \2', text)
        text = re.sub(r'(\d)\s*("\w+":)', r'\1, \2', text)
        text = re.sub(r'(true|false|null)\s*("\w+":)', r'\1, \2', text)
        text = re.sub(r'\]\s*("\w+":)', r'], \1', text)
        text = re.sub(r'\}\s*("\w+":)', r'}, \1', text)

        # 9. Fix missing commas in arrays (Between strings: "a" "b" -> "a", "b")
        text = re.sub(r'"\s+"', '", "', text)

        # 10. Fix unescaped newlines inside strings
        def fix_newlines(match):
            content = match.group(1)
            fixed = content.replace('\n', '\\n')
            return '"' + fixed + '"'
        
        repair_text = re.sub(r'"((?:[^"\\]|\\.)*)"', fix_newlines, text, flags=re.DOTALL)
        
        try:
            return json.loads(repair_text, strict=False)
        except json.JSONDecodeError as e:
            # Aggressive error logging for debugging
            logger.error("-" * 40)
            logger.error(f"FINAL JSON REPAIR FAILED: {e}")
            logger.error(f"Problematic content around error (char {e.pos}):")
            start = max(0, e.pos - 100)
            end = min(len(repair_text), e.pos + 100)
            logger.error(f"...{repair_text[start:end]}...")
            logger.error("-" * 40)
            # Log full content for deep analysis if needed (first 5000 chars)
            logger.debug(f"Full malformed text: {repair_text[:5000]}")
            raise

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

        # Fixed Code (instead of AI Fix Prompt)
        if review.issues:
            has_fixes = any(issue.get('fixed_code') for issue in review.issues)
            if has_fixes:
                lines.append("### 🔧 Fixed Code")
                lines.append("Here are the corrected code snippets for the identified issues:")
                lines.append("")
                
                for i, issue in enumerate(review.issues, 1):
                    if issue.get('fixed_code'):
                        file_info = issue.get('file', 'unknown')
                        if issue.get('line'):
                            file_info += f":{issue['line']}"
                        
                        lines.append(f"**Issue {i}**: {issue.get('description', '')}")
                        lines.append(f"*File: {file_info}*")
                        lines.append("")
                        lines.append("```" + (review.primary_language or "python"))
                        lines.append(issue['fixed_code'].strip())
                        lines.append("```")
                        lines.append("")
        
        
        # Generated Tests
        if review.generated_tests and review.generated_tests.strip() and review.generated_tests != "NO_TESTS_NEEDED":
            # Determine language for code fence
            test_language = review.primary_language or "python"
            lines.extend([
                "### 🧪 Generated Unit Tests",
                "The AI has generated the following tests for your changes:",
                "",
                "<details>",
                "<summary>Click to expand test code</summary>",
                "",
                f"```{test_language}",
                review.generated_tests.rstrip().replace("```", "``\\`"),
                "```",
                "",
                "</details>",
                "",
            ])

        
        return "\n".join(lines)
    
    def get_inline_comments(self, review: LLMReview) -> List[Dict[str, Any]]:
        """
        Convert LLM review issues into inline comment format for GitHub.
        
        Args:
            review: LLM review object with issues.
            
        Returns:
            List of inline comment dicts ready for GitHub API.
        """
        comments = []
        
        for issue in review.issues:
            # Only create inline comment if we have file and line info
            if not issue.get('file') or not issue.get('line'):
                continue
            
            # Build comment body with fixed code if available
            body_parts = [f"**{issue.get('type', 'Issue').title()}**: {issue.get('description', '')}"]
            
            if issue.get('suggestion'):
                body_parts.append(f"\n💡 *Suggestion:* {issue['suggestion']}")
            
            if issue.get('fixed_code'):
                # Use GitHub suggestion syntax for the "Accept Changes" button
                # Note: No language identifier is used for suggestion blocks
                body_parts.append(f"\n\n🔧 **Fixed Code:**\n```suggestion\n{issue['fixed_code'].strip()}\n```")
            
            inline_comment = {
                "path": issue['file'],
                "line": issue['line'],
                "body": "\n".join(body_parts)
            }
            
            # Support multi-line suggestions if start_line is provided
            if issue.get('start_line'):
                inline_comment["start_line"] = issue['start_line']
            
            comments.append(inline_comment)
        
        return comments



