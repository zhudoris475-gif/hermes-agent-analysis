# Hermes Agent 부족점 분석 및 개선 방법

> **분석 대상:** [NousResearch/hermes-agent](https://github.com/nousresearch/hermes-agent) v0.13.0
> **분석 일자:** 2026-05-08
> **분석 방법:** GitHub Issues, PR, 커뮤니티 리뷰, 기술 평가서, 학술 논문 종합

---

## 목차

1. [부족점 분석 (10대 영역)](#1-부족점-분석-10대-영역)
2. [개선 방법 상세 (5대 핵심 영역)](#2-개선-방법-상세-5대-핵심-영역)
3. [종합 로드맵](#3-종합-로드맵)
4. [참고 자료](#4-참고-자료)

---

## 1. 부족점 분석 (10대 영역)

### 1.1 🔴 메모리 누수 및 안정성 (가장 심각)

| 항목 | 내용 | 심각도 |
|------|------|--------|
| Gateway 메모리 누수 | Discord 세션 2개만으로 1시간에 ~8GB RAM 누적, OOM Kill 발생 | P1-Critical |
| TUI/GatewayClient 누수 | V8 힙이 기본값 초과하여 Fatal OOM, RPC 타임아웃 클로저 미해제 | P1 |
| MCP 서브프로세스 누수 | CLI 종료 후 고아 MCP 프로세스 잔류 | P1 |
| CLI 무응답(Freeze) | 작업 완료 후 더미 상태 진입, Ctrl-C 불响应 | P2 |
| Chrome headless 누적 | 게이트웨이 cgroup 하위에 ~500MB 추가 소모 | P2 |

**출처:** [Issue #18438](https://github.com/NousResearch/hermes-agent/issues/18438), [PR #13231](https://github.com/NousResearch/hermes-agent/pull/13231), [PR #19000](https://github.com/NousResearch/hermes-agent/pull/19000)

---

### 1.2 🟠 자가학습 시스템 결함

| 항목 | 내용 |
|------|------|
| 잘못된 경험 고착화 | 에이전트가 거의 항상 자신이 맞았다고 판단 → 오류 경험도 영구 저장 |
| 기본값 off | 가장 큰 차별화 기능이 비활성화 상태로 배포 |
| 모순 감지 불가 | "PostgreSQL 사용"과 "MySQL로 전환"이 영구히 공존 |
| 장기 기억 검색 정확도 | 62%에 불과 |

**출처:** [CNBoys](https://www.cnblogs.com/haibindev/Undeclared/19881562), [Issue #509](https://github.com/NousResearch/hermes-agent/issues/509), [CSDN](https://lvyuanj.blog.csdn.net/article/details/160060138)

---

### 1.3 🟠 토큰 오버헤드 및 비용

| 항목 | 내용 |
|------|------|
| 고정 오버헤드 | API 호출의 73%가 컨텍스트/Skill 로딩에 소비 |
| 도구 정의 비용 | 평균 $0.30/call의 고정 비용 |
| 반성 모듈 | 추가 15~25% 토큰 오버헤드 |
| 복잡 작업 실패율 | 5단계 이상 작업에서 35% 실패 |

---

### 1.4 🟡 코드 품질 및 아키텍처

| 항목 | 내용 |
|------|------|
| 모놀리식 구조 | CLI, 게이트웨이, 도구, RL, MCP, 15개 플랫폼 어댑터가 단일 저장소 |
| 거대 파일 | `auxiliary_client.py` 117KB, `credential_pool.py` 59KB, `cli.py` 11,000줄 |
| 테스트 취약 | 환경 정규화 래퍼 필요, 우발적 결합 다수 |
| 하드코딩 가격표 | `_OFF` 등 모델 가격 테이블 하드코딩 |

**출처:** [Michael O'Boyle 평가](https://gist.github.com/michaeloboyle/10461598db36066e4c366413d5416f83), [CSDN](https://blog.csdn.net/zhonglinzhang/article/details/160300106)

---

### 1.5 🟡 이슈 밀린(backlog)

| 항목 | 수치 |
|------|------|
| 총 이슈 | ~11,700개 |
| 미해결 | ~1,800개 |
| 기여자 | 30명 |
| 이슈/기여자 비율 | 390:1 (심각한 유지보수 부채) |

---

### 1.6 🟡 코드 인텔리전스 부재 (vs Claude Code)

| 기능 | Hermes | Claude Code |
|------|--------|-------------|
| LSP 통합 | ❌ 없음 | ✅ 9종 LSP 조작, 11개 언어 |
| AST 인식 | ❌ 없음 | ✅ 구조적 코드 편집 |
| IDE 통합 | 제한적 (ACP만) | VS Code, JetBrains |
| 파일 참조 | ❌ 없음 | `@` 파일 참조 |

---

### 1.7 🟡 멀티 에이전트 한계

| 항목 | 한계 |
|------|------|
| 위임 깊이 | 최대 2단계 |
| 동시 자식 | 최대 3개 |
| 의존성 인식 | 없음 (모두 동시 실행) |
| 충돌 복구 | 없음 |
| DAG 워크플로우 | 미지원 |

**출처:** [Issue #344](https://github.com/NousResearch/hermes-agent/issues/344)

---

### 1.8 🟡 보안 모델 한계

| 항목 | 내용 |
|------|------|
| 운영자 신뢰 전용 | 멀티 테넌트 배포 부적합 |
| 권한 우회 가능 | LLM이 사용자에게 직접 명령 실행 지시 가능 |
| OAuth 버그 | 토큰 idempotency 부재, 모델 선택 미연동 |

**출처:** [Issue #527](https://github.com/NousResearch/hermes-agent/issues/527), [Issue #12905](https://github.com/NousResearch/hermes-agent/issues/12905)

---

### 1.9 🟢 버전 안정성

| 항목 | 내용 |
|------|------|
| 급격한 업데이트 | 2개월 만에 v0.1→v0.9 (9버전) |
| v1.0 LTS | 미존재 |
| Breaking change | 빈번 |
| Windows 호환성 | 다중 이슈 보고 |

---

### 1.10 🟢 문서화 및 신뢰

| 항목 | 내용 |
|------|------|
| Star 증가 의혹 | Reddit에서 봇 홍보 의혹, 2개월 9만 Star는 이례적 |
| 데이터 수집 목적 | Trajectory 저장이 NousResearch 모델 훈련용 |
| 중국 시장 편중 | WeChat, DingTalk, Feishu 등 중국 플랫폼에 집중 |

---

## 2. 개선 방법 상세 (5대 핵심 영역)

### 2.1 메모리 누수 해결

#### 2.1.1 `deque` 기반 슬라이딩 윈도우 (P0, 난이도: 매우 낮음)

```python
from collections import deque

class AgentMemory:
    def __init__(self, max_size=20):
        self.history = deque(maxlen=max_size)  # O(1) 메모리 증가

    def add(self, message):
        self.history.append(message)  # 자동으로 오래된 항목 제거
```

#### 2.1.2 `weakref`로 순환 참조 제거 (P0, 난이도: 낮음)

```python
import weakref

class Tool:
    def __init__(self, agent):
        self.agent_ref = weakref.ref(agent)  # 강참조 → 약참조

    def run(self):
        agent = self.agent_ref()
        if agent:  # 에이전트가 여전히 존재하면
            agent.do_something()
```

#### 2.1.3 `tracemalloc` 실시간 모니터링 (P1, 난이도: 낮음)

```python
import tracemalloc

tracemalloc.start(25)  # 25단계 호출 스택 추적

snapshot1 = tracemalloc.take_snapshot()
run_agent_tasks()
snapshot2 = tracemalloc.take_snapshot()

for stat in snapshot2.compare_to(snapshot1, 'lineno')[:10]:
    print(stat)  # 누수 위치 즉시 식별
```

#### 2.1.4 백그라운드 GC 가디언 (P1, 난이도: 낮음)

```python
import gc, threading, time

class MemoryGuardian(threading.Thread):
    def run(self):
        while True:
            gc.collect()
            time.sleep(10)

guardian = MemoryGuardian()
guardian.daemon = True
guardian.start()
```

#### 2.1.5 LLM 기반 메모리 압축 (P2, 난이도: 중간)

```python
if len(memory.history) > 50:
    summary = llm.summarize(list(memory.history))
    memory.history.clear()
    memory.history.append(summary)
```

---

### 2.2 자가학습 검증 강화

#### 2.2.1 실패/성공 분리 저장 (P0, 난이도: 낮음)

```python
class ExperienceStore:
    def __init__(self):
        self.experiences = []      # 성공 경험
        self.lesson_memory = []    # 실패 교훈 (별도 저장)

    def add_experience(self, experience: dict, outcome: bool):
        if not outcome:
            lesson = self._distill_lesson(experience)
            self.lesson_memory.append(lesson)
            return  # 실패는 일반 메모리에 저장하지 않음
        if self._consensus_check(experience):
            self.experiences.append(experience)
```

#### 2.2.2 합의 기반 검증 (P1, 난이도: 중간)

```python
def _consensus_check(self, experience: dict) -> bool:
    """유사 경험 3개 조회 후 과반수 일치 확인"""
    similar = self._find_similar(experience, k=3)
    if len(similar) < 2:
        return True
    agreement = sum(1 for s in similar
                    if s['conclusion'] == experience['conclusion'])
    return agreement >= len(similar) / 2
```

#### 2.2.3 A-MemGuard 참고 (학술, ICLR 2026)

- **이중 메모리 구조:** 일반 메모리 + 교훈 메모리 분리
- **합의 기반 이상치 탐지:** 다수결로 잘못된 경험 차단
- **효과:** 공격 성공률 95% 이상 감소
- **출처:** [A-MemGuard - OpenReview](https://openreview.net/forum?id=fVxfCEv8xG)

---

### 2.3 토큰 오버헤드 최적화

#### 2.3.1 도구 정의 압축 (P0, 난이도: 매우 낮음)

- 도구 description을 1-2문장으로 단축
- 불필요한 파라미터 제거
- 효과: 즉각적인 토큰 절감

#### 2.3.2 동적 도구 로딩 (P0, 난이도: 중간)

Anthropic의 `defer_loading` 패턴 참고:

```json
{
  "tools": [
    {"type": "tool_search_tool_regex_20251119", "name": "tool_search_tool_regex"},
    {"name": "github.createPullRequest", "input_schema": {...}, "defer_loading": true}
  ]
}
```

- **효과:** 85% 토큰 절감 (77K → 8.7K)
- **도구 선택 정확도:** Opus 4 (49%→74%), Opus 4.5 (79.5%→88.1%)
- **출처:** [Anthropic Engineering Blog](https://www.anthropic.com/engineering/advanced-tool-use)

#### 2.3.3 Programmatic Tool Calling (P2, 난이도: 높음)

```python
# Claude가 Python 코드로 도구를 오케스트레이션
team = await get_team_members("engineering")
expenses = await asyncio.gather(*[get_expenses(m["id"], "Q3") for m in team])
# 최종 결과만 컨텍스트에 반환 → 37% 토큰 감소
```

#### 2.3.4 Acon (Microsoft) 컨텍스트 압축 (P1)

- 환경 관찰 기록을 규약화된 가이드라인으로 압축
- 작은 모델로 압축기 증류 (95% 정확도 유지)
- **효과:** 26-54% peak tokens 감소
- **코드:** https://github.com/microsoft/acon

---

### 2.4 모듈식 아키텍처 리팩토링

#### 2.4.1 목표 디렉토리 구조 (P0, 난이도: 낮음)

```
hermes/
├── cli/                  # CLI 진입점
├── core/                 # 핵심 Agent 로직
│   ├── agent.py
│   ├── planner.py
│   └── executor.py
├── memory/               # 메모리 시스템
│   ├── short_term.py
│   ├── long_term.py
│   └── compression.py
├── tools/                # 도구 정의 및 실행
│   ├── base.py
│   ├── registry.py
│   └── builtin/
├── prompts/              # 프롬프트 템플릿
├── config/               # 설정 관리
├── gateways/             # 15개 플랫폼 어댑터
│   ├── discord/
│   ├── telegram/
│   └── ...
├── mcp/                  # MCP 통합
└── tests/                # 테스트
```

#### 2.4.2 거대 파일 분리 우선순위

| 파일 | 현재 크기 | 분리 방안 |
|------|----------|----------|
| `auxiliary_client.py` | 117KB | 백엔드별 파일로 분리 (8개) |
| `credential_pool.py` | 59KB | CredentialEntry + Pool 로직 분리 |
| `cli.py` | 11,000줄 | 이미 51개 하위 모듈로 분산됨, 추가 정리 |

---

### 2.5 LSP/코드 인텔리전스 통합

#### 2.5.1 AST 기반 코드 편집 (P1, 난이도: 중간)

```python
import ast

class ASTAwareEditor:
    def find_function(self, source: str, func_name: str):
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                return ast.get_source_segment(source, node)
        return None
```

#### 2.5.2 pygls 기반 LSP 클라이언트 (P1, 난이도: 높음)

```python
from pygls.client import JsonRPCClient
from lsprotocol.types import DefinitionParams, TextDocumentIdentifier, Position

class AgentLSPClient:
    async def go_to_definition(self, file_path: str, line: int, col: int):
        params = DefinitionParams(
            text_document=TextDocumentIdentifier(uri=file_path),
            position=Position(line=line, character=col)
        )
        return await self.client.text_document_definition(params)
```

#### 2.5.3 Claude Code의 9종 LSP 조작 참고

| 조작 | 설명 |
|------|------|
| `goToDefinition` | 심볼 정의로 이동 |
| `findReferences` | 모든 사용처 찾기 |
| `hover` | 타입 정보 + 문서 |
| `documentSymbol` | 파일 내 모든 심볼 |
| `workspaceSymbol` | 이름으로 심볼 검색 |
| `goToImplementation` | 인터페이스 구현 찾기 |
| `incomingCalls` | 호출자 계층 |
| `outgoingCalls` | 피호출자 계층 |

**출처:** [Claude Code 설계와 구현 - Juejin](https://juejin.cn/post/7628492756223688767)

---

## 3. 종합 로드맵

### Phase 1: 긴급 수정 (1주)

| 순위 | 작업 | 난이도 | 소요시간 | 효과 |
|------|------|--------|----------|------|
| 1 | deque 슬라이딩 윈도우 메모리 | 매우 낮음 | 1시간 | OOM 방지 |
| 2 | weakref 순환 참조 제거 | 낮음 | 반나절 | 근본적 누수 해결 |
| 3 | tracemalloc 모니터링 추가 | 낮음 | 1시간 | 누수 진단 |
| 4 | 실패/성공 경험 분리 저장 | 낮음 | 반나절 | 잘못된 경험 고착화 방지 |
| 5 | 도구 정의 description 압축 | 매우 낮음 | 수시간 | 즉각적 토큰 절감 |

### Phase 2: 구조 개선 (2-4주)

| 순위 | 작업 | 난이도 | 소요시간 | 효과 |
|------|------|--------|----------|------|
| 6 | 디렉토리 구조 재설계 | 낮음 | 반나절 | 모듈화 기반 |
| 7 | 도메인별 모듈 분리 | 중간 | 3-5일 | 유지보수성 |
| 8 | defer_loading 동적 도구 로드 | 중간 | 3-5일 | 85% 토큰 절감 |
| 9 | 합의 기반 경험 검증 | 중간 | 2-3일 | 자가학습 품질 |
| 10 | AST 기반 코드 편집 | 중간 | 3-5일 | 의미적 편집 |

### Phase 3: 고급 기능 (1-3개월)

| 순위 | 작업 | 난이도 | 소요시간 | 효과 |
|------|------|--------|----------|------|
| 11 | pygls LSP 클라이언트 | 높음 | 1-2주 | 코드 인텔리전스 |
| 12 | 컨텍스트 압축 (Acon 스타일) | 중간-높음 | 1주 | 26-54% 메모리 감소 |
| 13 | Programmatic Tool Calling | 높음 | 1-2주 | 37% 토큰 감소 |
| 14 | 플러그인 시스템 | 높음 | 1-2주 | 확장성 |
| 15 | 진단 피드백 루프 | 중간 | 3-5일 | 코드 품질 자동 개선 |

---

## 4. 참고 자료

### GitHub Issues & PRs
- [Issue #18438 - Gateway 메모리 누수](https://github.com/NousResearch/hermes-agent/issues/18438)
- [Issue #16803 - CLI 무응답](https://github.com/NousResearch/hermes-agent/issues/16803)
- [Issue #509 - 모순 감지 불가](https://github.com/NousResearch/hermes-agent/issues/509)
- [Issue #344 - 멀티 에이전트 한계](https://github.com/NousResearch/hermes-agent/issues/344)
- [Issue #502 - 프로젝트 컨텍스트 부재](https://github.com/NousResearch/hermes-agent/issues/502)
- [Issue #527 - 권한 시스템 한계](https://github.com/NousResearch/hermes-agent/issues/527)
- [Issue #532 - 파일 업로드 부재](https://github.com/NousResearch/hermes-agent/issues/532)
- [Issue #12905 - OAuth 버그](https://github.com/NousResearch/hermes-agent/issues/12905)
- [Issue #17154 - 아키텍처 감사](https://github.com/NousResearch/hermes-agent/issues/17154)
- [PR #13231 - TUI 메모리 누수 수정](https://github.com/NousResearch/hermes-agent/pull/13231)
- [PR #19000 - MCP 서브프로세스 누수](https://github.com/NousResearch/hermes-agent/pull/19000)

### 기술 평가 & 커뮤니티 분석
- [Michael O'Boyle 기술 평가](https://gist.github.com/michaeloboyle/10461598db36066e4c366413d5416f83)
- [CNBoys 분석](https://www.cnblogs.com/haibindev/Undeclared/19881562)
- [AboutCoreLab 심층분석](https://github.com/aboutcorelab/sensing/wiki/20260414-AI-%EC%8B%AC%EC%B8%B5%EB%B6%84%EC%84%9D-hermes-agent-%EC%8B%AC%EC%B8%B5%EB%B6%84%EC%84%9D)
- [CSDN 코드 분석](https://blog.csdn.net/zhonglinzhang/article/details/160300106)
- [CSDN 실사용 후기](https://lvyuanj.blog.csdn.net/article/details/160060138)
- [aiskill.market 비교](https://aiskill.market/blog/47-hermes-tools-claude-code-doesnt-have)

### 학술 논문 & 기술 블로그
- [A-MemGuard (ICLR 2026)](https://openreview.net/forum?id=fVxfCEv8xG) - 자가학습 검증
- [VeriGuard (arXiv)](https://arxiv.org/html/2510.05156v1) - 형식 검증 기반 안전장치
- [Acon (Microsoft)](https://github.com/microsoft/acon) - 에이전트 컨텍스트 압축
- [Anthropic Advanced Tool Use](https://www.anthropic.com/engineering/advanced-tool-use) - 토큰 최적화
- [Claude Code LSP 아키텍처](https://juejin.cn/post/7628492756223688767) - LSP 통합 참고
- [Tencent Cloud Agent 메모리 해결](https://cloud.tencent.cn/developer/article/2603176) - 실전 사례

---

*이 보고서는 Hermes Agent의 기술적 부족점을 객관적으로 분석하고, 구체적인 개선 방법을 제안합니다. 모든 출처는 실제 GitHub Issues, PR, 기술 블로그, 학술 논문에서 확인 가능합니다.*
