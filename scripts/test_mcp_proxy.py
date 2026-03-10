#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request

BASE = "http://localhost:8888"


def fetch_json(url: str, method: str = "GET", payload: dict | None = None) -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_endpoint(name: str, endpoint: str) -> bool:
    print(f"测试 {name} ... ", end="")
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "1",
            "clientCapabilities": {},
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        },
    }
    try:
        response = fetch_json(f"{BASE}{endpoint}", method="POST", payload=payload)
    except Exception as e:
        print(f"失败 ({e})")
        return False
    if "result" in response:
        result = response["result"]
        server_name = result.get("serverInfo", {}).get("name") if isinstance(result, dict) else None
        print("通过")
        if server_name:
            print(f"  响应: {server_name}")
        else:
            print(f"  响应: {result}")
        return True
    print(f"失败\n  响应: {response}")
    return False


def process_count() -> str:
    try:
        if sys.platform.startswith("win"):
            result = subprocess.run(["tasklist"], capture_output=True, text=True, timeout=10)
            lines = result.stdout.splitlines()
            keywords = ("simple_server", "mcp-server-github", "message-board", "mcp_proxy")
            return str(sum(1 for line in lines if any(k in line for k in keywords)))
        result = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=10)
        lines = result.stdout.splitlines()
        count = 0
        for line in lines:
            if any(k in line for k in ("simple_server", "mcp-server-github", "message-board")) and "mcp_proxy" not in line:
                count += 1
        return str(count)
    except Exception:
        return "unknown"


def main() -> int:
    print("=== MCP 代理功能测试 ===\n")

    print("1. 检查 MCP 代理状态")
    try:
        health = fetch_json(f"{BASE}/health")
        print("✓ MCP 代理正在运行")
        print(f"  可用服务器: {health.get('servers')}")
    except urllib.error.URLError:
        print("✗ MCP 代理未运行")
        print("请先运行: python -m cli_bridge.mcp_proxy --config <config> --port 8888")
        return 1
    print()

    print("2. 测试 MCP 端点")
    ok = True
    ok &= test_endpoint("GitHub MCP", "/github")
    ok &= test_endpoint("DNF RAG MCP", "/dnf-rag")
    ok &= test_endpoint("Message Board MCP", "/message-board")
    print()

    print("3. 检查 MCP 进程数")
    print(f"  当前 MCP 进程数: {process_count()}")
    print()

    print("=== 测试完成 ===")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
