# remove_summary.py
import os
import re
import shutil
from datetime import datetime

CHAT_HTML_PATH = r"D:\chatapp2\chatFlow-main\templates\chat\chat.html"
VIEWS_PY_PATH = r"D:\chatapp2\chatFlow-main\chat_ai\views.py"

def remove_summary_from_html():
    if not os.path.exists(CHAT_HTML_PATH):
        print("❌ chat.html not found")
        return
    
    # Backup
    backup = CHAT_HTML_PATH + f".bak_summary_remove_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(CHAT_HTML_PATH, backup)
    print(f"✅ chat.html Backup created: {backup}")

    with open(CHAT_HTML_PATH, 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. Remove Summarize Button (Header la irukkurathu)
    # Ithu nthu dynamic-a JS la inject aagirundhaalum, edhuvum irundha remove aagum
    pattern_btn = re.compile(r'<button[^>]*id="summarizeBtn"[^>]*>.*?</button>', re.DOTALL)
    if pattern_btn.search(content):
        content = pattern_btn.sub('', content)
        print("✅ Removed Summarize button from HTML.")

    # 2. Remove Summarize Modal
    pattern_modal = re.compile(r'<div class="ai-modal" id="aiSummaryModal".*?</div>\s*</div>\s*</div>', re.DOTALL)
    if pattern_modal.search(content):
        content = pattern_modal.sub('', content)
        print("✅ Removed Summarize modal from HTML.")

    # 3. Remove Summary JS Functions
    pattern_js = re.compile(r'async function doSummarize\(\) \{.*?\n        \}', re.DOTALL)
    if pattern_js.search(content):
        content = pattern_js.sub('', content)
        print("✅ Removed doSummarize JS function.")

    with open(CHAT_HTML_PATH, 'w', encoding='utf-8') as f:
        f.write(content)
    print("🎉 chat.html cleaned successfully!")


def remove_summary_from_views():
    if not os.path.exists(VIEWS_PY_PATH):
        print("❌ views.py not found")
        return
    
    # Backup
    backup = VIEWS_PY_PATH + f".bak_summary_remove_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(VIEWS_PY_PATH, backup)
    print(f"✅ views.py Backup created: {backup}")

    with open(VIEWS_PY_PATH, 'r', encoding='utf-8') as f:
        content = f.read()

    # Remove summarize_chat function
    pattern_func = re.compile(r'@login_required\n@require_POST\ndef summarize_chat\(request\):.*?(?=\n@login_required|\ndef |\n# ============================================================)', re.DOTALL)
    
    if pattern_func.search(content):
        content = pattern_func.sub('', content)
        print("✅ Removed summarize_chat from views.py.")

    with open(VIEWS_PY_PATH, 'w', encoding='utf-8') as f:
        f.write(content)
    print("🎉 views.py cleaned successfully!")


if __name__ == '__main__':
    print("--- Removing Chat Summary Feature ---")
    remove_summary_from_html()
    remove_summary_from_views()
    print("--- Done! ---")