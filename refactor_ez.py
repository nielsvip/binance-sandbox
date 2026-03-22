import sys
import re

def final_surgical_fix(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    NL = chr(10)
    lines = content.split(NL)
    new_lines = []
    
    # Common keywords
    keywords = ['if', 'elif', 'while', 'for', 'return', 'yield', 'import', 'from', 'as', 'with', 'try', 'except', 'raise', 'def', 'class', 'async', 'await']

    for line in lines:
        if not line.strip():
            new_lines.append('')
            continue
            
        indent_len = len(line) - len(line.lstrip())
        indent = line[:indent_len]
        content_str = line.lstrip()
        
        # 1. REMOVE space before colon (E203) - e.g. "try :" -> "try:"
        content_str = re.sub(r'(\w)\s+:', r'\1:', content_str)
        
        # 2. ENSURE space after colon in one-liners (E231/E701) - e.g. "if cond:return" -> "if cond: return"
        # Only if the colon is not at the very end of the line
        content_str = re.sub(r':([^\s\)])', r': \1', content_str)
        
        # 3. Ensure space after comma
        content_str = re.sub(r',([^\s\)])', r', \1', content_str)
        
        # 4. Ensure space after keywords if followed by something other than space/newline/colon
        for kw in keywords:
            # Match keyword with word boundaries, followed by a non-space, non-colon, non-newline
            content_str = re.sub(r'\b' + kw + r'\b([^\s: 
])', kw + r' \1', content_str)

        # 5. Fix spacing around operators but very carefully
        # Space around ==, !=, <=, >=, <, >
        operators = ['==', '!=', '<=', '>=']
        for op in operators:
             content_str = re.sub(r'([^ \!\<\>\=])' + re.escape(op) + r'([^ \=])', r'\1 ' + op + r' \2', content_str)
        
        # Single < and > (avoiding html-like or other structures)
        content_str = re.sub(r'([^ \!\<\>\=])<([^ \=])', r'\1 < \2', content_str)
        content_str = re.sub(r'([^ \!\<\>\=])>([^ \=])', r'\1 > \2', content_str)

        new_lines.append(indent + content_str)

    # Final pass to collapse any accidental double spaces (except indentation)
    processed_lines = []
    for line in new_lines:
        if not line.strip():
            processed_lines.append('')
            continue
        indent_len = len(line) - len(line.lstrip())
        indent = line[:indent_len]
        # Collapse multiple spaces into one in the content part
        content_part = re.sub(r' +', ' ', line.lstrip())
        processed_lines.append(indent + content_part)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(NL.join(processed_lines) + NL)

if __name__ == '__main__':
    final_surgical_fix(sys.argv[1])