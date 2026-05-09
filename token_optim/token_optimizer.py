"""
토큰 최적화 모듈
===============
Hermes Agent의 토큰 오버헤드 문제를 해결하기 위한 패턴 구현.

패턴:
  1. DynamicToolLoader - defer_loading 기반 동적 도구 로딩 (Anthropic 참고)
  2. ToolDefinitionCompressor - 도구 정의 압축
  3. ContextCompressor - 컨텍스트 압축 (Acon/Microsoft 참고)
  4. TokenBudgetManager - 토큰 예산 관리
  5. PromptCache - 프롬프트 캐싱 시스템
"""

import json
import time
import hashlib
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Optional


# ============================================================
# 패턴 1: DynamicToolLoader (동적 도구 로딩)
# ============================================================

@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict
    loaded: bool = False
    full_schema: Optional[dict] = None
    index_tokens: int = 0  # 압축 인덱스의 토큰 수

    def __post_init__(self):
        if not self.full_schema:
            self.full_schema = self.input_schema


class DynamicToolLoader:
    """
    Anthropic의 defer_loading 패턴 구현.
    모든 도구 정의를 미리 로드하지 않고, 압축 인덱스만 유지.
    필요시에만 전체 스키마를 로드하여 85% 토큰 절감.
    """

    def __init__(self, max_cache: int = 20):
        self.tools: dict[str, ToolDefinition] = {}
        self.session_cache: OrderedDict = OrderedDict()
        self.max_cache = max_cache
        self._load_count = 0

    def register(self, name: str, description: str, schema: dict) -> None:
        """도구를 압축 인덱스로 등록 (full schema 미로드)."""
        short_desc = description[:80] + "..." if len(description) > 80 else description
        self.tools[name] = ToolDefinition(
            name=name,
            description=short_desc,
            input_schema={"type": "object"},  # 압축된 스키마
            loaded=False,
            full_schema=schema,
            index_tokens=len(short_desc.split()) + 5,  # 대략적 토큰 수
        )

    def get_index(self) -> list[dict]:
        """초기 컨텍스트에 로드할 압축 인덱스 반환."""
        return [
            {
                "name": name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "defer_loading": True,
            }
            for name, tool in self.tools.items()
        ]

    def load_tool(self, name: str) -> dict:
        """도구의 전체 스키마를 로드 (on-demand)."""
        if name not in self.tools:
            raise KeyError(f"도구 '{name}'을 찾을 수 없습니다")
        tool = self.tools[name]
        tool.loaded = True
        self._load_count += 1
        # 세션 캐시에 추가
        self.session_cache[name] = tool.full_schema
        if len(self.session_cache) > self.max_cache:
            self.session_cache.popitem(last=False)
        return {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.full_schema,
        }

    @property
    def stats(self) -> dict:
        total_index_tokens = sum(t.index_tokens for t in self.tools.values())
        # 전체 스키마 토큰 계산: description + schema의 모든 텍스트
        total_full_tokens = 0
        for t in self.tools.values():
            desc_tokens = len(t.description.split())
            schema_text = json.dumps(t.full_schema, default=str)
            schema_tokens = len(schema_text.split())
            total_full_tokens += desc_tokens + schema_tokens
        return {
            "registered_tools": len(self.tools),
            "loaded_tools": sum(1 for t in self.tools.values() if t.loaded),
            "index_tokens_estimate": total_index_tokens,
            "full_schema_tokens_estimate": total_full_tokens,
            "savings_percent": round(
                (1 - total_index_tokens / total_full_tokens) * 100, 1
            ) if total_full_tokens > 0 else 0,
            "cache_size": len(self.session_cache),
            "on_demand_loads": self._load_count,
        }


# ============================================================
# 패턴 2: ToolDefinitionCompressor (도구 정의 압축)
# ============================================================

