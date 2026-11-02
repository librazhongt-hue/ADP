"""ROS robot domain types and pure helpers for ADP scenarios A (goto), B (intent hub), C (VLM).

Ported lightly from ``ros_ws/src/test/voice_hub_test.py`` (PLACE_ALIASES / intent
heuristics) so ADP does not import the ROS workspace. Keep this file free of
``rclpy`` / Nav2 dependencies.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

try:
    import yaml
except ImportError:  # pragma: no cover - optional for dry-run defaults
    yaml = None  # type: ignore[assignment]

IntentName = Literal[
    "search",
    "goto",
    "drive",
    "arm_routine",
    "qa_nod",
    "stop",
    "exit",
    "chat",
    "unsupported",
]

PLACE_ALIASES: dict[str, tuple[str, ...]] = {
    "home": ("回起点", "回home", "回家里", "起点", "原点", "家里", "home", "家"),
    "table": ("工作台", "操作台", "桌子", "桌边", "桌旁", "台子", "table"),
    "drop": ("投放点", "放货点", "分拣点", "投放台", "放下点", "投放", "drop"),
    "lobby": ("西北大厅", "大厅", "lobby", "大堂"),
    "north_room": ("北侧房间", "北屋", "north_room", "北边房间"),
    "atrium": ("中庭", "atrium", "中央空地"),
    "northeast": ("东北空地", "东北", "northeast"),
    "east": ("东侧空地", "东边", "east"),
    "east_hall": ("东走廊", "东侧走廊", "east_hall"),
    "west": ("西侧", "西边", "west"),
    "south": ("南走廊", "南边", "south"),
    "south_end": ("南端", "最南边", "south_end"),
}

DEFAULT_WAYPOINTS: dict[str, dict[str, float]] = {
    "home": {"x": 0.0, "y": 0.0, "qw": 1.0, "qz": 0.0},
    "table": {"x": 4.4, "y": 0.024, "qw": 0.99905, "qz": -0.04360},
    "drop": {"x": 4.431, "y": 1.148, "qw": 0.7536, "qz": 0.6573},
}

STOP_KEYWORDS = ("停下", "停一下", "停止", "急停", "别动", "取消", "算了", "取消导航", "停止导航")
PORTER_HINT_RE = re.compile(r"(搬|搬运|送到|放到|拿到).{0,12}(红|绿|蓝|色块|物块)|(把).{0,8}(搬|送|放)")
DRIVE_HINT_RE = re.compile(
    r"(前进|后退|向前|向后|往前|往后|左转|右转|左移|右移|平移|"
    r"夹爪|舵机|末端|比个耶|摄像头朝前|相机朝前|观察前方|恢复初始|慢一点|快点)"
)
GOTO_CUE_RE = re.compile(r"(导航到|开到|前往|去到|去一趟|去一下|回到|回|去|到)")
SEARCH_HINT_RE = re.compile(r"(帮我找|找一下|找个|寻找|去找|找找)")
CHAT_HINT_RE = re.compile(
    r"(介绍一下|介绍下|你是谁|你叫什么|你好|聊聊|讲个笑话|你会什么|你能做什么)"
)
QA_HINT_RE = re.compile(r"(吗|呢|会不会|是不是|对不对|有没有|能不能).{0,4}$")

HUB_INTENT_SYSTEM = """你是机器人「语音中枢」意图分类器。每一句话都独立分类。
只输出一个 JSON，不要 Markdown：
{"intent":"search"|"goto"|"drive"|"arm_routine"|"qa_nod"|"stop"|"exit"|"chat"|"unsupported",
 "targets":["物体短描述"],"place":"航点英文名或空","reply":""}
