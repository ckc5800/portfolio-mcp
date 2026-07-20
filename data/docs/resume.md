# 이윤선 이력서

이윤선
6년차 AI 엔지니어
yoon7829@gmail.com | ckc5800.github.io | github.com/ckc5800 | taepseon.tistory.com
PROFESSIONAL SUMMARY
AI Engineer | MLOps Specialist
연구에서 시작해 인프라를 거쳐 서비스로 넘어온 6년차 AI 엔지니어입니다.
석사와 첫 직장들에서 논문 7편과 특허 2건을 쓰며 모델을 연구했고, 인피닉에서는 사내 첫 Kubernetes를 도입해 20여 개 모델의 배포 체계를 만들었습니다.
지금은 TTS 서비스를 설계부터 운영까지 맡아 첫 응답 시간을 2.3초에서 0.33초로 줄였습니다.
최근에는 RAG와 Multi-Agent 시스템을 만들며 LLM 시스템 엔지니어링으로 영역을 넓히는 중입니다.
MiCo AI (구 에이아이세스)
Manager  |  Apr 2025 – Present
TTS 프로젝트 (2025.09 ~ 현재)
PM 및 풀스택 개발 (Backend/Frontend/Infrastructure)
▸ 배경: 고객 상담 센터에 실시간 음성 합성이 필요했으나, 기존 시스템은 응답까지 2.3초가 걸리고 동시 처리도 12채널이 한계였음
▸ 시스템 아키텍처
vLLM 추론 엔진 위에 FastAPI 게이트웨이, Redis 캐싱과 분산락, React 관리자 대시보드까지 서비스 전 구간을 설계하고 구축. 긴 텍스트를 문장 단위로 나눠 전체 합성이 끝나기 전에 첫 문장부터 WebSocket으로 내보내는 스트리밍 구조를 만들어, 사용자가 첫 소리를 듣기까지의 대기 시간을 줄임.
▸ 기능 확장
참조 음성 5초만으로 화자 목소리를 복제하는 기능을 구현. OpenAI TTS API와 호환되는 인터페이스를 제공해 외부 서비스가 코드 수정 없이 연동되도록 지원.
▸ 성과 (KPI)
✓ TTFB: 2292ms → 334ms (85% 단축, 6.85배 개선)
✓ 동시 처리 채널: 12 → 24개 (2배 확대)
✓ Throughput: 0.68 → 1.41 rps (2배), 최적화 후 ~1.7 rps (2.5배 향상)
✓ 단건 지연시간 15% 감소 | RTF 0.1 이하 | CER(문자 오류율) 3~5%
✓ 32개 언어 지원 단일 플랫폼 | ITN 정규화 정확도 50.1% → 70.9%
▸ 핵심 엔지니어링 (5가지 문제 해결)
① 스트리밍 팝 노이즈: 재생 중 간헐적으로 '틱' 잡음이 나는 문제를 추적해, PCM 청크가 홀수 바이트로 끊겨 와 샘플 정렬이 깨지는 것을 원인으로 확인. 잔여 바이트를 다음 청크에 이어 붙이는 버퍼로 잡음을 제거
② 스토리지 누수: 보이스를 지워도 디스크 사용량이 계속 늘어나는 문제에서 파생 해시 캐시 파일이 남는 구조를 찾아내고, 파생 파일까지 추적해 지우는 정리 로직으로 누수를 차단
③ 세션 보안: 비밀번호를 바꿔도 이미 발급된 토큰으로 웹소켓이 열리는 허점을 발견하고, 토큰 발급 시점과 비밀번호 변경 시점을 대조해 변경 이전 토큰을 전부 무효화
④ ITN 정규화: URL과 이메일이 섞인 문장을 자연스러운 한국어 발음으로 읽어 주는 정규식 토크나이저를 직접 개발해 낭독 품질을 개선
⑤ CPU 병목: 요청마다 스레드풀이 새로 만들어지던 오버헤드를 프로파일링으로 찾아 제거하고, 텍스트 처리 로직을 O(N²)에서 O(1)로 개선
▸ 기술 스택
Python 3.12, FastAPI, WebSocket, vLLM-Omni, Redis (Pub/Sub, Distributed Lock, TTL Cache), React 18, TypeScript, CUDA Optimization, FP8 Quantization
STT 배포 (2025.07 ~ 2025.09)
• 배경: AIG 상담센터의 대량 상담 전화를 실시간으로 처리해야 했으나 기존 시스템은 동시 채널이 부족했음
• 팀 내 배포·서빙 담당으로 WhisperX 기반 STT 엔진을 맡아, 배치 처리와 실시간 스트리밍을 모두 지원하도록 운영 구조를 고도화
• 코드 모듈화와 전처리 병렬화로 처리 속도를 높이고, 로그·Docker 구조 개선과 컨테이너 재시작 모니터링으로 안정성을 보강
• 사내 회의록 기능에도 STT를 탑재 — Whisper 계열 모델별 성능을 조사해 최적 파라미터를 찾고, 파일 기반과 스트리밍 기반 처리를 모두 구현
• 기술: Triton Inference Server, Streaming ASR Pipeline, Docker
• Triton Inference Server 기반으로 추론 환경을 최적화해 동시 처리 채널을 늘렸고, 고객사 AIG의 요구사항 4건을 해결
Speaker Diarization (2025.04 ~ 2025.07)
• 배경: 상담 품질을 분석하려면 상담사와 고객의 발화부터 구분해야 했음
• Pyannote 사전 학습 모델을 한국어 상담 데이터로 파인튜닝하고 STT와 연동해, 상담사와 고객이 구분된 전사 결과가 나오는 파이프라인을 구성
• 기술: Pyannote, PyTorch, Audio Processing
인피닉 (INFINIIC)
AI Engineer/MLOps Engineer  Aug 2022 – Apr 2025
Kubernetes 기반 AI 인프라 (2024.03 ~ 2025.04)
• 배경: 20개가 넘는 AI 서비스를 수동으로 배포하느라 배포 한 번에 수 시간이 걸렸음
• 사내 최초 Kubernetes 도입 프로젝트를 맡아, 기존 ML 서비스 운영 환경 분석부터 온프레미스 클러스터 아키텍처 설계, 배포, 운영까지 전 과정을 수행
• 서비스들을 Docker로 컨테이너화하고 Kubernetes Deployment와 StatefulSet으로 올려, VM 수동 배포를 컨테이너 기반 자동 배포 체계로 전환
• 온프레미스에는 클라우드식 로드밸런서가 없어 MetalLB로 LoadBalancer를 직접 구성하고, Nginx Ingress Controller로 외부 요청 라우팅과 서비스 노출 구조를 설계. 영속 데이터는 NFS 기반 PV/PVC로 구성
• 기술: Kubernetes, Docker, Helm, GitLab CI/CD, Jenkins, Argo Workflow, ArgoCD, Harbor, MetalLB, Nginx Ingress, NFS(PV/PVC), Prometheus/Grafana/AlertManager, Loki/ELK
• GitLab에 소스가 올라오면 Jenkins와 Argo Workflow가 이미지를 빌드해 Harbor 레지스트리에 올리고, ArgoCD가 클러스터에 배포하는 CI/CD 파이프라인을 구축해 20개 이상의 ML 모델을 자동 배포
• Prometheus와 Grafana로 서비스·클러스터 모니터링을, AlertManager로 경보를, Loki와 ELK로 로그 수집·분석을 구축하고, 이후 클러스터 장애 대응과 리소스 관리까지 운영을 담당
NLP-based Text Summarization System (2024.01 ~ 2024.07)
• 배경: 휴가나 부재 후 복귀한 직원이 단체방에 쌓인 업무 대화를 따라잡는 데 오랜 시간이 걸린다는 문제에서 출발, 사내 메신저에 넣을 요약 모델을 개발
• 대화 요약은 BART, 문서 요약은 T5로 나눠 개발. 한국어 대화-요약 쌍 데이터 수집과 전처리, 임베딩 모델 선정까지 학습 파이프라인을 직접 구축
• 파인튜닝에는 사전 학습 표현을 훼손하지 않는 R3F 정규화 기법을 적용하고, ROUGE 점수와 사내 인원 휴먼 평가를 품질 지표로 사용
• 기술: BART, T5, R3F Fine-tuning, Hugging Face, PyTorch
• 사내 메신저에 요약 기능을 붙여 실제 적용. 긴 사내 공지를 한 줄로 줄이고, 실제 업무 대화를 2초대에 요약하는 결과까지 확인
• STT 결과 텍스트를 활용한 자연어 처리 기능 개발과 모델 적용용 데이터 전처리·평가 환경 구축도 함께 수행
Generative Model Research (2023.01 ~ 2023.12)
• 배경: 국방 도메인은 보안 제약 때문에 학습 데이터를 구하기 어려워, 생성 모델로 데이터를 만드는 쪽을 연구
• Latent Diffusion으로 합성 데이터를 생성하고 실제 모델 학습에 투입해, 부족한 학습 데이터를 보완할 수 있음을 검증
• 기술: Latent Diffusion, GAN, PyTorch
• 제1저자 논문 2편을 게재하고 국방기술학회에서 우수논문상을 수상 (2023.11)
3D Semantic Segmentation Research (2022.08 ~ 2022.12)
• 배경: 자율주행 인식에는 거리·높이 같은 3D 정보가 필요해, LiDAR와 카메라를 결합하는 센서 퓨전을 연구
• 한국자동차공학회에 제1저자 논문 2편을 게재하고, 제1발명자로 특허 2건을 등록
• 기술: Trans-Unet, LiDAR + Camera Sensor Fusion, PyTorch
이든티앤에스 (EDEN T&S)
AI Engineer  Apr 2022 – Aug 2022
OCR Dataset Auto-Generation Tool
• 배경: OCR 학습 데이터를 대량으로 만들어야 했지만 수동 주석 작업이 병목이었음
• 이미지 로딩부터 라벨링, 저장까지 갖춘 어노테이션 툴을 혼자 설계하고 개발
• 기술: PyQt, Tesseract OCR, ViT-based Table Detection, RNN Text Extraction
• 학습 데이터 생성을 자동화하고 문서 인식 성능을 개선해, 프로젝트 성공 성과금을 받음
큐헷지 (QUHEDGE)
Intern  |  Sep 2020 – Sep 2021
금융 데이터 수집 및 전처리 파이프라인
• 배경: 여러 소스에 흩어진 금융 데이터를 손으로 모아 쓰고 있었음
• 크롤링으로 데이터를 모으고 정제, 검증, 저장까지 이어지는 자동화 파이프라인을 구축해 수작업 수집을 대체
• 기술: Python, Pandas, NumPy, SQL
KISTI
알바  |  Oct 2017 – Jan 2018
한국어 고어 데이터 전처리
• 한국어 고어(古語) 데이터 정제 및 전처리 작업 수행
TECHNICAL SKILLS
Languages: Python (Expert), TypeScript, Bash, SQL
AI/ML: PyTorch, Hugging Face, vLLM, ONNX Runtime, Whisper
Model Serving: vLLM, Triton Inference Server, ONNX Runtime
Backend: FastAPI, gRPC, WebSocket, REST APIs, Asyncio
DevOps: Kubernetes, Docker, GitLab CI, Jenkins, ArgoCD, Harbor, Nginx
Data & Storage: Redis, PostgreSQL, MongoDB, S3, NFS
Monitoring: Prometheus, Grafana, Loki, ELK Stack, AlertManager
EDUCATION & CREDENTIALS
M.S. Computer Science  |  Inha University (2021.08)
B.S. Computer Science  |  Hannam University (2018.02)
Certifications: Linux Master (2016), Data Analytics Semi-Professional (2021)
Language: English (Intermediate, OPIc IM1)
PUBLICATIONS & PATENTS
Publications (First Author):
• 국방 데이터 확보를 위한 생성모델 Latent Diffusion 실험  |  국방기술학회 (2023) 우수논문상
• GAN을 활용한 데이터 생성 연구 동향  |  한국항공우주학회 (2023)
• 센서 퓨전 기반의 Trans-Unet을 활용한 2차원 시맨틱 세그멘테이션의 3차원적 해석  |  한국자동차공학회 (2023)
• 자율 주행 도메인의 3차원 시맨틱 세그멘테이션을 위한 센서 퓨전 기반의 Trans-Unet  |  한국자동차공학회 (2022)
• 비정형, 정형 데이터의 이미지 학습을 활용한 시장예측  |  스마트미디어학회 (2021)
• Trend of Malware Detection Using Deep Learning  |  ACM International Conference (2018)
• Deep Learning을 활용한 악성코드 탐지 방법 동향 분석  |  한국정보기술학회 (2018)
Patents (First Inventor):
• Sensor Fusion-based Semantic Segmentation Method  |  Reg. No. 1025382250000
• 3D Interpretation Method for Semantic Segmentation  |  Reg. No. 1025382310000
Awards: Outstanding Paper Award – Korea Defense Technology Society (Nov 2023)
