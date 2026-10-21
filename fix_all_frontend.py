# fix_all_frontend.py
import os
import shutil
from datetime import datetime

FILE_PATH = r"D:\chatapp2\chatFlow-main\templates\chat\chat.html"

# Ithu than puthu sendMessage function (Ithula AI logic + Save correct-a irukku)
NEW_SEND_MESSAGE_FUNCTION = """
        function sendMessage() {
            const input = document.getElementById('msgInput');
            const content = input.value.trim();
            if (!content) return;

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

            // ===== GROUP BRANCH =====
            if (currentRoomId) {
                if (socket && socket.readyState === 1) {
                    const payload = { action: 'send_group_message', room_id: currentRoomId, content: content };
                    if (replyToMessage) payload.reply_to_id = replyToMessage.id;
                    socket.send(JSON.stringify(payload));
                }
                input.value = '';
                cancelReply();
                sendStopTyping();
                toggleMicSend();
                updateFormattingHint();
                return;
            }

            // ===== NORMAL DM =====
            if (!activeUserId) return;
            const payload = { action: 'send_message', receiver_id: activeUserId, content: content };
            if (replyToMessage) payload.reply_to_id = replyToMessage.id;
            if (socket && socket.readyState === 1) socket.send(JSON.stringify(payload));
            input.value = '';
            cancelReply();
            sendStopTyping();
            toggleMicSend();
            updateFormattingHint();
        }
"""

# AI History Load panna function
NEW_HISTORY_LOGIC = """
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

def fix_chat_html():
    if not os.path.exists(FILE_PATH):
        print(f"❌ Error: File not found at {FILE_PATH}")
        return

    # Backup
    backup_path = FILE_PATH + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(FILE_PATH, backup_path)
    print(f"✅ Backup created: {backup_path}")

    with open(FILE_PATH, 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. Replace full sendMessage function
    start_tag = "function sendMessage() {"
    end_tag = "updateFormattingHint();\n        }" # Ithu sendMessage function oda last line
    
    start_idx = content.find(start_tag)
    if start_idx != -1:
        # Find the closing bracket of sendMessage (matching the one we found)
        # Since we know the exact structure, we can find the next function start or specific end tag
        # For safety, we'll find the next "function " after start_idx
        next_func_idx = content.find("\n        function ", start_idx + len(start_tag))
        if next_func_idx != -1:
            content = content[:start_idx] + NEW_SEND_MESSAGE_FUNCTION.strip() + "\n\n        " + content[next_func_idx:]
            print("✅ Replaced full sendMessage function.")
        else:
            print("⚠️ Could not find end of sendMessage function.")
    else:
        print("⚠️ Could not find sendMessage function.")

    # 2. Add History Load in activateUser
    search_activate = "loadMessages(userId);"
    if search_activate in content and "/ai/conversations/" not in content:
        content = content.replace(search_activate, search_activate + "\n" + NEW_HISTORY_LOGIC, 1)
        print("✅ Added AI History logic to activateUser.")
    else:
        print("⚠️ AI History logic already exists or activateUser not found.")

    # Save
    with open(FILE_PATH, 'w', encoding='utf-8') as f:
        f.write(content)

    print("🎉 chat.html updated successfully!")

if __name__ == '__main__':
    fix_chat_html()