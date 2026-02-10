#!/usr/bin/env python3
"""
Documentation validator for OpenEMR documentation.
Checks markdown syntax, validates links, verifies Mermaid diagrams.
"""

import os
import re
import sys
from pathlib import Path
from typing import List, Tuple, Dict

class DocValidator:
    def __init__(self, root_path: str):
        self.root_path = Path(root_path)
        self.errors = []
        self.warnings = []
        
    def validate_markdown_file(self, file_path: Path) -> bool:
        """Validate a single markdown file."""
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        relative_path = file_path.relative_to(self.root_path)
        
        # Check for broken internal links
        self._check_internal_links(content, file_path, relative_path)
        
        # Check for Mermaid diagram syntax
        self._check_mermaid_diagrams(content, relative_path)
        
        # Check for proper headers
        self._check_headers(content, relative_path)
        
        return len(self.errors) == 0
    
    def _check_internal_links(self, content: str, file_path: Path, relative_path: Path):
        """Check if internal markdown links are valid."""
        # Match markdown links: [text](path)
        link_pattern = r'\[([^\]]+)\]\(([^)]+)\)'
        matches = re.finditer(link_pattern, content)
        
        for match in matches:
            link_text = match.group(1)
            link_path = match.group(2)
            
            # Skip external links
            if link_path.startswith(('http://', 'https://', 'mailto:')):
                continue
                
            # Skip anchors only
            if link_path.startswith('#'):
                continue
            
            # Remove anchor if present
            clean_path = link_path.split('#')[0]
            if not clean_path:
                continue
            
            # Resolve relative path
            target_path = (file_path.parent / clean_path).resolve()
            
            if not target_path.exists():
                self.errors.append(f"{relative_path}: Broken link to '{link_path}'")
    
    def _check_mermaid_diagrams(self, content: str, relative_path: Path):
        """Validate Mermaid diagram syntax."""
        # Find mermaid code blocks
        mermaid_pattern = r'```mermaid\n(.*?)```'
        matches = re.finditer(mermaid_pattern, content, re.DOTALL)
        
        for match in matches:
            diagram = match.group(1)
            
            # Check for common issues
            if 'init' in diagram.lower() and '%%{init:' in diagram:
                self.warnings.append(
                    f"{relative_path}: Mermaid diagram contains init directive (may cause rendering issues)"
                )
            
            # Check for basic syntax
            if not any(keyword in diagram for keyword in ['graph', 'sequenceDiagram', 'classDiagram', 'stateDiagram', 'erDiagram', 'flowchart', 'gantt', 'pie']):
                self.warnings.append(
                    f"{relative_path}: Mermaid diagram missing diagram type declaration"
                )
    
    def _check_headers(self, content: str, relative_path: Path):
        """Check for proper header structure."""
        lines = content.split('\n')
        has_h1 = False
        
        for line in lines:
            if line.startswith('# '):
                if has_h1:
                    self.warnings.append(f"{relative_path}: Multiple H1 headers found")
                has_h1 = True
                break
        
        if not has_h1:
            self.warnings.append(f"{relative_path}: No H1 header found")
    
    def validate_directory(self, docs_path: str = "docs") -> bool:
        """Validate all markdown files in a directory."""
        docs_dir = self.root_path / docs_path
        
        if not docs_dir.exists():
            self.errors.append(f"Documentation directory not found: {docs_dir}")
            return False
        
        md_files = list(docs_dir.rglob("*.md"))
        
        if not md_files:
            self.warnings.append(f"No markdown files found in {docs_dir}")
            return True
        
        for md_file in md_files:
            self.validate_markdown_file(md_file)
        
        return len(self.errors) == 0
    
    def report(self) -> None:
        """Print validation report."""
        if self.errors:
            print(f"\n❌ Found {len(self.errors)} error(s):")
            for error in self.errors:
                print(f"  • {error}")
        
        if self.warnings:
            print(f"\n⚠️  Found {len(self.warnings)} warning(s):")
            for warning in self.warnings:
                print(f"  • {warning}")
        
        if not self.errors and not self.warnings:
            print("\n✅ All documentation checks passed!")


def main():
    if len(sys.argv) < 2:
        print("Usage: python validate_docs.py <repo_path> [docs_path]")
        print("Example: python validate_docs.py /Users/chlong/github/openemr-on-ecs docs")
        sys.exit(1)
    
    repo_path = sys.argv[1]
    docs_path = sys.argv[2] if len(sys.argv) > 2 else "docs"
    
    validator = DocValidator(repo_path)
    success = validator.validate_directory(docs_path)
    validator.report()
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
