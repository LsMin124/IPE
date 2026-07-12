"""Calibration anchors — 난이도 측정용 reference 문제 set 로더.

스펙: PROJECT_SPEC.md §4.6 (The Evaluator), IMPLEMENTATION_ROADMAP §1 P9.1

``anchors.json`` 은 Bronze~Diamond 티어를 균형 있게 덮는 40여 개 anchor를 포함
(RFC R4 확장: Bronze 8 / Silver 10 / Gold 12 / Platinum 10 / Diamond 4). 각 anchor:
- ``id``: 시스템 식별자 (예: ``bj_1753_gold5``)
- ``label``: 사람이 읽는 라벨 (예: ``Gold V``)
- ``summary``: 한 줄 요약
- ``factors``: dict {algorithm, n_max, complexity, data_structures}

Evaluator(P9.2)가 ``load_anchors()``로 list를 가져와 user prompt에 첨부 →
LLM이 reasoning에 사용한 anchor id를 명시.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ANCHORS_PATH = Path(__file__).parent / "anchors.json"


def load_anchors(path: Path | None = None) -> list[dict[str, Any]]:
    """``anchors.json`` 에서 anchor list를 로드.

    파일 부재 / JSON 오류 / list 아닌 경우 → 빈 list (안전 fallback).
    각 entry는 ``dict``인 것만 통과시킨다.

    Args:
        path: 명시적 경로 override (테스트용). 기본값은 ``ANCHORS_PATH``.
    """
    target = path if path is not None else ANCHORS_PATH
    if not target.exists():
        return []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [a for a in data if isinstance(a, dict)]
