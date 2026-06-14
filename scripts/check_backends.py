import urllib.request
import json
import sys
import argparse

def main():
    parser = argparse.ArgumentParser(description="Check backend health")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080", help="Gateway base URL")
    args = parser.parse_args()

    url = f"{args.base_url}/api/backends/health"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"Failed to fetch {url}: {e}")
        sys.exit(1)

    print(f"Status: {data.get('status')}")
    print(f"Checked at: {data.get('checked_at')}")
    print("-" * 80)
    
    eligible_count = 0
    backends = data.get("backends", [])
    
    fmt = "{:<15} {:<10} {:<10} {:<10} {:<10} {:<10} {:<15}"
    print(fmt.format("ID", "Configured", "Enabled", "Reachable", "Eligible", "Latency", "Models"))
    print("-" * 80)
    for b in backends:
        latency = f"{b.get('latency_ms')}ms" if b.get('latency_ms') is not None else "N/A"
        models = len(b.get('models', []))
        print(fmt.format(
            b.get("id"),
            str(b.get("configured")),
            str(b.get("enabled")),
            str(b.get("reachable")),
            str(b.get("eligible")),
            latency,
            f"{models} models"
        ))
        if b.get("eligible"):
            eligible_count += 1
            
    if eligible_count == 0:
        print("\nERROR: No inference backends are eligible.")
        sys.exit(1)

if __name__ == "__main__":
    main()
