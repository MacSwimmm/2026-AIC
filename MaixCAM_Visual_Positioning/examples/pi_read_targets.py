"""Read the saved device targets on Raspberry Pi; does not command a chassis."""
import argparse
import json
import urllib.request
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", required=True, help="MaixCAM reachable IP")
    p.add_argument("--token-file", required=True, type=Path, help="Private file containing the device API token")
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    token = args.token_file.read_text().strip()
    request = urllib.request.Request(f"http://{args.host}:18765/api/targets/export",
                                    headers={"Authorization": "Bearer " + token})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=5) as response: result = json.load(response)
    if result.get("frame") != "ground" or result.get("units") != "m":
        raise ValueError("Unexpected coordinate convention")
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output: args.output.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__": main()
