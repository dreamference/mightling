import requests
import time

URL_MODELS = "http://localhost:8000/v1/models"

print("Waiting for server to start...")
while True:
    try:
        res = requests.get(URL_MODELS)
        if res.status_code == 200:
            print("Server is up!")
            break
    except:
        pass
    time.sleep(2)

import test_api
print("\nRunning tests...")
test_api.test_request(enable_cache=None)
test_api.test_request(enable_cache=True)
test_api.test_request(enable_cache=False)
