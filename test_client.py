"""MCP 서버 스모크 테스트 — stdio로 서버를 띄우고 4개 도구를 실제 호출한다."""
import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).parent / "server.py")


async def main():
    params = StdioServerParameters(command=sys.executable, args=["-X", "utf8", SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print(f"도구 {len(tools.tools)}개 등록:")
            for t in tools.tools:
                print(f"  - {t.name}")

            cases = [
                ("portfolio_get_profile", {}),
                ("portfolio_list_projects", {"company": "에이아이세스"}),
                ("portfolio_get_publications", {}),
                ("portfolio_search", {"query": "TTFB 최적화", "top_k": 2}),
            ]
            for name, args in cases:
                result = await session.call_tool(name, args)
                text = result.content[0].text if result.content else ""
                status = "OK" if text and not result.isError else "FAIL"
                print(f"[{status}] {name} → {text[:100]}...")


if __name__ == "__main__":
    asyncio.run(main())
