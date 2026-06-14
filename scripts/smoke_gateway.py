import urllib.request
import json
import sys
import argparse
import time

def post_json(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    start = time.time()
    with urllib.request.urlopen(req, timeout=30) as resp:
        res = json.loads(resp.read().decode())
        latency = int((time.time() - start) * 1000)
        return res, latency

def main():
    parser = argparse.ArgumentParser(description="Smoke test the Gateway")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080", help="Gateway base URL")
    parser.add_argument("--skip-quality", action="store_true", help="Skip the quality model test")
    args = parser.parse_args()

    print(f"Running smoke test against {args.base_url}...")
    
    # 1. Models
    try:
        req = urllib.request.Request(f"{args.base_url}/v1/models")
        with urllib.request.urlopen(req, timeout=5) as resp:
            models = json.loads(resp.read().decode()).get("data", [])
            print(f"[OK] Fetched {len(models)} models from gateway.")
    except Exception as e:
        print(f"[FAIL] Failed to fetch models: {e}")
        sys.exit(1)

    # 2. Local-fast
    payload = {
        "model": "local-fast",
        "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
        "max_tokens": 10,
        "stream": False
    }
    try:
        res, lat = post_json(f"{args.base_url}/v1/chat/completions", payload)
        content = res["choices"][0]["message"]["content"].strip()
        print(f"[OK] local-fast responded in {lat}ms: {content}")
    except Exception as e:
        print(f"[FAIL] local-fast failed: {e}")
        sys.exit(1)
        
    # 3. Local-quality
    if not args.skip_quality:
        payload["model"] = "local-quality"
        try:
            res, lat = post_json(f"{args.base_url}/v1/chat/completions", payload)
            content = res["choices"][0]["message"]["content"].strip()
            print(f"[OK] local-quality responded in {lat}ms: {content}")
        except Exception as e:
            print(f"[FAIL] local-quality failed: {e}")
            sys.exit(1)

    # 4. Cloud-best blocked test
    payload["model"] = "cloud-best"
    try:
        # Assuming cloud-best is disabled by default in security.yaml
        post_json(f"{args.base_url}/v1/chat/completions", payload)
        print("[FAIL] cloud-best succeeded but it should be blocked!")
        sys.exit(1)
    except urllib.error.HTTPError as e:
        if e.code in [400, 403, 500, 502, 503]:
            print(f"[OK] cloud-best blocked as expected (HTTP {e.code})")
        else:
            print(f"[FAIL] cloud-best failed with unexpected HTTP {e.code}")
            sys.exit(1)
            
    print("All smoke tests passed.")

if __name__ == "__main__":
    main()
