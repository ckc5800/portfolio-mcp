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
from typing import Annotated, Any, NotRequired, TypedDict

# mcp 2.0이 FastMCP를 MCPServer로 개명하고 모듈을 옮겼다(fastmcp → mcpserver).
# 데코레이터·리소스 API 형태는 같아서 import만 흡수하면 양쪽 메이저에서 돈다.
try:                                     # mcp >= 2.0
    from mcp.server.mcpserver import MCPServer as FastMCP
    from mcp.server.mcpserver.resources import TextResource
except ImportError:                      # mcp 1.x
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.resources import TextResource
from mcp.types import Completion
from pydantic import Field
from rank_bm25 import BM25Okapi

BASE_DIR = Path(__file__).resolve().parent
DOCS_DIR = BASE_DIR / "data" / "docs"
PROFILE_PATH = BASE_DIR / "data" / "profile.json"

CHUNK_SIZE = 800
MIN_CHUNK_CHARS = 30   # 구분선('---')·제목 줄만 남은 조각은 인덱싱하지 않는다

# instructions는 initialize 응답에 실려, 클라이언트 LLM이 프롬프트를 열지
# 않아도 서버 사용법을 안다. 프롬프트(candidate_briefing 등)의 요약판이다.
mcp = FastMCP(
    "portfolio_mcp",
    instructions=(
        "AI 엔지니어 이윤선의 포트폴리오 서버(read-only). "
        "portfolio_get_profile로 전체 맥락을 잡고, portfolio_list_projects로 "
        "프로젝트를 고른 뒤, 기술 세부사항은 portfolio_search로 검색하라. "
        "검색 결과가 '…(이하 생략)'으로 잘려 있으면 portfolio://docs/<source> "
        "리소스에서 문서 전문을 이어 읽어라. "
        "경력·수치·사실은 도구가 반환한 것만 인용하라."
    ),
)


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


# 소문자화 이후에 매칭하므로 ASCII 클래스는 소문자만 있으면 된다.
# 'vllm-omni', 'rank_bm25'처럼 구두점으로 이어진 식별자는 하나의 런으로 잡는다.
_ASCII_RUN = r"[0-9a-z]+(?:[._-][0-9a-z]+)*"
_HANGUL_RUN = r"[가-힣]+"
_RUNS = re.compile(f"{_ASCII_RUN}|{_HANGUL_RUN}")
_SPLITTABLE = re.compile(r"[._-]")


def _tokenize(text: str) -> list[str]:
    """런(run) 단위 토큰 + 한글 문자 bigram. BM25용 토크나이저.

    단어 단위 토큰만 쓰면 '쿠버네티스로'와 '쿠버네티스'가 다른 토큰이라
    조사가 붙은 한국어 질의에서 검색이 0건이 된다 — 실제로
    '쿠버네티스로 뭐 했어', '최적화를'이 아무것도 못 찾았다. 한글 런에
    문자 bigram을 함께 넣으면 두 표기가 bigram으로 겹쳐 해결된다.
    portfolio-rag-agent의 bm25_tokenize와 같은 규칙이다(복제 이유는
    상단 정제 규칙 주석과 같다 — 의존성 2개 단독 실행이 목표).
    """
    tokens: list[str] = []
    for run in _RUNS.findall(text.lower()):
        tokens.append(run)
        if "가" <= run[0] <= "힣":
            if len(run) >= 2:                      # 한글만 bigram
                tokens += [run[i:i + 2] for i in range(len(run) - 1)]
        elif _SPLITTABLE.search(run):
            # 'rank_bm25' → 부분 토큰도 함께 (통째 토큰은 유지)
            tokens += [p for p in _SPLITTABLE.split(run) if p]
    return tokens


def _load_kb():
    docs, chunks = {}, []
    for path in sorted(DOCS_DIR.glob("*.md")):
        text = _clean_markdown(path.read_text(encoding="utf-8"))
        docs[path.name] = text
        chunks.extend(_chunk_text(text, path.name))
    chunks = [c for c in chunks if len(c["text"]) >= MIN_CHUNK_CHARS]
    if not chunks:
        # 이대로 두면 BM25Okapi가 ZeroDivisionError를 던진다 — 원인을 말해준다
        raise RuntimeError(f"지식 베이스가 비어 있습니다 — {DOCS_DIR}에 .md 문서가 필요합니다")
    bm25 = BM25Okapi([_tokenize(c["text"]) for c in chunks])
    return docs, chunks, bm25


