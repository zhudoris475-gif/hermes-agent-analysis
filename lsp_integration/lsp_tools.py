"""
LSP 통합 모듈
=============
Hermes Agent에 코드 인텔리전스 기능을 추가.

구현:
  1. ASTEditor - AST 기반 의미적 코드 편집 (LSP 없이도 가능)
  2. SymbolExtractor - 파일에서 심볼(함수/클래스/변수) 추출
  3. ReferenceFinder - 심볼 참조(사용처) 찾기
  4. CodeNavigator - 파일 간 심볼 네비게이션
  5. DiagnosticCollector - 코드 품질 진단 수집
"""

import ast
import os
import re
from dataclasses import dataclass, field
from typing import Optional


# ============================================================
# 패턴 1: ASTEditor (AST 기반 코드 편집)
# ============================================================

class ASTEditor:
    """
    Python AST를 사용한 의미적 코드 편집.
    단순 문자열 치환이 아닌 구조적 편집으로 안전한 코드 수정.
    Claude Code의 AST 인식 패턴 참고.
    """

    def __init__(self, source: str):
        self.source = source
        self.tree = ast.parse(source)

    def find_function(self, name: str) -> Optional[dict]:
        """함수 정의 찾기."""
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return {
                    "name": node.name,
                    "line_start": node.lineno,
                    "line_end": node.end_lineno or node.lineno,
                    "args": [a.arg for a in node.args.args],
                    "source": ast.get_source_segment(self.source, node),
                }
        return None

    def find_class(self, name: str) -> Optional[dict]:
        """클래스 정의 찾기."""
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef) and node.name == name:
                methods = [
                    n.name for n in node.body if isinstance(n, ast.FunctionDef)
                ]
                return {
                    "name": node.name,
                    "line_start": node.lineno,
                    "line_end": node.end_lineno or node.lineno,
                    "methods": methods,
                    "bases": [self._get_name(b) for b in node.bases],
                    "source": ast.get_source_segment(self.source, node),
                }
        return None

    def find_variable(self, name: str) -> list[dict]:
        """변수 할당 찾기."""
        results = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == name:
                        results.append({
                            "name": name,
                            "line": node.lineno,
                            "source": ast.get_source_segment(self.source, node),
                        })
        return results

    def get_function_source(self, name: str) -> Optional[str]:
        func = self.find_function(name)
        return func["source"] if func else None

    def get_all_functions(self) -> list[dict]:
        """모든 함수 목록."""
        results = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef):
                results.append({
                    "name": node.name,
                    "line": node.lineno,
                    "args": [a.arg for a in node.args.args],
                })
        return results

    def get_all_classes(self) -> list[dict]:
        """모든 클래스 목록."""
        results = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef):
                results.append({
                    "name": node.name,
                    "line": node.lineno,
                    "methods": [n.name for n in node.body if isinstance(n, ast.FunctionDef)],
                })
        return results

    def get_imports(self) -> list[dict]:
        """모든 import 문."""
        results = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    results.append({"module": alias.name, "alias": alias.asname, "line": node.lineno})
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    results.append({"module": f"from {module}", "name": alias.name, "line": node.lineno})
        return results

    @staticmethod
    def _get_name(node) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return f"{ASTEditor._get_name(node.value)}.{node.attr}"
        return str(node)


# ============================================================
# 패턴 2: SymbolExtractor (심볼 추출)
# ============================================================

@dataclass
class Symbol:
    name: str
    kind: str  # function, class, variable, import
    line: int
    file_path: str
    detail: str = ""