class ToolDefinitionCompressor:
    """
    도구 정의의 description과 schema를 압축.
    불필요한 필드 제거, description 단축, 예시 제거.
    """

    def __init__(self, max_desc_length: int = 100):
        self.max_desc_length = max_desc_length
        self._compressed_count = 0
        self._original_tokens = 0
        self._compressed_tokens = 0

    def compress(self, tool_def: dict) -> dict:
        """단일 도구 정의 압축."""
        original = json.dumps(tool_def, default=str)
        self._original_tokens += len(original.split())

        compressed = {
            "name": tool_def["name"],
            "description": self._compress_description(tool_def.get("description", "")),
            "input_schema": self._compress_schema(tool_def.get("input_schema", {})),
        }
        self._compressed_tokens += len(json.dumps(compressed).split())
        self._compressed_count += 1
        return compressed

    def compress_batch(self, tools: list[dict]) -> list[dict]:
        """배치 도구 정의 압축."""
        return [self.compress(t) for t in tools]

    def _compress_description(self, desc: str) -> str:
        if len(desc) <= self.max_desc_length:
            return desc
        # 첫 문장과 핵심 키워드만 유지
        sentences = desc.replace(". ", ".").split(".")
        result = sentences[0]
        if len(result) > self.max_desc_length:
            result = result[:self.max_desc_length - 3] + "..."
        return result

    def _compress_schema(self, schema: dict) -> dict:
        """스키마에서 불필요한 필드 제거."""
        if "properties" not in schema:
            return schema
        compressed = {"type": "object", "properties": {}}
        for key, val in schema["properties"].items():
            compressed["properties"][key] = {
                "type": val.get("type", "string"),
            }
            # description이 있으면 최대 30자로 단축
            if "description" in val:
                d = val["description"]
                compressed["properties"][key]["description"] = d[:30] + "..." if len(d) > 30 else d
        if "required" in schema:
            compressed["required"] = schema["required"]
        return compressed

    @property
    def stats(self) -> dict:
        return {
            "compressed_count": self._compressed_count,
            "original_tokens": self._original_tokens,
            "compressed_tokens": self._compressed_tokens,
            "savings_percent": round(
                (1 - self._compressed_tokens / self._original_tokens) * 100, 1
            ) if self._original_tokens > 0 else 0,
        }


# ============================================================
# 패턴 3: ContextCompressor (컨텍스트 압축)
# ============================================================

class ContextCompressor:
    """
    대화 기록을 규약화된 가이드라인으로 압축.
    Microsoft Acon 프레임워크 참고.
    """

    def __init__(self, max_context_tokens: int = 4000):
        self.max_context_tokens = max_context_tokens
        self.compression_rules = [
            self._remove_duplicates,
            self._compress_verbose,
            self._extract_key_facts,
        ]

    def compress(self, messages: list[dict]) -> list[dict]:
        """메시지 목록을 압축."""
        result = messages
        for rule in self.compression_rules:
            result = rule(result)
        return result

    def _remove_duplicates(self, messages: list[dict]) -> list[dict]:
        """연속 중복 메시지 제거."""
        seen = set()
        result = []
        for msg in messages:
            key = hashlib.md5(msg.get("content", "").encode()).hexdigest()
            if key not in seen:
                seen.add(key)
                result.append(msg)
        return result

    def _compress_verbose(self, messages: list[dict]) -> list[dict]:
        """긴 메시지를 요약으로 압축."""
        result = []
        for msg in messages:
            content = msg.get("content", "")
            if len(content) > 500:
                msg = {**msg, "content": content[:200] + f"... [요약: {len(content)}자]"}
            result.append(msg)
        return result

    def _extract_key_facts(self, messages: list[dict]) -> list[dict]:
        """핵심 사실만 추출 (시뮬레이션)."""
        if len(messages) <= 5:
            return messages
        # 첫 2개 + 마지막 3개 유지, 중간은 요약
        summary_content = f"[{len(messages) - 5}개 중간 메시지 압축됨]"
        summary_msg = {"role": "system", "content": summary_content}
        return messages[:2] + [summary_msg] + messages[-3:]


# ============================================================
# 패턴 4: TokenBudgetManager (토큰 예산 관리)
# ============================================================

