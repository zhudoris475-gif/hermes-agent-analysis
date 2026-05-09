"""
아키텍처 리팩토링 모듈
====================
Hermes Agent의 모놀리식 구조를 모듈식 아키텍처로 리팩토링.

구현:
  1. ModuleRegistry - 플러그인 기반 모듈 등록/발견 시스템
  2. EventBus - 모듈 간 느슨한 결합을 위한 이벤트 버스
  3. ConfigManager - 프로젝트별 설정 관리 (.hermes.md 지원)
  4. ToolRegistry - 도구 등록/검색/의존성 관리
  5. ServiceLocator - 의존성 주입 컨테이너
"""

import json
import os
import time
import importlib
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


# ============================================================
# 패턴 1: ModuleRegistry (플러그인 모듈 시스템)
# ============================================================

@dataclass
class ModuleInfo:
    name: str
    version: str
    description: str
    dependencies: list[str] = field(default_factory=list)
    enabled: bool = True


class ModuleRegistry:
    """
    플러그인 기반 모듈 등록 시스템.
    각 기능을 독립 모듈로 등록하고, 의존성을 자동 해결.
    Hermes의 모놀리식 구조를 모듈식으로 분리.
    """

    def __init__(self):
        self._modules: dict[str, ModuleInfo] = {}
        self._handlers: dict[str, Callable] = {}

    def register(self, module: ModuleInfo) -> None:
        """모듈 등록."""
        self._modules[module.name] = module

    def unregister(self, name: str) -> None:
        """모듈 제거."""
        if name in self._modules:
            del self._modules[name]

    def get(self, name: str) -> Optional[ModuleInfo]:
        return self._modules.get(name)

    def resolve_dependencies(self, name: str) -> list[str]:
        """모듈의 의존성 트리를 재귀적으로 해결."""
        resolved = []
        visited = set()

        def _resolve(mod_name: str):
            if mod_name in visited:
                return
            visited.add(mod_name)
            mod = self._modules.get(mod_name)
            if mod:
                for dep in mod.dependencies:
                    _resolve(dep)
                resolved.append(mod_name)

        _resolve(name)
        return resolved

    def list_modules(self) -> list[dict]:
        return [
            {"name": m.name, "version": m.version, "enabled": m.enabled,
             "deps": m.dependencies}
            for m in self._modules.values()
        ]

    @property
    def stats(self) -> dict:
        return {
            "total_modules": len(self._modules),
            "enabled": sum(1 for m in self._modules.values() if m.enabled),
            "disabled": sum(1 for m in self._modules.values() if not m.enabled),
        }


# ============================================================
# 패턴 2: EventBus (이벤트 버스)
# ============================================================

class EventBus:
    """
    모듈 간 느슨한 결합을 위한 발행-구독 이벤트 버스.
    모듈이 직접 참조하지 않고 이벤트로 통신.
    """

    def __init__(self):
        self._subscribers: dict[str, list[Callable]] = {}
        self._event_log: list[dict] = []

    def subscribe(self, event: str, handler: Callable) -> None:
        if event not in self._subscribers:
            self._subscribers[event] = []
        self._subscribers[event].append(handler)

    def unsubscribe(self, event: str, handler: Callable) -> None:
        if event in self._subscribers:
            self._subscribers[event] = [
                h for h in self._subscribers[event] if h != handler
            ]

    def publish(self, event: str, data: Any = None) -> int:
        handlers = self._subscribers.get(event, [])
        for handler in handlers:
            handler(data)
        self._event_log.append({
            "event": event, "data": str(data)[:50],
            "handlers_called": len(handlers), "timestamp": time.time()
        })
        return len(handlers)

    def get_event_log(self, event: Optional[str] = None) -> list[dict]:
        if event:
            return [e for e in self._event_log if e["event"] == event]
        return self._event_log

    @property
    def stats(self) -> dict:
        return {
            "subscribed_events": len(self._subscribers),
            "total_handlers": sum(len(h) for h in self._subscribers.values()),
            "total_events_published": len(self._event_log),
        }


# ============================================================
# 패턴 3: ConfigManager (프로젝트별 설정)
# ============================================================

