# 이윤선 이력서

이윤선
AI Inference/Serving Engineer
yoon7829@gmail.com | ckc5800.github.io | github.com/ckc5800 | taepseon.tistory.com

## PROFESSIONAL SUMMARY

모델을 서비스로 만드는 구간에서 병목을 계측으로 찾아 지연과 처리량을 바꾸는 일을 합니다.

- 엔진 처리량 4.2배 (0.73 → 3.09 rps) — FP8 양자화·vLLM 0.24 마이그레이션(Code2Wav CUDA-graph 배칭)
- TTS 첫 응답 49% 단축 (1,943 → 985ms) — 전체 합성 후 전송을 문장 단위 즉시 전송으로 전환
- 20여 개 모델이 운영되는 Kubernetes 클러스터의 관측 스택 5종 단독 구축 (3인 인프라 팀)
- 사내 메신저 요약 기능 전사 배포 (전 직원 약 300명) · 고객사 AIG 상담센터 STT 배포·운영
- 논문 7편 · 특허 등록 2건 (제1저자 · 제1발명자), 한국국방기술학회 우수논문상

## TECHNICAL SKILLS

- Serving · Inference: vLLM, Triton Inference Server, ONNX Runtime, FP8 Quantization, CUDA
- Backend · Infra: FastAPI, gRPC, WebSocket, asyncio, Redis, Kubernetes, Docker, ArgoCD, Jenkins, Harbor, NFS
- Observability: Prometheus, Grafana, AlertManager, Loki, ELK
- AI/ML: PyTorch, Hugging Face, Whisper, LangGraph
- Languages: Python, TypeScript, SQL
- Certifications · Language: Linux Master Level 2 (2016.03), ADsP 데이터분석 준전문가 (2021.02) | English: OPIc IM1 (2021.10)

## MiCo AI (구 에이아이세스)

AI Engineer · Serving | Apr 2025 – Present

### Qwen3-TTS 플랫폼 구축 (2026.03 ~ 현재)

PM 겸 풀스택 단독 개발 (Backend·Frontend·Infra)

- 배경: 상담센터를 타깃으로 제안할 자사 TTS 솔루션을 신규 구축 — 기존 시스템 교체가 아니라 제품을 처음부터 만드는 과제. 모델 선정(1.7B → 0.6B)부터 아키텍처 설계·최적화·배포·운영까지 전 구간 단독 수행, 고객사 PoC·시연 진행
- 시스템 아키텍처: vLLM 추론 엔진 위에 FastAPI 게이트웨이, Redis 캐싱·분산락, React 관리자 대시보드까지 전 구간 설계·개발. 긴 텍스트를 문장 단위로 나눠 첫 문장부터 WebSocket으로 내보내는 스트리밍 구조로 첫 소리까지의 대기를 단축
- 기능 확장: 참조 음성 5초로 화자 목소리를 복제하는 기능 구현. OpenAI TTS API 호환 인터페이스로 외부 서비스가 코드 수정 없이 연동되도록 구성
- 운영 고도화: vLLM(GPU)·Supertonic(CPU) 듀얼 엔진 — 장애 시 Circuit Breaker가 CPU 엔진으로 자동 폴백. 무중단 롤링 재배포 체계와 온보딩 가이드·운영 런북·장애복구 절차·성능 명세를 갖춰 운영 이관 가능 수준으로 정비
- 성과 (KPI) — Throughput: 최대 안정치 0.73 → 3.09 rps (4.2배). "1.7 rps가 천장"이라던 사내 결론을 버전×executor 2×2 전수 벤치로 반증 (1.7 → 3.09 rps, +80%). 상승분의 몸통은 vLLM 0.24 네이티브 Code2Wav CUDA-graph 배칭이고, executor 백엔드 교체 자체의 효과는 약 +6%
- 부하 테스트 3층 설계(스모크 → 순간 폭주 동시 4~256 → Poisson 도착 지속 부하) — Stream 기준 동시 256건까지 실패 없이 수용, 정상 부하 TTFB p50 70ms(p95 219ms), 최대 처리량 8.7 rps. 모델 경량화(1.7B → 0.6B) 후 재튜닝으로 burst p95 TTFB 0.5초(동시 15)·1초(동시 30), 동시 스트림 수용 한계 36 (RTF 마진 기준)
- 32개 언어 지원 단일 플랫폼 (vLLM 10 + Supertonic 22) | ITN 정규화 정확도 50.1% → 70.9% (검증셋 기준)
- 핵심 엔지니어링 7건 — 전면 백색소음 장애(SSE JSON이 PCM으로 재생), CPU 스파이크(ReDoS 가드가 치환마다 재생성), 팝 노이즈(PCM 홀수 바이트 정렬), 스토리지 누수, 세션 보안, ITN 토크나이저, WAV 헤더 단일화
- 오픈소스 기여 (vllm-project/vllm-omni) — 호스트 메모리 누수를 abort된 요청의 sender 상태 미회수로 규명, 최소 패치 A/B로 257 → 60 MiB/h (77% 감소) 검증. 부하·할당자·캐시가 다른 4개 구성에서 증가율은 13배 차이 나지만 abort당 36~56 KB로 일정한 불변량이 근거. 이슈 #6352 리포트 후 같은 원인의 기존 PR #4349에 검증 데이터 제공
- 기술: Python 3.12, FastAPI, WebSocket, vLLM-Omni, Redis, React 18, TypeScript, CUDA, FP8 Quantization

