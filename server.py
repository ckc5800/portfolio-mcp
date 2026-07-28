"""portfolio_mcp — AI 엔지니어 이윤선의 포트폴리오를 노출하는 MCP 서버.

MCP 클라이언트(Claude Desktop, Claude Code 등)가 포트폴리오 문서 검색과
구조화된 경력 정보 조회를 도구로 사용할 수 있게 한다.

- 검색: BM25 키워드 검색 (외부 서비스/임베딩 서버 불필요 — 설치 즉시 동작)
- 구조화 정보: data/profile.json (검증된 사실만 수록)
- Transport: stdio (로컬 서버)
"""
import json
import re
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field
from rank_bm25 import BM25Okapi

BASE_DIR = Path(__file__).resolve().parent
DOCS_DIR = BASE_DIR / "data" / "docs"
PROFILE_PATH = BASE_DIR / "data" / "profile.json"

CHUNK_SIZE = 800
MIN_CHUNK_CHARS = 30   # 구분선('---')·제목 줄만 남은 조각은 인덱싱하지 않는다

mcp = FastMCP("portfolio_mcp")


# ── 원문 정제 ────────────────────────────────────────────
#
# 노션 내보내기에는 검색에 쓸모없는 장식이 섞여 있다. 특히 내부 페이지 링크가
# 퍼센트 인코딩된 한글 파일명으로 나오는데(%EC%9D%B4...), BM25 토크나이저가
# 이걸 'ec', 'd', 'b', '9' 같은 쓰레기 토큰으로 쪼갠다. 토큰이 늘면 BM25의
# 문서 길이 정규화가 해당 청크에 페널티를 주므로 순위가 실제로 나빠지고,
# 도구가 돌려주는 본문에도 그대로 섞여 모델의 컨텍스트를 낭비한다.
#
# 규칙은 portfolio-rag-agent의 ingest.clean_markdown과 같다. 이 서버는
# 의존성 2개로 단독 실행되는 것이 목표라 공용 모듈로 빼지 않고 복제했다.
_COLOR_MACRO = re.compile(r"\$\\color\{[^}]*\}\{([^}]*)\}\$")
_IMAGE_EMBED = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\((?:[^()]|\([^()]*\))*\)")
_LEFTOVER_TARGET = re.compile(
    r"\]\((?!https?://)(?:[^()]|\([^()]*\))*%[0-9A-Fa-f]{2}(?:[^()]|\([^()]*\))*\)")
_HTML_TAG = re.compile(r"</?(?:br|div|span|aside|img|p)\b[^>]*/?>", re.I)
_EXTRA_BLANK = re.compile(r"\n{3,}")


def _strip_notion_link(m: re.Match) -> str:
    """노션 내부 링크는 텍스트만 남기고, 외부 URL 링크는 그대로 둔다."""
    full = m.group(0)
    target = full[full.rindex("](") + 2:-1]
    if target.startswith(("http://", "https://")):
        return full          # 실제 URL — "깃허브 주소" 같은 질문에 답해야 한다
    if "%" in target:
        return m.group(1)    # 인코딩된 내부 경로 — 텍스트만 남긴다
    return full


def _clean_markdown(text: str) -> str:
    text = _COLOR_MACRO.sub(r"\1", text)
    text = _IMAGE_EMBED.sub("", text)
    text = _MD_LINK.sub(_strip_notion_link, text)
    text = _LEFTOVER_TARGET.sub("", text)
    text = _HTML_TAG.sub(" ", text)
    return _EXTRA_BLANK.sub("\n\n", text)


# ── 지식 베이스 로딩 (서버 시작 시 1회) ──────────────────

def _chunk_text(text: str, source: str) -> list[dict]:
    """단락 단위로 병합하며 CHUNK_SIZE 근처로 청킹."""
    chunks, buf = [], ""
    for para in text.split("\n\n"):
        if len(buf) + len(para) > CHUNK_SIZE and buf:
            chunks.append({"source": source, "text": buf.strip()})
            buf = ""
        buf += para + "\n\n"
    if buf.strip():
        chunks.append({"source": source, "text": buf.strip()})
    return chunks


def _tokenize(text: str) -> list[str]:
    """한/영/숫자 토큰 추출 (소문자화). BM25용 단순 토크나이저."""
    return re.findall(r"[가-힣]+|[a-zA-Z]+|\d+", text.lower())


def _load_kb():
    chunks = []
    for path in sorted(DOCS_DIR.glob("*.md")):
        text = _clean_markdown(path.read_text(encoding="utf-8"))
        chunks.extend(_chunk_text(text, path.name))
    chunks = [c for c in chunks if len(c["text"]) >= MIN_CHUNK_CHARS]
    bm25 = BM25Okapi([_tokenize(c["text"]) for c in chunks])
    return chunks, bm25


