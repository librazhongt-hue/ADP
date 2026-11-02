"""Stationary VLM detect helpers for scenario C (no rclpy / no ros_ws import).

Prompt and JSON shape mirror ``ros_ws/src/test/vlm_search_common.py`` so results
are comparable; multimodal HTTP uses the same OpenAI-compatible endpoint as
``ADP_LLM_*`` (model id from ``VLM_MODEL`` / ``ADP_VLM_MODEL``).
"""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VLM_DETECT_PROMPT = """
你作为图像识别专家，根据用户指令在图片中定位目标。
## 硬性规则
1. 只输出一个 JSON 对象，不要 markdown，不要解释，不要列举其它物体。
2. 从用户话里提取要找的物体；object 的 name 用英文。
3. 若画面中**没有**该物体，只输出：
{"found":false}
4. 若找到：坐标为相对整图宽高的归一化值 [0,1]（左上 0,0，右下 1,1），框贴物体可见边缘：
{"found":true,"object":"english_name","xyxy":[xmin,ymin,xmax,ymax]}
""".strip()

VLM_MAX_TOKENS = 96


@dataclass
class VlmDetectResult:
    found: bool
    object_name: str = ""
    xyxy_norm: list[float] | None = None
    raw_text: str = ""
    error: str = ""
    source: str = ""  # fake | file_vlm | deferred_live


def resolve_vlm_model() -> str:
    return (
        os.environ.get("ADP_VLM_MODEL")
        or os.environ.get("VLM_MODEL")
        or os.environ.get("ADP_LLM_DEFAULT_MODEL")
        or "gpt-4o-mini"
    )


def extract_search_target(utterance: str, explicit: str | None = None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    text = utterance.strip()
    for prefix in ("帮我找", "找一下", "找个", "寻找", "去找", "找找", "找"):
        if prefix in text:
            text = text.split(prefix, 1)[-1]
            break
    return text.strip(" ，。,.的一下") or utterance.strip()


def parse_vlm_json(raw: str) -> VlmDetectResult:
    text = (raw or "").strip()
    if not text:
        return VlmDetectResult(found=False, raw_text=text, error="empty_response")
    cleaned = re.sub(r"```json|```", "", text).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        return VlmDetectResult(found=False, raw_text=text, error="no_json")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        return VlmDetectResult(found=False, raw_text=text, error=f"json_decode:{exc}")
    if not isinstance(data, dict):
        return VlmDetectResult(found=False, raw_text=text, error="json_not_object")
    if data.get("found") is False:
        return VlmDetectResult(found=False, raw_text=text, error="found_false")
    name = str(data.get("object") or data.get("name") or "").strip()
    xyxy = data.get("xyxy")
    if not xyxy or len(xyxy) != 4:
        return VlmDetectResult(
            found=False, object_name=name, raw_text=text, error="missing_xyxy"
        )
    try:
        norm = [float(xyxy[0]), float(xyxy[1]), float(xyxy[2]), float(xyxy[3])]
    except (TypeError, ValueError) as exc:
        return VlmDetectResult(
            found=False, object_name=name, raw_text=text, error=f"bad_xyxy:{exc}"
        )
    # Heuristic: values look like pixels — normalize later unavailable; keep as-is if <=1.5
    if max(norm) > 1.5:
        return VlmDetectResult(
            found=True,
            object_name=name or "object",
            xyxy_norm=None,
            raw_text=text,
            error="pixel_xyxy_unnormalized",
        )
    return VlmDetectResult(
        found=True,
        object_name=name or "object",
        xyxy_norm=norm,
        raw_text=text,
    )


def fake_detect(target: str) -> VlmDetectResult:
    """Deterministic offline stand-in when no image / no live camera."""
    t = target.strip().lower()
    if any(x in t for x in ("不存在", "没有的", "unicorn", "火星")):
        return VlmDetectResult(
            found=False, object_name="", raw_text='{"found":false}', source="fake"
        )
    return VlmDetectResult(
        found=True,
        object_name="object",
        xyxy_norm=[0.2, 0.2, 0.5, 0.6],
        raw_text='{"found":true,"object":"object","xyxy":[0.2,0.2,0.5,0.6]}',
        source="fake",
    )


def _image_data_url(path: Path) -> tuple[str, str]:
    data = path.read_bytes()
    suffix = path.suffix.lower()
    mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}", mime


async def call_vlm_on_image_file(
    *,
    image_path: Path,
    target: str,
    base_url: str,
    api_key: str,
    model: str,
    timeout_seconds: float = 120.0,
) -> VlmDetectResult:
    import httpx

    data_url, _mime = _image_data_url(image_path)
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": VLM_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": VLM_DETECT_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {
                        "type": "text",
                        "text": (
                            f"目标：{target}\n"
                            f"有则 found:true + 归一化 xyxy(0~1)；无则只输出 {{\"found\":false}}。"
                        ),
                    },
                ],
            },
        ],
    }
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        resp = await client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()
    content = (data["choices"][0]["message"].get("content") or "").strip()
    result = parse_vlm_json(content)
    result.source = "file_vlm"
    return result