DOCS, CHUNKS, BM25 = _load_kb()
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


# ── Resources ────────────────────────────────────────────
#
# 검색(portfolio_search)은 관련 조각을 찾는 입구고, 리소스는 문서 전문을
# 읽는 경로다. 검색 결과가 '…(이하 생략)'으로 잘려 있으면 클라이언트가
# 해당 문서 리소스를 열어 이어 읽으면 된다. 검색 인덱스와 같은 정제본을
# 노출해 두 경로의 내용이 항상 일치하게 한다.

def _doc_description(text: str) -> str:
    """첫 제목 줄을 설명으로 쓴다 — 파일명보다 무슨 문서인지 잘 말해준다."""
    for line in text.splitlines():
        if line.startswith("#"):
            return line.lstrip("# ").strip() + " — 문서 전문"
    return "포트폴리오 문서 전문"


for _name, _text in DOCS.items():
    mcp.add_resource(TextResource(
        uri=f"portfolio://docs/{_name}",
        name=_name,
        description=_doc_description(_text),
        mime_type="text/markdown",
        text=_text,
    ))

mcp.add_resource(TextResource(
    uri="portfolio://profile",
    name="profile.json",
    description="검증된 경력 사실 전체 — 경력·프로젝트·논문·특허·학력·기술 스택",
    mime_type="application/json",
    text=json.dumps(PROFILE, ensure_ascii=False, indent=2),
))


def _snippet(text: str, limit: int = 700) -> str:
    """긴 청크는 잘라서 반환하되, 잘렸다는 사실을 숨기지 않는다.

    말없이 자르면 모델이 문장이 중간에 끝난 걸 데이터 오류로 오해할 수 있다.
    표식이 있으면 문서 리소스(portfolio://docs/<source>)로 이어 읽으면
    된다는 걸 안다.
    """
    return text if len(text) <= limit else text[:limit] + " …(이하 생략)"


# ── Prompts ──────────────────────────────────────────────
#
# 도구·리소스에 이어 MCP의 세 번째 프리미티브. 클라이언트 UI가 사용자에게
# 노출하는 진입점 템플릿으로, 어떤 도구를 어떤 순서로 쓸지 안내한다.
# 서버가 자기 도구의 올바른 사용법을 함께 배포하는 셈이다.

@mcp.prompt(name="candidate_briefing", title="후보 브리핑")
def candidate_briefing(focus: str = "") -> str:
    """채용 담당자 관점의 이윤선 후보 브리핑을 작성하게 한다.

    focus: 집중할 영역 (예: 'MLOps', 'TTS', '리더십'). 빈 값이면 전체.
    """
    focus_line = f"특히 '{focus}' 관련 경험을 중심으로 봐 주세요.\n" if focus else ""
    return (
        "이윤선(AI 엔지니어) 후보의 포트폴리오를 조사해 채용 담당자용 "
        "브리핑을 작성해 주세요.\n" + focus_line +
        "\n진행 순서:\n"
        "1. portfolio_get_profile — 경력·학력·기술 스택 전체 맥락\n"
        "2. portfolio_list_projects — 프로젝트 목록에서 대표 성과 선별\n"
        "3. portfolio_search — 선별한 성과의 기술적 세부(의사결정, 트러블슈팅) 확인\n"
        "4. portfolio_get_publications — 논문·특허·수상\n"
        "\n작성 규칙: 도구가 반환한 검증된 사실과 수치만 인용하고, 수치에는 "
        "출처 프로젝트를 함께 적어 주세요. 추측은 추측이라고 표시해 주세요."
    )