### TTS 스트리밍 API 리팩토링 (2025.09 ~ 2025.10)

- 기존 TTS(ZipVoice 기반)의 TTFB가 텍스트 길이에 비례해 늘어나 실시간 상담에 부적합. 전임 담당자에게 인계받아 스트리밍 API 리팩토링 전담 (기여도 100%)
- 브라우저 decodeAudioData()가 두 번째 청크부터 실패한다는 사내 사용 부서(API 호출측)의 제보를 추적 — 엔진이 첫 청크에만 RIFF WAV 헤더를 포함하는 것이 원인. 모든 SSE 청크에 독립 WAV 헤더를 부착해 해결
- 전체 합성 완료 후 분할 전송하던 기존 구조의 TTFB 한계를 확인하고, gRPC server streaming 기반 실시간 엔드포인트를 신설 — 문장 단위 합성 즉시 전송. API v2 표준화(v1 하위 호환)·Swagger 문서 정비·비교 데모 페이지 제공
- 성과: 같은 텍스트 완주 비교에서 TTFB 1,943ms → 985ms (49% 단축, 데모 페이지 콘솔 로그). 첫 문장이 짧은 런에서 334ms까지 측정됐으나 완주 비교가 아니라 대표값으로 쓰지 않음
- 기술: Python, FastAPI, Streaming API, ZipVoice

### STT 배포 (2025.07 ~ 2025.09)

- AIG 상담센터의 대량 상담 전화를 실시간 처리해야 했으나 동시 채널이 부족했음 — 팀 내 배포·서빙 담당으로 Faster Whisper 기반 STT 엔진의 배포·운영과 구조 개선을 맡음
- 팀이 구성한 Triton 기반 파일·스트리밍 두 추론 모델(HTTP·gRPC)을 배포·운영. 전처리·후처리·서비스 계층을 모듈화해 두 경로의 공통 로직을 통합하고, 로그·Docker 구조와 컨테이너 자동 재시작 정책으로 안정성 보강
- 화자 구간을 Faster Whisper clip_timestamps로 변환해 화자별 전사를 생성하는 기능은 구현했으나, 결과 품질이 기준에 못 미쳐 실제 서비스에는 적용되지 않았음
- 기술: Triton Inference Server(Python Backend), Faster Whisper, Streaming ASR, gRPC/HTTP, Docker

### 화자 분리(Speaker Diarization) 검토 (2025.04 ~ 2025.07)