class ConfigManager:
    """
    글로벌 설정 + 프로젝트별 설정을 관리.
    Claude Code의 .claude/ 디렉토리 패턴 참고.
    """

    DEFAULT_GLOBAL = {
        "model": "gpt-4",
        "max_tokens": 4096,
        "temperature": 0.7,
        "tools_enabled": True,
    }

    def __init__(self, global_path: Optional[str] = None):
        self.global_config = dict(self.DEFAULT_GLOBAL)
        self.project_configs: dict[str, dict] = {}
        if global_path and os.path.exists(global_path):
            self._load_global(global_path)

    def _load_global(self, path: str) -> None:
        with open(path) as f:
            data = json.load(f)
            self.global_config.update(data)

    def load_project(self, project_path: str, config: dict) -> None:
        """프로젝트별 설정 로드."""
        self.project_configs[project_path] = config

    def get(self, key: str, project_path: Optional[str] = None) -> Any:
        """설정값 조회. 프로젝트 설정이 글로벌을 오버라이드."""
        if project_path and project_path in self.project_configs:
            proj = self.project_configs[project_path]
            if key in proj:
                return proj[key]
        return self.global_config.get(key)

    def set(self, key: str, value: Any, project_path: Optional[str] = None) -> None:
        if project_path:
            if project_path not in self.project_configs:
                self.project_configs[project_path] = {}
            self.project_configs[project_path][key] = value
        else:
            self.global_config[key] = value

    @property
    def stats(self) -> dict:
        return {
            "global_keys": len(self.global_config),
            "projects": len(self.project_configs),
        }


# ============================================================
# 패턴 4: ToolRegistry (도구 등록/검색)
# ============================================================

@dataclass
class ToolInfo:
    name: str
    category: str
    handler: Callable
    description: str = ""
    tags: list[str] = field(default_factory=list)


class ToolRegistry:
    """
    도구를 카테고리별로 등록하고, 태그 기반 검색 지원.
    Hermes의 40+ 도구를 체계적으로 관리.
    """

    def __init__(self):
        self._tools: dict[str, ToolInfo] = {}

    def register(self, tool: ToolInfo) -> None:
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Optional[ToolInfo]:
        return self._tools.get(name)

    def search_by_category(self, category: str) -> list[ToolInfo]:
        return [t for t in self._tools.values() if t.category == category]

    def search_by_tag(self, tag: str) -> list[ToolInfo]:
        return [t for t in self._tools.values() if tag in t.tags]

    def list_all(self) -> list[dict]:
        return [
            {"name": t.name, "category": t.category, "tags": t.tags}
            for t in self._tools.values()
        ]

    def execute(self, name: str, **kwargs) -> Any:
        tool = self._tools.get(name)
        if not tool:
            raise KeyError(f"도구 '{name}'을 찾을 수 없습니다")
        return tool.handler(**kwargs)

    @property
    def stats(self) -> dict:
        categories = set(t.category for t in self._tools.values())
        return {
            "total_tools": len(self._tools),
            "categories": len(categories),
            "category_list": list(categories),
        }


# ============================================================
# 패턴 5: ServiceLocator (의존성 주입)
# ============================================================

class ServiceLocator:
    """
    싱글톤 서비스 컨테이너.
    모듈 간 직접 의존 대신 서비스 locator를 통한 간접 참조.
    """

    def __init__(self):
        self._services: dict[str, Any] = {}
        self._factories: dict[str, Callable] = {}

    def register(self, name: str, instance: Any) -> None:
        self._services[name] = instance

    def register_factory(self, name: str, factory: Callable) -> None:
        self._factories[name] = factory

    def get(self, name: str) -> Any:
        if name in self._services:
            return self._services[name]
        if name in self._factories:
            instance = self._factories[name]()
            self._services[name] = instance
            return instance
        raise KeyError(f"서비스 '{name}'을 찾을 수 없습니다")

    def has(self, name: str) -> bool:
        return name in self._services or name in self._factories

    def list_services(self) -> list[str]:
        return list(self._services.keys()) + list(self._factories.keys())

    @property
    def stats(self) -> dict:
        return {
            "instances": len(self._services),
            "factories": len(self._factories),
            "total": len(self._services) + len(self._factories),
        }


# ============================================================
# 통합 테스트
# ============================================================