CHUNKS, BM25 = _load_kb()
PROFILE = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def _company_matches(query: str, project_company: str) -> bool:
    """회사명 필터 — 구 사명으로 물어도 찾히게 한다.

    career에는 정식 명칭이 'MiCo AI (구 에이아이세스)'처럼 들어 있는데
    projects에는 'MiCo AI'로만 적혀 있다. 그래서 사용자가 '에이아이세스'로
    물으면 아무것도 안 나왔다 — 현 직장인데도. career를 별칭 사전처럼 써서
    두 표기를 잇는다.
    """
    if query in project_company:
        return True
    return any(query in c["company"] and project_company in c["company"]
               for c in PROFILE["career"])


# ── Tools ────────────────────────────────────────────────

@mcp.tool(
    name="portfolio_search",
    annotations={
        "title": "포트폴리오 문서 검색",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def portfolio_search(
    query: Annotated[str, Field(
        description="검색 질의 (예: 'TTFB 최적화', 'Kubernetes CI/CD', '특허 번호')",
        min_length=1, max_length=200)],
    top_k: Annotated[int, Field(
        description="반환할 문서 청크 수", ge=1, le=10)] = 4,
) -> str:
    """이윤선의 포트폴리오/기술문서에서 관련 내용을 키워드(BM25) 검색한다.

    프로젝트 상세, 기술적 의사결정, 트러블슈팅 과정 등 profile 도구가
    제공하지 않는 세부 내용을 찾을 때 사용한다.
    출처 파일명과 함께 관련 청크를 반환한다.
    """
    scores = BM25.get_scores(_tokenize(query))
    ranked = sorted(range(len(CHUNKS)), key=lambda i: scores[i], reverse=True)
    results = [
        {"source": CHUNKS[i]["source"],
         "score": round(float(scores[i]), 2),
         "text": CHUNKS[i]["text"][:700]}
        for i in ranked[:top_k] if scores[i] > 0
    ]
    if not results:
        return json.dumps({
            "results": [],
            "hint": "관련 문서를 찾지 못했습니다. 다른 키워드로 재검색하거나 "
                    "portfolio_list_projects로 프로젝트 목록을 먼저 확인하세요.",
        }, ensure_ascii=False)
    return json.dumps({"results": results}, ensure_ascii=False)


@mcp.tool(
    name="portfolio_list_projects",
    annotations={
        "title": "프로젝트 목록 조회",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def portfolio_list_projects(
    company: Annotated[str, Field(
        description="회사명으로 필터링 (예: '에이아이세스', '인피닉'). 빈 값이면 전체 반환",
        max_length=50)] = "",
) -> str:
    """이윤선의 전체 프로젝트 목록(회사, 기간, 역할, 성과 요약)을 반환한다.

    검증된 수치·성과만 수록되어 있다. 특정 프로젝트의 기술 세부사항이
    필요하면 portfolio_search로 이어서 검색한다.
    """
    projects = PROFILE["projects"]
    if company:
        projects = [p for p in projects if _company_matches(company, p["company"])]
        if not projects:
            companies = sorted({p["company"] for p in PROFILE["projects"]})
            return json.dumps({
                "projects": [],
                "hint": f"'{company}' 프로젝트가 없습니다. 보유 회사: {companies}",
            }, ensure_ascii=False)
    return json.dumps({"projects": projects}, ensure_ascii=False)


@mcp.tool(
    name="portfolio_get_publications",
    annotations={
        "title": "논문·특허 조회",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def portfolio_get_publications() -> str:
    """이윤선의 논문(제1저자 7편), 특허(제1발명자 2건), 수상 내역을 반환한다."""
    return json.dumps({
        "publications": PROFILE["publications"],
        "patents": PROFILE["patents"],
        "award": "우수논문상 — 국방기술학회 (2023.11)",
    }, ensure_ascii=False)


@mcp.tool(
    name="portfolio_get_profile",
    annotations={
        "title": "경력 프로필 조회",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def portfolio_get_profile() -> str:
    """이윤선의 기본 프로필(소개, 경력 회사·기간·직급, 학력, 기술 스택, 링크)을 반환한다.

    대화 시작 시 전체 맥락을 잡는 용도로 먼저 호출하기에 적합하다.
    """
    return json.dumps({
        "name": PROFILE["name"],
        "title": PROFILE["title"],
        "career": PROFILE["career"],
        "education": PROFILE["education"],
        "skills": PROFILE["skills"],
        "links": PROFILE["links"],
    }, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()  # stdio transport
