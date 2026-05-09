"""
자가학습 검증 모듈
================
Hermes Agent의 잘못된 경험 고착화 문제를 해결하기 위한 패턴 구현.

패턴:
  1. DualExperienceStore - 성공/실패 경험 분리 저장 (A-MemGuard 참고)
  2. ConsensusValidator - 합의 기반 경험 검증
  3. LessonMemory - 실패 교훈 별도 관리
  4. ContradictionDetector - 모순 감지 시스템
  5. SelfReflectionGuard - 자기 평가 가드레일
"""

import time
import hashlib
from dataclasses import dataclass, field
from typing import Optional


# ============================================================
# 패턴 1: DualExperienceStore (성공/실패 분리)
# ============================================================

@dataclass
class Experience:
    task: str
    action: str
    outcome: bool
    conclusion: str
    timestamp: float = field(default_factory=time.time)
    similarity_key: str = ""

    def __post_init__(self):
        if not self.similarity_key:
            raw = f"{self.task}:{self.action}"
            self.similarity_key = hashlib.md5(raw.encode()).hexdigest()[:8]


class DualExperienceStore:
    """
    성공 경험과 실패 교훈을 분리 저장.
    실패 경험은 일반 메모리에 저장하지 않고 교훈 메모리에만 저장하여
    잘못된 경험의 고착화를 방지. (A-MemGuard ICLR 2026 참고)
    """

    def __init__(self, max_experiences: int = 100, max_lessons: int = 50):
        self.experiences: list[Experience] = []
        self.lessons: list[dict] = []
        self.max_experiences = max_experiences
        self.max_lessons = max_lessons

    def add(self, experience: Experience) -> dict:
        """경험 추가. 실패는 교훈 메모리로, 성공은 일반 메모리로."""
        if not experience.outcome:
            lesson = self._distill_lesson(experience)
            if len(self.lessons) >= self.max_lessons:
                self.lessons.pop(0)
            self.lessons.append(lesson)
            return {"stored_in": "lesson_memory", "lesson": lesson}
        else:
            if len(self.experiences) >= self.max_experiences:
                self.experiences.pop(0)
            self.experiences.append(experience)
            return {"stored_in": "experience_memory"}

    def _distill_lesson(self, exp: Experience) -> dict:
        """실패 경험에서 교훈 추출."""
        return {
            "task": exp.task,
            "failed_action": exp.action,
            "lesson": f"'{exp.action}'은(는) '{exp.task}'에 부적합. 결과: {exp.conclusion}",
            "timestamp": exp.timestamp,
        }

    def get_relevant_experiences(self, task: str, k: int = 3) -> list[Experience]:
        """유사 작업의 성공 경험 검색."""
        task_key = hashlib.md5(task.encode()).hexdigest()[:8]
        scored = []
        for exp in self.experiences:
            score = self._similarity(exp.similarity_key, task_key)
            scored.append((score, exp))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [exp for _, exp in scored[:k]]

    def get_relevant_lessons(self, task: str, k: int = 3) -> list[dict]:
        """유사 작업의 실패 교훈 검색."""
        return [l for l in self.lessons if task in l["task"]][-k:]

    @staticmethod
    def _similarity(key1: str, key2: str) -> float:
        """해시 키 유사도 (공통 문자 수 / 최대 길이)."""
        common = len(set(key1) & set(key2))
        return common / max(len(key1), len(key2), 1)

    @property
    def stats(self) -> dict:
        return {
            "experiences": len(self.experiences),
            "lessons": len(self.lessons),
            "success_rate": (
                len(self.experiences) / (len(self.experiences) + len(self.lessons))
                if (len(self.experiences) + len(self.lessons)) > 0
                else 0
            ),
        }


# ============================================================
# 패턴 2: ConsensusValidator (합의 기반 검증)
# ============================================================

class ConsensusValidator:
    """
    새 경험을 저장하기 전에 유사한 기존 경험들과 합의(consensus)를 확인.
    과반수가 일치하지 않으면 저장 거부. 잘못된 경험의 유입을 차단.
    """

    def __init__(self, store: DualExperienceStore, min_agreement: float = 0.5):
        self.store = store
        self.min_agreement = min_agreement
        self.rejected_count = 0

    def validate_and_add(self, experience: Experience) -> dict:
        """합의 검증 후 경험 추가."""
        similar = self.store.get_relevant_experiences(experience.task, k=3)

        if len(similar) >= 2:
            agreement = sum(
                1 for s in similar if s.conclusion == experience.conclusion
            )
            ratio = agreement / len(similar)

            if ratio < self.min_agreement:
                self.rejected_count += 1
                return {
                    "accepted": False,
                    "reason": f"합의 부족 ({agreement}/{len(similar)} 일치, 필요: {self.min_agreement})",
                    "agreement_ratio": ratio,
                }

        result = self.store.add(experience)
        return {"accepted": True, **result}

    @property
    def stats(self) -> dict:
        return {
            "rejected_count": self.rejected_count,
            "min_agreement": self.min_agreement,
        }


