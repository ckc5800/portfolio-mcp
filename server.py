"""portfolio_mcp: AI 엔지니어 이윤선의 포트폴리오를 노출하는 MCP 서버.

MCP 클라이언트(Claude Desktop, Claude Code 등)가 포트폴리오 문서 검색과
구조화된 경력 정보 조회를 도구로 사용할 수 있게 한다.

- 검색: BM25 키워드 검색 (외부 서비스나 임베딩 서버 없이 설치 즉시 동작)
- 구조화 정보: data/profile.json (검증된 사실만 수록)
- Transport: stdio (로컬 서버)
"""
import asyncio
import html
import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Annotated, Any, NotRequired, TypedDict
from xml.etree import ElementTree

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

# 검색 결과로 인정할 최소 점수. '질의 토큰 1개당' 기준이다.
#
# bigram 토크나이저를 넣은 뒤로 아무 한국어 질의나 조금씩은 매칭된다.
# '양자컴퓨팅 큐비트 결맞음'처럼 이 포트폴리오와 무관한 질문에도 점수 5점대가
# 나와서, "못 찾았으니 다른 키워드로" 힌트가 사실상 죽어 있었다.
#
# 절대 임계값은 쓸 수 없다. 실측하면 정상 단일어 질의가 더 낮게 나온다.
# 'MetalLB' 4.6, 'OCR' 4.4, 'Redis' 1.8인데 무의미 질의가 5.5다. BM25 점수는
# 질의 토큰 수에 비례해 커지기 때문이다. 토큰 수로 나누면 뒤집힌다:
# 무의미 질의 0.00~0.55 / 정상 질의 1.82~4.87. 양쪽에 여유를 두고 1.0.
MIN_SCORE_PER_TOKEN = 1.0

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
        "최근 활동은 portfolio_get_github_activity(GitHub)·"
        "portfolio_get_blog_posts(블로그)로 실시간 조회하고, 재직 회사의 "
        "공식 홈페이지는 portfolio_get_company_info로 확인하라. "
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
# 아키텍처 다이어그램의 박스 그리기 문자. 노션 인코딩 노이즈와 같은 계열의
# 문제인데 방향이 반대다. 이 문자들은 토큰화되지 않으므로 청크의 글자 수만
# 부풀린다. 그러면 BM25가 보는 토큰 수는 그대로라 다이어그램 청크가 '아주
# 짧은 문서'로 취급돼 길이 정규화에서 부당하게 유리해진다(실측: 732자에
# 토큰 42개, 같은 길이 산문은 토큰 171개). 레이아웃만 지우고 내용은 남긴다.
_BOX_DRAWING = re.compile(r"[─-╿]+")
_EXTRA_BLANK = re.compile(r"\n{3,}")


def _strip_notion_link(m: re.Match) -> str:
    """노션 내부 링크는 텍스트만 남기고, 외부 URL 링크는 그대로 둔다."""
    full = m.group(0)
    target = full[full.rindex("](") + 2:-1]
    if target.startswith(("http://", "https://")):
        return full          # 실제 URL. "깃허브 주소" 같은 질문에 답해야 한다
    if "%" in target:
        return m.group(1)    # 인코딩된 내부 경로. 텍스트만 남긴다
    return full


def _clean_markdown(text: str) -> str:
    text = _COLOR_MACRO.sub(r"\1", text)
    text = _IMAGE_EMBED.sub("", text)
    text = _MD_LINK.sub(_strip_notion_link, text)
    text = _LEFTOVER_TARGET.sub("", text)
    text = _HTML_TAG.sub(" ", text)
    text = _BOX_DRAWING.sub(" ", text)
    return _EXTRA_BLANK.sub("\n\n", text)


# ── 지식 베이스 로딩 (서버 시작 시 1회) ──────────────────

