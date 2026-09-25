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
    from mcp.server.mcpserver.resources import FunctionResource, TextResource
except ImportError:                      # mcp 1.x
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.resources import FunctionResource, TextResource
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
        "검색 결과에는 조각이 나온 절 제목(section)과 문서 전문 주소(resource)가 "
        "함께 온다. 조각만으로 맥락이 부족하면 resource를 열어 이어 읽어라. "
        "최근 활동은 portfolio_get_github_activity(GitHub)·"
        "portfolio_get_blog_posts(블로그)로 실시간 조회하고, 재직 회사의 "
        "공식 홈페이지는 portfolio_get_company_info로 확인하라. "
        "기간을 비교하거나 개월 수를 더해야 하면 직접 계산하지 말고 "
        "portfolio_get_timeline을 써라(시작·종료·개월 수가 계산돼 있다). "
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


_HEADING_LINE = re.compile(r"^#{1,6}\s+(.+?)\s*$")


def _assign_sections(chunks: list[dict]) -> None:
    """각 청크에 소속 절 제목을 달아 준다.

    청킹하고 나면 청크의 68%에 제목 줄이 없다. 그러면 도구는 파일명만 붙은
    조각을 돌려주고, 클라이언트는 그게 어느 프로젝트 이야기인지 모른 채
    읽는다. 실제로 profile.json에서 서로 다른 TTS 프로젝트 둘이 한 항목으로
    섞여 있던 적이 있어서, 출처를 흐리는 건 위험하다.

    청크 안에 제목이 있으면 그 첫 제목이 소속이고, 없으면 앞 청크에서
    이어지는 제목을 물려받는다.
    """
    current = ""
    for c in chunks:
        heads = [m.group(1) for m in
                 (_HEADING_LINE.match(line) for line in c["text"].split("\n")) if m]
        c["section"] = (heads[0] if heads else current)[:60]
        if heads:
            current = heads[-1]


def _chunk_text(text: str, source: str) -> list[dict]:
    """단락 단위로 병합하며 CHUNK_SIZE 근처로 청킹. 절 제목도 함께 붙인다."""
    chunks, buf = [], ""
    for para in text.split("\n\n"):
        for block in _split_oversized(para):
            if len(buf) + len(block) > CHUNK_SIZE and buf:
                chunks.append({"source": source, "text": buf.strip()})
                buf = ""
            buf += block + "\n\n"
    if buf.strip():
        chunks.append({"source": source, "text": buf.strip()})
    _assign_sections(chunks)
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
    # 절 제목을 본문과 함께 색인한다. 제목의 단어가 그 절 전체에 걸리므로
    # 본문이 제목을 다시 말하지 않는 조각도 찾힌다. 실측으로 top1 정답이
    # 11/12에서 12/12가 됐고, 무의미 질의 거부는 6/6 그대로다.
    bm25 = BM25Okapi([_tokenize(c["section"] + "\n" + c["text"]) for c in chunks])
    return docs, chunks, bm25


DOCS, CHUNKS, BM25 = _load_kb()

def _data_mtime() -> float:
    """데이터 파일 중 가장 최근 수정 시각. 내용이 바뀌었는지 판별하는 값."""
    paths = [PROFILE_PATH, *sorted(DOCS_DIR.glob("*.md"))]
    return max((p.stat().st_mtime for p in paths if p.exists()), default=0.0)


_DATA_MTIME = _data_mtime()


