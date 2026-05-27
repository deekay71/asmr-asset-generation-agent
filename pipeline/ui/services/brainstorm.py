"""Brainstorm cleaning-step plans for a new item.

Strategy: hybrid — Gemini Flash text proposes a list of state steps + tool
steps, conditioned on a digest of the user's past *approved* sessions so the
plan style matches their existing levels.

Returns a list of step dicts ready to feed into sessions.add_step / update_fields.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

from . import sessions
from .projects import PIPELINE_DIR

if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))


def _genai_client():
    """Google AI Studio client using GOOGLE_KEY from env / .env."""
    from dotenv import load_dotenv
    load_dotenv(str(PIPELINE_DIR.parent / ".env"))
    key = os.environ.get("GOOGLE_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("No GOOGLE_KEY / GEMINI_API_KEY in env or .env")
    from google import genai
    return genai.Client(api_key=key)


def _digest_past_sessions(max_sessions: int = 6) -> str:
    """Pull the most recent past sessions (active + archived) and summarise
    the steps that had at least one approved generation. Gives the LLM a
    short style reference."""
    bits: list[str] = []
    for ses in (sessions.list_sessions(archived=False)
                + sessions.list_sessions(archived=True))[:max_sessions]:
        approved_steps: list[dict] = []
        for s in ses.get("steps", []):
            has_approved = any(g.get("verdict") == "approve"
                               for g in s.get("generations", []))
            if not has_approved:
                continue
            approved_steps.append({
                "kind": s.get("kind", "state"),
                "step_id": s.get("step_id"),
                "step": s.get("fields", {}).get("step", ""),
                "concept": s.get("fields", {}).get("concept", "")[:200],
            })
        if approved_steps:
            bits.append(f"### Session: {ses.get('name', ses['id'])}\n"
                        + json.dumps(approved_steps, indent=2, ensure_ascii=False))
    if not bits:
        return "(no past approved sessions yet — start fresh)"
    return "\n\n".join(bits)


def _system_prompt(lang: str) -> str:
    if lang == "vi":
        return """Bạn thiết kế chuỗi asset cho game ASMR dọn dẹp.

⚠️ NGÔN NGỮ: Toàn bộ NỘI DUNG các trường JSON (concept, per_object_description,
camera, object_shape, color, style, v.v.) PHẢI VIẾT BẰNG TIẾNG VIỆT. Chỉ giữ
nguyên tiếng Anh cho `kind` ("state" / "tool"), tên file `output`, và step_id.

Cho một vật phẩm, bạn xuất ra JSON gồm hai phần: các bước trạng thái (state)
biểu diễn quá trình từ BẨN KINH KHỦNG sang SẠCH HOÀN HẢO, và các công cụ
(tool) mà người chơi dùng.

QUY TẮC TUYỆT ĐỐI:
- Số bước trạng thái = ĐÚNG con số người dùng yêu cầu, không thêm không bớt.
- Bước 1 = TRẠNG THÁI BẨN NHẤT (đồ vật bị bỏ quên hàng chục năm: bụi dày,
  mốc, vết bẩn ăn sâu, rỉ sét, lá khô — tuỳ loại).
- Bước cuối = TRẠNG THÁI HOÀN HẢO (sạch, mới, sáng bóng, thoả mãn).
- Bước 2 KHÔNG ĐƯỢC sạch — vẫn còn nhiều vấn đề chờ giải quyết ở bước sau.
  Mỗi bước trung gian chỉ giải quyết MỘT loại bẩn / hư hại, để chuỗi cảm
  thấy mượt mà và thoả mãn.
- Bước n giữ NGUYÊN hình dáng, tỉ lệ, hướng nhìn, phối cảnh như bước n-1;
  chỉ thay đổi lớp bẩn / hiệu ứng.
- 2–4 công cụ phù hợp với phương pháp dọn.
- Match phong cách (style) của các phiên đã được duyệt nếu có.

Xuất JSON THUẦN — KHÔNG thêm prose, KHÔNG dùng markdown fence:

[
  {
    "kind": "state",
    "step": "<tên ngắn, vd '00_super_dirty', '01_dust_off'>",
    "concept": "<mô tả ngắn về trạng thái này>",
    "camera": "chính diện, không nghiêng",
    "object_count": "1 (1 vật, ở giữa)",
    "object_shape": "<silhouette / tỉ lệ>",
    "object_position": "giữa khung, hiện đầy đủ, biệt lập",
    "per_object_description": "<prompt hình ảnh đầy đủ — loại bẩn / hiệu ứng / màu>",
    "negative_prompt": "không có chữ, không logo, không tay người, không UI, không phòng nền"
  },
  ...
  {
    "kind": "tool",
    "name": "<tên công cụ>",
    "tool_count": "1",
    "tool_camera": "top-down product render",
    "color": "<màu / chất liệu>",
    "style": "2D game asset",
    "output": "<tool>.png",
    "per_object_description": "<prompt hình ảnh>",
    "negative_prompt": "không có chữ, không logo"
  }
]
"""
    return """You design ASMR cleaning-game asset chains.

Given an item, output JSON containing state steps (representing the journey
from EXTREMELY DIRTY to PERFECTLY CLEAN) plus the cleaning tools used.