class SymbolExtractor:
    """
    Python 파일에서 심볼을 추출하여 인덱스 생성.
    Claude Code의 documentSymbol 기능 참고.
    """

    def __init__(self):
        self._index: dict[str, list[Symbol]] = {}

    def index_file(self, file_path: str, source: str) -> list[Symbol]:
        """파일의 심볼을 추출하여 인덱스에 추가."""
        symbols = []
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return symbols

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                sym = Symbol(node.name, "function", node.lineno, file_path,
                             f"args: {[a.arg for a in node.args.args]}")
                symbols.append(sym)
            elif isinstance(node, ast.ClassDef):
                sym = Symbol(node.name, "class", node.lineno, file_path,
                             f"bases: {len(node.bases)}, methods: {sum(1 for n in node.body if isinstance(n, ast.FunctionDef))}")
                symbols.append(sym)

        if file_path not in self._index:
            self._index[file_path] = []
        self._index[file_path].extend(symbols)
        return symbols

    def search(self, name: str) -> list[Symbol]:
        """이름으로 심볼 검색."""
        results = []
        for file_syms in self._index.values():
            for sym in file_syms:
                if name.lower() in sym.name.lower():
                    results.append(sym)
        return results

    def get_file_symbols(self, file_path: str) -> list[Symbol]:
        return self._index.get(file_path, [])

    @property
    def stats(self) -> dict:
        total = sum(len(s) for s in self._index.values())
        return {
            "indexed_files": len(self._index),
            "total_symbols": total,
        }


# ============================================================
# 패턴 3: ReferenceFinder (참조 찾기)
# ============================================================

class ReferenceFinder:
    """
    심볼의 모든 참조(사용처)를 찾기.
    Claude Code의 findReferences 기능 참고.
    """

    def __init__(self):
        self._references: dict[str, list[dict]] = {}

    def find_references(self, name: str, source: str, file_path: str = "<unknown>") -> list[dict]:
        """소스 코드에서 이름의 모든 참조를 찾기."""
        refs = []
        lines = source.split("\n")
        for i, line in enumerate(lines, 1):
            # 간단한 패턴 매칭 (실제 LSP보다 정밀도는 낮음)
            pattern = r'\b' + re.escape(name) + r'\b'
            matches = list(re.finditer(pattern, line))
            if matches:
                # 정의인지 참조인지 구분
                for m in matches:
                    is_def = any(kw in line[:m.start()] for kw in ["def ", "class ", "import ", "from "])
                    refs.append({
                        "name": name,
                        "file": file_path,
                        "line": i,
                        "column": m.start() + 1,
                        "line_content": line.strip(),
                        "is_definition": is_def,
                    })
        key = f"{file_path}:{name}"
        if key not in self._references:
            self._references[key] = []
        self._references[key].extend(refs)
        return refs

    def get_references(self, name: str, file_path: str = "") -> list[dict]:
        if file_path:
            return self._references.get(f"{file_path}:{name}", [])
        # 모든 파일에서 검색
        results = []
        for key, refs in self._references.items():
            if key.endswith(f":{name}"):
                results.extend(refs)
        return results

    @property
    def stats(self) -> dict:
        return {
            "tracked_symbols": len(self._references),
            "total_references": sum(len(r) for r in self._references.values()),
        }


# ============================================================
# 패턴 4: CodeNavigator (파일 간 네비게이션)
# ============================================================

class CodeNavigator:
    """
    여러 파일 간 심볼 네비게이션.
    Claude Code의 goToDefinition 기능 참고.
    """

    def __init__(self):
        self.extractor = SymbolExtractor()
        self.finder = ReferenceFinder()
        self._file_sources: dict[str, str] = {}

    def add_file(self, file_path: str, source: str) -> list[Symbol]:
        """파일을 추가하고 심볼 인덱스 생성."""
        self._file_sources[file_path] = source
        symbols = self.extractor.index_file(file_path, source)
        # 참조도 함께 인덱스
        for sym in symbols:
            self.finder.find_references(sym.name, source, file_path)
        return symbols

    def go_to_definition(self, name: str) -> Optional[Symbol]:
        """심볼 정의로 이동."""
        results = self.extractor.search(name)
        # 정의(함수/클래스) 우선 반환
        definitions = [s for s in results if s.kind in ("function", "class")]
        return definitions[0] if definitions else (results[0] if results else None)

    def find_all_references(self, name: str) -> list[dict]:
        """모든 파일에서 참조 찾기."""
        return self.finder.get_references(name)

    def get_call_hierarchy(self, name: str) -> dict:
        """함수 호출 계층 분석."""
        callers = []
        callees = []
        source = self._file_sources.get("<unknown>", "")
        for fp, src in self._file_sources.items():
            refs = self.finder.find_references(name, src, fp)
            for ref in refs:
                if not ref["is_definition"]:
                    callers.append(ref)
        return {"name": name, "callers": callers, "callees": callees}

    @property
    def stats(self) -> dict:
        return {
            "files": len(self._file_sources),
            **self.extractor.stats,
            **self.finder.stats,
        }


