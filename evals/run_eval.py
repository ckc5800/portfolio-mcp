# -*- coding: utf-8 -*-
"""questions.xml 의 질문이 도구만으로 답에 도달하는지 검증한다.

재는 것과 재지 않는 것을 분명히 해 둔다.
  재는 것   — 각 질문의 정답 문자열이 도구 응답 안에 실제로 있는가.
             없으면 클라이언트 LLM 이 아무리 똑똑해도 그 질문에는 못 답한다.
  안 재는 것 — LLM 이 그 응답에서 옳은 문장을 만들어 내는가. 그건 클라이언트
             몫이고, 모델·프롬프트에 따라 흔들려서 서버 회귀 테스트로는
             쓸 수 없다.

숫자 답은 표기 흔들림을 흡수한다('12' 는 '12개월'·'12 개월' 어디에 있어도 통과,
'2023.05.25' 는 '2023-05-25' 도 인정). 반대로 답이 없는데 통과하는 일이
없도록, 비교·계산이 필요한 질문은 계산된 필드(months 등)가 실제로 그 값을
내는지까지 본다.

    python evals/run_eval.py            # 전체 실행
    python evals/run_eval.py --verbose  # 실패 항목의 도구 응답도 출력
"""
import asyncio
import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402

QUESTIONS = Path(__file__).parent / "questions.xml"

_CALL = re.compile(r"^(\w+)\((.*)\)$", re.S)


def _parse_call(spec: str) -> tuple[str, dict]:
    """'portfolio_search(query=abc, source=resume.md)' → (이름, 인자)."""
    m = _CALL.match(spec.strip())
    if not m:
        raise ValueError(f"호출 형식이 아님: {spec}")
    name, raw = m.group(1), m.group(2).strip()
    args: dict[str, object] = {}
    if raw:
        for part in raw.split(","):
            if "=" not in part:
                raise ValueError(f"인자 형식이 아님: {part} ({spec})")
            k, v = part.split("=", 1)
            v = v.strip()
            args[k.strip()] = int(v) if v.isdigit() else v
    return name, args


def _answer_pattern(answer: str) -> re.Pattern:
    """정답을 응답에서 찾을 정규식으로. 숫자는 더 긴 수의 일부로 걸리면 안 된다.

    초판은 공백·구분자를 지운 문자열 포함으로 채점했는데, 그러면 정답 '8'이
    응답 어딘가의 '2018'에 걸려 통과했다. 채점기가 무르면 평가가 거짓말을
    한다(rag-agent 에서 같은 함정을 겪었다). 숫자에는 앞뒤 자릿수 경계를
    요구하고, 날짜·범위 구분자만 흔들림을 허용한다.
    """
    parts, buf = [], ""
    for ch in answer:
        if ch in " ,-./~":
            if buf:
                parts.append(re.escape(buf)); buf = ""
            parts.append(r"[\s,\-./~]*")
        else:
            buf += ch
    if buf:
        parts.append(re.escape(buf))
    body = "".join(parts)
    head = r"(?<![\d.])" if answer[:1].isdigit() else ""
    tail = r"(?![\d])" if answer[-1:].isdigit() else ""
    return re.compile(head + body + tail, re.I)

async def _run_call(name: str, args: dict) -> object:
    fn = getattr(server, name, None)
    if fn is None:
        raise ValueError(f"그런 도구가 없음: {name}")
    return await fn(**args)


async def main(verbose: bool = False) -> int:
    tree = ElementTree.parse(QUESTIONS)
    pairs = tree.getroot().findall("qa_pair")
    passed, failures = 0, []
    for i, qa in enumerate(pairs, 1):
        question = qa.findtext("question", "").strip()
        answer = qa.findtext("answer", "").strip()
        spec = qa.findtext("tools", "").strip()
        try:
            name, args = _parse_call(spec)
            result = await _run_call(name, args)
        except Exception as exc:                      # 도구 호출 자체가 깨진 경우
            failures.append((i, question, f"도구 호출 실패: {exc}"))
            print(f"[FAIL] {i:2}. {question[:50]} — 도구 호출 실패: {exc}")
            continue
        blob = json.dumps(result, ensure_ascii=False)
        ok = bool(_answer_pattern(answer).search(blob))
        if ok:
            passed += 1
            print(f"[OK  ] {i:2}. {question[:50]} → {answer}")
        else:
            failures.append((i, question, blob))
            print(f"[FAIL] {i:2}. {question[:50]} → '{answer}' 가 {name} 응답에 없음")
            if verbose:
                print("        " + blob[:600])
    print(f"\n도구로 답에 도달: {passed}/{len(pairs)}")
    if failures:
        print("실패한 질문은 도구 응답에 근거가 없다는 뜻이다 — "
              "데이터를 고치거나, 그 답을 내는 도구를 추가할 것.")
    return 0 if passed == len(pairs) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--verbose" in sys.argv)))