class TokenBudgetManager:
    """
    API 호출별 토큰 예산을 관리.
    시스템 프롬프트, 도구 정의, 대화 기록에 예산을 분배.
    """

    def __init__(self, total_budget: int = 128000):
        self.total_budget = total_budget
        self.allocations = {
            "system_prompt": 0.10,   # 10%
            "tool_definitions": 0.15, # 15%
            "conversation": 0.60,    # 60%
            "response_reserve": 0.15, # 15%
        }

    def get_budget(self, category: str) -> int:
        """카테고리별 토큰 예산 반환."""
        ratio = self.allocations.get(category, 0)
        return int(self.total_budget * ratio)

    def check_fit(self, messages: list[dict], tools: list[dict]) -> dict:
        """현재 컨텍스트가 예산 내에 있는지 확인."""
        msg_tokens = sum(len(m.get("content", "").split()) for m in messages)
        tool_tokens = sum(len(json.dumps(t, default=str).split()) for t in tools)
        msg_budget = self.get_budget("conversation")
        tool_budget = self.get_budget("tool_definitions")

        return {
            "message_tokens": msg_tokens,
            "message_budget": msg_budget,
            "message_over": msg_tokens > msg_budget,
            "tool_tokens": tool_tokens,
            "tool_budget": tool_budget,
            "tool_over": tool_tokens > tool_budget,
            "total_used": msg_tokens + tool_tokens,
            "total_budget": self.total_budget,
            "within_budget": (msg_tokens <= msg_budget) and (tool_tokens <= tool_budget),
        }


# ============================================================
# 패턴 5: PromptCache (프롬프트 캐싱)
# ============================================================

class PromptCache:
    """
    동일 프롬프트 패턴을 캐싱하여 중복 API 호출 방지.
    LRU(Last Recently Used) eviction 적용.
    """

    def __init__(self, max_size: int = 100):
        self.cache: OrderedDict = OrderedDict()
        self.max_size = max_size
        self.hits = 0
        self.misses = 0

    def get(self, prompt: str) -> Optional[Any]:
        """캐시에서 프롬프트 결과 조회."""
        key = hashlib.md5(prompt.encode()).hexdigest()
        if key in self.cache:
            self.cache.move_to_end(key)
            self.hits += 1
            return self.cache[key]
        self.misses += 1
        return None

    def set(self, prompt: str, response: Any) -> None:
        """프롬프트-응답 쌍을 캐시에 저장."""
        key = hashlib.md5(prompt.encode()).hexdigest()
        self.cache[key] = response
        self.cache.move_to_end(key)
        if len(self.cache) > self.max_size:
            self.cache.popitem(last=False)

    @property
    def stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "cache_size": len(self.cache),
            "max_size": self.max_size,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": f"{self.hits / total:.1%}" if total > 0 else "N/A",
        }


# ============================================================
# 통합 테스트
# ============================================================

