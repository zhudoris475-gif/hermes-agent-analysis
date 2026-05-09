"""
메모리 누수 해결 모듈
==================
Hermes Agent의 메모리 누수 문제를 해결하기 위한 5가지 패턴 구현.

패턴:
  1. SlidingWindowMemory - deque 기반 O(1) 메모리 관리
  2. WeakRefAgent - weakref 기반 순환 참조 제거
  3. MemoryMonitor - tracemalloc 기반 실시간 누수 탐지
  4. MemoryGuardian - 백그라운드 GC 가디언 스레드
  5. LLMCompressedMemory - LLM 기반 대화 기록 압축
"""

import sys
import gc
import time
import weakref
import threading
import tracemalloc
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional


# ============================================================
# 패턴 1: SlidingWindowMemory (deque 기반)
# ============================================================

class SlidingWindowMemory:
    """
    deque(maxlen=N)을 사용한 O(1) 메모리 관리.
    최대 N개의 메시지만 유지하며, 초과 시 자동으로 오래된 항목 제거.
    Hermes의 무한 증가 대화 기록 문제를 해결.
    """

    def __init__(self, max_size: int = 50):
        self.max_size = max_size
        self.history: deque = deque(maxlen=max_size)
        self._added_count = 0
        self._evicted_count = 0

    def add(self, message: dict) -> None:
        """메시지 추가. maxlen 초과 시 자동으로 가장 오래된 항목 제거."""
        old_len = len(self.history)
        self.history.append(message)
        self._added_count += 1
        if len(self.history) == old_len and old_len == self.max_size:
            self._evicted_count += 1

    def get_recent(self, n: int = 10) -> list:
        """최근 N개 메시지 반환."""
        return list(self.history)[-n:]

    def get_all(self) -> list:
        """전체 기록 반환."""
        return list(self.history)

    @property
    def stats(self) -> dict:
        return {
            "current_size": len(self.history),
            "max_size": self.max_size,
            "total_added": self._added_count,
            "total_evicted": self._evicted_count,
            "memory_bounded": len(self.history) <= self.max_size,
        }


# ============================================================
# 패턴 2: WeakRefAgent (순환 참조 제거)
# ============================================================

class Agent:
    """에이전트 본체. Tool이 자신을 참조하더라도 weakref로 순환 참조 방지."""

    def __init__(self, name: str):
        self.name = name
        self._tools: list = []
        self._tool_refs: list = []  # weakref 저장소

    def register_tool(self, tool: "Tool") -> None:
        """도구 등록. 에이전트 참조를 weakref로만 유지."""
        tool.bind_agent(self)
        self._tools.append(tool)
        self._tool_refs.append(weakref.ref(tool))

    def get_alive_tools(self) -> list:
        """여전히 존재하는 도구만 반환."""
        alive = []
        for ref in self._tool_refs:
            tool = ref()
            if tool is not None:
                alive.append(tool)
        return alive

    def do_something(self) -> str:
        return f"[{self.name}] 작업 실행 완료"


class Tool:
    """
    에이전트의 도구. 에이전트 참조를 weakref로 유지하여
    Agent -> Tool -> Agent 순환 참조를 끊음.
    """

    def __init__(self, name: str):
        self.name = name
        self._agent_ref: Optional[weakref.ref] = None

    def bind_agent(self, agent: Agent) -> None:
        """강참조 대신 약참조로 에이전트를 저장."""
        self._agent_ref = weakref.ref(agent)

    def run(self) -> str:
        """도구 실행. 에이전트가 여전히 존재하면 참조."""
        agent = self._agent_ref() if self._agent_ref else None
        if agent is not None:
            return f"[{self.name}] -> {agent.do_something()}"
        return f"[{self.name}] 에이전트 참조 불가 (GC 대상)"

    @property
    def agent_alive(self) -> bool:
        """에이전트가 여전히 살아있는지 확인."""
        return self._agent_ref() is not None


# ============================================================
# 패턴 3: MemoryMonitor (tracemalloc 기반)
# ============================================================

