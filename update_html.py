# update_html.py
import os
import re
import shutil
from datetime import datetime

FILE_PATH = r"D:\chatapp2\chatFlow-main\templates\chat\chat.html"

NEW_AI_SEND_LOGIC = """
            // ===== AI USER BRANCH =====
            if (activeIsAi && activeUserId) {
                const todayIso = new Date().toISOString().slice(0, 10);
                addDateSeparatorIfNeeded({ date_iso: todayIso, date_display: 'TODAY' });
                
                appendMessage({
                    id: 'temp-' + Date.now(), content: content,
                    timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
                    date_iso: todayIso, date_display: 'TODAY', is_mine: true,
                    deleted_for_everyone: false, reply_to: null, is_starred: false, is_read: false,
                    message_type: 'text', call_duration: '', reactions: [], expires_at: null,
                });
                
                const area = document.getElementById('chatArea');
                area.scrollTop = area.scrollHeight;
                input.value = ''; toggleMicSend(); updateFormattingHint();
                
                const status = document.getElementById('headerStatus');
                status.classList.add('typing-active');
                status.innerHTML = '🤖 AI is typing<span class="typing-dots"><span></span><span></span><span></span></span>';
                
                const fd = new FormData();
                fd.append('message', content);
                fd.append('csrfmiddlewaretoken', getCSRF());
                
                fetch('/ai/chat/', {
                    method: 'POST', headers: { 'X-CSRFToken': getCSRF() }, body: fd,
                })
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    status.classList.remove('typing-active');
                    status.innerText = '🤖 AI assistant';
                    
                    if (data.status === 'ok') {
                        appendMessage({
                            id: data.ai_message_id || ('ai-' + Date.now()), content: data.reply,
                            timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
                            date_iso: todayIso, date_display: 'TODAY', is_mine: false,
                            deleted_for_everyone: false, reply_to: null, is_starred: false, is_read: false,
                            message_type: 'text', call_duration: '', reactions: [], expires_at: null,
                        });
                        area.scrollTop = area.scrollHeight;
                        if (data.reply) playAiTTS(data.reply);
                    } else {
                        showToast(data.message || 'AI failed');
                    }
                })
                .catch(function(err) {
                    status.classList.remove('typing-active');
                    status.innerText = '🤖 AI assistant';
                    showToast('AI error: ' + err.message);
                });
                return;
            }
"""

NEW_AI_HISTORY_LOGIC = """
            // ⚠️ AI History Load on Refresh
            if (isAi) {
                fetch('/ai/conversations/')
                    .then(r => r.json())
                    .then(data => {
                        if (data.status === 'ok' && data.conversations.length > 0) {
                            const convId = data.conversations[0].id;
                            fetch('/ai/conversations/' + convId + '/')
                                .then(r => r.json())
                                .then(convData => {
                                    if (convData.status === 'ok') {
                                        const area = document.getElementById('chatArea');
                                        area.innerHTML = ''; // Clear old messages
                                        const msgs = convData.conversation.messages;
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
                                });
                        }
                    });
            }
"""

def update_chat_html():
    if not os.path.exists(FILE_PATH):
        print(f"❌ Error: File not found at {FILE_PATH}")
        return

    # 1. Backup create pannuvom
    backup_path = FILE_PATH + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(FILE_PATH, backup_path)
    print(f"✅ Backup created: {backup_path}")

    with open(FILE_PATH, 'r', encoding='utf-8') as f:
        content = f.read()

    # 2. Replace sendMessage AI Branch (Robust approach)
    # Search for the AI branch block
    start_marker = "// ===== AI USER BRANCH ====="
    end_marker = "return;\n            }"
    
    if start_marker in content:
        start_idx = content.find(start_marker)
        # Find the next 'return;' after the start marker
        end_idx = content.find(end_marker, start_idx)
        
        if end_idx != -1:
            # Replace the old block with the new one
            content = content[:start_idx] + NEW_AI_SEND_LOGIC.strip() + content[end_idx + len(end_marker):]
            print("✅ Replaced sendMessage AI logic.")
        else:
            print("⚠️ Could not find end of AI logic block.")
    else:
        print("⚠️ Could not find start of AI logic block.")

    # 3. Add History Load in activateUser
    search_activate = "loadMessages(userId);"
    if search_activate in content and "/ai/conversations/" not in content:
        content = content.replace(search_activate, search_activate + "\n" + NEW_AI_HISTORY_LOGIC, 1)
        print("✅ Added AI History logic to activateUser.")
    else:
        print("⚠️ AI History logic already exists or activateUser not found.")

    # 4. Save
    with open(FILE_PATH, 'w', encoding='utf-8') as f:
        f.write(content)

    print("🎉 chat.html updated successfully!")

if __name__ == '__main__':
    update_chat_html()