def _split_oversized(para: str) -> list[str]:
    """빈 줄 없이 이어지는 거대 블록을 줄 단위로 다시 쪼갠다.

    노션은 중첩 리스트를 들여쓰기 + 단일 개행으로 내보낸다. 그래서 문서
    한 편이 통째로 '단락 하나'가 되는 일이 생긴다. 실제로 portfolio.md에
    16,623자짜리 단락이 있었다. 빈 줄로만 자르면 이게 청크 하나가 되는데,
    BM25는 문서 길이로 점수를 정규화하므로 그 거대 청크가 어떤 질의에도
    상위로 못 올라오고, 올라와도 도구는 앞부분만 잘라 돌려준다.
    """
    if len(para) <= CHUNK_SIZE:
        return [para]
    parts, buf = [], ""
    for line in para.split("\n"):
        if len(buf) + len(line) > CHUNK_SIZE and buf:
            parts.append(buf.strip())
            buf = ""
        buf += line + "\n"
    if buf.strip():
        parts.append(buf.strip())
    return parts


def _chunk_text(text: str, source: str) -> list[dict]:
    """단락 단위로 병합하며 CHUNK_SIZE 근처로 청킹."""
    chunks, buf = [], ""
    for para in text.split("\n\n"):
        for block in _split_oversized(para):
            if len(buf) + len(block) > CHUNK_SIZE and buf:
                chunks.append({"source": source, "text": buf.strip()})
                buf = ""
            buf += block + "\n\n"
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
    조사가 붙은 한국어 질의에서 검색이 0건이 된다. 실제로
    '쿠버네티스로 뭐 했어', '최적화를'이 아무것도 못 찾았다. 한글 런에
    문자 bigram을 함께 넣으면 두 표기가 bigram으로 겹쳐 해결된다.
    portfolio-rag-agent의 bm25_tokenize와 같은 규칙이다(복제 이유는
    상단 정제 규칙 주석과 같다. 의존성 2개로 단독 실행하는 게 목표다).
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
        # 이대로 두면 BM25Okapi가 ZeroDivisionError를 던진다. 원인을 말해준다
        raise RuntimeError(f"지식 베이스가 비어 있습니다. {DOCS_DIR}에 .md 문서가 필요합니다")
    bm25 = BM25Okapi([_tokenize(c["text"]) for c in chunks])
    return docs, chunks, bm25


