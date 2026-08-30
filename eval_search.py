"""검색 품질 평가. README가 주장하는 수치를 이 스크립트로 재현한다.

README에 청킹·토크나이저·임계값을 고친 근거로 실측 표를 실어 뒀는데,
정작 그 표를 재현할 방법이 저장소에 없으면 수치는 그냥 주장이 된다.
실제로 그렇게 방치한 사이 README의 청크 수가 현재 코드와 어긋났다.

두 가지를 잰다.

1. 정답 포함률. 문서에 실제로 있는 사실로 질의를 만들고, 도구가 반환하는
   본문(잘림 포함)에 정답 키워드가 들어 있는지 본다. 청크에 있는데 잘려
   나가면 사용자에게는 없는 것과 같으므로, 인덱스가 아니라 반환값을 채점한다.
2. 무의미 질의 거부율. 이 포트폴리오와 무관한 질문에 MIN_SCORE_PER_TOKEN이
   결과 0건과 힌트를 돌려주는지 본다. 정밀도 쪽 대조군이 없으면 "정답을 잘
   찾는다"는 지표는 그냥 아무거나 많이 돌려줘서 올릴 수 있다.

기준선에 미달하면 종료 코드 1을 반환하므로 CI가 검색 품질 회귀를 잡는다.
"""
import asyncio
import sys

import server

# (질의, 반환 본문에 반드시 있어야 하는 키워드). 전부 data/docs에 있는 사실만 쓴다.
# 문서에 없는 용어를 정답으로 쓰면(예전에 'YOLO'로 그랬다) 평가가 거짓말을 한다.
CASES = [
    ("TTFB 최적화", "TTFB"),
    ("팝 노이즈 어떻게 해결했어", "노이즈"),
    ("쿠버네티스로 뭐 했어", "쿠버네티스"),
    ("MetalLB LoadBalancer", "MetalLB"),
    ("ArgoCD CI/CD 파이프라인", "ArgoCD"),
    ("BART 대화 요약", "BART"),
    ("특허 등록번호", "등록번호"),
    ("Pyannote 화자 분할", "Pyannote"),
    ("Latent Diffusion 합성 데이터", "Diffusion"),
    ("Triton Inference Server", "Triton"),
    ("탱크 탐지 모델", "탱크"),
    ("ITN 정확도", "ITN"),
]

# 이 포트폴리오와 무관한 질의. 결과 0건과 힌트가 정답이다
JUNK = [
    "존재하지않는키워드zzz",
    "양자컴퓨팅 큐비트 결맞음",
    "블록체인 스마트컨트랙트 가스비",
    "오늘 점심 뭐 먹지",
    "고양이 사료 추천",
    "축구 월드컵 우승",
]

# 알려진 한계. 통과를 기대하지 않으므로 채점에 넣지 않고 현황만 출력한다.
# MIN_SCORE_PER_TOKEN을 손대려는 사람이 여기부터 보게 하려고 남긴다.
#
# 코퍼스 전체에 퍼진 흔한 용어를 단독으로 물으면 IDF가 낮아 점수가 안 나온다.
# 그래서 정상 질의가 무의미 질의보다 아래에 깔린다 (실측, 최고점/토큰수):
#
#     'vLLM 써봤어요?'          1.1 / 5토큰  = 0.23   ← 정상
#     '양자컴퓨팅 큐비트 결맞음'    5.1 / 11토큰 = 0.47   ← 무의미
#     '블록체인 스마트컨트랙트 가스비' 5.3 / 14토큰 = 0.38   ← 무의미
#     '추론 최적화 어떻게 했어요'    10.6 / 11토큰 = 0.97   ← 정상
#
# 점수로도 점수/토큰으로도 두 집합이 겹친다 — 임계값을 어디에 두든 한쪽이
# 깨진다. 실제로 문턱을 '코퍼스에 있는 토큰 수'로만 계산해 봤더니 자연어
# 질의는 살아났지만 무의미 거부가 6/6 → 3/6으로 무너졌다.
# 임계값 문제가 아니라 코퍼스 규모 문제다(청크 56개, 한 사람 한 주제라
# vllm·kubernetes 같은 핵심 용어가 대부분의 청크에 등장한다).
# 완화책은 이미 있다 — 0건이면 hint가 재검색을 안내한다.
KNOWN_MISSES = [
    ("vLLM 써봤어요?", "흔한 용어 단독 — IDF가 낮아 문턱 미달"),
    ("연봉 얼마 받았어요", "코퍼스에 없는 정보 — 0건이 정답"),
]

# 회귀 기준선. 현재 실측보다 살짝 낮게 둬서 잡음은 통과시키고 붕괴는 잡는다.
# 현재 top1 12/12. 한 건 정도의 흔들림은 통과시키고 붕괴만 잡는다.
MIN_TOP1 = 11
MIN_TOP4 = 12
MIN_JUNK_REJECTED = 6


async def main() -> int:
    top1 = top4 = 0
    misses = []
    for query, keyword in CASES:
        found = await server.portfolio_search(query, 4)
        bodies = [r["text"].lower() for r in found["results"]]
        needle = keyword.lower()
        hit1 = bool(bodies) and needle in bodies[0]
        hitk = any(needle in b for b in bodies)
        top1 += hit1
        top4 += hitk
        if not hit1:
            misses.append(f"{query!r} (기대 {keyword}, top4={'O' if hitk else 'X'})")

    rejected = 0
    leaked = []
    for query in JUNK:
        found = await server.portfolio_search(query, 4)
        if found["results"]:
            leaked.append(f"{query!r} → {len(found['results'])}건")
        else:
            rejected += 1

    known = []
    for query, why in KNOWN_MISSES:
        found = await server.portfolio_search(query, 4)
        n = len(found["results"])
        known.append(f"{query!r} {n}건 — {why}")

    sizes = sorted(len(c["text"]) for c in server.CHUNKS)
    print(f"청크 {len(sizes)}개 · 최대 {sizes[-1]}자 · 중앙값 {sizes[len(sizes) // 2]}자")
    print(f"정답 포함률   top1 {top1}/{len(CASES)} · top4 {top4}/{len(CASES)}")
    print(f"무의미 질의 거부 {rejected}/{len(JUNK)}")
    for m in misses:
        print(f"  - top1 미스: {m}")
    for m in leaked:
        print(f"  - 무의미 통과: {m}")
    print("알려진 한계 (채점 제외):")
    for m in known:
        print(f"  - {m}")

    failed = (top1 < MIN_TOP1 or top4 < MIN_TOP4 or rejected < MIN_JUNK_REJECTED)
    print("FAIL: 검색 품질 기준선 미달" if failed else "OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
