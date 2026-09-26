"""Minimal local AI tool client; only Python's standard library is required."""
import argparse
import json
import os
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class MDReaderClient:
    def __init__(self, base_url="http://127.0.0.1:8642", token=None):
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost") or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
            raise ValueError("base_url 必须是本机 HTTP 地址")
        self.base_url = base_url.rstrip("/")
        self.token = token or os.environ.get("MDREADER_AI_TOKEN", "")
        if not self.token:
            raise ValueError("请设置 MDREADER_AI_TOKEN")

    def _request(self, path, payload=None):
        req = Request(self.base_url + path,
                      data=None if payload is None else json.dumps(payload).encode("utf-8"),
                      headers={"Authorization": "Bearer " + self.token,
                               "Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=30) as response:
                return json.load(response)
        except HTTPError as exc:
            with exc:
                error = json.load(exc)
            raise RuntimeError("HTTP %d: %s" % (exc.code, error.get("error", error))) from None

    def tools(self):
        return self._request("/api/ai/v1/tools")["tools"]

    def call(self, tool_name, **arguments):
        return self._request("/api/ai/v1/tools/call", {"name": tool_name, "arguments": arguments})["result"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8642")
    parser.add_argument("name", nargs="?", help="工具名；省略则列出工具定义")
    parser.add_argument("arguments", nargs="?", default="{}", help="JSON 参数对象")
    args = parser.parse_args()
    client = MDReaderClient(args.url)
    result = client.call(args.name, **json.loads(args.arguments)) if args.name else client.tools()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
