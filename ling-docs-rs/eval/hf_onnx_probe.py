"""Which candidate repositories carry ONNX exports, and their licences (metadata only)."""
import json
import sys
import urllib.request

for repo in sys.argv[1:]:
    try:
        info = json.load(urllib.request.urlopen(f"https://huggingface.co/api/models/{repo}?blobs=true", timeout=60))
    except Exception as exc:  # noqa: BLE001
        print(repo, "ERR", exc)
        continue
    lic = (info.get("cardData") or {}).get("license")
    onnx = [(s["rfilename"], round((s.get("size") or 0) / 2**20)) for s in info.get("siblings", [])
            if s["rfilename"].endswith((".onnx", ".onnx_data"))]
    print(repo, lic, onnx[:12])
