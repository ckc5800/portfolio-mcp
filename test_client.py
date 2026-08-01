"""MCP 서버 스모크 테스트 — stdio로 서버를 띄우고 4개 도구를 실제 호출한다."""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import AnyUrl

SERVER = str(Path(__file__).parent / "server.py")


async def main() -> int:
    failures = 0
    params = StdioServerParameters(command=sys.executable, args=["-X", "utf8", SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print(f"도구 {len(tools.tools)}개 등록:")
            for t in tools.tools:
                print(f"  - {t.name}")
            if len(tools.tools) != 4:
                print(f"[FAIL] 도구 수 4개 기대, {len(tools.tools)}개 등록됨")
                failures += 1

            # 리소스: 문서 5편 + profile.json = 6개
            resources = await session.list_resources()
            uris = sorted(str(r.uri) for r in resources.resources)
            print(f"리소스 {len(uris)}개 등록:")
            for uri in uris:
                print(f"  - {uri}")
            if len(uris) != 6 or "portfolio://profile" not in uris:
                print(f"[FAIL] 리소스 6개(profile 포함) 기대, {uris}")
                failures += 1

            # 읽기까지 검증 — 등록만 되고 내용이 빈 리소스면 의미가 없다
            for uri, check in [
                ("portfolio://docs/tts-deepdive.md", lambda t: "팝 노이즈" in t),
                ("portfolio://profile", lambda t: bool(json.loads(t).get("career"))),
            ]:
                res = await session.read_resource(AnyUrl(uri))
                text = res.contents[0].text if res.contents else ""
                ok = check(text)
                failures += not ok
                print(f"[{'OK' if ok else 'FAIL'}] read_resource({uri}) → {len(text)}자")

            # 응답이 '왔는지'가 아니라 '내용이 맞는지'를 본다.
            # 이전 버전은 bool(text)만 봐서, 도구가 "결과 없음 + hint"를
            # 돌려줘도 통과했다 — 실제로 회사명 필터가 깨져 있었는데
            # 테스트는 계속 초록색이었다. 실패할 수 있어야 테스트다.
            def has_projects(d):
                return len(d.get("projects", [])) > 0

            def has_results(d):
                return len(d.get("results", [])) > 0

            cases = [
                ("portfolio_get_profile", {},
                 lambda d: bool(d.get("career")) and bool(d.get("skills"))),
                # 구 사명으로 물어도 현 직장 프로젝트가 나와야 한다
                ("portfolio_list_projects", {"company": "에이아이세스"}, has_projects),
                ("portfolio_list_projects", {"company": "인피닉"}, has_projects),
                ("portfolio_get_publications", {},
                 lambda d: len(d.get("patents", [])) == 2 and bool(d.get("award"))),
                ("portfolio_search", {"query": "TTFB 최적화", "top_k": 2}, has_results),
                # 조사가 붙은 질의 — bigram 토크나이저 이전에는 0건이었다
                ("portfolio_search", {"query": "쿠버네티스로 뭐 했어", "top_k": 3},
                 has_results),
            ]
            for name, args, check in cases:
                result = await session.call_tool(name, args)
                text = result.content[0].text if result.content else ""
                ok = bool(text) and not result.isError
                if ok:
                    try:
                        ok = check(json.loads(text))
                    except (json.JSONDecodeError, TypeError, AttributeError) as e:
                        ok, text = False, f"{type(e).__name__}: {e}"
                failures += not ok
                label = f"{name}({args})" if args else name
                print(f"[{'OK' if ok else 'FAIL'}] {label} → {text[:90]}...")

    return failures


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
