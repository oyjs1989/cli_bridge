#!/usr/bin/env python3
"""
MCP Server Proxy - 将 stdio MCP 服务器包装为 HTTP 服务
通过 Unix Domain Socket 提供共享访问
"""
import asyncio
import json
import os
import signal
import sys
import argparse
from typing import Dict, Any, Optional
from pathlib import Path

import aiohttp
from aiohttp import web


class MCPServer:
    """MCP 服务器代理"""
    
    def __init__(self, name: str, config: Dict[str, Any]):
        self.name = name
        self.config = config
        self.process: Optional[asyncio.subprocess.Process] = None
        self.running = False
        
    async def start(self):
        """启动 stdio MCP 服务器"""
        if self.running:
            return
        
        cmd = self.config['command']
        args = self.config.get('args', [])
        env = self.config.get('env', {})
        
        # 合并环境变量
        full_env = os.environ.copy()
        full_env.update(env)
        
        self.process = await asyncio.create_subprocess_exec(
            cmd,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=full_env
        )
        
        self.running = True
        print(f"[{self.name}] MCP server started (PID: {self.process.pid})")
        
    async def stop(self):
        """停止 MCP 服务器"""
        if not self.running:
            return
        
        if self.process:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
            
            self.process = None
        
        self.running = False
        print(f"[{self.name}] MCP server stopped")
    
    async def send_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """发送请求到 MCP 服务器"""
        if not self.process or not self.running:
            raise RuntimeError(f"MCP server {self.name} is not running")
        
        # 发送 JSON-RPC 请求
        request_str = json.dumps(request) + "\n"
        self.process.stdin.write(request_str.encode())
        await self.process.stdin.drain()
        
        # 读取响应
        response_line = await self.process.stdout.readline()
        if not response_line:
            raise RuntimeError(f"No response from MCP server {self.name}")
        
        return json.loads(response_line.decode())


class MCPProxy:
    """MCP 代理服务器"""
    
    def __init__(self, config_path: str):
        self.config = self._load_config(config_path)
        self.servers: Dict[str, MCPServer] = {}
        self.app = web.Application()
        self.app.router.add_post('/{server_name}', self.handle_request)
        self.app.router.add_get('/health', self.handle_health)
        
    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """加载 MCP 配置"""
        with open(config_path, 'r') as f:
            return json.load(f)
    
    async def start_servers(self):
        """启动所有 MCP 服务器"""
        mcp_servers = self.config.get('mcpServers', {})
        
        for name, config in mcp_servers.items():
            if config.get('disabled', False):
                continue
            
            if config.get('type') != 'stdio':
                continue
            
            server = MCPServer(name, config)
            await server.start()
            self.servers[name] = server
    
    async def stop_servers(self):
        """停止所有 MCP 服务器"""
        for server in self.servers.values():
            await server.stop()
        self.servers.clear()
    
    async def handle_request(self, request: web.Request) -> web.Response:
        """处理 MCP 请求"""
        server_name = request.match_info['server_name']
        
        if server_name not in self.servers:
            return web.json_response({
                'error': f'Unknown MCP server: {server_name}'
            }, status=404)
        
        try:
            body = await request.json()
            server = self.servers[server_name]
            response = await server.send_request(body)
            return web.json_response(response)
        except Exception as e:
            return web.json_response({
                'error': str(e)
            }, status=500)
    
    async def handle_health(self, request: web.Request) -> web.Response:
        """健康检查"""
        return web.json_response({
            'status': 'healthy',
            'servers': list(self.servers.keys())
        })
    
    async def start_http_server(self, port: int):
        """启动 HTTP 服务器"""
        runner = web.AppRunner(self.app)
        await runner.setup()
        
        # 使用 TCP 端口（iflow 不支持 Unix Socket）
        site = web.TCPSite(runner, 'localhost', port)
        await site.start()
        
        print(f"MCP Proxy listening on HTTP port: http://localhost:{port}")
        print(f"Available MCP servers: {list(self.servers.keys())}")
        
        return runner


async def main():
    parser = argparse.ArgumentParser(description='MCP Server Proxy')
    parser.add_argument('--config', required=True, help='Path to MCP config file')
    parser.add_argument('--port', default=8888, type=int, help='HTTP port (default: 8888)')
    args = parser.parse_args()
    
    proxy = MCPProxy(args.config)
    
    # 启动信号处理
    loop = asyncio.get_event_loop()
    
    def signal_handler():
        print("\nShutting down...")
        loop.create_task(shutdown(proxy))
    
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)
    
    # 启动 MCP 服务器
    await proxy.start_servers()
    
    # 启动 HTTP 服务器
    runner = await proxy.start_http_server(args.port)
    
    # 保持运行
    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        await shutdown(proxy)


async def shutdown(proxy: MCPProxy):
    """关闭代理"""
    await proxy.stop_servers()
    print("Shutdown complete")
    sys.exit(0)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted")
        sys.exit(0)