# ============================================================
# 패턴 5: DiagnosticCollector (코드 품질 진단)
# ============================================================

@dataclass
class Diagnostic:
    file_path: str
    line: int
    severity: str  # error, warning, info
    message: str
    rule: str


class DiagnosticCollector:
    """
    정적 분석으로 코드 품질 진단 수집.
    Claude Code의 publishDiagnostics 수신 패턴 참고.
    """

    def __init__(self):
        self._diagnostics: list[Diagnostic] = []
        self._rules = {
            "no_unused_imports": self._check_unused_imports,
            "function_complexity": self._check_function_complexity,
            "missing_docstring": self._check_missing_docstring,
            "line_too_long": self._check_line_length,
        }

    def analyze(self, file_path: str, source: str) -> list[Diagnostic]:
        """소스 코드 분석."""
        results = []
        for rule_name, rule_func in self._rules.items():
            results.extend(rule_func(file_path, source))
        self._diagnostics.extend(results)
        return results

    def _check_unused_imports(self, file_path: str, source: str) -> list[Diagnostic]:
        """사용되지 않는 import 검출."""
        results = []
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return results

        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name.split(".")[0]
                    imported_names.add((name, node.lineno))
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    name = alias.asname or alias.name
                    imported_names.add((name, node.lineno))

        # 소스에서 실제 사용 여부 확인
        used_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used_names.add(node.id)

        for name, line in imported_names:
            if name not in used_names:
                results.append(Diagnostic(
                    file_path, line, "warning",
                    f"사용되지 않는 import: {name}", "no_unused_imports"
                ))
        return results

    def _check_function_complexity(self, file_path: str, source: str) -> list[Diagnostic]:
        """함수 복잡도 검출 (if/for/while 개수)."""
        results = []
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return results

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                complexity = 0
                for child in ast.walk(node):
                    if isinstance(child, (ast.If, ast.For, ast.While, ast.ExceptHandler)):
                        complexity += 1
                if complexity > 5:
                    results.append(Diagnostic(
                        file_path, node.lineno, "warning",
                        f"함수 '{node.name}' 복잡도 높음 (분기점: {complexity})",
                        "function_complexity"
                    ))
        return results

    def _check_missing_docstring(self, file_path: str, source: str) -> list[Diagnostic]:
        """docstring 누락 검출."""
        results = []
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return results

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                docstring = ast.get_docstring(node)
                if not docstring:
                    kind = "함수" if isinstance(node, ast.FunctionDef) else "클래스"
                    results.append(Diagnostic(
                        file_path, node.lineno, "info",
                        f"{kind} '{node.name}'에 docstring 없음",
                        "missing_docstring"
                    ))
        return results

    def _check_line_length(self, file_path: str, source: str, max_length: int = 100) -> list[Diagnostic]:
        """줄 길이 검출."""
        results = []
        for i, line in enumerate(source.split("\n"), 1):
            if len(line) > max_length:
                results.append(Diagnostic(
                    file_path, i, "info",
                    f"줄 길이 {len(line)}자 (최대 {max_length}자)",
                    "line_too_long"
                ))
        return results

    def get_diagnostics(self, file_path: Optional[str] = None) -> list[dict]:
        if file_path:
            return [d for d in self._diagnostics if d.file_path == file_path]
        return [{"file": d.file_path, "line": d.line, "severity": d.severity,
                 "message": d.message, "rule": d.rule} for d in self._diagnostics]

    def get_summary(self) -> dict:
        errors = sum(1 for d in self._diagnostics if d.severity == "error")
        warnings = sum(1 for d in self._diagnostics if d.severity == "warning")
        infos = sum(1 for d in self._diagnostics if d.severity == "info")
        return {"errors": errors, "warnings": warnings, "infos": infos, "total": len(self._diagnostics)}


# ============================================================
# 통합 테스트
# ============================================================

SAMPLE_CODE = '''
import os
import sys
import json

def calculate_sum(numbers):
    total = 0
    for n in numbers:
        if n > 0:
            total += n
        else:
            if n < -100:
                total += n * 2
            else:
                total += n
    return total

class DataProcessor:
    def process(self, data):
        return data

    def transform(self, data):
        return data
'''