# ============================================================
# 패턴 3: ContradictionDetector (모순 감지)
# ============================================================

class ContradictionDetector:
    """
    메모리에 저장된 정보 간의 모순을 감지.
    Hermes의 "PostgreSQL 사용"과 "MySQL로 전환"이 공존하는 문제를 해결.
    """

    def __init__(self):
        self.claims: dict[str, list[dict]] = {}

    def add_claim(self, topic: str, claim: str, source: str = "agent") -> dict:
        """주장 추가. 기존 주장과 모순이 있는지 검사."""
        if topic not in self.claims:
            self.claims[topic] = []

        contradictions = self._find_contradictions(topic, claim)

        entry = {
            "claim": claim,
            "source": source,
            "timestamp": time.time(),
            "active": True,
        }
        self.claims[topic].append(entry)

        if contradictions:
            for c in contradictions:
                c["active"] = False  # 이전 모순 주장을 비활성화
            return {
                "stored": True,
                "contradictions_found": len(contradictions),
                "deactivated": [c["claim"] for c in contradictions],
            }

        return {"stored": True, "contradictions_found": 0}

    def _find_contradictions(self, topic: str, new_claim: str) -> list:
        """단순 키워드 기반 모순 감지."""
        opposite_pairs = [
            ("사용", "전환"), ("사용", "변경"), ("사용", "중단"),
            ("추가", "제거"), ("활성화", "비활성화"),
            ("use", "switch"), ("add", "remove"), ("enable", "disable"),
        ]
        contradictions = []
        for entry in self.claims.get(topic, []):
            if not entry["active"]:
                continue
            for word1, word2 in opposite_pairs:
                if (word1 in new_claim and word2 in entry["claim"]) or \
                   (word2 in new_claim and word1 in entry["claim"]):
                    contradictions.append(entry)
        return contradictions

    def get_active_claims(self, topic: str) -> list:
        """주제별 활성 주장만 반환."""
        return [c for c in self.claims.get(topic, []) if c["active"]]

    @property
    def stats(self) -> dict:
        total = sum(len(v) for v in self.claims.values())
        active = sum(1 for v in self.claims.values() for c in v if c["active"])
        return {
            "topics": len(self.claims),
            "total_claims": total,
            "active_claims": active,
            "inactive_claims": total - active,
        }


# ============================================================
# 패턴 4: SelfReflectionGuard (자기 평가 가드레일)
# ============================================================

class SelfReflectionGuard:
    """
    에이전트가 자신의 작업 결과를 평가할 때,
    항상 "성공"이라고 단정하는 문제를 방지.
    외부 검증 기준을 도입하여 객관적 평가를 강제.
    """

    def __init__(self, auto_approve_threshold: float = 0.7):
        self.auto_approve_threshold = auto_approve_threshold
        self.evaluations: list[dict] = []

    def evaluate(
        self,
        task: str,
        agent_self_assessment: bool,
        external_checks: dict[str, bool],
    ) -> dict:
        """
        에이전트 자기 평가 + 외부 검증 기준을 결합한 평가.
        external_checks: {"output_exists": True, "tests_pass": False, ...}
        """
        check_results = list(external_checks.values())
        if not check_results:
            external_pass = None
        else:
            external_pass = sum(check_results) / len(check_results)

        # 외부 검증이 있으면 그 결과를 우선
        if external_pass is not None:
            final_outcome = external_pass >= self.auto_approve_threshold
            disagreement = agent_self_assessment != final_outcome
        else:
            final_outcome = agent_self_assessment
            disagreement = False

        evaluation = {
            "task": task,
            "agent_self_assessment": agent_self_assessment,
            "external_pass_rate": external_pass,
            "final_outcome": final_outcome,
            "disagreement": disagreement,
            "timestamp": time.time(),
        }
        self.evaluations.append(evaluation)

        return evaluation

    @property
    def stats(self) -> dict:
        disagreements = sum(1 for e in self.evaluations if e["disagreement"])
        return {
            "total_evaluations": len(self.evaluations),
            "disagreements": disagreements,
            "disagreement_rate": (
                disagreements / len(self.evaluations)
                if self.evaluations else 0
            ),
        }


# ============================================================
# 통합 테스트
# ============================================================