DOCS, CHUNKS, BM25 = _load_kb()
PROFILE = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def _company_matches(query: str, project_company: str) -> bool:
    """회사명 필터. 구 사명으로 물어도 찾히게 한다.

    career에는 정식 명칭이 'MiCo AI (구 에이아이세스)'처럼 들어 있는데
    projects에는 'MiCo AI'로만 적혀 있다. 그래서 사용자가 '에이아이세스'로
    물으면 아무것도 안 나왔다. 현 직장인데도. career를 별칭 사전처럼 써서
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
    """첫 제목 줄을 설명으로 쓴다. 파일명보다 무슨 문서인지 잘 말해준다."""
    for line in text.splitlines():
        if line.startswith("#"):
            return line.lstrip("# ").strip() + " (문서 전문)"
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
    description="검증된 경력 사실 전체. 경력·프로젝트·논문·특허·학력·기술 스택",
    mime_type="application/json",
    text=json.dumps(PROFILE, ensure_ascii=False, indent=2),
))


def _snippet(text: str, limit: int = CHUNK_SIZE) -> str:
    """긴 청크는 잘라서 반환하되, 잘렸다는 사실을 숨기지 않는다.

    말없이 자르면 모델이 문장이 중간에 끝난 걸 데이터 오류로 오해할 수 있다.
    표식이 있으면 문서 리소스(portfolio://docs/<source>)로 이어 읽으면
    된다는 걸 안다. 기본 한계를 CHUNK_SIZE에 맞춰 두는 이유는, 그보다
    작으면 정상 크기 청크마저 매번 잘려 나가기 때문이다.
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
        "1. portfolio_get_profile: 경력·학력·기술 스택 전체 맥락\n"
        "2. portfolio_list_projects: 프로젝트 목록에서 대표 성과 선별\n"
        "3. portfolio_search: 선별한 성과의 기술적 세부(의사결정, 트러블슈팅) 확인\n"
        "4. portfolio_get_publications: 논문·특허·수상\n"
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
# 프롬프트 인자(topic, focus) 자동완성. 제안 목록을 profile.json과 문서
# 제목에서 뽑으므로 하드코딩 없이 데이터와 항상 일치한다.
#
# 처음에는 프로젝트명·기술 스택만 썼는데, 그러면 tech_deep_dive의 docstring이
# 예시로 드는 'TTFB 최적화'조차 제안되지 않았다. 정작 검색으로는 잘 찾히는
# 주제인데도. 실제 주제어는 문서 제목에 있어서 거기서도 함께 뽑는다.

_HEADING = re.compile(r"^#{1,4}\s+(.+?)\s*$", re.M)
_HEADING_NUMBER = re.compile(r"^[\d.\s]+")
_TERM = re.compile(r"[A-Za-z][A-Za-z0-9-]{2,}")
# 흔한 영어 단어는 주제어가 아니다. 제안 목록만 어지럽힌다
_TERM_STOP = {"the", "and", "for", "with", "from", "this", "that", "was", "were",
              "have", "has", "not", "you", "your", "web", "api", "app", "use"}


def _heading_terms() -> set[str]:
    """문서 제목을 주제어 후보로 쓴다. 번호 접두사는 떼고 길이를 제한한다."""
    terms = set()
    for text in DOCS.values():
        for title in _HEADING.findall(text):
            title = _HEADING_NUMBER.sub("", re.sub(r"[*`\[\]()]", "", title)).strip()
            if 2 <= len(title) <= 40:
                terms.add(title)
    return terms


def _corpus_terms(min_docs: int = 2) -> set[str]:
    """문서 2편 이상에 나오는 영문 기술 용어. TTFB·ITN 같은 약어가 여기서 나온다."""
    seen_in: dict[str, set[str]] = {}
    for name, text in DOCS.items():
        for term in set(_TERM.findall(text)):
            if term.lower() not in _TERM_STOP:
                seen_in.setdefault(term, set()).add(name)
    return {t for t, docs in seen_in.items() if len(docs) >= min_docs}


def _dedupe_ci(terms: set[str]) -> list[str]:
    """대소문자만 다른 중복을 없앤다. 'ITN'과 'itn'을 둘 다 제안할 이유가 없다."""
    best: dict[str, str] = {}
    for term in sorted(terms):          # 정렬상 대문자 표기가 먼저 와서 채택된다
        best.setdefault(term.lower(), term)
    return sorted(best.values())


_COMPLETION_VOCAB = _dedupe_ci(
    {p["name"] for p in PROFILE["projects"]}
    | {s for group in PROFILE["skills"].values() for s in group}
    | _heading_terms()
    | _corpus_terms()
)


@mcp.completion()
async def _complete(ref, argument, context):
    if argument.name not in ("topic", "focus"):
        return None
    typed = argument.value.lower()
    matches = [v for v in _COMPLETION_VOCAB if typed in v.lower()]
    # 'TT'를 치면 'TTS 프로젝트'가 'HTTP'보다 먼저여야 한다. 접두사 일치를
    # 앞에 두고, 같은 조건이면 짧은 쪽(더 일반적인 주제어)을 먼저 제안한다.
    matches.sort(key=lambda v: (not v.lower().startswith(typed), len(v), v))
    return Completion(values=matches[:20], total=len(matches))


# ── 실시간 조회 (표준 라이브러리만 사용, 의존성 2개 유지) ──
#
# 정적 사실은 profile.json이지만, "요즘도 활동하나?"는 웹에서만 답할 수
# 있다. GitHub·블로그는 이윤선 본인의 공개 데이터라 이 서버의 범위
# 안이다. 일반 웹 검색은 넣지 않는다. 클라이언트가 이미 갖고 있고,
# 이 서버는 이윤선 데이터만 정확하게 내려주는 것이 역할이다.

_HTTP_TIMEOUT = 6
_HTTP_MAX_BYTES = 2_000_000  # GitHub/블로그 응답은 수 KB대. 그보다 크면 잘라서 메모리 보호
_CACHE_TTL = 600          # GitHub 무인증 60회/시 제한 대비
_FAIL_TTL = 60            # 실패도 잠깐 기억한다 (아래 설명)
_http_cache: dict[str, tuple[float, str | Exception]] = {}


def _http_get(url: str) -> str:
    """TTL 캐시를 얹은 GET. blocking이므로 도구에서는 to_thread로 감싼다.

    실패도 짧게 캐시한다. 안 그러면 네트워크가 막힌 환경에서 도구를 부를
    때마다 타임아웃까지 6초씩 기다리는데, instructions가 클라이언트에게
    이 도구들을 쓰라고 안내하므로 연속 호출이 실제로 일어난다.
    """
    now = time.time()
    hit = _http_cache.get(url)
    if hit and hit[0] > now:
        if isinstance(hit[1], Exception):
            raise hit[1]
        return hit[1]
    req = urllib.request.Request(url, headers={"User-Agent": "portfolio-mcp"})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            body = resp.read(_HTTP_MAX_BYTES).decode("utf-8", errors="replace")
    except Exception as e:
        _http_cache[url] = (now + _FAIL_TTL, e)
        raise
    _http_cache[url] = (now + _CACHE_TTL, body)
    return body


# ── 도구 출력 스키마 ─────────────────────────────────────
#
# dict를 TypedDict 타입으로 반환하면 FastMCP가 텍스트 JSON과 함께
# structuredContent를 내려주고, 반환 타입에서 outputSchema를 만들어
# 클라이언트에 공개한다. 필드는 profile.json의 실제 키와 맞춰야 한다.
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


class Career(TypedDict):
    company: str
    period: str
    role: str
    # 공식 사이트를 확인하지 못한 회사는 이 키가 없다. 추측해서 채우지 않는다
    homepage: NotRequired[str | None]


class ProfileOutput(TypedDict):
    name: str
    title: str
    career: list[Career]
    education: list[dict[str, Any]]
    skills: dict[str, list[str]]
    links: dict[str, str]


class PublicationsOutput(TypedDict):
    publications: list[dict[str, Any]]
    patents: list[dict[str, Any]]
    award: str


class Repo(TypedDict):
    name: str
    description: str | None
    language: str | None
    stars: int
    pushed_at: str
    url: str


class GithubOutput(TypedDict):
    repos: list[Repo]
    hint: NotRequired[str | None]


class Post(TypedDict):
    title: str
    link: str | None
    published: str | None


class BlogOutput(TypedDict):
    posts: list[Post]
    blog: NotRequired[str | None]
    hint: NotRequired[str | None]


class CompanyOutput(TypedDict):
    companies: list[Career]
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
    tokens = _tokenize(query)
    scores = BM25.get_scores(tokens)
    floor = MIN_SCORE_PER_TOKEN * max(len(tokens), 1)
    ranked = sorted(range(len(CHUNKS)), key=lambda i: scores[i], reverse=True)
    results = [
        {"source": CHUNKS[i]["source"],
         "score": round(float(scores[i]), 2),
         "text": _snippet(CHUNKS[i]["text"])}
        # floor가 0이어도 점수 0인 청크는 결과가 아니다. 두 조건을 함께 본다
        for i in ranked[:top_k] if scores[i] > 0 and scores[i] >= floor
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
async def portfolio_get_publications() -> PublicationsOutput:
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
async def portfolio_get_profile() -> ProfileOutput:
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


@mcp.tool(
    name="portfolio_get_github_activity",
    annotations={
        "title": "GitHub 활동 실시간 조회",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def portfolio_get_github_activity() -> GithubOutput:
    """이윤선의 GitHub 공개 저장소를 실시간 조회한다 (최근 푸시 순 10개).

    profile의 정적 사실과 달리 '요즘도 활동하는가'를 오늘 자 데이터로
    보여준다. 조회 실패 시 hint와 함께 빈 결과를 반환한다. 그 경우
    portfolio_list_projects의 정적 데이터로 답하라.
    """
    user = PROFILE["links"]["github"].rstrip("/").rsplit("/", 1)[-1]
    url = f"https://api.github.com/users/{user}/repos?sort=pushed&per_page=10"
    try:
        repos = json.loads(await asyncio.to_thread(_http_get, url))
    except Exception as e:
        return {"repos": [], "hint": f"GitHub 조회 실패({type(e).__name__}). "
                "portfolio_list_projects의 정적 데이터로 답하세요."}
    return {"repos": [
        {"name": r["name"], "description": r["description"],
         "language": r["language"], "stars": r["stargazers_count"],
         "pushed_at": r["pushed_at"], "url": r["html_url"]}
        for r in repos
    ]}


@mcp.tool(
    name="portfolio_get_blog_posts",
    annotations={
        "title": "블로그 최신 글 실시간 조회",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def portfolio_get_blog_posts() -> BlogOutput:
    """이윤선의 기술 블로그 최신 글을 RSS로 실시간 조회한다 (최대 5건).

    조회 실패 시 hint와 함께 빈 결과를 반환한다. 그 경우 links.blog
    주소를 안내하라.
    """
    url = PROFILE["links"]["blog"].rstrip("/") + "/rss"
    try:
        root = ElementTree.fromstring(await asyncio.to_thread(_http_get, url))
        posts = [
            # 티스토리 RSS는 제목을 이중 인코딩한다(&quot; 등). 한 번 되돌린다
            {"title": html.unescape(i.findtext("title") or ""),
             "link": i.findtext("link"),
             "published": i.findtext("pubDate")}
            for i in root.iter("item")
        ][:5]
    except Exception as e:
        return {"posts": [], "hint": f"블로그 RSS 조회 실패({type(e).__name__}). "
                f"블로그 주소를 안내하세요: {PROFILE['links']['blog']}"}
    return {"posts": posts, "blog": PROFILE["links"]["blog"]}


@mcp.tool(
    name="portfolio_get_company_info",
    annotations={
        "title": "재직 회사 정보 조회",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def portfolio_get_company_info(
    company: Annotated[str, Field(
        description="회사명으로 필터링 (예: '인피닉', '에이아이세스'). 빈 값이면 전체 반환",
        max_length=50)] = "",
) -> CompanyOutput:
    """이윤선이 다닌 회사의 재직 정보(기간·직급)와 검증된 공식 홈페이지를 반환한다.

    회사의 최신 사업 현황·채용 정보는 이 서버의 데이터 범위 밖이다.
    반환된 homepage URL을 웹에서 직접 열람하거나 검색하라. homepage가
    없는 회사는 공식 사이트를 확인하지 못한 곳이다(추측해서 채우지 않았다).
    """
    companies = [c for c in PROFILE["career"]
                 if not company or company in c["company"]]
    if not companies:
        names = [c["company"] for c in PROFILE["career"]]
        return {"companies": [],
                "hint": f"'{company}' 재직 이력이 없습니다. 재직 회사: {names}"}
    return {
        "companies": companies,
        "hint": "회사의 최신 정보(사업 현황, 뉴스, 채용)는 homepage를 "
                "직접 열람하거나 웹 검색으로 확인하세요.",
    }


if __name__ == "__main__":
    mcp.run()  # stdio transport
