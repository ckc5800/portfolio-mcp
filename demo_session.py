"""데모용 MCP 세션 — 실제 서버를 띄워 도구·리소스·프롬프트를 호출하고 트랜스크립트를 출력한다."""
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
            resources = await session.list_resources()
            prompts = await session.list_prompts()
            print(f"$ MCP 서버 연결됨 — 도구 {len(tools.tools)}개 · "
                  f"리소스 {len(resources.resources)}개 · 프롬프트 {len(prompts.prompts)}개")
            print()

            async def call(name, args, label):
                print(f"> {label}")
                print(f"  [tool call] {name}({json.dumps(args, ensure_ascii=False)})")
                result = await session.call_tool(name, args)
                # structured output — 텍스트 파싱 불필요 (mcp 2.0은 snake_case)
                return getattr(result, "structured_content", None) \
                    or result.structuredContent

            d = await call("portfolio_get_profile", {}, "이윤선이 누구야?")
            print(f"  → {d['name']} — {d['title']}")
            print(f"  → 경력: " + " / ".join(c['company'].split(' (')[0] for c in d['career']))
            print()

            d = await call("portfolio_search", {"query": "쿠버네티스로 뭐 했어", "top_k": 1},
                           "쿠버네티스로 뭐 했어?  (조사가 붙어도 한글 bigram으로 매칭)")
            r = d["results"][0]
            print(f"  → [{r['source']}] {' '.join(r['text'].split())[:100]}...")
            print()

            print("> TTS 딥다이브 문서 전문 읽어줘")
            print("  [resource] portfolio://docs/tts-deepdive.md")
            res = await session.read_resource("portfolio://docs/tts-deepdive.md")
            text = res.contents[0].text
            print(f"  → 마크다운 전문 {len(text):,}자 — 검색 결과가 잘렸을 때 이어 읽는 경로")
            print()

            print("> (프롬프트) tech_deep_dive(topic='TTFB 최적화')")
            gp = await session.get_prompt("tech_deep_dive", {"topic": "TTFB 최적화"})
            first_line = gp.messages[0].content.text.split("\n")[0]
            print(f"  → \"{first_line}\"")


if __name__ == "__main__":
    asyncio.run(main())