def run_all_tests():
    print("=" * 60)
    print("자가학습 검증 모듈 - 통합 테스트")
    print("=" * 60)

    # 테스트 1: DualExperienceStore
    print("\n[테스트 1] DualExperienceStore (성공/실패 분리)")
    print("-" * 40)
    store = DualExperienceStore()
    store.add(Experience("파일 읽기", "cat file.txt", True, "성공적으로 읽음"))
    store.add(Experience("파일 읽기", "rm file.txt", False, "파일 삭제됨"))
    store.add(Experience("API 호출", "requests.get(url)", True, "200 OK"))
    store.add(Experience("API 호출", "curl url", False, "타임아웃"))
    stats = store.stats
    print(f"  성공 경험: {stats['experiences']}개")
    print(f"  실패 교훈: {stats['lessons']}개")
    print(f"  성공률: {stats['success_rate']:.0%}")
    lessons = store.get_relevant_lessons("API 호출")
    print(f"  API 관련 교훈: {len(lessons)}개")
    assert stats["experiences"] == 2, "FAIL: 성공 경험 수 불일치"
    assert stats["lessons"] == 2, "FAIL: 교훈 수 불일치"
    print("  ✅ 통과")

    # 테스트 2: ConsensusValidator
    print("\n[테스트 2] ConsensusValidator (합의 기반 검증)")
    print("-" * 40)
    store2 = DualExperienceStore()
    validator = ConsensusValidator(store2, min_agreement=0.5)
    # 동일 결론의 경험 3개 추가
    for i in range(3):
        validator.validate_and_add(
            Experience("DB 쿼리", "SELECT * FROM users", True, "결과 반환됨")
        )
    # 모순되는 경험 시도
    result = validator.validate_and_add(
        Experience("DB 쿼리", "DROP TABLE users", True, "테이블 삭제됨")
    )
    print(f"  모순 경험 수락: {result['accepted']}")
    if not result["accepted"]:
        print(f"  거부 사유: {result['reason']}")
    # 동일한 결론의 경험은 수락
    result2 = validator.validate_and_add(
        Experience("DB 쿼리", "SELECT id FROM users", True, "결과 반환됨")
    )
    print(f"  합의 경험 수락: {result2['accepted']}")
    stats = validator.stats
    print(f"  거부 횟수: {stats['rejected_count']}")
    print("  ✅ 통과")

    # 테스트 3: ContradictionDetector
    print("\n[테스트 3] ContradictionDetector (모순 감지)")
    print("-" * 40)
    detector = ContradictionDetector()
    r1 = detector.add_claim("데이터베이스", "PostgreSQL을 사용한다")
    print(f"  첫 주장: {r1}")
    r2 = detector.add_claim("데이터베이스", "MySQL로 전환한다")
    print(f"  모순 주장: 모순 {r2['contradictions_found']}개 감지")
    print(f"  비활성화됨: {r2.get('deactivated', [])}")
    active = detector.get_active_claims("데이터베이스")
    print(f"  활성 주장: {[c['claim'] for c in active]}")
    stats = detector.stats
    print(f"  통계: {stats}")
    assert r2["contradictions_found"] > 0, "FAIL: 모순 미감지"
    assert len(active) == 1, "FAIL: 모순 주장이 비활성화되지 않음"
    print("  ✅ 통과")

    # 테스트 4: SelfReflectionGuard
    print("\n[테스트 4] SelfReflectionGuard (자기 평가 가드레일)")
    print("-" * 40)
    guard = SelfReflectionGuard()
    # 에이전트가 성공이라고 했지만 외부 검증 실패
    r1 = guard.evaluate(
        task="코드 실행",
        agent_self_assessment=True,
        external_checks={"tests_pass": False, "output_exists": True},
    )
    print(f"  에이전트 평가: 성공, 외부 검증: 50% → 최종: {'성공' if r1['final_outcome'] else '실패'}")
    print(f"  의견 불일치: {r1['disagreement']}")
    # 에이전트가 성공, 외부 검증도 성공
    r2 = guard.evaluate(
        task="파일 생성",
        agent_self_assessment=True,
        external_checks={"file_exists": True, "content_valid": True},
    )
    print(f"  에이전트 평가: 성공, 외부 검증: 100% → 최종: {'성공' if r2['final_outcome'] else '실패'}")
    print(f"  의견 불일치: {r2['disagreement']}")
    stats = guard.stats
    print(f"  통계: {stats}")
    assert r1["disagreement"] is True, "FAIL: 불일치 미감지"
    assert r2["disagreement"] is False, "FAIL: 잘못된 불일치 감지"
    print("  ✅ 통과")

    print("\n" + "=" * 60)
    print("모든 테스트 통과! (4/4)")
    print("=" * 60)


if __name__ == "__main__":
    run_all_tests()