规则：任务意图优先于闲聊；goto 的 place 用航点英文名；search 填 targets；
「去找某某」是 search 不是 goto；unsupported=抓取/搬运。
"""

PHYSICAL_INTENTS = frozenset({"goto", "drive", "arm_routine", "search"})


@dataclass
class WaypointCatalog:
    places: dict[str, dict[str, Any]] = field(default_factory=lambda: dict(DEFAULT_WAYPOINTS))

    @property
    def names(self) -> list[str]:
        return sorted(self.places.keys())


def load_waypoint_catalog(path: str | Path | None) -> WaypointCatalog:
    if path is None:
        return WaypointCatalog()
    p = Path(path)
    if not p.exists():
        return WaypointCatalog()
    if yaml is None:
        raise RuntimeError("PyYAML is required to load waypoints.yaml; pip install pyyaml")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"waypoints file must be a mapping: {p}")
    places = {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)}
    return WaypointCatalog(places=places or dict(DEFAULT_WAYPOINTS))


def resolve_place(text: str, known: list[str] | None = None) -> str | None:
    t = text.strip().lower()
    allowed = known or list(PLACE_ALIASES.keys())
    # Longer aliases first to avoid "家" beating "回家里" incorrectly on short match order.
    candidates: list[tuple[str, str]] = []
    for key in allowed:
        aliases = PLACE_ALIASES.get(key, (key,))
        for alias in aliases:
            candidates.append((key, alias.lower()))
        if key not in PLACE_ALIASES:
            candidates.append((key, key.lower()))
    candidates.sort(key=lambda x: len(x[1]), reverse=True)
    for key, alias in candidates:
        if alias and alias in t:
            return key
    return None


def heuristic_intent(utterance: str, known_places: list[str] | None = None) -> dict[str, Any]:
    text = utterance.strip()
    if any(k in text for k in STOP_KEYWORDS):
        return {"intent": "stop", "targets": [], "place": "", "reply": "", "source": "heuristic"}
    if PORTER_HINT_RE.search(text):
        return {"intent": "unsupported", "targets": [], "place": "", "reply": "", "source": "heuristic"}
    if "退出" in text or "退下" in text or "再见" in text:
        return {"intent": "exit", "targets": [], "place": "", "reply": "", "source": "heuristic"}
    if SEARCH_HINT_RE.search(text):
        target = SEARCH_HINT_RE.sub("", text)
        for prefix in ("帮我", "一下", "一个"):
            target = target.replace(prefix, "")
        target = target.strip(" ，。,.的")
        return {
            "intent": "search",
            "targets": [target] if target else ["未知目标"],
            "place": "",
            "reply": "",
            "source": "heuristic",
        }
    if GOTO_CUE_RE.search(text):
        place = resolve_place(text, known_places)
        if place:
            return {"intent": "goto", "targets": [], "place": place, "reply": "", "source": "heuristic"}
    if DRIVE_HINT_RE.search(text):
        if any(x in text for x in ("夹爪", "舵机", "比个耶", "摄像头", "相机", "恢复初始")):
            return {"intent": "arm_routine", "targets": [], "place": "", "reply": "", "source": "heuristic"}
        return {"intent": "drive", "targets": [], "place": "", "reply": "", "source": "heuristic"}
    if CHAT_HINT_RE.search(text):
        return {"intent": "chat", "targets": [], "place": "", "reply": "", "source": "heuristic"}
    if QA_HINT_RE.search(text):
        return {"intent": "qa_nod", "targets": [], "place": "", "reply": "", "source": "heuristic"}
    return {"intent": "chat", "targets": [], "place": "", "reply": "", "source": "heuristic"}


def parse_intent_json(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    return data


@dataclass
class RobotUtteranceSnapshot:
    utterance: str
    known_places: list[str]
    scenario: str  # "a" | "b" | "c"
    target: str | None = None
    """Scenario C: explicit search target; else derived from utterance."""

    image_path: str | None = None
    """Scenario C: optional still image for offline/file VLM (no camera)."""

    motion: str = "once"
    """Scenario C Act mode: once | wander | nav."""
