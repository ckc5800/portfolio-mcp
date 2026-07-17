"""데모용 MCP 세션 — 실제 서버를 띄워 도구를 호출하고 대화형 트랜스크립트를 출력한다."""
import asyncio
import json
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
            print("$ MCP 서버 연결됨 — 도구 " + str(len(tools.tools)) + "개:")
            for t in tools.tools:
                print(f"   · {t.name}")
            print()

            async def call(name, args, label):
                print(f"> {label}")
                print(f"  [tool call] {name}({json.dumps(args, ensure_ascii=False)})")
                result = await session.call_tool(name, args)
                data = json.loads(result.content[0].text)
                return data

            d = await call("portfolio_get_profile", {}, "이윤선이 누구야?")
            print(f"  → {d['name']} — {d['title']}")
            print(f"  → 경력: " + " / ".join(c['company'].split(' ')[0] for c in d['career']))
            print()

            d = await call("portfolio_search", {"query": "스트리밍 팝 노이즈 해결", "top_k": 1},
                           "TTS 스트리밍 팝 노이즈는 어떻게 해결했어?")
            r = d["results"][0]
            print(f"  → [{r['source']}] {r['text'][:120]}...")
            print()

            d = await call("portfolio_get_publications", {}, "특허 등록번호 알려줘")
            for p in d["patents"]:
                print(f"  → {p['title']} — 등록번호 {p['reg_no']}")


if __name__ == "__main__":
    asyncio.run(main())
