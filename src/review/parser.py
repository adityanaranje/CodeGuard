"""
Diff parser for extracting and analyzing code changes from PRs.
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict
from enum import Enum


class ChangeType(Enum):
    """Type of line change."""
    ADDED = "added"
    REMOVED = "removed"
    CONTEXT = "context"


@dataclass
class DiffLine:
    """Represents a single line in a diff."""
    content: str
    change_type: ChangeType
    old_line_number: Optional[int]
    new_line_number: Optional[int]


@dataclass
class DiffHunk:
    """Represents a hunk (section) of changes in a diff."""
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: List[DiffLine]
    header: str


@dataclass
class FileDiff:
    """Represents all changes to a single file."""
    filename: str
    status: str  # added, modified, deleted, renamed
    hunks: List[DiffHunk]
    additions: int
    deletions: int
    
    @property
    def added_lines(self) -> List[Tuple[int, str]]:
        """Get all added lines with their line numbers."""
        added = []
        for hunk in self.hunks:
            for line in hunk.lines:
                if line.change_type == ChangeType.ADDED and line.new_line_number:
                    added.append((line.new_line_number, line.content))
        return added
    
    @property
    def removed_lines(self) -> List[Tuple[int, str]]:
        """Get all removed lines with their line numbers."""
        removed = []
        for hunk in self.hunks:
            for line in hunk.lines:
                if line.change_type == ChangeType.REMOVED and line.old_line_number:
                    removed.append((line.old_line_number, line.content))
        return removed
    
    @property
    def full_diff_text(self) -> str:
        """Get the full diff as text for LLM review."""
        lines = [f"File: {self.filename} ({self.status})"]
        for hunk in self.hunks:
            lines.append(hunk.header)
            for line in hunk.lines:
                if line.change_type == ChangeType.ADDED:
                    lines.append(f"+{line.content}")
                elif line.change_type == ChangeType.REMOVED:
                    lines.append(f"-{line.content}")
                else:
                    lines.append(f" {line.content}")
        return "\n".join(lines)


class DiffParser:
    """Parser for GitHub diff/patch format."""
    
    # Regex for hunk headers like @@ -1,5 +1,7 @@
    HUNK_HEADER_RE = re.compile(
        r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$"
    )
    
    def parse_patch(self, patch: str, filename: str, status: str = "modified") -> FileDiff:
        """
        Parse a single file's patch/diff.
        
        Args:
            patch: The patch string from GitHub API.
            filename: Name of the file.
            status: File status (added, modified, deleted).
            
        Returns:
            FileDiff object with parsed hunks.
        """
        if not patch:
            return FileDiff(
                filename=filename,
                status=status,
                hunks=[],
                additions=0,
                deletions=0
            )
        
        hunks = []
        additions = 0
        deletions = 0
        
        lines = patch.split("\n")
        current_hunk: Optional[DiffHunk] = None
        old_line = 0
        new_line = 0
        
        for line in lines:
            # Check for hunk header
            match = self.HUNK_HEADER_RE.match(line)
            if match:
                # Save previous hunk
                if current_hunk:
                    hunks.append(current_hunk)
                
                old_start = int(match.group(1))
                old_count = int(match.group(2)) if match.group(2) else 1
                new_start = int(match.group(3))
                new_count = int(match.group(4)) if match.group(4) else 1
                header_text = match.group(5).strip()
                
                current_hunk = DiffHunk(
                    old_start=old_start,
                    old_count=old_count,
                    new_start=new_start,
                    new_count=new_count,
                    lines=[],
                    header=line
                )
                old_line = old_start
                new_line = new_start
                continue
            
            if current_hunk is None:
                continue
            
            # Parse diff lines
            if line.startswith("+"):
                diff_line = DiffLine(
                    content=line[1:],
                    change_type=ChangeType.ADDED,
                    old_line_number=None,
                    new_line_number=new_line
                )
                current_hunk.lines.append(diff_line)
                new_line += 1
                additions += 1
            elif line.startswith("-"):
                diff_line = DiffLine(
                    content=line[1:],
                    change_type=ChangeType.REMOVED,
                    old_line_number=old_line,
                    new_line_number=None
                )
                current_hunk.lines.append(diff_line)
                old_line += 1
                deletions += 1
            elif line.startswith(" ") or line == "":
                # Context line
                content = line[1:] if line.startswith(" ") else ""
                diff_line = DiffLine(
                    content=content,
                    change_type=ChangeType.CONTEXT,
                    old_line_number=old_line,
                    new_line_number=new_line
                )
                current_hunk.lines.append(diff_line)
                old_line += 1
                new_line += 1
        
        # Don't forget the last hunk
        if current_hunk:
            hunks.append(current_hunk)
        
        return FileDiff(
            filename=filename,
            status=status,
            hunks=hunks,
            additions=additions,
            deletions=deletions
        )
    
    def parse_pr_files(self, pr_files: List[Dict]) -> List[FileDiff]:
        """
        Parse all files from a PR.
        
        Args:
            pr_files: List of file dicts with filename, status, patch.
            
        Returns:
            List of FileDiff objects.
        """
        diffs = []
        for file in pr_files:
            diff = self.parse_patch(
                patch=file.get("patch", ""),
                filename=file["filename"],
                status=file.get("status", "modified")
            )
            diffs.append(diff)
        return diffs
    
    def get_file_extension(self, filename: str) -> str:
        """Get file extension from filename."""
        if "." in filename:
            return filename.rsplit(".", 1)[-1].lower()
        return ""
    
    def get_language(self, filename: str) -> str:
        """Determine programming language from filename."""
        ext_map = {
            "py": "python",
            "js": "javascript",
            "jsx": "javascript",
            "ts": "typescript",
            "tsx": "typescript",
            "java": "java",
            "go": "go",
            "rs": "rust",
            "rb": "ruby",
            "php": "php",
            "c": "c",
            "cpp": "cpp",
            "h": "c",
            "hpp": "cpp",
            "cs": "csharp",
            "swift": "swift",
            "kt": "kotlin",
            "scala": "scala",
            "sql": "sql",
            "sh": "bash",
            "bash": "bash",
            "yml": "yaml",
            "yaml": "yaml",
            "json": "json",
            "xml": "xml",
            "html": "html",
            "css": "css",
            "md": "markdown",
        }
        ext = self.get_file_extension(filename)
        return ext_map.get(ext, "unknown")
    
    def format_for_review(self, diffs: List[FileDiff], max_lines: int = 500) -> str:
        """
        Format diffs for LLM review, respecting token limits.
        
        Args:
            diffs: List of FileDiff objects.
            max_lines: Maximum total lines to include.
            
        Returns:
            Formatted diff string for LLM.
        """
        output = []
        total_lines = 0
        
        for diff in diffs:
            if total_lines >= max_lines:
                output.append(f"\n... and {len(diffs) - len(output)} more files (truncated)")
                break
            
            file_text = diff.full_diff_text
            file_lines = file_text.count("\n") + 1
            
            if total_lines + file_lines > max_lines:
                # Truncate this file
                remaining = max_lines - total_lines
                truncated = "\n".join(file_text.split("\n")[:remaining])
                output.append(truncated)
                output.append("... (file truncated)")
                break
            
            output.append(file_text)
            output.append("")  # Blank line between files
            total_lines += file_lines
        
        return "\n".join(output)