@mcp.prompt(name="tech_deep_dive", title="기술 딥다이브")
def tech_deep_dive(topic: str) -> str:
    """특정 기술 주제에서 이윤선이 실제로 한 일을 깊게 조사하게 한다.

    topic: 조사할 주제 (예: 'TTFB 최적화', 'Kubernetes', '스트리밍 팝 노이즈').
    """
    return (
        f"이윤선의 포트폴리오에서 '{topic}' 관련 경험을 깊게 조사해 주세요.\n\n"
        f"1. portfolio_search로 '{topic}'을(를) 검색하고, 결과가 부족하면 "
        "연관 키워드로 2~3회 재검색해 주세요.\n"
        "2. 검색 결과가 '…(이하 생략)'으로 잘려 있으면 해당 문서 리소스 "
        "portfolio://docs/<source>를 열어 전문을 읽어 주세요.\n"
        "3. 문제 상황 → 접근 → 결과(수치) 구조로 정리해 주세요.\n\n"
        "문서에 없는 내용은 지어내지 말고 없다고 말해 주세요."
    )


# ── Completions ──────────────────────────────────────────
#
# 프롬프트 인자(topic, focus) 자동완성. 제안 목록을 profile.json의
# 프로젝트명·기술 스택에서 뽑으므로 하드코딩 없이 데이터와 항상 일치한다.

_COMPLETION_VOCAB = sorted(
    {p["name"] for p in PROFILE["projects"]}
    | {s for group in PROFILE["skills"].values() for s in group}
)


@mcp.completion()
async def _complete(ref, argument, context):
    if argument.name not in ("topic", "focus"):
        return None
    prefix = argument.value.lower()
    values = [v for v in _COMPLETION_VOCAB if prefix in v.lower()]
    return Completion(values=values[:20], total=len(values))
#
# dict를 TypedDict 타입으로 반환하면 FastMCP가 텍스트 JSON과 함께
# structuredContent를 내려주고, 반환 타입에서 outputSchema를 만들어
# 클라이언트에 공개한다. 필드는 profile.json의 실제 키와 맞춰야 한다 —
# 필수 필드가 빠지면 출력 검증에서 걸려 스모크 테스트가 빨간불이 된다.

class SearchHit(TypedDict):
    source: str
    score: float
    text: str


class SearchOutput(TypedDict):
    results: list[SearchHit]
    # str만 쓰면 이 SDK가 생략된 키를 None으로 채워 넣어 출력 검증에 걸린다
    hint: NotRequired[str | None]


class Project(TypedDict):
    name: str
    company: str
    period: str
    role: str
    summary: str


class ProjectsOutput(TypedDict):
    projects: list[Project]
    hint: NotRequired[str | None]


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
) -> SearchOutput:
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
         "text": _snippet(CHUNKS[i]["text"])}
        for i in ranked[:top_k] if scores[i] > 0
    ]
    if not results:
        return {
            "results": [],
            "hint": "관련 문서를 찾지 못했습니다. 다른 키워드로 재검색하거나 "
                    "portfolio_list_projects로 프로젝트 목록을 먼저 확인하세요.",
        }
    return {"results": results}


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
) -> ProjectsOutput:
    """이윤선의 전체 프로젝트 목록(회사, 기간, 역할, 성과 요약)을 반환한다.

    검증된 수치·성과만 수록되어 있다. 특정 프로젝트의 기술 세부사항이
    필요하면 portfolio_search로 이어서 검색한다.
    """
    projects = PROFILE["projects"]
    if company:
        projects = [p for p in projects if _company_matches(company, p["company"])]
        if not projects:
            companies = sorted({p["company"] for p in PROFILE["projects"]})
            return {
                "projects": [],
                "hint": f"'{company}' 프로젝트가 없습니다. 보유 회사: {companies}",
            }
    return {"projects": projects}


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
async def portfolio_get_publications() -> dict[str, Any]:
    """이윤선의 논문(제1저자 7편), 특허(제1발명자 2건), 수상 내역을 반환한다."""
    return {
        "publications": PROFILE["publications"],
        "patents": PROFILE["patents"],
        "award": PROFILE["award"],
    }


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
async def portfolio_get_profile() -> dict[str, Any]:
    """이윤선의 기본 프로필(소개, 경력 회사·기간·직급, 학력, 기술 스택, 링크)을 반환한다.

    대화 시작 시 전체 맥락을 잡는 용도로 먼저 호출하기에 적합하다.
    """
    return {
        "name": PROFILE["name"],
        "title": PROFILE["title"],
        "career": PROFILE["career"],
        "education": PROFILE["education"],
        "skills": PROFILE["skills"],
        "links": PROFILE["links"],
    }


if __name__ == "__main__":
    mcp.run()  # stdio transport
