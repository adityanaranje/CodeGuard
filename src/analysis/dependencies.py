"""
Dependency impact analyzer for detecting potential breaking changes.
"""

import ast
import os
from typing import List, Set, Dict
from pathlib import Path

from src.review.parser import FileDiff


class DependencyAnalyzer:
    """Analyzes code dependencies to detect potential impact of changes."""
    
    def __init__(self, repo_path: str = "."):
        """
        Initialize dependency analyzer.
        
        Args:
            repo_path: Path to the repository root.
        """
        self.repo_path = Path(repo_path)
    
    def analyze_impact(self, diffs: List[FileDiff]) -> List[str]:
        """
        Analyze the impact of code changes on other files.
        
        Args:
            diffs: List of file diffs.
            
        Returns:
            List of warning messages about potential impacts.
        """
        warnings = []
        
        for diff in diffs:
            # Only analyze Python files for now
            if not diff.filename.endswith('.py'):
                continue
            
            # Extract modified functions/classes
            modified_symbols = self._extract_modified_symbols(diff)
            
            if not modified_symbols:
                continue
            
            # Find files that import this module
            dependent_files = self._find_dependent_files(diff.filename)
            
            if dependent_files:
                warnings.append(
                    f"⚠️ **{diff.filename}** is imported by {len(dependent_files)} file(s). "
                    f"Changes to `{', '.join(list(modified_symbols)[:3])}` may affect: "
                    f"`{', '.join(list(dependent_files)[:5])}`"
                )
        
        return warnings
    
    def _extract_modified_symbols(self, diff: FileDiff) -> Set[str]:
        """
        Extract function and class names that were modified.
        
        Args:
            diff: File diff object.
            
        Returns:
            Set of modified symbol names.
        """
        symbols = set()
        
        # Simple heuristic: look for 'def ' or 'class ' in added/removed lines
        for _, content in diff.added_lines:
            if content.strip().startswith('def '):
                # Extract function name
                try:
                    func_name = content.split('def ')[1].split('(')[0].strip()
                    symbols.add(func_name)
                except:
                    pass
            elif content.strip().startswith('class '):
                try:
                    class_name = content.split('class ')[1].split('(')[0].split(':')[0].strip()
                    symbols.add(class_name)
                except:
                    pass
        
        return symbols
    
    def _find_dependent_files(self, filename: str) -> Set[str]:
        """
        Find Python files that import the given file.
        
        Args:
            filename: The file to check dependencies for.
            
        Returns:
            Set of file paths that import this file.
        """
        dependent_files = set()
        
        # Convert filename to module path
        module_name = filename.replace('/', '.').replace('\\', '.').replace('.py', '')
        
        # Search for imports in all Python files
        try:
            for py_file in self.repo_path.rglob('*.py'):
                if py_file.name == Path(filename).name:
                    continue
                
                try:
                    with open(py_file, 'r', encoding='utf-8') as f:
                        content = f.read()
                        
                    # Check for various import patterns
                    if (f'import {module_name}' in content or 
                        f'from {module_name}' in content or
                        f'from {Path(filename).stem} import' in content):
                        dependent_files.add(str(py_file.relative_to(self.repo_path)))
                except:
                    pass
        except:
            pass
        
        return dependent_files
