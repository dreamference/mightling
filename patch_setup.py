import re

with open('dreamference/chat/gmail_search_service.py', 'r') as f:
    content = f.read()

setup_page_new = '''            def _setup_page(self) -> None:
                """
                Serves the HTML for Puffin's own Google OAuth flow.
                """
                self._html(
                    """
                    <h2>Connect Google</h2>
                    <p>Puffin authenticates directly with Google. Your mail and files are read locally.</p>
                    <p class="note">ⓘ The consent screen will say <b style="display:inline">GNOME</b> — Puffin authenticates through the GNOME desktop's Google integration. No Puffin credentials are sent to Google.</p>
                    
                    <button id="start-btn" style="padding: 8px 16px; background: #0a8f8b; color: white; border: none; border-radius: 4px; cursor: pointer;">Authorize with Google</button>
                    
                    <div style="margin-top: 30px; border-top: 1px solid #eee; padding-top: 20px;">
                        <p class="muted">Different host? Browser shows "localhost refused to connect"?<br>Copy the full URL from the address bar and paste it here:</p>
                        <input type="text" id="paste-url" placeholder="http://localhost:8767/?state=...&code=..." style="width: 100%; padding: 8px; margin-bottom: 8px; box-sizing: border-box;">
                        <button id="complete-btn" style="padding: 6px 12px; background: #f6f8f8; color: #4b5563; border: 1px solid #ddd; border-radius: 4px; cursor: pointer;">Submit URL</button>
                        <p id="paste-msg" style="color: green; display: none; margin-top: 10px; font-weight: 600;"></p>
                    </div>

                    <script>
                        document.getElementById('start-btn').onclick = async () => {
                            const res = await fetch('/api/google/oauth/start', { method: 'POST' });
                            const data = await res.json();
                            if (data.auth_url) window.open(data.auth_url, '_blank');
                        };
                        document.getElementById('complete-btn').onclick = async () => {
                            const url = document.getElementById('paste-url').value;
                            if (!url) return;
                            const res = await fetch('/api/google/oauth/complete', {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({ url })
                            });
                            const data = await res.json();
                            const msg = document.getElementById('paste-msg');
                            msg.style.display = 'block';
                            if (data.status === 'ok') {
                                msg.textContent = 'Connected as ' + data.email;
                                msg.style.color = 'green';
                            } else {
                                msg.textContent = 'Error: ' + data.error;
                                msg.style.color = 'red';
                            }
                        };
                    </script>
                    """
                )'''

# Regex to match from def _setup_page to the end of the method (before the next def)
pat = r'            def _setup_page.*?def do_POST'
content = re.sub(pat, setup_page_new + '\n\n            def do_POST', content, flags=re.DOTALL)

with open('dreamference/chat/gmail_search_service.py', 'w') as f:
    f.write(content)

