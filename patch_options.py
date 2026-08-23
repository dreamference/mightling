import re

with open('dreamference/chat/gmail_search_service.py', 'r') as f:
    content = f.read()

options_handler = '''
            def do_OPTIONS(self) -> None:
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", HOST_ORIGIN)
                self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()
'''

content = re.sub(r'(            def do_POST\(self\) -> None:)', options_handler + r'\n\1', content)

with open('dreamference/chat/gmail_search_service.py', 'w') as f:
    f.write(content)