def run_all_tests():
    print("=" * 60)
    print("LSP 통합 모듈 - 통합 테스트")
    print("=" * 60)

    # 테스트 1: ASTEditor
    print("\n[테스트 1] ASTEditor (AST 기반 코드 편집)")
    print("-" * 40)
    editor = ASTEditor(SAMPLE_CODE)
    func = editor.find_function("calculate_sum")
    print(f"  함수 찾기: {func['name']} (줄 {func['line_start']}-{func['line_end']})")
    print(f"  인자: {func['args']}")
    cls = editor.find_class("DataProcessor")
    print(f"  클래스 찾기: {cls['name']} (줄 {cls['line_start']}-{cls['line_end']})")
    print(f"  메서드: {cls['methods']}")
    all_funcs = editor.get_all_functions()
    print(f"  전체 함수: {[f['name'] for f in all_funcs]}")
    imports = editor.get_imports()
    print(f"  import: {[i['module'] for i in imports]}")
    assert func is not None, "FAIL: 함수 찾기 실패"
    assert cls is not None, "FAIL: 클래스 찾기 실패"
    assert len(all_funcs) == 3, "FAIL: 함수 수 불일치"
    print("  ✅ 통과")

    # 테스트 2: SymbolExtractor
    print("\n[테스트 2] SymbolExtractor (심볼 추출)")
    print("-" * 40)
    extractor = SymbolExtractor()
    symbols = extractor.index_file("test.py", SAMPLE_CODE)
    print(f"  추출된 심볼: {len(symbols)}개")
    for s in symbols:
        print(f"    {s.kind}: {s.name} (줄 {s.line}) - {s.detail}")
    search = extractor.search("data")
    print(f"  'data' 검색: {len(search)}개")
    assert len(symbols) >= 3, "FAIL: 심볼 추출 부족"
    print("  ✅ 통과")

    # 테스트 3: ReferenceFinder
    print("\n[테스트 3] ReferenceFinder (참조 찾기)")
    print("-" * 40)
    finder = ReferenceFinder()
    refs = finder.find_references("data", SAMPLE_CODE, "test.py")
    defs = [r for r in refs if r["is_definition"]]
    non_defs = [r for r in refs if not r["is_definition"]]
    print(f"  'data' 참조: {len(refs)}개 (정의: {len(defs)}, 사용: {len(non_defs)})")
    for r in refs[:5]:
        print(f"    줄 {r['line']}: {'[정의]' if r['is_definition'] else '[참조]'} {r['line_content'][:50]}")
    assert len(refs) > 0, "FAIL: 참조 찾기 실패"
    print("  ✅ 통과")

    # 테스트 4: CodeNavigator
    print("\n[테스트 4] CodeNavigator (파일 간 네비게이션)")
    print("-" * 40)
    nav = CodeNavigator()
    nav.add_file("test.py", SAMPLE_CODE)
    nav.add_file("other.py", "from test import DataProcessor\ndp = DataProcessor()\ndp.process('hello')")
    definition = nav.go_to_definition("DataProcessor")
    print(f"  정의 이동: {definition.name if definition else '없음'} @ {definition.file_path if definition else ''}")
    all_refs = nav.find_all_references("DataProcessor")
    print(f"  전체 참조: {len(all_refs)}개")
    print(f"  통계: {nav.stats}")
    assert definition is not None, "FAIL: 정의 이동 실패"
    assert len(all_refs) >= 1, "FAIL: 참조 수 부족"
    print("  ✅ 통과")

    # 테스트 5: DiagnosticCollector
    print("\n[테스트 5] DiagnosticCollector (코드 진단)")
    print("-" * 40)
    diag = DiagnosticCollector()
    results = diag.analyze("test.py", SAMPLE_CODE)
    summary = diag.get_summary()
    print(f"  진단 결과: {summary['total']}건 (에러: {summary['errors']}, 경고: {summary['warnings']}, 정보: {summary['infos']})")
    for d in results[:5]:
        print(f"    [{d.severity}] 줄 {d.line}: {d.message} ({d.rule})")
    assert summary["total"] > 0, "FAIL: 진단 결과 없음"
    print("  ✅ 통과")

    print("\n" + "=" * 60)
    print("모든 테스트 통과! (5/5)")
    print("=" * 60)


if __name__ == "__main__":
    run_all_tests()
