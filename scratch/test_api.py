import requests
import time
import json

URL = "http://localhost:8000/v1/chat/completions"
HEADERS = {"Content-Type": "application/json"}

def test_request(enable_cache=None):
    payload = {
        "model": "Intel/Qwen3.5-122B-A10B-int4-AutoRound",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Count from 1 to 3."}
        ],
        "max_tokens": 10,
        "temperature": 0.0
    }
    
    if enable_cache is not None:
        payload["enable_cache"] = enable_cache

    print(f"Testing with enable_cache={enable_cache}...")
    start = time.time()
    
    try:
        response = requests.post(URL, headers=HEADERS, json=payload)
        elapsed = time.time() - start
        
        if response.status_code == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"]
            print(f"  [SUCCESS] (Took {elapsed:.2f}s) Response: {content.strip()}")
        else:
            print(f"  [ERROR] HTTP {response.status_code}: {response.text}")
    except Exception as e:
        print(f"  [EXCEPTION]: {e}")

if __name__ == "__main__":
    test_request(enable_cache=None)    # Default
    test_request(enable_cache=True)    # Explicit True
    test_request(enable_cache=False)   # Explicit False
    test_request(enable_cache=None)    # Test if cache hits for the first request