def run_all_tests():
    print("=" * 60)
    print("아키텍처 리팩토링 모듈 - 통합 테스트")
    print("=" * 60)

    # 테스트 1: ModuleRegistry
    print("\n[테스트 1] ModuleRegistry (플러그인 모듈 시스템)")
    print("-" * 40)
    registry = ModuleRegistry()
    registry.register(ModuleInfo("core", "1.0.0", "핵심 에이전트 로직"))
    registry.register(ModuleInfo("memory", "1.0.0", "메모리 관리", ["core"]))
    registry.register(ModuleInfo("tools", "1.0.0", "도구 실행", ["core"]))
    registry.register(ModuleInfo("gateway", "1.0.0", "메시징 게이트웨이", ["core", "tools"]))
    deps = registry.resolve_dependencies("gateway")
    print(f"  gateway 의존성 트리: {deps}")
    assert "core" in deps, "FAIL: 의존성 해결 실패"
    assert "tools" in deps, "FAIL: 의존성 해결 실패"
    print(f"  모듈 목록: {registry.list_modules()}")
    print(f"  통계: {registry.stats}")
    print("  ✅ 통과")

    # 테스트 2: EventBus
    print("\n[테스트 2] EventBus (이벤트 버스)")
    print("-" * 40)
    bus = EventBus()
    received = []
    bus.subscribe("tool.executed", lambda d: received.append(d))
    bus.subscribe("tool.executed", lambda d: received.append(f"logged: {d}"))
    n = bus.publish("tool.executed", "read_file")
    print(f"  핸들러 호출 수: {n}")
    print(f"  수신 데이터: {received}")
    bus.publish("agent.started", "Hermes")
    log = bus.get_event_log()
    print(f"  이벤트 로그: {len(log)}건")
    print(f"  통계: {bus.stats}")
    assert n == 2, "FAIL: 핸들러 호출 수 불일치"
    assert len(received) == 2, "FAIL: 수신 데이터 불일치"
    print("  ✅ 통과")

    # 테스트 3: ConfigManager
    print("\n[테스트 3] ConfigManager (프로젝트별 설정)")
    print("-" * 40)
    config = ConfigManager()
    config.set("model", "claude-3-opus")
    config.load_project("/project-a", {"model": "gpt-4", "max_tokens": 8192})
    config.load_project("/project-b", {"temperature": 0.3})
    print(f"  글로벌 model: {config.get('model')}")
    print(f"  project-a model: {config.get('model', '/project-a')}")
    print(f"  project-b model: {config.get('model', '/project-b')}")
    print(f"  project-a max_tokens: {config.get('max_tokens', '/project-a')}")
    assert config.get("model") == "claude-3-opus", "FAIL: 글로벌 설정 오류"
    assert config.get("model", "/project-a") == "gpt-4", "FAIL: 프로젝트 오버라이드 실패"
    print(f"  통계: {config.stats}")
    print("  ✅ 통과")

    # 테스트 4: ToolRegistry
    print("\n[테스트 4] ToolRegistry (도구 등록/검색)")
    print("-" * 40)
    tools = ToolRegistry()
    tools.register(ToolInfo("read_file", "file", lambda path: f"content of {path}", tags=["fs", "read"]))
    tools.register(ToolInfo("write_file", "file", lambda path, content: "ok", tags=["fs", "write"]))
    tools.register(ToolInfo("web_search", "network", lambda q: f"results for {q}", tags=["web"]))
    tools.register(ToolInfo("exec_cmd", "system", lambda cmd: f"output of {cmd}", tags=["shell"]))
    file_tools = tools.search_by_category("file")
    fs_tools = tools.search_by_tag("fs")
    result = tools.execute("read_file", path="/test.txt")
    print(f"  파일 카테고리: {[t.name for t in file_tools]}")
    print(f"  fs 태그: {[t.name for t in fs_tools]}")
    print(f"  실행 결과: {result}")
    print(f"  통계: {tools.stats}")
    assert len(file_tools) == 2, "FAIL: 카테고리 검색 실패"
    assert len(fs_tools) == 2, "FAIL: 태그 검색 실패"
    print("  ✅ 통과")

    # 테스트 5: ServiceLocator
    print("\n[테스트 5] ServiceLocator (의존성 주입)")
    print("-" * 40)
    locator = ServiceLocator()
    locator.register("config", ConfigManager())
    locator.register_factory("event_bus", lambda: EventBus())
    config_svc = locator.get("config")
    bus_svc = locator.get("event_bus")
    bus_svc2 = locator.get("event_bus")  # 동일 인스턴스
    print(f"  서비스 목록: {locator.list_services()}")
    print(f"  config 타입: {type(config_svc).__name__}")
    print(f"  event_bus 싱글톤: {bus_svc is bus_svc2}")
    assert bus_svc is bus_svc2, "FAIL: 팩토리 싱글톤 실패"
    assert locator.has("config"), "FAIL: 서비스 존재 확인 실패"
    print(f"  통계: {locator.stats}")
    print("  ✅ 통과")

    print("\n" + "=" * 60)
    print("모든 테스트 통과! (5/5)")
    print("=" * 60)


if __name__ == "__main__":
    run_all_tests()
