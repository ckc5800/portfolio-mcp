# Portfolio MCP Server

AI 엔지니어 이윤선의 포트폴리오를 **MCP(Model Context Protocol) 서버**로 노출합니다.
Claude Desktop, Claude Code 등 MCP 클라이언트에서 포트폴리오 검색·경력 조회를
도구로 사용할 수 있습니다 — "이 사람 TTFB 최적화 어떻게 했어?"라고 AI에게 물어보세요.

```
MCP Client (Claude 등)
   │  stdio
   ▼
portfolio_mcp ──── BM25 검색 ──── data/docs/*.md   (기술문서 5편)
   └────────────── 구조화 조회 ─── data/profile.json (검증된 경력 사실)
```

## Tools

| 도구 | 설명 |
|---|---|
| `portfolio_get_profile` | 기본 프로필 — 경력 회사·기간·직급, 학력, 기술 스택, 링크 |
| `portfolio_list_projects` | 프로젝트 11개 목록 (회사 필터 지원) — 기간·역할·검증된 성과 요약 |
| `portfolio_get_publications` | 논문 7편(제1저자)·특허 2건(제1발명자)·수상 내역 |
| `portfolio_search` | 기술문서 BM25 검색 — 트러블슈팅 과정, 아키텍처 세부사항 등 |

모든 도구는 read-only이며, 데이터는 원본 기록에서 검증된 사실만 수록했습니다.

## 설계 결정

- **의존성 최소화**: `mcp` + `rank_bm25` 뿐. 임베딩 서버·외부 API·GPU 불필요 →
  clone 후 30초 안에 동작. MCP 서버는 도구만 제공하고 추론은 클라이언트 LLM이
  담당하므로, 서버는 가볍고 결정적으로 유지
- **BM25 키워드 검색**: 포트폴리오 규모(문서 5편, ~80청크)에서는 벡터 검색 대비
  운영 비용 없이 충분한 재현율. 한/영/숫자 정규식 토크나이저 적용
- **구조화 + 비구조화 이원화**: 확정 사실(경력·논문·수치)은 `profile.json`으로
  정확하게, 서술형 세부사항은 문서 검색으로 유연하게
- **Actionable 오류 응답**: 검색 결과 없음/필터 불일치 시 다음 행동을 안내하는
  hint 필드 반환

## Quickstart

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt

# 스모크 테스트 (서버 기동 + 4개 도구 호출)
python test_client.py
```

### Claude Code에 연결

```bash
claude mcp add portfolio -- python -X utf8 /path/to/portfolio-mcp/server.py
```

### Claude Desktop에 연결

`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "portfolio": {
      "command": "python",
      "args": ["-X", "utf8", "C:/path/to/portfolio-mcp/server.py"]
    }
  }
}
```

연결 후 이렇게 물어보세요:

> "이윤선의 TTS 프로젝트에서 스트리밍 팝 노이즈를 어떻게 해결했는지 찾아줘"
> "인피닉에서 한 프로젝트 목록 보여줘"
> "특허 등록번호 알려줘"

## Tech

Python · MCP SDK (FastMCP) · BM25 (rank_bm25) · stdio transport