class MemoryMonitor:
    """
    tracemalloc을 사용한 실시간 메모리 누수 탐지.
    스냅샷 비교로 어느 코드 라인에서 메모리가 누수되는지 식별.
    """

    def __init__(self, stack_depth: int = 25):
        self.stack_depth = stack_depth
        self._snapshots: list = []
        self._enabled = False

    def start(self) -> None:
        tracemalloc.start(self.stack_depth)
        self._enabled = True

    def stop(self) -> None:
        tracemalloc.stop()
        self._enabled = False

    def take_snapshot(self) -> None:
        if not self._enabled:
            raise RuntimeError("MemoryMonitor이 시작되지 않았습니다")
        self._snapshots.append(tracemalloc.take_snapshot())

    def compare_last_two(self, limit: int = 10) -> list:
        """마지막 두 스냅샷을 비교하여 메모리 증가 상위 항목 반환."""
        if len(self._snapshots) < 2:
            raise ValueError("비교할 스냅샷이 2개 이상 필요합니다")
        snap1, snap2 = self._snapshots[-2], self._snapshots[-1]
        stats = snap2.compare_to(snap1, "lineno")
        results = []
        for stat in stats[:limit]:
            results.append({
                "file": stat.traceback.format()[0] if stat.traceback else "unknown",
                "size_diff": stat.size_diff,
                "count_diff": stat.count_diff,
            })
        return results

    def current_usage(self) -> dict:
        """현재 메모리 사용량."""
        if not self._enabled:
            return {"allocated": 0, "fragmentation": 0}
        current, peak = tracemalloc.get_traced_memory()
        return {
            "allocated_mb": round(current / 1024 / 1024, 2),
            "peak_mb": round(peak / 1024 / 1024, 2),
        }

    @property
    def snapshot_count(self) -> int:
        return len(self._snapshots)


# ============================================================
# 패턴 4: MemoryGuardian (백그라운드 GC)
# ============================================================

class MemoryGuardian(threading.Thread):
    """
    백그라운드에서 주기적으로 gc.collect()를 실행하는 가디언 스레드.
    daemon=True로 설정하여 메인 스레드 종료 시 자동 종료.
    """

    def __init__(self, interval_seconds: float = 10.0):
        super().__init__(daemon=True)
        self.interval = interval_seconds
        self._running = False
        self._gc_count = 0
        self._freed_bytes = 0

    def run(self) -> None:
        self._running = True
        while self._running:
            before = gc.collect()
            freed = gc.collect() - before if before else 0
            self._gc_count += 1
            self._freed_bytes += freed
            time.sleep(self.interval)

    def stop(self) -> None:
        self._running = False

    @property
    def stats(self) -> dict:
        return {
            "gc_runs": self._gc_count,
            "running": self._running,
            "interval_seconds": self.interval,
        }


# ============================================================
# 패턴 5: LLMCompressedMemory (대화 기록 압축)
# ============================================================

class LLMCompressedMemory:
    """
    대화 기록이 임계치를 초과하면 요약으로 압축.
    실제 LLM 호출 대신 시뮬레이션된 요약 함수 사용.
    """

    def __init__(self, max_messages: int = 50, compress_threshold: int = 40):
        self.max_messages = max_messages
        self.compress_threshold = compress_threshold
        self.messages: list = []
        self.summaries: list = []
        self._compress_count = 0

    def add(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})
        if len(self.messages) >= self.compress_threshold:
            self._compress()

    def _compress(self) -> None:
        """메시지를 요약으로 압축 (시뮬레이션)."""
        old_count = len(self.messages)
        summary = self._simulate_summary(self.messages)
        self.summaries.append(summary)
        self.messages = self.messages[-10:]  # 최근 10개만 유지
        self._compress_count += 1

    def _simulate_summary(self, messages: list) -> dict:
        """실제 환경에서는 LLM API 호출로 대체."""
        return {
            "type": "summary",
            "message_count": len(messages),
            "compressed_at": time.time(),
            "content": f"[요약: {len(messages)}개 메시지 압축됨]",
        }

    def get_context(self) -> list:
        """요약 + 최근 메시지를 결합한 컨텍스트 반환."""
        return self.summaries + self.messages

    @property
    def stats(self) -> dict:
        return {
            "current_messages": len(self.messages),
            "summaries": len(self.summaries),
            "compress_count": self._compress_count,
            "total_context_size": len(self.get_context()),
        }


