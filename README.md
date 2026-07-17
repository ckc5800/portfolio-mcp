# portfolio-mcp

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
   └─────────── 구조화 조회 ── data/profile.json   경력 사실
```

| 도구 | 하는 일 |
|---|---|
| `portfolio_get_profile` | 경력 회사·기간·직급, 학력, 기술 스택, 링크 |
| `portfolio_list_projects` | 프로젝트 11개 목록. 회사명 필터 지원 |
| `portfolio_get_publications` | 논문 7편(제1저자), 특허 2건(제1발명자), 수상 |
| `portfolio_search` | 기술문서 BM25 검색. 트러블슈팅 과정 같은 세부 내용용 |

전부 read-only다.

## 설계하면서 정한 것들

- 의존성은 `mcp` SDK와 `rank_bm25` 둘뿐이다. 처음엔 임베딩 검색도 고려했는데
  문서 5편에 청크 80개 규모에서 벡터 검색은 과하다. BM25면 충분하고,
  덕분에 GPU도 외부 API도 없이 clone 후 바로 돈다.
- 추론은 클라이언트 LLM의 몫이다. 서버는 데이터만 정확하게 내려주면 된다.
- 확정된 사실(경력, 논문, 수치)은 `profile.json`으로, 서술형 내용은 문서 검색으로
  분리했다. 숫자가 검색 랭킹에 따라 흔들리면 안 되기 때문이다.
- 검색 결과가 비면 "다른 키워드로 재검색하거나 목록부터 보라"는 힌트를
  응답에 같이 넣는다. 에러 메시지가 다음 행동을 알려줘야 agent가 헤매지 않는다.

## 실행

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt

python test_client.py   # 서버 기동 + 도구 4개 호출 스모크 테스트
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
