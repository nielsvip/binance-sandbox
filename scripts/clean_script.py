import sys
import re

def clean_file(file_path):
    with open(file_path, 'r') as f:
        lines = f.readlines()
    
    # 1. Remove all blank lines and whitespace-only lines
    lines = [l.strip('\r\n') for l in lines if l.strip()]
    
    new_lines = []
    for i, line in enumerate(lines):
        # 2. Add blank line ONLY before TOP-LEVEL def or class
        # Top level means it starts exactly with 'def ' or 'class ' (no leading spaces)
        if (line.startswith('def ') or line.startswith('class ')) and i > 0:
            new_lines.append("\n")
        new_lines.append(line + "\n")
        
    with open(file_path, 'w') as f:
        for line in new_lines:
            f.write(line)

if __name__ == "__main__":
    clean_file(sys.argv[1])