# ============================================================
# 통합 테스트
# ============================================================

def run_all_tests():
    """모든 메모리 패턴을 순차적으로 테스트."""
    print("=" * 60)
    print("메모리 누수 해결 모듈 - 통합 테스트")
    print("=" * 60)

    # 테스트 1: SlidingWindowMemory
    print("\n[테스트 1] SlidingWindowMemory")
    print("-" * 40)
    mem = SlidingWindowMemory(max_size=5)
    for i in range(10):
        mem.add({"role": "user", "content": f"메시지 {i}"})
    stats = mem.stats
    print(f"  현재 크기: {stats['current_size']}/5 (바운드 됨: {stats['memory_bounded']})")
    print(f"  추가: {stats['total_added']}, 제거: {stats['total_evicted']}")
    print(f"  최근 3개: {[m['content'] for m in mem.get_recent(3)]}")
    assert stats["current_size"] == 5, "FAIL: 크기가 max_size를 초과"
    assert stats["memory_bounded"] is True, "FAIL: 메모리 바운드 실패"
    print("  ✅ 통과")

    # 테스트 2: WeakRefAgent
    print("\n[테스트 2] WeakRefAgent (순환 참조 제거)")
    print("-" * 40)
    agent = Agent("Hermes")
    tool1 = Tool("FileRead")
    tool2 = Tool("WebSearch")
    agent.register_tool(tool1)
    agent.register_tool(tool2)
    print(f"  에이전트: {agent.name}")
    print(f"  도구 실행: {tool1.run()}")
    print(f"  도구 실행: {tool2.run()}")
    print(f"  에이전트 참조存活: {tool1.agent_alive}")
    assert tool1.agent_alive is True, "FAIL: 에이전트 참조 불가"
    print("  ✅ 통과")

    # 테스트 3: MemoryMonitor
    print("\n[테스트 3] MemoryMonitor (tracemalloc)")
    print("-" * 40)
    monitor = MemoryMonitor()
    monitor.start()
    monitor.take_snapshot()
    # 의도적으로 메모리 할당
    big_list = [str(i) * 1000 for i in range(1000)]
    monitor.take_snapshot()
    usage = monitor.current_usage()
    print(f"  현재 사용량: {usage['allocated_mb']}MB / 피크: {usage['peak_mb']}MB")
    print(f"  스냅샷 수: {monitor.snapshot_count}")
    diff = monitor.compare_last_two(limit=3)
    if diff:
        print(f"  메모리 증가 상위: {diff[0]['file']} (+{diff[0]['size_diff']} bytes)")
    monitor.stop()
    print("  ✅ 통과")

    # 테스트 4: MemoryGuardian
    print("\n[테스트 4] MemoryGuardian (백그라운드 GC)")
    print("-" * 40)
    guardian = MemoryGuardian(interval_seconds=0.5)
    guardian.start()
    time.sleep(1.5)  # 약 3회 GC 실행 대기
    guardian.stop()
    stats = guardian.stats
    print(f"  GC 실행 횟수: {stats['gc_runs']}")
    print(f"  실행 중: {stats['running']}")
    assert stats["gc_runs"] >= 1, "FAIL: GC가 실행되지 않음"
    print("  ✅ 통과")

    # 테스트 5: LLMCompressedMemory
    print("\n[테스트 5] LLMCompressedMemory (대화 압축)")
    print("-" * 40)
    comp = LLMCompressedMemory(max_messages=50, compress_threshold=10)
    for i in range(25):
        comp.add("user", f"질문 {i}")
        comp.add("assistant", f"답변 {i}")
    stats = comp.stats
    print(f"  현재 메시지: {stats['current_messages']}")
    print(f"  요약 수: {stats['summaries']}")
    print(f"  압축 횟수: {stats['compress_count']}")
    print(f"  총 컨텍스트 크기: {stats['total_context_size']}")
    assert stats["compress_count"] >= 2, "FAIL: 압축이 실행되지 않음"
    assert stats["current_messages"] <= 10, "FAIL: 압축 후 메시지가 임계치 초과"
    print("  ✅ 통과")

    print("\n" + "=" * 60)
    print("모든 테스트 통과! (5/5)")
    print("=" * 60)


if __name__ == "__main__":
    run_all_tests()