- 자사 메신저 제품의 영상통화 회의록·전사에 화자 구분이 필요해 Pyannote 사전 학습 모델을 평가하고 공개 데이터로 파인튜닝 시도
- 데이터 양·라벨 부족으로 DER 개선이 없어 미채택 — 판정 근거를 남기고 종료. STT 결합까지 진행되지 않음
- 기술: Pyannote, PyTorch, Audio Processing

## 인피닉 (INFINIQ)

AI Engineer · MLOps Engineer | Aug 2022 – Apr 2025

### Kubernetes 기반 AI 인프라 (2024.03 ~ 2025.04)

- AI 서비스 20여 개를 수동 배포해 1회에 수 시간이 걸리던 상황 — 인프라 엔지니어 3인이 온프레미스에 사내 최초 Kubernetes 플랫폼을 구축하고, 그중 관측성 전 계층(지표·경보·로그)과 스토리지를 맡아 설계·구축·운영
- 스토리지 담당 — NFS 기반 PV/PVC로 모델 가중치·데이터 영속 볼륨을 구성해, 파드가 재시작돼도 모델을 다시 받지 않도록 정비. 클러스터 네트워크(MetalLB LoadBalancer, Nginx Ingress 라우팅)는 팀에서 구성
- 서비스를 Docker로 컨테이너화해 Deployment·StatefulSet으로 올리고, GitLab → Jenkins·Argo Workflow → Harbor → ArgoCD GitOps 파이프라인으로 20여 개 ML 모델을 자동 배포 (팀 성과)
- 관측 스택 5종을 단독 구축 — Prometheus 지표와 Grafana 대시보드로 20여 개 모델 상태를 한 화면에 모으고, AlertManager 경보로 사람이 발견하기 전에 장애를 알리도록 전환. 온프레미스라 Elasticsearch·Loki를 클러스터에 직접 설치·운영
- 아쉬운 점: 관측 스택은 세웠지만 SLO를 정의하지 못했음. 경보를 리소스 임계값에 걸어 둬서 사용자가 체감하는 장애와 어긋나는 경우가 있었음
- 기술: Kubernetes, Docker, Helm, GitLab CI/CD, Jenkins, Argo Workflow, ArgoCD, Harbor, MetalLB, Nginx Ingress, NFS(PV/PVC), Prometheus/Grafana/AlertManager, Loki/ELK

### NLP 문서·대화 요약 시스템 (2024.01 ~ 2024.07)

- 휴가나 부재 후 복귀한 직원이 단체방에 쌓인 업무 대화를 따라잡는 데 오랜 시간이 걸린다는 문제에서 출발
- 대화 요약은 BART, 문서 요약은 T5로 나눠 개발. 한국어 대화-요약 쌍 수집·전처리부터 임베딩 모델 선정까지 학습 파이프라인을 직접 구축하고, 사전 학습 표현을 훼손하지 않는 R3F 정규화로 파인튜닝
- ROUGE와 사내 인원 휴먼 평가를 품질 지표로 사용. 사내 메신저(전 직원 약 300명)에 실제 적용해 긴 공지를 한 줄로 줄이고 업무 대화를 2초대에 요약
- 기술: BART, T5, R3F Fine-tuning, Hugging Face, PyTorch

### 생성모델 · 3D Segmentation 연구 (2022.08 ~ 2023.12)

- 국방 도메인은 보안 제약으로 학습 데이터 확보가 어려워, Latent Diffusion으로 합성 데이터를 생성하고 실제 모델 학습에 투입해 데이터 부족을 보완할 수 있음을 검증 — 제1저자 논문 2편, 한국국방기술학회 우수논문상 (2023.11)
- LiDAR·카메라 센서 퓨전 Trans-Unet으로 3D 시맨틱 세그멘테이션 연구 — 제1저자 논문 2편(한국자동차공학회), 제1발명자 특허 등록 2건
- 기술: Latent Diffusion, LoRA, Trans-Unet, LiDAR + Camera Sensor Fusion, YOLO, PyTorch

## 이든티앤에스 (EDEN T&S)

