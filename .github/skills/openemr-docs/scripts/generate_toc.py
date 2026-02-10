#!/usr/bin/env python3
"""
Generate or update table of contents for markdown files.
Scans headers and creates a formatted TOC with proper indentation.
"""

import re
import sys
from pathlib import Path
from typing import List, Tuple

def extract_headers(content: str) -> List[Tuple[int, str, str]]:
    """
    Extract headers from markdown content.
    Returns list of (level, text, anchor) tuples.
    """
    headers = []
    lines = content.split('\n')
    
    for line in lines:
        # Match markdown headers (# Header)
        match = re.match(r'^(#{1,6})\s+(.+)$', line)
        if match:
            level = len(match.group(1))
            text = match.group(2).strip()
            
            # Generate anchor (GitHub-style)
            anchor = text.lower()
            anchor = re.sub(r'[^\w\s-]', '', anchor)
            anchor = re.sub(r'[-\s]+', '-', anchor)
            
            headers.append((level, text, anchor))
    
    return headers


def generate_toc(headers: List[Tuple[int, str, str]], start_level: int = 2) -> str:
    """
    Generate table of contents from headers.
    start_level: minimum header level to include (default 2 to skip H1)
    """
    if not headers:
        return ""
    
    toc_lines = []
    min_level = min(h[0] for h in headers if h[0] >= start_level)
    
    for level, text, anchor in headers:
        if level < start_level:
            continue
        
        indent = "  " * (level - min_level)
        toc_line = f"{indent}- [{text}](#{anchor})"
        toc_lines.append(toc_line)
    
    return '\n'.join(toc_lines)


def insert_or_update_toc(content: str, toc: str, marker: str = "## Table of Contents") -> str:
    """
    Insert or update TOC in markdown content.
    Looks for TOC marker and replaces content until next header or blank line.
    """
    lines = content.split('\n')
    result_lines = []
    
    in_toc = False
    toc_inserted = False
    i = 0
    
    while i < len(lines):
        line = lines[i]
        
        if marker in line and not toc_inserted:
            # Found TOC marker - replace old TOC
            result_lines.append(line)
            result_lines.append('')
            result_lines.append(toc)
            result_lines.append('')
            
            # Skip old TOC content until next header or double newline
            i += 1
            while i < len(lines):
                next_line = lines[i]
                if next_line.startswith('#') or (i + 1 < len(lines) and lines[i] == '' and lines[i + 1].startswith('#')):
                    break
                i += 1
            
            toc_inserted = True
            continue
        
        result_lines.append(line)
        i += 1
    
    # If TOC marker wasn't found, add it after first header
    if not toc_inserted:
        new_lines = []
        for i, line in enumerate(result_lines):
            new_lines.append(line)
            if line.startswith('# ') and i < len(result_lines) - 1:
                new_lines.append('')
                new_lines.append(marker)
                new_lines.append('')
                new_lines.append(toc)
                new_lines.append('')
        result_lines = new_lines if len(new_lines) > len(result_lines) else result_lines
    
    return '\n'.join(result_lines)


def process_file(file_path: Path, update: bool = True, start_level: int = 2) -> str:
    """
    Generate or update TOC for a markdown file.
    Returns the TOC as a string.
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    headers = extract_headers(content)
    toc = generate_toc(headers, start_level)
    
    if update and toc:
        updated_content = insert_or_update_toc(content, toc)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(updated_content)
        print(f"✅ Updated TOC in {file_path}")
    
    return toc


def main():
    if len(sys.argv) < 2:
        print("Usage: python generate_toc.py <markdown_file> [--update] [--start-level N]")
        print("  --update: Update the file in place")
        print("  --start-level N: Minimum header level to include (default: 2)")
        print("\nExample: python generate_toc.py docs/README.md --update")
        sys.exit(1)
    
    file_path = Path(sys.argv[1])
    update = '--update' in sys.argv
    
    start_level = 2
    if '--start-level' in sys.argv:
        idx = sys.argv.index('--start-level')
        if idx + 1 < len(sys.argv):
            start_level = int(sys.argv[idx + 1])
    
    if not file_path.exists():
        print(f"❌ File not found: {file_path}")
        sys.exit(1)
    
    toc = process_file(file_path, update, start_level)
    
    if not update:
        print("\n📋 Generated TOC:\n")
        print(toc)
        print("\n(Use --update to modify the file)")


if __name__ == "__main__":
    main()