def _refresh_if_changed() -> None:
    """데이터 파일이 바뀌었으면 다시 읽는다.

    서버는 stdio 로 한 번 떠서 오래 살아 있다. 그동안 profile.json 이나
    docs/*.md 를 고치면, 재시작 전까지 낡은 사실을 계속 내려보낸다
    (2026-09-22 실측: 고객사명을 지운 뒤에도 옛 값이 그대로 응답됨).
    그래서 도구 호출마다 mtime 을 보고 바뀐 경우에만 다시 읽는다.
    읽기 실패 시에는 직전 상태를 유지한다 — 편집 도중의 반쪽 파일로
    서버가 죽는 것보다, 낡았지만 온전한 데이터를 내려보내는 편이 낫다.
    """
    global DOCS, CHUNKS, BM25, PROFILE, _VOCAB, _COMPLETION_VOCAB, _DATA_MTIME
    now = _data_mtime()
    if now <= _DATA_MTIME:
        return
    try:
        docs, chunks, bm25 = _load_kb()
        profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except Exception:
        _DATA_MTIME = now   # 같은 실패를 매 호출마다 반복하지 않는다
        return
    DOCS, CHUNKS, BM25, PROFILE = docs, chunks, bm25, profile
    _VOCAB = {run for c in CHUNKS
              for run in _RUNS.findall((c["section"] + " " + c["text"]).lower())}
    _COMPLETION_VOCAB = _dedupe_ci(
        {p["name"] for p in PROFILE["projects"]}
        | {s for group in PROFILE["skills"].values() for s in group}
        | _heading_terms()
        | _corpus_terms()
    )
    _DATA_MTIME = now

# 코퍼스에 실재하는 '온전한' 토큰 집합. bigram으로 만들어 낸 조각은 넣지 않는다.
_VOCAB = {run for c in CHUNKS
          for run in _RUNS.findall((c["section"] + " " + c["text"]).lower())}


def _provenance() -> str:
    """이 응답이 어느 파일의 언제 상태에서 나왔는지.

    도구가 사실을 말할 때 근거가 어디서 왔는지 같이 줘야 클라이언트가
    "언제 기준 정보인가"에 답할 수 있다. 데이터를 고치면 mtime 이 함께
    올라가므로 따로 버전을 관리할 필요가 없다.
    """
    t = time.localtime(_DATA_MTIME) if _DATA_MTIME else time.localtime()
    return "data/profile.json·data/docs (갱신 %s)" % time.strftime("%Y-%m-%d", t)


def _has_corpus_term(query: str) -> bool:
    """질의가 코퍼스에 실재하는 단어를 하나라도 담고 있는가.

    MIN_SCORE_PER_TOKEN은 점수 축에서 무의미 질의를 걸러 주지만, 자연스러운
    한국어 질문까지 같이 막았다. 어미가 bigram으로 토큰 수를 부풀리는데
    그 조각들은 코퍼스에 없어 점수는 0을 보태면서 문턱만 올리기 때문이다.
    'vLLM 써봤어요?'가 0건이었다. vLLM은 문서에 가득한데도.

    점수 축으로는 두 집합이 겹쳐서 임계값을 어디에 둬도 한쪽이 깨진다.
    갈리는 축은 따로 있다. 무의미 질의는 온전한 단어가 코퍼스에 하나도
    없고(0/2~0/4), 정상 질의는 자연어라도 반드시 하나는 있다.
    그래서 이 관문을 통과하면 점수 문턱을 걷는다.
    """
    return any(run in _VOCAB for run in _RUNS.findall(query.lower()))
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


# 텍스트를 미리 담아 두지 않고 읽을 때 만든다. TextResource 로 굳혀 두면
# 파일을 고쳐도 리소스만 옛 내용을 계속 내보낸다(도구는 _refresh_if_changed 로
# 새로 읽는데 리소스는 아니라, 같은 서버가 두 가지 사실을 말하게 된다).
def _doc_reader(doc_name: str):
    def read() -> str:
        _refresh_if_changed()
        text = DOCS.get(doc_name)
        if text is None:                     # 파일이 사라진 경우
            return "%s 문서가 없습니다. 사용 가능한 문서: %s" % (
                doc_name, sorted(DOCS))
        return text
    return read


for _name, _text in DOCS.items():
    mcp.add_resource(FunctionResource(
        uri=f"portfolio://docs/{_name}",
        name=_name,
        description=_doc_description(_text),
        mime_type="text/markdown",
        fn=_doc_reader(_name),
    ))