AI Engineer | Apr 2022 – Aug 2022

- OCR 데이터 자동 생성 툴 — 수동 주석이 병목이던 학습 데이터 생성을, 이미지 로딩·라벨링·저장을 갖춘 어노테이션 툴을 단독 개발해 자동화. 인식 성능 개선으로 프로젝트 성공 성과금 수령
- 기술: PyQt, Tesseract OCR, ViT-based Table Detection, RNN Text Extraction

## 큐헷지 (QUHEDGE)

Contract | Sep 2020 – Sep 2021

- 금융 데이터 크롤링·정제·검증·저장까지 자동화 파이프라인을 구축해 수작업 수집을 대체
- 기술: Python, Pandas, NumPy, SQL

## KISTI

Part-time | Oct 2017 – Jan 2018

- 한국어 고어(古語) 데이터 정제 및 전처리 작업 수행

## PERSONAL PROJECTS

개인 프로젝트 | Jul 2026 – Present | github.com/ckc5800

- rag-agent — LangGraph Corrective-RAG + 하이브리드 검색(FAISS·BM25 RRF). 평가셋을 10 → 140문항으로 늘려가며 검색/생성 분리 측정, 코퍼스 15.6배 확장 후에도 튜닝값 유지 확인 (140문항 84.3%). 채점기 결함을 찾아내 6%p 보정
- visual-search — 패션 상품 44,072개 CLIP 인덱스를 한국어 문장형 질의로 검색. 실패를 gold 크기 구간별로 분해해 원인을 규명한 뒤 메타 필터로 결합, hit@10 90% → 98%. 다국어 전략 6종 실측 비교
- demand-forecast — M5 30,490 시리즈 롤링 백테스트로 제로샷 Chronos와 튜닝 베이스라인 비교. 계층 레벨에 따라 판정이 역전됨을 실측
- portfolio-mcp — 포트폴리오 조회용 MCP 서버·클라이언트 양쪽 구현 (FastMCP, 의존성 2개)
- pdm-agent — 센서 이상탐지 + LLM 진단 파이프라인. NASA C-MAPSS 검증 — 고장 전 경보 94/100대, RUL 오차(MAE) 12.9사이클(30사이클 전 기준)
- llm-bench — 추론 벤치마크 CLI. Ollama 직렬화 병목 진단·재측정 — TTFT p50 12.4초 → 0.93초

## EDUCATION & CREDENTIALS

- M.S. Computer Science | Inha University (2021.08)
- B.S. Computer Science | Hannam University (2018.02)
- Certifications: Linux Master Level 2 (2016.03), ADsP 데이터분석 준전문가 (2021.02)
- Language: English (OPIc IM1, 2021.10)

## PUBLICATIONS & PATENTS

Publications (First Author):

- 국방 데이터 확보를 위한 생성모델 Latent Diffusion 실험 | 국방기술학회 (2023) 우수논문상
- GAN을 활용한 데이터 생성 연구 동향 | 한국항공우주학회 (2023)
- 센서 퓨전 기반의 Trans-Unet을 활용한 2차원 시맨틱 세그멘테이션의 3차원적 해석 | 한국자동차공학회 (2023)
- 자율 주행 도메인의 3차원 시맨틱 세그멘테이션을 위한 센서 퓨전 기반의 Trans-Unet | 한국자동차공학회 (2022)
- 비정형, 정형 데이터의 이미지 학습을 활용한 시장예측 | 스마트미디어학회 (2021)
- Trend of Malware Detection Using Deep Learning | ACM International Conference (2018)
- Deep Learning을 활용한 악성코드 탐지 방법 동향 분석 | 한국정보기술학회 (2018)

Patents (First Inventor):

- 센서 퓨전 기반의 시맨틱 세그멘테이션 방법 | 등록번호 1025382250000
- 시맨틱 세그멘테이션의 3차원 해석 방법 | 등록번호 1025382310000

Awards: 우수논문상 — 한국국방기술학회 (2023.11)
