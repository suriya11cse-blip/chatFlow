# fix_history_ui.py
import os
import re
import shutil
from datetime import datetime

FILE_PATH = r"D:\chatapp2\chatFlow-main\templates\chat\chat.html"

# Ithu than puthu loadMessages function (AI History load panna)
NEW_LOAD_MESSAGES_FUNCTION = """
        async function loadMessages(userId) {
            if (activeIsAi) {
                // ⚠️ AI History Load Here
                try {
                    const convRes = await fetch('/ai/conversations/');
                    const convData = await convRes.json();
                    
                    if (convData.status === 'ok' && convData.conversations.length > 0) {
                        const convId = convData.conversations[0].id;
                        const msgRes = await fetch('/ai/conversations/' + convId + '/');
                        const msgData = await msgRes.json();
                        
                        if (msgData.status === 'ok') {
                            const area = document.getElementById('chatArea');
                            area.innerHTML = '';
                            const msgs = msgData.conversation.messages;
                            let lastDate = null;
                            
                            msgs.forEach(m => {
                                const msgDate = new Date(m.created_at).toISOString().slice(0, 10);
                                if (msgDate !== lastDate) {
                                    const sep = document.createElement('div');
                                    sep.className = 'date-separator';
                                    sep.innerHTML = '<span>TODAY</span>';
                                    area.appendChild(sep);
                                    lastDate = msgDate;
                                }
                                
                                appendMessage({
                                    id: 'ai-hist-' + Date.now() + Math.random(),
                                    content: m.content,
                                    timestamp: new Date(m.created_at).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
                                    date_iso: msgDate, date_display: 'TODAY',
                                    is_mine: m.role === 'user',
                                    deleted_for_everyone: false, reply_to: null, is_starred: false, is_read: true,
                                    message_type: 'text', call_duration: '', reactions: [], expires_at: null,
                                });
                            });
                            area.scrollTop = area.scrollHeight;
                        }
                    } else {
                        document.getElementById('chatArea').innerHTML = '<div class="empty-state"><div class="icon">🤖</div><div class="text">ChatFlow AI</div></div>';
                    }
                } catch (err) {
                    console.error("AI Load Error:", err);
                }
                return; // ⚠️ Normal load skip pannum
            }

            // ===== NORMAL USER CHAT LOAD =====
            const res = await fetch('/messages/' + userId + '/');
            const data = await res.json();
            renderMessages(data.messages);
            setTimeout(function() { const a = document.getElementById('chatArea'); a.scrollTop = a.scrollHeight; }, 50);
            if (socket && socket.readyState === 1) socket.send(JSON.stringify({ action: 'mark_read', sender_id: userId }));
        }
"""

def fix_load_messages():
    if not os.path.exists(FILE_PATH):
        print(f"❌ Error: File not found at {FILE_PATH}")
        return

    # Backup
    backup_path = FILE_PATH + f".bak_load_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(FILE_PATH, backup_path)
    print(f"✅ Backup created: {backup_path}")

    with open(FILE_PATH, 'r', encoding='utf-8') as f:
        content = f.read()

    # Find old loadMessages function
    start_tag = "async function loadMessages(userId) {"
    start_idx = content.find(start_tag)
    
    if start_idx != -1:
        # Next function-a thedum (e.g., function renderMessages)
        next_func_idx = content.find("function renderMessages", start_idx)
        if next_func_idx != -1:
            content = content[:start_idx] + NEW_LOAD_MESSAGES_FUNCTION.strip() + "\n\n        " + content[next_func_idx:]
            print("✅ Replaced loadMessages function successfully.")
        else:
            print("⚠️ Could not find end of loadMessages function.")
    else:
        print("⚠️ Could not find loadMessages function.")

    with open(FILE_PATH, 'w', encoding='utf-8') as f:
        f.write(content)

    print("🎉 chat.html updated successfully!")

if __name__ == '__main__':
    fix_load_messages()