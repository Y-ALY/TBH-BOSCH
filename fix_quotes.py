import os

def process_file(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    updated = False
    new_lines = []
    
    for line in lines:
        if 'return "<svg ' in line:
            # Change 'return "<svg...>"' to 'return `<svg...>`'
            # Note: We only want to change the outermost double quotes to backticks
            start_idx = line.find('return "<svg')
            if start_idx != -1:
                # Find the first quote after return
                first_quote = line.find('"', start_idx)
                # Find the last quote on the line
                last_quote = line.rfind('"')
                
                if first_quote != -1 and last_quote != -1 and first_quote != last_quote:
                    # Replace them with backticks
                    line = line[:first_quote] + '`' + line[first_quote+1:last_quote] + '`' + line[last_quote+1:]
                    updated = True
        
        # Also fix any inline assignments like `empty-icon">"<svg` ? No, those were HTML and they used single quotes or no quotes if they were HTML elements.
        # Check for assignment like: `icon = "<svg ...>"`
        elif '="<svg ' in line or ': "<svg ' in line:
            pass # we can manually do backticks using regex
            
        new_lines.append(line)
        
    if updated:
        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        print(f"Fixed quotes in {filepath}")

for root, _, files in os.walk("templates"):
    for file in files:
        if file.endswith(".html"):
            process_file(os.path.join(root, file))