@mcp.resource(
    "portfolio://docs/{doc_name}",
    name="portfolio_doc",
    description="기술문서 전문. doc_name 은 resources/list 의 파일명 (예: resume.md)",
    mime_type="text/markdown",
)
def _doc_template(doc_name: str) -> str:
    """문서를 이름으로 읽는 템플릿.

    고정 등록은 기동 시점 목록이라, 문서를 추가하면 재시작 전까지 리소스로
    노출되지 않는다. 템플릿을 함께 열어 두면 새 문서도 바로 읽힌다.
    """
    return _doc_reader(doc_name)()


def _profile_json() -> str:
    _refresh_if_changed()
    return json.dumps(PROFILE, ensure_ascii=False, indent=2)


mcp.add_resource(FunctionResource(
    uri="portfolio://profile",
    name="profile.json",
    description="검증된 경력 사실 전체. 경력·프로젝트·논문·특허·학력·기술 스택",
    mime_type="application/json",
    fn=_profile_json,
))


def _snippet(text: str, limit: int = CHUNK_SIZE) -> str:
    """긴 청크는 잘라서 반환하되, 잘렸다는 사실을 숨기지 않는다.

    말없이 자르면 모델이 문장이 중간에 끝난 걸 데이터 오류로 오해할 수 있다.
    표식이 있으면 문서 리소스(portfolio://docs/<source>)로 이어 읽으면
    된다는 걸 안다. 기본 한계를 CHUNK_SIZE에 맞춰 두는 이유는, 그보다
    작으면 정상 크기 청크마저 매번 잘려 나가기 때문이다.
    """
    return text if len(text) <= limit else text[:limit] + " …(이하 생략)"


def _with_neighbors(idx: int, window: int = 1) -> str:
    """같은 문서의 앞뒤 청크를 이어 붙인다.

    한 청크는 800자에서 끊기므로 긴 설명은 문장 중간에서 잘린다. 그때마다
    리소스로 문서 전문(1만 자 이상)을 여는 것은 컨텍스트 낭비라, 필요한
    만큼만 이어 준다. 문서 경계는 넘지 않는다.
    """
    src = CHUNKS[idx]["source"]
    lo = idx
    while lo - 1 >= 0 and idx - (lo - 1) <= window and CHUNKS[lo - 1]["source"] == src:
        lo -= 1
    hi = idx
    while hi + 1 < len(CHUNKS) and (hi + 1) - idx <= window and CHUNKS[hi + 1]["source"] == src:
        hi += 1
    return (chr(10) * 2).join(CHUNKS[j]["text"] for j in range(lo, hi + 1))


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


@mcp.prompt(name="job_fit", title="공고 요건 대조")
def job_fit(requirements: str) -> str:
    """채용 공고의 자격 요건을 한 줄씩 근거와 대조하게 한다.

    requirements: 공고의 자격 요건. 줄바꿈이나 쉼표로 구분한 목록.
    """
    template = """
아래 채용 요건을 이윤선의 포트폴리오와 한 줄씩 대조해 주세요.

[요건]
{requirements}

진행 방법
1. 요건을 개별 항목으로 나눕니다.
2. 항목마다 portfolio_check_skill 을 호출해 스택 등재 여부·관련 프로젝트·
   문서 근거를 받습니다.
3. 근거가 나온 프로젝트는 portfolio_get_project 로 기간과 역할까지 확인합니다.
   기간을 비교해야 하면 portfolio_get_timeline 을 쓰고 직접 계산하지 마세요.
4. 표로 정리합니다 — 요건 | 판정 | 근거(프로젝트·수치·문서) | 기간.

판정은 셋 중 하나만 씁니다.
  충족      프로젝트와 수치로 뒷받침되는 경우
  부분      인접 경험은 있으나 그 기술 자체의 기록은 없는 경우
  근거 없음  도구가 기록을 찾지 못한 경우

규칙
- found=false 인 항목을 "비슷한 경험이 있다"로 바꾸지 마세요. 없는 것은
  없다고 적고, 가장 가까운 실제 경험을 따로 한 줄 덧붙이세요.
- 수치는 도구가 돌려준 값만 쓰고 반올림하지 마세요.
- 각 도구 응답의 source 필드(데이터 갱신일)를 표 아래 한 줄로 밝혀 주세요.
"""
    return template.format(requirements=requirements.strip())


