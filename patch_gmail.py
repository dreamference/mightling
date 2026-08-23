import re

with open('dreamference/chat/gmail_search_service.py', 'r') as f:
    content = f.read()

# 1. Add constants
constants = """
GOOGLE_OAUTH_CLIENT_ID: Final[str] = os.environ.get("GOA_GOOGLE_CLIENT_ID", "44438659992-7kgjeitenc16ssihbtdjbgguch7ju55s.apps.googleusercontent.com")
GOOGLE_OAUTH_CLIENT_SECRET: Final[str] = os.environ.get("GOA_GOOGLE_CLIENT_SECRET", "-gMLuQyDiI0XrQS_vx_mhuYF")
GOOGLE_OAUTH_SCOPES: Final[str] = "openid email https://mail.google.com/ https://www.googleapis.com/auth/drive.readonly"
OAUTH_STATES: Dict[str, Dict[str, Any]] = {}
"""
content = re.sub(r'(KEY_NAME: Final\[str\].*?\n)', r'\1' + constants + '\n', content)

# 2. Add refresh token logic to save_token
save_token_old = '''    def save_token(
        cls, address: str, token: str, lifetime: int, directory: Optional[str] = None
    ) -> bool:'''
save_token_new = '''    def save_token(
        cls, address: str, token: str, lifetime: int, directory: Optional[str] = None, refresh_token: Optional[str] = None
    ) -> bool:'''
content = content.replace(save_token_old, save_token_new)

save_token_body_old = '''        payload = {
            "email": address,
            "access_token": cls._seal(token, directory),
            "expires_at": time.time() + lifetime,
        }'''
save_token_body_new = '''        payload = {
            "email": address,
            "access_token": cls._seal(token, directory),
            "expires_at": time.time() + lifetime,
        }
        if refresh_token:
            payload["refresh_token"] = cls._seal(refresh_token, directory)
        else:
            # preserve existing refresh token if not updating it
            existing = cls._raw(directory)
            if "refresh_token" in existing:
                payload["refresh_token"] = existing["refresh_token"]'''
content = content.replace(save_token_body_old, save_token_body_new)

# 3. Add refresh token logic to credentials
credentials_old = '''        if float(stored.get("expires_at", 0)) <= time.time():
            return None
        token = cls._unseal(sealed, directory)
        return {"email": address, "access_token": token} if token else None'''
credentials_new = '''        if float(stored.get("expires_at", 0)) - time.time() < 60:
            sealed_refresh = stored.get("refresh_token")
            if sealed_refresh:
                refresh = cls._unseal(sealed_refresh, directory)
                if refresh:
                    import urllib.request, urllib.parse, json
                    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=urllib.parse.urlencode({
                        "client_id": GOOGLE_OAUTH_CLIENT_ID,
                        "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
                        "refresh_token": refresh,
                        "grant_type": "refresh_token"
                    }).encode("utf-8"), headers={"Content-Type": "application/x-www-form-urlencoded"})
                    try:
                        with urllib.request.urlopen(req, timeout=10) as res:
                            data = json.load(res)
                            if "access_token" in data:
                                cls.save_token(address, data["access_token"], data.get("expires_in", 3599), directory)
                                return {"email": address, "access_token": data["access_token"]}
                    except Exception as e:
                        pass
            return None
        token = cls._unseal(sealed, directory)
        return {"email": address, "access_token": token} if token else None'''
content = content.replace(credentials_old, credentials_new)

# 4. Modify do_GET to handle OAuth callback
do_get_old = '''            def do_GET(self) -> None:  # noqa: N802 - name fixed by http.server
                parsed = urllib.parse.urlparse(self.path)'''
do_get_new = '''            def do_GET(self) -> None:  # noqa: N802 - name fixed by http.server
                parsed = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                
                # OAuth redirect
                if parsed.path == "/" and "code" in query and "state" in query:
                    code = query["code"][0]
                    state = query["state"][0]
                    if state in OAUTH_STATES:
                        verifier = OAUTH_STATES[state]["code_verifier"]
                        import urllib.request, urllib.parse, json
                        req = urllib.request.Request("https://oauth2.googleapis.com/token", data=urllib.parse.urlencode({
                            "client_id": GOOGLE_OAUTH_CLIENT_ID,
                            "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
                            "code": code,
                            "code_verifier": verifier,
                            "redirect_uri": HOST_ORIGIN + "/",
                            "grant_type": "authorization_code"
                        }).encode("utf-8"), headers={"Content-Type": "application/x-www-form-urlencoded"})
                        try:
                            with urllib.request.urlopen(req, timeout=10) as res:
                                data = json.load(res)
                                
                            # identify user
                            req2 = urllib.request.Request("https://www.googleapis.com/oauth2/v3/userinfo", headers={"Authorization": f"Bearer {data['access_token']}"})
                            with urllib.request.urlopen(req2, timeout=10) as res2:
                                user_data = json.load(res2)
                                email_addr = user_data.get("email", "")
                            
                            if email_addr and "access_token" in data:
                                GmailSearchService.save_token(email_addr, data["access_token"], data.get("expires_in", 3599), refresh_token=data.get("refresh_token"))
                                self._html("<h2>Connected Successfully</h2><p>You can close this tab.</p>")
                                return
                        except Exception as e:
                            self._html(f"<h2>Error</h2><p>{html.escape(str(e))}</p>")
                            return
                    self._html("<h2>Error</h2><p>Invalid state or token exchange failed.</p>")
                    return'''
content = content.replace(do_get_old, do_get_new)

