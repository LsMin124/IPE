"""Calibration anchors 단위 테스트 (P9.1).

스펙: ARCHITECTURE.md §3.10, IMPLEMENTATION_ROADMAP §1 P9.1
범위: ``load_anchors`` 의 fallback 경로 + 기본 anchors.json 무결성.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ipe.calibration import ANCHORS_PATH, load_anchors

# BOJ 티어 라벨 규격 — DifficultyReport.label 검증과 동일 어휘.
_LABEL_RE = re.compile(r"^(Bronze|Silver|Gold|Platinum|Diamond|Ruby) (I|II|III|IV|V)$")


def test_default_anchors_load() -> None:
    """프로젝트 default anchors.json은 충분한 수(현재 44개, Bronze~Diamond)의 anchor를 포함."""
    anchors = load_anchors()
    assert len(anchors) >= 40  # 확장된 calibration set (RFC R4) — truncation 가드
    # 모든 entry가 (id, label, summary, factors) 4개 필드를 보유
    for a in anchors:
        assert "id" in a and isinstance(a["id"], str)
        assert "label" in a and isinstance(a["label"], str)
        assert "summary" in a and isinstance(a["summary"], str)
        assert "factors" in a and isinstance(a["factors"], dict)


def test_default_anchor_ids_unique_and_well_formed() -> None:
    """anchor id 는 전역 유일 + ``bj_<번호>_<tier><등급>`` 규격 (인용 필터의 전제)."""
    anchors = load_anchors()
    ids = [a["id"] for a in anchors]
    assert len(ids) == len(set(ids))  # 중복 id → 인용 교집합 필터 왜곡
    pattern = re.compile(r"bj_\d+_(bronze|silver|gold|platinum|diamond|ruby)[1-5]")
    for aid in ids:
        assert pattern.fullmatch(aid), aid


def test_default_anchor_labels_valid_tier_format() -> None:
    """label 은 반드시 '<티어> <로마숫자>' 형식 — LLM 출력 label 과 동일 어휘."""
    for a in load_anchors():
        assert _LABEL_RE.fullmatch(a["label"]), a["label"]


def test_default_anchor_factors_shape() -> None:
    """factors 는 {algorithm, n_max, complexity, data_structures} 고정 스키마."""
    for a in load_anchors():
        f = a["factors"]
        assert isinstance(f["algorithm"], str) and f["algorithm"]
        assert f["n_max"] is None or isinstance(f["n_max"], int)
        assert isinstance(f["complexity"], str) and f["complexity"].startswith("O(")
        assert isinstance(f["data_structures"], list)
        assert all(isinstance(d, str) for d in f["data_structures"])


def test_default_anchor_tier_coverage_balanced() -> None:
    """티어 분포가 균형(Bronze~Diamond 전 티어 커버) — 편중 anchor set 회귀 가드.

    기존 20개 set 는 Bronze 4 / Silver 6 / Gold 9 / Platinum 1 / Diamond 0 으로
    상위 티어 판별이 불가능했다. 확장 set 의 티어별 최소 하한을 고정한다.
    """
    counts: dict[str, int] = {}
    for a in load_anchors():
        tier = a["label"].split(" ")[0]
        counts[tier] = counts.get(tier, 0) + 1
    assert counts.get("Bronze", 0) >= 6
    assert counts.get("Silver", 0) >= 8
    assert counts.get("Gold", 0) >= 10
    assert counts.get("Platinum", 0) >= 8
    assert counts.get("Diamond", 0) >= 3


def test_default_anchors_path_exists() -> None:
    """기본 anchors.json 경로는 패키지 안에 존재."""
    assert ANCHORS_PATH.exists()
    assert ANCHORS_PATH.name == "anchors.json"


def test_load_anchors_missing_file_returns_empty(tmp_path: Path) -> None:
    """파일이 없으면 빈 list 반환 (raise 안 함)."""
    missing = tmp_path / "no_such.json"
    assert load_anchors(missing) == []


def test_load_anchors_malformed_json_returns_empty(tmp_path: Path) -> None:
    """JSON 파싱 실패 시 빈 list 반환."""
    bad = tmp_path / "bad.json"
    bad.write_text("{ not valid json", encoding="utf-8")
    assert load_anchors(bad) == []


def test_load_anchors_non_list_returns_empty(tmp_path: Path) -> None:
    """top-level이 list가 아니면 빈 list 반환."""
    obj = tmp_path / "obj.json"
    obj.write_text(json.dumps({"id": "x"}), encoding="utf-8")
    assert load_anchors(obj) == []


def test_load_anchors_filters_non_dict_entries(tmp_path: Path) -> None:
    """list 안의 dict가 아닌 entry는 필터링."""
    mixed = tmp_path / "mixed.json"
    mixed.write_text(
        json.dumps([{"id": "ok"}, "not_a_dict", 42, {"id": "ok2"}]),
        encoding="utf-8",
    )
    out = load_anchors(mixed)
    assert len(out) == 2
    assert all(isinstance(a, dict) for a in out)
