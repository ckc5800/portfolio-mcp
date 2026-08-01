# portfolio-mcp

![CI](https://github.com/ckc5800/portfolio-mcp/actions/workflows/ci.yml/badge.svg)

내 포트폴리오를 MCP(Model Context Protocol) 서버로 만들었다.
Claude Desktop이나 Claude Code에 붙이면 AI가 내 경력, 프로젝트, 논문을
도구로 직접 조회한다. "이 사람 TTFB 최적화 어떻게 했어?" 같은 질문이 가능해진다.

![demo](assets/demo.svg)

데모는 `demo_session.py`로 실행한 실제 세션 출력을 그대로 옮긴 것이다.

## 구성

```
MCP Client (Claude 등)
   │  stdio
   ▼
portfolio_mcp ── BM25 검색 ─── data/docs/*.md      기술문서 5편
   ├─────────── 구조화 조회 ── data/profile.json   경력 사실
   ├─────────── 리소스 노출 ── portfolio://…       문서 전문 읽기
   └─────────── 프롬프트 2개 ─ 브리핑·딥다이브     도구 사용 안내 템플릿
```

| 도구 | 하는 일 |
|---|---|
| `portfolio_get_profile` | 경력 회사·기간·직급, 학력, 기술 스택, 링크 |
| `portfolio_list_projects` | 프로젝트 16개 목록. 회사명 필터 지원(구 사명도 인식) |
| `portfolio_get_publications` | 논문 7편(제1저자), 특허 2건(제1발명자), 수상 |
| `portfolio_search` | 기술문서 BM25 검색. 트러블슈팅 과정 같은 세부 내용용 |

도구 4개는 텍스트 JSON과 함께 **structuredContent**도 내려주고, 반환
타입(TypedDict)에서 생성한 **outputSchema**를 클라이언트에 공개한다 —
클라이언트가 파싱 없이 스키마가 보장된 결과를 바로 쓸 수 있다.

도구 외에 **MCP 리소스**도 6개 노출한다 — 기술문서 전문
`portfolio://docs/<파일명>` 5개와 구조화 프로필 `portfolio://profile`.
역할 분담은 이렇다: 검색은 관련 조각을 *찾는* 입구고, 리소스는 문서 전문을
*읽는* 경로다. 검색 결과가 길어서 `…(이하 생략)`으로 잘려 있으면 클라이언트가
해당 문서 리소스를 열어 이어 읽으면 된다. 리소스도 검색 인덱스와 같은
정제본을 내보내므로 두 경로의 내용이 항상 일치한다.

**MCP 프롬프트**도 2개 제공한다 — `candidate_briefing`(채용 담당자용 브리핑,
focus 인자로 영역 지정)과 `tech_deep_dive`(주제별 기술 딥다이브, 재검색과
리소스 이어읽기 절차 포함). 프롬프트는 어떤 도구를 어떤 순서로 쓸지 안내하는
템플릿이라, 서버가 자기 도구의 올바른 사용법을 함께 배포하는 셈이다. 이로써
MCP 3대 프리미티브(도구·리소스·프롬프트)를 모두 구현한다.

전부 read-only다.

## 설계하면서 정한 것들

- 의존성은 `mcp` SDK와 `rank_bm25` 둘뿐이다. 처음엔 임베딩 검색도 고려했는데
  문서 5편에 청크 수십 개 규모에서 벡터 검색은 과하다. BM25면 충분하고,
  덕분에 GPU도 외부 API도 없이 clone 후 바로 돈다.
- 추론은 클라이언트 LLM의 몫이다. 서버는 데이터만 정확하게 내려주면 된다.
- 확정된 사실(경력, 논문, 수치)은 `profile.json`으로, 서술형 내용은 문서 검색으로
  분리했다. 숫자가 검색 랭킹에 따라 흔들리면 안 되기 때문이다.
- 검색 결과가 비면 "다른 키워드로 재검색하거나 목록부터 보라"는 힌트를
  응답에 같이 넣는다. 에러 메시지가 다음 행동을 알려줘야 agent가 헤매지 않는다.

## 뒤늦게 고친 것 세 가지

**1. 원문 정제를 아예 안 하고 있었다.** `data/docs/*.md`는 노션에서 내보낸
그대로였고, 서버는 `read_text()`로 읽어 바로 청킹하고 있었다. 문제는 노션이
내부 페이지 링크를 퍼센트 인코딩된 한글 파일명으로 내보낸다는 것이다
(`[Experience](%EC%9D%B4%EC%9C%A4%EC%84%A0%20...)`). BM25 토크나이저는 이걸
`ec`, `d`, `b`, `9` 같은 쓰레기 토큰으로 쪼갠다. 토큰이 늘면 **BM25의 문서 길이
정규화가 해당 청크에 페널티를 줘서 순위가 실제로 나빠지고**, 도구가 돌려주는
본문에도 그대로 섞여 클라이언트 LLM의 컨텍스트를 낭비한다.

정제를 넣으니 **BM25 토큰 7,653개 → 6,310개(18% 감소)**, 청크 32 → 26개.
줄어든 1,343개는 전부 순위를 왜곡하던 쓰레기다. 외부 http(s) URL은 "깃허브
주소" 같은 질문에 답해야 하므로 그대로 남긴다. (같은 문제를
[portfolio-rag-agent](https://github.com/ckc5800/portfolio-rag-agent)에서
검수 스크립트로 찾았고, 규칙을 이쪽에도 옮겼다. 이 서버는 의존성 2개로 단독
실행되는 게 목표라 공용 모듈로 빼지 않고 복제했다.)

**2. 스모크 테스트가 실패할 수 없는 테스트였다.** `test_client.py`는 응답
텍스트가 비어 있지 않은지만 봤다. 그래서 도구가 "결과 없음 + 힌트"를 돌려줘도
`[OK]`로 통과했다 — 실제로 `portfolio_list_projects(company="에이아이세스")`가
**빈 배열을 반환하고 있었는데** 테스트는 계속 초록색이었다.

원인은 표기 불일치였다. `career`에는 `MiCo AI (구 에이아이세스)`로, `projects`에는
`MiCo AI`로 적혀 있어서 구 사명으로 물으면 아무것도 안 나왔다. 현 직장인데도.
`career`를 별칭 사전처럼 써서 두 표기를 잇고, 테스트는 **응답이 왔는지가 아니라
내용이 맞는지**(프로젝트가 1개 이상인지, 특허가 2건인지)를 검사하도록 바꿨다.

**3. 조사가 붙은 질의에서 검색이 죽어 있었다.** 토크나이저가 단어 단위라
'쿠버네티스로'와 '쿠버네티스'가 서로 다른 토큰이었다. 실측하면:

| 질의 | 이전 | 이후 |
|---|---|---|
| `쿠버네티스로 뭐 했어` | **0건** | portfolio.md (14.16) |
| `최적화를` | **0건** | resume.md (4.28) |
| `TTFB 최적화` | tts-deepdive.md 1위 | tts-deepdive.md 1위 (유지) |

한국어는 교착어라 실제 질문에는 거의 항상 조사가 붙는데, 정확히 그 형태에서
0건이 나오고 있었다. 한글 런(run)에 문자 bigram을 추가해 해결했다 —
'쿠버네티스로'와 '쿠버네티스'가 bigram(`쿠버`, `버네`, …)으로 겹치게 된다.
같은 문제를 [portfolio-rag-agent](https://github.com/ckc5800/portfolio-rag-agent)에서
검색 평가로 먼저 찾았고(recall@1 80%), 그쪽의 `bm25_tokenize` 규칙을 이식했다.
기존에 잘 되던 질의의 1위 문서는 바뀌지 않는 것을 확인했다.

## 실행

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt

python test_client.py   # 서버 기동 + 도구·리소스·프롬프트 전부 실제 호출하는 스모크 테스트
```

## Claude에 연결

Claude Code:

```bash
claude mcp add portfolio /path/to/.venv/Scripts/python.exe /path/to/portfolio-mcp/server.py
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "portfolio": {
      "command": "C:/path/to/portfolio-mcp/.venv/Scripts/python.exe",
      "args": ["C:/path/to/portfolio-mcp/server.py"]
    }
  }
}
```

연결하고 이런 걸 물어보면 된다.

- 이윤선의 TTS 프로젝트에서 스트리밍 팝 노이즈를 어떻게 해결했는지 찾아줘
- 인피닉에서 한 프로젝트 목록 보여줘
- 특허 등록번호 알려줘

Python / MCP SDK (FastMCP) / rank_bm25 / stdio
