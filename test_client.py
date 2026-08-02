"""MCP 서버 스모크 테스트 — stdio로 서버를 띄우고 4개 도구를 실제 호출한다."""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import PromptReference

SERVER = str(Path(__file__).parent / "server.py")


def _attr(obj, *names):
    """mcp 2.0이 결과 필드를 snake_case로 바꿨다(isError → is_error 등).
    설치된 메이저에 있는 쪽을 읽는다."""
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    raise AttributeError(f"{type(obj).__name__}: {names} 중 어느 것도 없음")


async def main() -> int:
    failures = 0
    params = StdioServerParameters(command=sys.executable, args=["-X", "utf8", SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()

            # instructions가 initialize 응답에 실려 오는지
            ok = bool(init.instructions) and "portfolio_search" in init.instructions
            failures += not ok
            print(f"[{'OK' if ok else 'FAIL'}] initialize.instructions → "
                  f"{len(init.instructions or '')}자")

            tools = await session.list_tools()
            print(f"도구 {len(tools.tools)}개 등록:")
            for t in tools.tools:
                print(f"  - {t.name}")
            if len(tools.tools) != 6:
                print(f"[FAIL] 도구 수 6개 기대, {len(tools.tools)}개 등록됨")
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
                # str로 넘긴다 — mcp 1.x는 AnyUrl로 코어션하고 2.x는 str을 기대한다
                res = await session.read_resource(uri)
                text = res.contents[0].text if res.contents else ""
                ok = check(text)
                failures += not ok
                print(f"[{'OK' if ok else 'FAIL'}] read_resource({uri}) → {len(text)}자")

            # 프롬프트: candidate_briefing, tech_deep_dive
            prompts = await session.list_prompts()
            print(f"프롬프트 {len(prompts.prompts)}개 등록:")
            for p in prompts.prompts:
                print(f"  - {p.name}")
            if len(prompts.prompts) != 2:
                print(f"[FAIL] 프롬프트 2개 기대, {len(prompts.prompts)}개 등록됨")
                failures += 1

            # 인자가 본문에 실제로 들어가고, 도구 사용 안내가 포함되는지
            gp = await session.get_prompt("tech_deep_dive", {"topic": "TTFB 최적화"})
            ptext = gp.messages[0].content.text if gp.messages else ""
            ok = "TTFB 최적화" in ptext and "portfolio_search" in ptext
            failures += not ok
            print(f"[{'OK' if ok else 'FAIL'}] get_prompt(tech_deep_dive) → {len(ptext)}자")

            # 자동완성: 'TT' → profile.json에서 뽑은 'TTS 프로젝트'가 제안돼야 한다
            comp = await session.complete(
                PromptReference(type="ref/prompt", name="tech_deep_dive"),
                {"name": "topic", "value": "TT"})
            values = comp.completion.values
            ok = any("TTS" in v for v in values)
            failures += not ok
            print(f"[{'OK' if ok else 'FAIL'}] complete(topic='TT') → {values[:3]}")

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
                # 실시간 도구는 오프라인 CI에서도 깨지지 않아야 한다 —
                # 데이터가 오거나, 폴백을 안내하는 hint가 오거나 (graceful)
                ("portfolio_get_github_activity", {},
                 lambda d: bool(d.get("repos")) or bool(d.get("hint"))),
                ("portfolio_get_blog_posts", {},
                 lambda d: bool(d.get("posts")) or bool(d.get("hint"))),
            ]
            for name, args, check in cases:
                result = await session.call_tool(name, args)
                text = result.content[0].text if result.content else ""
                # 텍스트 JSON과 structuredContent가 둘 다 내려와야 한다
                ok = (bool(text) and not _attr(result, "is_error", "isError")
                      and _attr(result, "structured_content",
                                "structuredContent") is not None)
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