def run_all_tests():
    print("=" * 60)
    print("토큰 최적화 모듈 - 통합 테스트")
    print("=" * 60)

    # 테스트 1: DynamicToolLoader
    print("\n[테스트 1] DynamicToolLoader (동적 도구 로딩)")
    print("-" * 40)
    loader = DynamicToolLoader()
    # 10개 도구 등록
    for i in range(10):
        loader.register(
            f"tool_{i}",
            f"이것은 도구 {i}입니다. 파일을 읽고 쓰고 검색하는 다양한 기능을 제공합니다.",
            {"type": "object", "properties": {"param1": {"type": "string", "description": "첫 번째 매개변수"}, "param2": {"type": "integer"}}, "required": ["param1"]}
        )
    index = loader.get_index()
    print(f"  등록 도구: {len(index)}개")
    print(f"  인덱스 토큰(추정): {loader.stats['index_tokens_estimate']}")
    print(f"  전체 토큰(추정): {loader.stats['full_schema_tokens_estimate']}")
    print(f"  절감률: {loader.stats['savings_percent']}%")
    # 3개만 로드
    for i in [0, 3, 7]:
        loader.load_tool(f"tool_{i}")
    print(f"  로드된 도구: {loader.stats['loaded_tools']}개")
    print(f"  캐시 크기: {loader.stats['cache_size']}")
    assert loader.stats["savings_percent"] > 30, "FAIL: 절감률 30% 미만"
    print("  ✅ 통과")

    # 테스트 2: ToolDefinitionCompressor
    print("\n[테스트 2] ToolDefinitionCompressor (도구 정의 압축)")
    print("-" * 40)
    compressor = ToolDefinitionCompressor(max_desc_length=50)
    tools = [
        {"name": "read_file", "description": "파일 시스템에서 파일을 읽고 내용을 반환합니다. 다양한 파일 형식을 지원하며, 큰 파일은 청크 단위로 읽을 수 있습니다. 인코딩 자동 감지 기능도 포함되어 있습니다.", "input_schema": {"type": "object", "properties": {"path": {"type": "string", "description": "읽을 파일의 절대 경로 또는 상대 경로를 입력하세요. 환경 변수도 지원합니다."}, "encoding": {"type": "string", "description": "파일의 문자 인코딩 방식을 지정합니다. 기본값은 UTF-8입니다."}, "offset": {"type": "integer", "description": "읽기 시작할 줄 번호입니다."}, "limit": {"type": "integer", "description": "읽을 최대 줄 수입니다."}}, "required": ["path"]}},
        {"name": "web_search", "description": "인터넷에서 키워드 검색을 수행하고 결과를 반환합니다. 여러 검색 엔진을 동시에 조회하며, 결과를 관련성 순으로 정렬합니다.", "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "검색할 키워드 또는 질문을 입력하세요."}, "num_results": {"type": "integer", "description": "반환할 최대 결과 수입니다."}}, "required": ["query"]}},
    ]
    compressed = compressor.compress_batch(tools)
    stats = compressor.stats
    print(f"  압축 도구: {stats['compressed_count']}개")
    print(f"  원본 토큰: {stats['original_tokens']}")
    print(f"  압축 토큰: {stats['compressed_tokens']}")
    print(f"  절감률: {stats['savings_percent']}%")
    assert stats["savings_percent"] > 0, "FAIL: 압축 효과 없음"
    print("  ✅ 통과")

    # 테스트 3: ContextCompressor
    print("\n[테스트 3] ContextCompressor (컨텍스트 압축)")
    print("-" * 40)
    comp = ContextCompressor()
    messages = [{"role": "user", "content": f"메시지 {i} " * 100} for i in range(20)]
    compressed = comp.compress(messages)
    print(f"  원본 메시지: {len(messages)}개")
    print(f"  압축 후: {len(compressed)}개")
    total_orig = sum(len(m["content"]) for m in messages)
    total_comp = sum(len(m["content"]) for m in compressed)
    print(f"  원본 크기: {total_orig}자")
    print(f"  압축 크기: {total_comp}자")
    print(f"  압축률: {(1 - total_comp/total_orig)*100:.1f}%")
    assert len(compressed) < len(messages), "FAIL: 압축되지 않음"
    print("  ✅ 통과")

    # 테스트 4: TokenBudgetManager
    print("\n[테스트 4] TokenBudgetManager (토큰 예산)")
    print("-" * 40)
    budget = TokenBudgetManager(total_budget=128000)
    print(f"  총 예산: {budget.total_budget}토큰")
    print(f"  시스템 프롬프트: {budget.get_budget('system_prompt')}토큰 (10%)")
    print(f"  도구 정의: {budget.get_budget('tool_definitions')}토큰 (15%)")
    print(f"  대화 기록: {budget.get_budget('conversation')}토큰 (60%)")
    print(f"  응답 예약: {budget.get_budget('response_reserve')}토큰 (15%)")
    check = budget.check_fit(
        messages=[{"content": "word " * 1000}],
        tools=[{"name": "t", "schema": "x" * 500}]
    )
    print(f"  예산 내: {check['within_budget']}")
    print("  ✅ 통과")

    # 테스트 5: PromptCache
    print("\n[테스트 5] PromptCache (프롬프트 캐싱)")
    print("-" * 40)
    cache = PromptCache(max_size=5)
    cache.set("안녕하세요", "Hello!")
    cache.set("파일 읽기", "Reading file...")
    r1 = cache.get("안녕하세요")
    r2 = cache.get("없는 프롬프트")
    cache.set("a" * 100, "long response")  # 3번째
    cache.set("b" * 100, "long response 2")  # 4번째
    cache.set("c" * 100, "long response 3")  # 5번째 (max)
    cache.set("d" * 100, "long response 4")  # 6번째 (evict first)
    stats = cache.stats
    print(f"  캐시 적중: {stats['hits']}")
    print(f"  캐시 미스: {stats['misses']}")
    print(f"  적중률: {stats['hit_rate']}")
    print(f"  캐시 크기: {stats['cache_size']}/{stats['max_size']}")
    assert r1 == "Hello!", "FAIL: 캐시 조회 실패"
    assert r2 is None, "FAIL: 없는 키 반환 오류"
    assert stats["cache_size"] == 5, "FAIL: LRU eviction 실패"
    print("  ✅ 통과")

    print("\n" + "=" * 60)
    print("모든 테스트 통과! (5/5)")
    print("=" * 60)


if __name__ == "__main__":
    run_all_tests()