@mcp.prompt(name="tech_deep_dive", title="기술 딥다이브")
def tech_deep_dive(topic: str) -> str:
    """특정 기술 주제에서 이윤선이 실제로 한 일을 깊게 조사하게 한다.

    topic: 조사할 주제 (예: 'TTFB 최적화', 'Kubernetes', '스트리밍 팝 노이즈').
    """
    return (
        f"이윤선의 포트폴리오에서 '{topic}' 관련 경험을 깊게 조사해 주세요.\n\n"
        f"1. portfolio_search로 '{topic}'을(를) 검색하고, 결과가 부족하면 "
        "연관 키워드로 2~3회 재검색해 주세요.\n"
        "2. 조각만으로 맥락이 부족하면 결과에 들어 있는 resource 주소를 열어 "
        "문서 전문을 읽어 주세요. section 값으로 어느 절 이야기인지 확인할 수 있습니다.\n"
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
    # 조각이 어느 절에서 나왔는지. 파일명만으로는 어느 프로젝트 이야기인지
    # 알 수 없어서, 클라이언트가 서로 다른 프로젝트를 섞을 위험이 있다
    section: str
    # 문서 전문을 이어 읽을 리소스 주소. 클라이언트가 조립하지 않아도 되게 한다
    resource: str
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
    # 이 사실이 어느 파일의 언제 상태에서 나왔는지
    source: NotRequired[str | None]
    hint: NotRequired[str | None]


class Career(TypedDict):
    company: str
    period: str
    role: str
    # 공식 사이트를 확인하지 못한 회사는 이 키가 없다. 추측해서 채우지 않는다
    homepage: NotRequired[str | None]


class ProfileOutput(TypedDict):
    source: NotRequired[str | None]
    name: str
    title: str
    career: list[Career]
    education: list[dict[str, Any]]
    skills: dict[str, list[str]]
    links: dict[str, str]


class PublicationsOutput(TypedDict):
    source: NotRequired[str | None]
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


class SkillEvidence(TypedDict):
    source: NotRequired[str | None]
    skill: str
    found: bool
    # profile.json 의 기술 스택에 올라 있는가 (본인이 공개적으로 내세우는 기술)
    in_stack: bool
    stack_category: NotRequired[str | None]
    # 그 기술이 등장하는 프로젝트. 이름·기간·회사만 (상세는 portfolio_get_project)
    projects: list[Project]
    documents: list[SearchHit]
    hint: NotRequired[str | None]

class TimelineEntry(TypedDict):
    kind: str            # career | project | publication | patent | education
    label: str
    start: str           # YYYY.MM (일자 정보가 없으면 그 달의 1일로 본다)
    end: str             # YYYY.MM 또는 "현재"
    months: int          # 시작월~종료월 포함 개월 수. 시점 항목은 0
    ongoing: bool
    company: NotRequired[str | None]
    role: NotRequired[str | None]


class ProjectDetail(TypedDict):
    name: str
    company: str
    period: str
    role: str
    summary: str
    # 계산된 기간. 문자열 period 를 클라이언트가 다시 파싱하지 않게 한다
    start: str
    end: str
    months: int
    ongoing: bool
    # 이 프로젝트를 다루는 문서 조각. 어느 문서 어느 절인지 함께 준다
    documents: list[SearchHit]


class ProjectDetailOutput(TypedDict):
    project: NotRequired[ProjectDetail | None]
    source: NotRequired[str | None]
    hint: NotRequired[str | None]

class TimelineOutput(TypedDict):
    as_of: str
    source: NotRequired[str | None]
    entries: list[TimelineEntry]
    total_career_months: NotRequired[int | None]
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
    offset: Annotated[int, Field(
        description="상위 몇 건을 건너뛸지. 10건 너머를 볼 때 사용", ge=0, le=50)] = 0,
    source: Annotated[str, Field(
        description="특정 문서 안에서만 검색 (예: 'resume.md', 'tts'). 빈 값이면 전체",
        max_length=60)] = "",
    with_context: Annotated[bool, Field(
        description="앞뒤 청크를 이어 붙여 반환. 잘린 설명을 이어 읽을 때 사용")] = False,
) -> SearchOutput:
    """이윤선의 포트폴리오/기술문서에서 관련 내용을 키워드(BM25) 검색한다.

    프로젝트 상세, 기술적 의사결정, 트러블슈팅 과정 등 profile 도구가
    제공하지 않는 세부 내용을 찾을 때 사용한다.
    출처 파일명과 함께 관련 청크를 반환한다.

    source 로 문서를 좁힐 수 있다(부분 문자열이면 된다 — 'tts' 는
    tts-deepdive.md 에 걸린다). 사용 가능한 문서명은 결과의 source 나
    portfolio://docs/ 리소스 목록에 있다.
    with_context=true 면 인접 청크를 함께 이어 붙여, '(이하 생략)' 으로
    잘린 설명을 리소스를 열지 않고도 이어 읽을 수 있다.
    """
    _refresh_if_changed()
    tokens = _tokenize(query)
    scores = BM25.get_scores(tokens)
    allowed = None
    if source.strip():
        key = source.strip().lower()
        allowed = {i for i, c in enumerate(CHUNKS) if key in c["source"].lower()}
        if not allowed:
            names = sorted({c["source"] for c in CHUNKS})
            return {"results": [],
                    "hint": f"'{source}' 문서가 없습니다. 사용 가능한 문서: {names}"}
    # 코퍼스에 실재하는 단어가 질의에 있으면 문턱을 걷는다. 없으면 bigram이
    # 우연히 스친 것뿐이라 문턱으로 막는다.
    floor = 0.0 if _has_corpus_term(query) else MIN_SCORE_PER_TOKEN * max(len(tokens), 1)
    pool = range(len(CHUNKS)) if allowed is None else sorted(allowed)
    ranked = sorted(pool, key=lambda i: scores[i], reverse=True)
    results = [
        {"source": CHUNKS[i]["source"],
         "section": CHUNKS[i]["section"],
         "resource": f"portfolio://docs/{CHUNKS[i]['source']}",
         "score": round(float(scores[i]), 2),
         "text": _with_neighbors(i) if with_context else _snippet(CHUNKS[i]["text"])}
        # floor가 0이어도 점수 0인 청크는 결과가 아니다. 두 조건을 함께 본다
        for i in ranked[offset:offset + top_k] if scores[i] > 0 and scores[i] >= floor
    ]
    if not results:
        if offset:
            return {"results": [], "hint": f"offset={offset} 너머에는 결과가 "
                    "없습니다. offset 을 줄이거나 질의를 바꾸세요."}
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
    _refresh_if_changed()
    projects = PROFILE["projects"]
    if company:
        projects = [p for p in projects if _company_matches(company, p["company"])]
        if not projects:
            companies = sorted({p["company"] for p in PROFILE["projects"]})
            return {
                "projects": [],
                "hint": f"'{company}' 프로젝트가 없습니다. 보유 회사: {companies}",
            }
    return {"projects": projects, "source": _provenance()}


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
    _refresh_if_changed()
    return {
        "source": _provenance(),
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
    _refresh_if_changed()
    return {
        "source": _provenance(),
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
    _refresh_if_changed()
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


# ── 기간 계산 ─────────────────────────────────────────────
#
# "A와 B 중 먼저 시작한 쪽", "가장 오래 근무한 회사", "총 경력" 같은 질문은
# 모델이 날짜 산술을 직접 하다 틀리는 자리다(rag-agent 평가에서 비교 유형이
# 61~67%로 가장 낮았고, 실패 사례 상당수가 개월 수 계산 오류였다).
# 문자열을 그대로 넘기지 않고 서버가 정렬·개월 수까지 계산해서 준다.

_PERIOD_SEP = re.compile(r"\s*~\s*")
_YM = re.compile(r"(\d{4})(?:[.\-/](\d{1,2}))?")


def _parse_ym(token: str) -> tuple[int, int] | None:
    """'2024.08' → (2024, 8). 월이 없으면 1월로 본다. 파싱 실패는 None."""
    m = _YM.search(token)
    if not m:
        return None
    year = int(m.group(1))
    month = int(m.group(2)) if m.group(2) else 1
    return (year, min(max(month, 1), 12))


def _month_span(start: tuple[int, int], end: tuple[int, int]) -> int:
    """시작월부터 종료월 직전까지의 개월 수 (종료월 미포함).

    본인 이력서 표기와 같은 셈법이다. 이력서가 2026.08 시점에 "총 경력
    5년 4개월"로 적혀 있는데, 종료월을 포함해 세면 경계 달이 두 번
    잡혀(이든 ~2022.08 과 인피닉 2022.08~) 5년 9개월이 나온다.
    """
    return max((end[0] - start[0]) * 12 + (end[1] - start[1]), 0)


def _now_ym() -> tuple[int, int]:
    t = time.localtime()
    return (t.tm_year, t.tm_mon)


def _timeline_entry(kind: str, label: str, period: str,
                    company: str | None = None, role: str | None = None):
    """'2024.08 ~ 2025.04'·'2021'·'2025.04 ~ 현재' 를 공통 형태로 바꾼다."""
    parts = _PERIOD_SEP.split(period.strip())
    start = _parse_ym(parts[0])
    if start is None:
        return None
    ongoing = len(parts) > 1 and ("현재" in parts[1] or "present" in parts[1].lower())
    if ongoing:
        end = _now_ym()
    elif len(parts) > 1:
        end = _parse_ym(parts[1]) or start
    else:
        end = start           # 단일 시점 (예: 대회 참가 "2021")
    point = len(parts) == 1
    entry: TimelineEntry = {
        "kind": kind,
        "label": label,
        "start": "%04d.%02d" % start,
        "end": "현재" if ongoing else "%04d.%02d" % end,
        "months": 0 if point else _month_span(start, end),
        "ongoing": ongoing,
    }
    if company:
        entry["company"] = company
    if role:
        entry["role"] = role
    return entry


@mcp.tool(
    name="portfolio_get_timeline",
    annotations={
        "title": "경력·프로젝트 타임라인 조회",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def portfolio_get_timeline(
    kind: Annotated[str, Field(
        description="career | project | publication | patent | education | all",
        max_length=20)] = "all",
) -> TimelineOutput:
    """경력·프로젝트·논문·특허·학력을 시작 시점 순으로 정렬해 개월 수와 함께 반환한다.

    "A와 B 중 먼저 시작한 것", "가장 오래 근무한 회사", "총 경력"처럼
    날짜를 비교하거나 기간을 더하는 질문에는 이 도구를 쓴다. 문자열 기간을
    직접 계산하지 말 것 — start/end/months 가 이미 계산된 값이다.
    months 는 종료월 직전까지 센 값이다(이력서 표기와 같은 셈법).
    ongoing 이 true 면 end 는 오늘(as_of) 기준이다. 단일 시점 항목(대회·
    논문·특허·학위)은 months 가 0 이다.
    """
    _refresh_if_changed()
    want = kind.strip().lower() or "all"
    entries: list[TimelineEntry] = []
    if want in ("all", "career"):
        for c in PROFILE["career"]:
            e = _timeline_entry("career", c["company"], c["period"],
                                company=c["company"], role=c.get("role"))
            if e:
                entries.append(e)
    if want in ("all", "project"):
        for pj in PROFILE["projects"]:
            e = _timeline_entry("project", pj["name"], pj["period"],
                                company=pj.get("company"), role=pj.get("role"))
            if e:
                entries.append(e)
    if want in ("all", "publication"):
        for pub in PROFILE["publications"]:
            e = _timeline_entry("publication", pub["title"], pub.get("year", ""))
            if e:
                entries.append(e)
    if want in ("all", "patent"):
        for pt in PROFILE["patents"]:
            e = _timeline_entry("patent", pt["title"], pt.get("date", ""))
            if e:
                entries.append(e)
    if want in ("all", "education"):
        for ed in PROFILE["education"]:
            e = _timeline_entry("education", f"{ed['degree']} · {ed['school']}",
                                ed.get("year", ""))
            if e:
                entries.append(e)
    if not entries:
        return {"as_of": "%04d.%02d" % _now_ym(), "entries": [],
                "hint": "kind 는 career · project · publication · patent · "
                        "education · all 중 하나입니다."}
    entries.sort(key=lambda e: (e["start"], e["label"]))
    out: TimelineOutput = {"as_of": "%04d.%02d" % _now_ym(), "entries": entries,
                           "source": _provenance()}
    if want in ("all", "career"):
        # 재직 기간은 겹치지 않으므로 단순 합이 총 경력이다
        out["total_career_months"] = sum(e["months"] for e in entries
                                         if e["kind"] == "career")
        out["hint"] = ("total_career_months 는 재직 이력 전체의 합이며 "
                       "KISTI(파트타임 3개월)를 포함한다. 정규직만 세려면 "
                       "role 이 Part-time 인 항목을 빼라.")
    return out

@mcp.tool(
    name="portfolio_get_project",
    annotations={
        "title": "프로젝트 상세 조회",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def portfolio_get_project(
    name: Annotated[str, Field(
        description="프로젝트 이름 일부 (예: 'Qwen3', '멀티모달', '예지보전')",
        min_length=1, max_length=80)],
    max_documents: Annotated[int, Field(
        description="함께 반환할 문서 조각 수", ge=0, le=6)] = 3,
) -> ProjectDetailOutput:
    """프로젝트 하나의 확정 정보와 그 프로젝트를 다루는 문서 조각을 함께 반환한다.

    목록(portfolio_list_projects)은 요약만 주고 세부는 검색으로 따로 찾아야
    했다. 이 도구는 둘을 한 번에 준다 — 확정 사실(기간·역할·요약, 계산된
    개월 수)과 근거 문서 조각(어느 문서 어느 절인지 포함).

    이름은 부분 일치면 된다. 여러 개가 걸리면 후보를 hint 로 돌려준다.
    """
    _refresh_if_changed()
    key = name.strip().lower()
    matches = [p for p in PROFILE["projects"] if key in p["name"].lower()]
    if not matches:
        # 회사명으로 물었을 수도 있다 — 그 경우 목록 도구로 안내한다
        names = [p["name"] for p in PROFILE["projects"]]
        return {"hint": f"'{name}' 프로젝트가 없습니다. 전체 목록: {names}"}
    if len(matches) > 1:
        exact = [p for p in matches if p["name"].lower() == key]
        if not exact:
            return {"hint": "여러 프로젝트가 걸립니다. 더 구체적으로 지정하세요: "
                            f"{[p['name'] for p in matches]}"}
        matches = exact
    pj = matches[0]
    span = _timeline_entry("project", pj["name"], pj["period"])
    docs: list[SearchHit] = []
    if max_documents:
        # 프로젝트 이름의 앞부분을 질의로 쓴다. 괄호 안 저장소명은 문서에
        # 없는 경우가 많아 떼어낸다 (예: "예지보전 Agent (pdm-agent)")
        head = pj["name"].split(" (")[0]
        tokens = _tokenize(head)
        scores = BM25.get_scores(tokens)
        ranked = sorted(range(len(CHUNKS)), key=lambda i: scores[i], reverse=True)
        docs = [
            {"source": CHUNKS[i]["source"],
             "section": CHUNKS[i]["section"],
             "resource": f"portfolio://docs/{CHUNKS[i]['source']}",
             "score": round(float(scores[i]), 2),
             "text": _snippet(CHUNKS[i]["text"])}
            for i in ranked[:max_documents] if scores[i] > 0
        ]
    detail: ProjectDetail = {
        "name": pj["name"],
        "company": pj.get("company", ""),
        "period": pj.get("period", ""),
        "role": pj.get("role", ""),
        "summary": pj.get("summary", ""),
        "start": span["start"] if span else "",
        "end": span["end"] if span else "",
        "months": span["months"] if span else 0,
        "ongoing": bool(span and span["ongoing"]),
        "documents": docs,
    }
    out: ProjectDetailOutput = {"project": detail, "source": _provenance()}
    if not docs:
        out["hint"] = ("이 프로젝트를 다루는 문서 조각을 찾지 못했습니다. "
                       "summary 가 현재 확인된 전부입니다.")
    return out

@mcp.tool(
    name="portfolio_check_skill",
    annotations={
        "title": "기술 경험 확인",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def portfolio_check_skill(
    skill: Annotated[str, Field(
        description="확인할 기술 (예: 'Triton', 'Kubernetes', 'Rust')",
        min_length=1, max_length=40)],
    max_documents: Annotated[int, Field(
        description="근거로 붙일 문서 조각 수", ge=0, le=5)] = 2,
) -> SkillEvidence:
    """특정 기술을 실제로 다뤘는지, 어디서 다뤘는지 근거와 함께 답한다.

    "X 경험 있나요"는 채용 검토에서 가장 자주 나오는 질문인데, 기술 스택
    목록만 보면 나열인지 실무인지 구분되지 않는다. 이 도구는 세 가지를
    함께 준다 — 스택 등재 여부, 그 기술이 나오는 프로젝트, 문서 근거.

    found 가 false 면 "경험이 없다"는 뜻이다. 없는 경험을 있다고 만들지
    말고 그대로 전하라. 스택에는 없지만 프로젝트·문서에는 나오는 경우도
    있다(in_stack=false, projects 비어 있지 않음) — 그때는 "스택으로
    내세우지는 않지만 해당 작업 기록은 있다"가 정확한 답이다.
    """
    _refresh_if_changed()
    key = skill.strip().lower()
    in_stack, category = False, None
    for cat, items in PROFILE["skills"].items():
        for item in items:
            if key in item.lower():
                in_stack, category = True, cat
                break
        if in_stack:
            break
    projects = [p for p in PROFILE["projects"]
                if key in json.dumps(p, ensure_ascii=False).lower()]
    docs: list[SearchHit] = []
    if max_documents:
        tokens = _tokenize(skill)
        scores = BM25.get_scores(tokens)
        ranked = sorted(range(len(CHUNKS)), key=lambda i: scores[i], reverse=True)
        docs = [
            {"source": CHUNKS[i]["source"],
             "section": CHUNKS[i]["section"],
             "resource": f"portfolio://docs/{CHUNKS[i]['source']}",
             "score": round(float(scores[i]), 2),
             "text": _snippet(CHUNKS[i]["text"])}
            for i in ranked[:max_documents]
            # 점수만 보면 조각 토큰이 스쳐도 걸린다. 실제로 그 단어가
            # 들어 있는 청크만 근거로 인정한다
            if scores[i] > 0 and key in CHUNKS[i]["text"].lower()
        ]
    out: SkillEvidence = {
        "source": _provenance(),
        "skill": skill,
        "found": bool(in_stack or projects or docs),
        "in_stack": in_stack,
        "projects": projects,
        "documents": docs,
    }
    if category:
        out["stack_category"] = category
    if not out["found"]:
        cats = {c: v for c, v in PROFILE["skills"].items()}
        out["hint"] = ("이 기술을 다룬 기록이 없습니다. 없는 경험을 지어내지 "
                       f"말고 그대로 전하세요. 보유 기술: {cats}")
    elif not in_stack:
        out["hint"] = ("기술 스택 목록에는 없지만 프로젝트·문서에 등장합니다. "
                       "'스택으로 내세우지는 않으나 작업 기록은 있다'가 정확합니다.")
    return out

if __name__ == "__main__":
    mcp.run()  # stdio transport