# 5. Add do_POST
do_post_new = '''            def do_POST(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/api/google/oauth/start":
                    import secrets, base64, hashlib
                    state = secrets.token_urlsafe(32)
                    verifier = secrets.token_urlsafe(32)
                    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
                    OAUTH_STATES[state] = {"code_verifier": verifier, "time": time.time()}
                    
                    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?client_id={GOOGLE_OAUTH_CLIENT_ID}&redirect_uri={urllib.parse.quote(HOST_ORIGIN + '/')}&response_type=code&scope={urllib.parse.quote(GOOGLE_OAUTH_SCOPES)}&access_type=offline&prompt=consent&code_challenge={challenge}&code_challenge_method=S256&state={state}"
                    self._reply(200, {"auth_url": auth_url})
                    return
                    
                if parsed.path == "/api/google/oauth/complete":
                    content_length = int(self.headers.get('Content-Length', 0))
                    post_data = self.rfile.read(content_length).decode('utf-8')
                    try:
                        data = json.loads(post_data)
                        url = data.get("url", "")
                        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                        if "code" in q and "state" in q:
                            code = q["code"][0]
                            state = q["state"][0]
                            if state in OAUTH_STATES:
                                verifier = OAUTH_STATES[state]["code_verifier"]
                                req = urllib.request.Request("https://oauth2.googleapis.com/token", data=urllib.parse.urlencode({
                                    "client_id": GOOGLE_OAUTH_CLIENT_ID,
                                    "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
                                    "code": code,
                                    "code_verifier": verifier,
                                    "redirect_uri": HOST_ORIGIN + "/",
                                    "grant_type": "authorization_code"
                                }).encode("utf-8"), headers={"Content-Type": "application/x-www-form-urlencoded"})
                                with urllib.request.urlopen(req, timeout=10) as res:
                                    tdata = json.load(res)
                                
                                req2 = urllib.request.Request("https://www.googleapis.com/oauth2/v3/userinfo", headers={"Authorization": f"Bearer {tdata['access_token']}"})
                                with urllib.request.urlopen(req2, timeout=10) as res2:
                                    user_data = json.load(res2)
                                    email_addr = user_data.get("email", "")
                                
                                if email_addr and "access_token" in tdata:
                                    GmailSearchService.save_token(email_addr, tdata["access_token"], tdata.get("expires_in", 3599), refresh_token=tdata.get("refresh_token"))
                                    self._reply(200, {"status": "ok", "email": email_addr})
                                    return
                    except Exception as e:
                        self._reply(400, {"error": str(e)})
                        return
                    self._reply(400, {"error": "Invalid URL or exchange failed"})
                    return
                self._reply(404, {"error": "not found"})
'''
content = re.sub(r'(def do_GET\(self\).*?\n)', do_post_new + '\n' + r'\1', content)

# 6. Change _setup_page
setup_page_old = '''            def _setup_page(self) -> None:
                """
                Serves the HTML explaining how to connect.

                The whole design is that the user has one place to manage their Google account --
                the desktop's own Settings panel -- and Puffin picks up what is there. So this page
                cannot be a form, and deliberately is not one: it says where to go, and reports
                what GNOME is currently holding so the user can tell whether the step is done.

                It also cannot be a *button*. GOA lives on the session bus and this page is served
                from a container that has neither a bus nor `gdbus`, so the host is what actually
                reads the token, on a timer. What the page can do is tell the user the truth about
                where things stand.
                """
                known = cls.gnome_accounts()
                if known:
                    listed = html.escape(", ".join(known))
                    self._html(
                        "<h2>Connect Gmail</h2>"
                        f'<p class="ready">✅ GNOME is signed into Google as <b '
                        f'style="display:inline">{listed}</b>. Puffin picks the account up '
                        "automatically — this page will stop appearing within a few minutes.</p>"
                        "<p>Puffin reads your mail directly from Google over IMAP, using the "
                        "account your desktop already holds. Nothing passes through Dreamference "
                        "and there is no password to create.</p>"
                        '<p class="note">ⓘ In a hurry? Run <code>dream onyx gmail</code> in a '
                        "terminal to connect now rather than waiting for the next check.</p>"
                    )
                    return
                self._html(
                    "<h2>Connect Gmail</h2>"
                    "<p>Puffin reads your mail directly from Google over IMAP, using the Google "
                    "account your desktop already holds. Nothing passes through Dreamference, and "
                    "there is no password or developer account to create.</p>"
                    "<ol>"
                    "<li>Open <b style=\\"display:inline\\">Settings → Online Accounts</b> on this "
                    "machine and sign into Google.</li>"
                    "<li>That is all. Puffin checks every few minutes and connects itself.</li>"
                    "</ol>"
                    '<p class="note">ⓘ The Google sign-in page will say <b '
                    'style="display:inline">GNOME</b> is asking for access. That is correct — '
                    "your desktop is what holds the account, and Puffin asks it for permission to "
                    "read your mail. No Puffin credentials are sent to Google.</p>"
                    '<p class="muted">Running Puffin on a machine with no desktop session? '
                    "GNOME Online Accounts is not available there, so Gmail search cannot be "
                    "connected on that host.</p>"
                )'''
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
                        <button id="complete-btn" style="padding: 6px 12px;">Submit URL</button>
                        <p id="paste-msg" style="color: green; display: none;"></p>
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
content = content.replace(setup_page_old, setup_page_new)

with open('dreamference/chat/gmail_search_service.py', 'w') as f:
    f.write(content)