HARD RULES:
- State step count = EXACTLY the number the user asked for. No more, no less.
- Step 1 = WORST POSSIBLE STATE (object neglected for decades — thick dust,
  mold, deeply embedded stains, rust, dead leaves — whatever fits the item).
- Last step = PRISTINE (clean, like-new, satisfying, fresh).
- Step 2 must NOT be clean — it must still have visible problems that later
  steps will resolve. Each intermediate step removes ONE category of dirt /
  damage, so the chain feels gradual and satisfying.
- Step n keeps the EXACT same silhouette, proportions, camera angle, and
  perspective as step n-1; only the dirt/effects/state changes.
- 2–4 tools matching the cleaning method.
- Match the style of past approved sessions when present.

Output strict JSON only — no prose, no fences:

[
  {
    "kind": "state",
    "step": "<short name e.g. '00_super_dirty', '01_dust_off'>",
    "concept": "<short description of this state>",
    "camera": "front-on, no tilt",
    "object_count": "1 (single item, centered)",
    "object_shape": "<silhouette/proportions>",
    "object_position": "centered, fully visible, isolated",
    "per_object_description": "<full visual prompt — what dirt/effects/colors>",
    "negative_prompt": "no text, no logos, no hands, no UI, no room background"
  },
  ...
  {
    "kind": "tool",
    "name": "<tool name>",
    "tool_count": "1",
    "tool_camera": "top-down product render",
    "color": "<color/material>",
    "style": "2D game asset",
    "output": "<tool>.png",
    "per_object_description": "<visual prompt>",
    "negative_prompt": "no text, no logos"
  }
]
"""


def _user_prompt(item_name: str, n_states: int, n_tools: int,
                 hints: str, digest: str, lang: str) -> str:
    if lang == "vi":
        u = (
            f"VẬT PHẨM: {item_name}\n\n"
            f"YÊU CẦU CHÍNH XÁC: {n_states} bước trạng thái + {n_tools} công cụ.\n"
            f"- Bước đầu tiên = trạng thái bẩn nhất (đồ vật bỏ quên hàng chục năm)\n"
            f"- Bước cuối = trạng thái hoàn hảo (sạch như mới)\n"
            f"- Bước 2 PHẢI còn vấn đề chưa giải quyết (KHÔNG sạch)\n"
            f"- Mỗi bước giữa loại bỏ một loại bẩn — quá trình dần dần\n\n"
            f"PHIÊN ĐÃ DUYỆT TRƯỚC (style ref, có thể rỗng):\n{digest}\n\n"
        )
        if hints.strip():
            u += f"GỢI Ý CỦA NGƯỜI DÙNG:\n{hints.strip()}\n\n"
        u += "Xuất JSON ngay."
        return u
    u = (
        f"ITEM: {item_name}\n\n"
        f"EXACT REQUIREMENT: {n_states} state steps + {n_tools} tools.\n"
        f"- First step = worst possible state (object neglected for decades)\n"
        f"- Last step = pristine (like-new, clean)\n"
        f"- Step 2 MUST still have unresolved problems (NOT clean)\n"
        f"- Each intermediate step removes one dirt/damage category — gradual chain\n\n"
        f"PAST APPROVED SESSIONS (style reference, may be empty):\n{digest}\n\n"
    )
    if hints.strip():
        u += f"USER HINTS:\n{hints.strip()}\n\n"
    u += "Output the JSON array now."
    return u


def brainstorm(item_name: str, n_states_hint: int = 5,
               n_tools_hint: int = 3, extra_hints: str = "",
               lang: str = "en") -> list[dict]:
    """Call Gemini Flash text to propose steps. Returns parsed JSON list.
    Enforces exact state-step count, gradual cleaning chain, language."""
    digest = _digest_past_sessions()
    user = _user_prompt(item_name, n_states_hint, n_tools_hint,
                        extra_hints, digest, lang)
    client = _genai_client()
    r = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[_system_prompt(lang), user],
    )
    text = (r.text or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    text = text.strip().rstrip("`").strip()

    try:
        plan = json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Brainstorm returned non-JSON: {e}\n\n{text[:500]}")
    if not isinstance(plan, list):
        raise RuntimeError(f"Brainstorm returned non-array: {type(plan)}")

    # Hard-clamp the counts in case the model overshot or undershot.
    states = [s for s in plan if s.get("kind") == "state"]
    tools = [s for s in plan if s.get("kind") == "tool"]
    if len(states) > n_states_hint:
        states = states[:n_states_hint]
    if len(tools) > n_tools_hint:
        tools = tools[:n_tools_hint]
    return states + tools


def create_session_from_plan(name: str, backend: str, plan: list[dict]) -> dict:
    """Create a new session and pre-populate it with the brainstormed steps."""
    ses = sessions.create(name, backend)
    for step_def in plan:
        kind = step_def.get("kind", "state")
        if kind not in ("state", "tool", "image"):
            kind = "state"
        step_id = sessions.add_step(ses["id"], kind=kind)
        # Strip the kind key so update_fields doesn't choke.
        fields = {k: v for k, v in step_def.items()
                  if k != "kind" and k in sessions.EMPTY_FIELDS}
        sessions.update_fields(ses["id"], step_id, fields)
    return sessions.load(ses["id"])
