#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 模型检测 · 桌面版 2.1

本版改动：
1. 说明列用中文写明失败原因（HTTP 402 · 余额不足…）
2. 「受限」独立成状态与筛选
3. 筛选：全部 / 可用 / 受限 / 不可用 / 专用
4. 结束后自动排序：可用在前，同组按延迟升序
5. 修复选择器全选/清空无反应、标题字体错位，列表字体放大
6. 新增「输出 token 数」检测项 + 结束后的 Token 消耗小窗
7. 版本号按 +0.1 迭代
8. 日间 / 暗夜主题切换
9. 背景图（bg.jpg，自动提亮/压暗，不影响可读性）
10. 工具栏分两行，杜绝按钮被挤成单字
11. 文本模型跑完后询问是否继续检测图片等专用模型
"""

import csv
import ctypes
import json
import os
import queue
import random
import re
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageTk

APP_NAME = "AI 模型检测"
APP_VER = "2.2"
UPDATE_REPO = "peter8889/AI-Model-Availability-Checker"
TELEGRAM = "https://t.me/OiPeter"
USD_RATE = 7.1   # 美元→人民币折算（仅参考）

# ---------------------------------------------------------------- 主题
LIGHT = dict(
    BG="#F5F7FA", CARD="#FFFFFF", BORDER="#E4E8EE", TEXT="#111827", MUTED="#6B7280",
    FAINT="#9CA3AF", SUBTITLE="#374151", INPUT="#FAFBFC", STRIPE="#FAFBFC", CHIP_BG="#EDF0F4", CHIP_FG="#4B5563",
    CHIP_HOVER="#E3E7EC", KNOB_OFF="#CBD5E1", HEAD_BG="#F7F9FB", SEL_BG="#E8F0FE",
    TROUGH="#E9EDF2", DISABLED="#A9BEF5", ACCENT="#2563EB", ACCENT_DARK="#1D4ED8",
    GREEN="#15803D", RED="#B42318", AMBER="#B45309", PURPLE="#7C3AED",
    GREEN_BG="#ECFDF3", RED_BG="#FEF3F2",
)
DARK = dict(
    BG="#11151C", CARD="#1A1F29", BORDER="#2A3140", TEXT="#E8EBF0", MUTED="#98A2B3",
    FAINT="#6B7585", SUBTITLE="#C7D0DC", INPUT="#151A23", STRIPE="#1D232E", CHIP_BG="#242B36", CHIP_FG="#AEB8C6",
    CHIP_HOVER="#2E3644", KNOB_OFF="#3A4351", HEAD_BG="#232A35", SEL_BG="#1E3A5F",
    TROUGH="#232A35", DISABLED="#2E3644", ACCENT="#3B82F6", ACCENT_DARK="#2563EB",
    GREEN="#4ADE80", RED="#F87171", AMBER="#FBBF24", PURPLE="#A78BFA",
    GREEN_BG="#12291C", RED_BG="#2A1517",
)

CUR = dict(LIGHT)


def apply_palette(p):
    globals().update(p)


apply_palette(LIGHT)

# 状态
OK = "可用"
DEGRADED = "受限"
MISSING = "不可用"
NO_ACCESS = "无权限"
RATE = "限流"
UNKNOWN = "未知"
KEY_BAD = "密钥失效"
SKIP = "跳过"

GROUP = {OK: 0, DEGRADED: 1, MISSING: 2, NO_ACCESS: 2, RATE: 2, UNKNOWN: 2, KEY_BAD: 2, SKIP: 3}
BADGE = {
    OK: ("✔", "ok"), DEGRADED: ("!", "limited"), MISSING: ("✖", "bad"),
    NO_ACCESS: ("✖", "bad"), RATE: ("…", "muted"), UNKNOWN: ("?", "muted"),
    KEY_BAD: ("✖", "bad"), SKIP: ("–", "muted"),
}

MISSING_PAT = re.compile(
    r"model[_ ]?not[_ ]?found|no such model|does not exist|invalid model|unknown model"
    r"|not a valid model|model .{0,40}(not exist|not available|not supported|not found)"
    r"|无可用渠道|没有可用渠道|模型不存在|不存在的模型|无效的模型|模型.*?(不可用|不存在|未找到|未开通)",
    re.I,
)
NO_ACCESS_PAT = re.compile(r"permission|not allowed|not authorized|access denied|无权|无权限|未授权|禁止访问", re.I)

ZH_STATUS = {
    400: "请求参数有误", 401: "密钥无效或已过期", 402: "余额不足或未开通付费",
    403: "无权限访问该模型", 404: "模型或接口地址不存在", 405: "请求方法不被支持",
    408: "请求超时", 409: "请求冲突", 413: "请求体过大", 415: "不支持的请求格式",
    422: "参数校验失败", 429: "触发限流或额度已用尽", 500: "上游服务内部错误",
    502: "上游网关错误", 503: "服务不可用或无可用渠道", 504: "上游响应超时", 529: "上游过载",
}
IMAGE_PROMPT = "anime girl with long red hair lying on a bed, soft morning light, illustration"
BANNER_TOP = 0.255   # 横幅纵向焦点（0=顶部，1=底部），换壁纸时调这个
MAX_BODY = 8 * 1024 * 1024


def zh_reason(status, detail=""):
    """把上游英文报错翻成一句中文原因。"""
    raw = detail or ""
    d = raw.lower()
    if status is None:
        if "timed out" in d or "timeout" in d:
            return "请求超时"
        if "certificate" in d or "ssl" in d:
            return "HTTPS 证书校验失败"
        if "refused" in d or "urlopen" in d or "getaddrinfo" in d or "connection" in d:
            return "网络不可达，连接失败"
        return "网络异常"
    if "not a chat model" in d or "not a chat" in d:
        return "不是对话模型"
    if "context length" in d or "maximum context" in d or "too long" in d:
        return "超出上下文长度"
    if "insufficient" in d or "quota" in d or "balance" in d or "credit" in d or "arrears" in d:
        return "余额不足或额度用尽"
    if "rate limit" in d or "too many requests" in d:
        return "触发限流"
    if "invalid api key" in d or "incorrect api key" in d or "invalid_api_key" in d:
        return "密钥无效"
    if "无可用渠道" in raw or "没有可用渠道" in raw:
        return "无可用渠道"
    if "model_not_found" in d or "model not found" in d or "does not exist" in d:
        return "模型不存在"
    if "unsupported" in d or "not supported" in d or "invalid_request" in d or "invalid value" in d:
        return "参数不被支持"
    if "overload" in d or "busy" in d:
        return "上游繁忙，请稍后重试"
    return ZH_STATUS.get(status, f"请求失败（HTTP {status}）")


# ---------------------------------------------------------------- 网络
def http_json(method, url, key, payload=None, timeout=25):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    if key:
        req.add_header("Authorization", "Bearer " + key)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(MAX_BODY)
            return r.status, raw.decode("utf-8", "replace"), (time.perf_counter() - t0) * 1000
    except urllib.error.HTTPError as e:
        try:
            body = e.read(MAX_BODY).decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body, (time.perf_counter() - t0) * 1000
    except Exception as e:
        return None, f"{type(e).__name__}: {e}", (time.perf_counter() - t0) * 1000


def http_stream(url, key, payload, timeout=25):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "text/event-stream")
    if key:
        req.add_header("Authorization", "Bearer " + key)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = (r.headers.get("Content-Type") or "").lower()
            if "event-stream" not in ctype:
                r.read(2048)
                return False, None, "未返回 SSE，可能是假流式"
            while True:
                line = r.readline()
                if not line:
                    return False, None, "流式响应为空"
                s = line.decode("utf-8", "replace").strip()
                if s.startswith("data:") and s[5:].strip() not in ("", "[DONE]"):
                    return True, round((time.perf_counter() - t0) * 1000), ""
    except urllib.error.HTTPError as e:
        try:
            body = e.read(2048).decode("utf-8", "replace")
            return False, None, zh_reason(e.code, err_text(body))
        except Exception:
            return False, None, f"HTTP {e.code}"
    except Exception as e:
        return False, None, "请求超时" if "timed out" in str(e).lower() else str(e)


def err_text(body):
    try:
        obj = json.loads(body)
    except Exception:
        return re.sub(r"\s+", " ", body or "")[:200]
    if isinstance(obj, dict):
        for path in (("error", "message"), ("error", "msg"), ("message",), ("msg",), ("detail",), ("error",)):
            cur = obj
            for p in path:
                cur = cur.get(p) if isinstance(cur, dict) else None
                if cur is None:
                    break
            if isinstance(cur, str) and cur:
                return re.sub(r"\s+", " ", cur)[:200]
    return re.sub(r"\s+", " ", body or "")[:200]


def classify(status, body):
    txt = body or ""
    if status == 200:
        return OK, ""
    if status == 401:
        return KEY_BAD, err_text(txt)
    if status == 404:
        return (MISSING if not NO_ACCESS_PAT.search(txt) else NO_ACCESS), err_text(txt)
    if status == 403:
        return (MISSING if MISSING_PAT.search(txt) else NO_ACCESS), err_text(txt)
    if status == 429:
        return RATE, err_text(txt)
    if status == 400:
        if MISSING_PAT.search(txt):
            return MISSING, err_text(txt)
        if NO_ACCESS_PAT.search(txt):
            return NO_ACCESS, err_text(txt)
        return DEGRADED, err_text(txt)
    if status is None:
        return UNKNOWN, err_text(txt)
    if 500 <= status < 600:
        return UNKNOWN, err_text(txt)
    return UNKNOWN, err_text(txt) or f"HTTP {status}"


def kind_of(model):
    m = model.lower()
    if re.search(r"embed|bge[-_]|gte[-_]|text-embedding|rerank|jina", m):
        return "embed"
    if re.search(r"dall-e|imagen|flux|sd[-_]?xl|midjourney|stable-diffusion|image|kolors|banana", m):
        return "image"
    if re.search(r"whisper|tts|speech|asr|audio|voice|kokoro", m):
        return "audio"
    if re.search(r"sora|veo|kling|runway|video|hailuo", m):
        return "video"
    if re.search(r"moderation|guard", m):
        return "other"
    return "chat"


def build_payload(kind, model, tokens=1, variant="max_tokens"):
    if kind == "embed":
        return {"model": model, "input": "hi"}
    if variant == "max_completion_tokens":
        return {"model": model, "messages": [{"role": "user", "content": "hi"}],
                "max_completion_tokens": tokens, "stream": False}
    return {"model": model, "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": tokens, "temperature": 0, "stream": False}


def parse_tokens(body):
    try:
        u = json.loads(body).get("usage") or {}
        if not isinstance(u, dict):
            return None
        p = u.get("prompt_tokens")
        c = u.get("completion_tokens")
        t = u.get("total_tokens")
        if t is None and (p is not None or c is not None):
            t = (p or 0) + (c or 0)
        if t is None:
            return None
        return {"prompt": p or 0, "completion": c or 0, "total": t}
    except Exception:
        return None


def test_tools(base, key, model, timeout):
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "What's the weather in Beijing? Use the tool."}],
        "tools": [{"type": "function", "function": {
            "name": "get_weather", "description": "Get current weather of a city",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}],
        "tool_choice": "auto", "max_tokens": 64, "temperature": 0, "stream": False,
    }
    status, body, _ = http_json("POST", base.rstrip("/") + "/chat/completions", key, payload, timeout)
    if status != 200:
        return False
    try:
        msg = json.loads(body)["choices"][0]["message"]
        return bool(msg.get("tool_calls"))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 思考参数的"档位模式"映射
# 依据（2026-10 查证）：
#   OpenAI  : reasoning_effort = none/minimal/low/medium/high/xhigh/max
#             GPT-5.1 起默认 none；5.1 之前默认 medium 且不支持 none；
#             minimal 仅初代 GPT-5；xhigh 仅 codex-max 之后；gpt-5-pro 只认 high
#   Qwen    : enable_thinking(bool) + thinking_budget(int)
#             qwen3.5/3.6/3.7/3.8 默认开启；qwen3-vl-plus 默认关闭；
#             带 thinking 后缀的是强制思考模型，关不掉
#   DeepSeek: thinking.type(enabled/disabled) + reasoning_effort = low/high/max
#             默认开启且 effort=high；low/medium/xhigh 都会映射到 high
#   GLM     : thinking.type(enabled/disabled)
#             GLM-5.2+ 另支持 reasoning_effort = max/high/low（默认 max）
#             GLM-5.3 只认 max/high/low，传 disabled 会直接报错
# ---------------------------------------------------------------------------
THINK_MODES = {
    "enum":   "枚举档位（reasoning_effort）",
    "budget": "预算档位（thinking_budget）",
    "switch": "仅开关（thinking.type）",
    "always": "强制思考（无法关闭）",
    "none":   "不支持思考",
}


# 上下文窗口 / 最大输出（token），按模型名前缀匹配，取第一个命中
# 出处：各家 2026-10 公开文档，取不到的写 None 表示未知
SPEC_RULES = [
    # (匹配规则(小写子串), 上下文, 最大输出, 备注)
    (("qwen3.8-max",), 1000000, 65536),
    (("qwen3.8",), 1000000, 65536),
    (("qwen3.7",), 1000000, 32768),
    (("qwen3.6", "qwen3.5"), 262144, 32768),
    (("qwen3-vl",), 262144, 32768),
    (("qwen3-max", "qwen-max"), 262144, 32768),
    (("qwen3",), 131072, 32768),
    (("qwen",), 131072, 8192),

    (("deepseek-v4", "deepseek-flash", "deepseek-pro"), 1000000, 131072),
    (("deepseek-reasoner",), 131072, 65536),
    (("deepseek",), 131072, 8192),

    (("glm-5.3", "glm-5.2", "glm-5.1", "glm-5"), 1000000, 131072),
    (("glm-4.7", "glm-4.6", "glm-4.5"), 200000, 131072),
    (("glm",), 131072, 4096),

    (("gpt-6",), 1000000, 128000),
    (("gpt-5.6", "gpt-5.5", "gpt-5.4"), 400000, 128000),
    (("gpt-5",), 400000, 128000),
    (("o3", "o4"), 200000, 100000),
    (("o1",), 200000, 100000),
    (("gpt-4.1",), 1000000, 32768),
    (("gpt-4o",), 128000, 16384),

    (("claude-fable-5", "claude-opus-5", "claude-sonnet-5", "claude-haiku-5"), 1000000, 128000),
    (("claude",), 200000, 64000),

    (("kimi-k3",), 1000000, 131072),
    (("kimi-k2",), 262144, 32768),
    (("kimi",), 131072, 8192),

    (("hy4",), 1000000, 65536),
    (("hunyuan",), 262144, 32768),

    (("step-5",), 1000000, 131072),
    (("step-3",), 262144, 32768),

    (("minimax-m3.1", "minimax-m3-1", "minimax-m3"), 1000000, 131072),
    (("minimax",), 262144, 32768),

    (("mimo-v2.6",), 1000000, 131072),
    (("mimo",), 262144, 32768),

    (("gemini-4", "gemini-3"), 1000000, 65536),
    (("gemini-2.5",), 1048576, 65536),
    (("gemini",), 1000000, 65536),
]


def model_spec(model):
    """返回 (上下文, 最大输出)，未知返回 (None, None)。"""
    m = (model or "").lower()
    for pats, ctx, out in ((r[0], r[1], r[2]) for r in SPEC_RULES):
        for p in pats:
            if p in m:
                return ctx, out
    return None, None


def fmt_tok(n):
    if not n:
        return "—"
    if n >= 1000000:
        v = n / 1000000
        return f"{v:.0f}M" if abs(v - round(v)) < 0.05 else f"{v:.1f}M"
    if n >= 1000:
        v = n / 1000
        return f"{v:.0f}K" if abs(v - round(v)) < 0.05 else f"{v:.0f}K"
    return str(n)


def spec_text(model):
    ctx, out = model_spec(model)
    if not ctx and not out:
        return "—"
    return f"{fmt_tok(ctx)} / {fmt_tok(out)}"


def resolve_think_mode(base, model):
    """按厂商域名 + 模型名推断思考参数形态。"""
    host = ""
    try:
        host = (urllib.parse.urlsplit(normalize_base(base)).hostname or "").lower()
    except Exception:
        pass
    m = (model or "").lower()

    def mk(mode, levels, params, note, default=None, budgets=None):
        return {"mode": mode, "levels": levels, "params": params, "note": note,
                "default": default or (levels[1] if len(levels) > 1 else (levels[0] if levels else "")),
                "budgets": budgets or {}, "label": THINK_MODES.get(mode, mode)}

    # ---- 通义 / 百炼
    if "qwen" in m or "dashscope" in host or "aliyuncs" in host:
        if "thinking" in m:
            return mk("always", ["on"], [], "带 thinking 后缀，强制思考，无法关闭")
        if any(k in m for k in ("qwen3.5", "qwen3.6", "qwen3.7", "qwen3.8", "qwen3-")):
            return mk("budget", ["off", "low", "medium", "high"],
                      ["enable_thinking", "thinking_budget"],
                      "预算控制；max_tokens 在思考模式上限 32768",
                      default="medium",
                      budgets={"low": 1024, "medium": 4096, "high": 16384})
        if "qwen3-vl" in m:
            return mk("budget", ["off", "low", "medium", "high"],
                      ["enable_thinking", "thinking_budget"],
                      "视觉模型默认关闭思考，需显式开启",
                      default="medium",
                      budgets={"low": 1024, "medium": 4096, "high": 16384})
        return mk("none", [], [], "非思考模型")

    # ---- DeepSeek（deepseek-flash 当前为 V4.1-Flash-1017）
    if "deepseek" in host or m.startswith("deepseek"):
        if "reasoner" in m and "v4" not in m:
            return mk("always", ["low", "high", "max"], ["reasoning_effort"],
                      "旧 reasoner：始终思考，可用 effort 调节力度", default="high")
        return mk("enum", ["off", "low", "high", "max"],
                  ["thinking.type", "reasoning_effort"],
                  "thinking 开关 + effort（low/high/max）；medium 会映射成 high",
                  default="high")

    # ---- 智谱 GLM
    if "bigmodel" in host or "z.ai" in host or m.startswith("glm"):
        if any(k in m for k in ("glm-5.3", "glm-5.4", "glm-5.5", "glm-5.6", "glm-6")):
            return mk("enum", ["low", "high", "max"], ["reasoning_effort"],
                      "5.3 起只认 max/high/low；thinking.type 传 disabled 会直接报错",
                      default="max")
        if any(k in m for k in ("glm-5.2", "glm-5.1", "glm-5")):
            return mk("enum", ["off", "low", "high", "max"], ["thinking.type", "reasoning_effort"],
                      "max/high/low；none 或 minimal 表示放弃思考", default="max")
        if any(k in m for k in ("glm-4.5", "glm-4.6", "glm-4.7")):
            return mk("switch", ["off", "on"], ["thinking.type"],
                      "只有开关，没有分档（模型自行决定思考多深）", default="on")
        return mk("none", [], [], "不支持深度思考")

    # ---- OpenAI 系
    if "openai" in host or m.startswith(("gpt-", "o1", "o3", "o4", "chatgpt")):
        if "pro" in m and m.startswith(("gpt-5-pro", "o3-pro")):
            return mk("enum", ["high"], ["reasoning_effort"], "只支持 high", default="high")
        if any(k in m for k in ("gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-6.1")):
            return mk("enum", ["none", "low", "medium", "high", "xhigh", "max"],
                      ["reasoning_effort"],
                      "GPT-6 家族；max 需 Responses API，Chat Completions 用 high",
                      default="medium")
        if any(k in m for k in ("gpt-6", "gpt-5.6", "gpt-5.5", "gpt-5.4")):
            return mk("enum", ["none", "low", "medium", "high", "xhigh", "max"],
                      ["reasoning_effort"],
                      "GPT-5.4 起支持 none~xhigh；max 需 Responses API",
                      default="medium")
        if m.startswith("gpt-5.1"):
            lv = ["none", "low", "medium", "high"]
            if "codex-max" in m:
                lv = ["none", "low", "medium", "high", "xhigh"]
            return mk("enum", lv, ["reasoning_effort"],
                      "GPT-5.1 默认 none；minimal 已不支持", default="medium")
        if m.startswith("gpt-5"):
            return mk("enum", ["minimal", "low", "medium", "high"], ["reasoning_effort"],
                      "初代 GPT-5：默认 medium，不支持 none", default="medium")
        if m.startswith(("o1", "o3", "o4")):
            return mk("enum", ["low", "medium", "high"], ["reasoning_effort"],
                      "o 系列：默认 medium", default="medium")
        return mk("none", [], [], "非推理模型")

    # ---- Anthropic（Opus / Sonnet / Haiku 5.5）
    if "anthropic" in host or "claude" in m:
        if any(k in m for k in ("haiku", "fable")):
            return mk("budget", ["off", "low", "medium"], ["thinking.budget_tokens"],
                      "小模型预算上限较低", default="low",
                      budgets={"low": 1024, "medium": 4096})
        return mk("budget", ["off", "low", "medium", "high"], ["thinking.budget_tokens"],
                  "预算控制（budget_tokens），没有档位枚举", default="medium",
                  budgets={"low": 2048, "medium": 8192, "high": 16384})

    # ---- 阶跃星辰（Step 5 Preview）
    if "stepfun" in host or m.startswith("step-"):
        return mk("unknown", ["off", "low", "medium", "high"], [],
                  "文档未公开思考档位参数，用通用探测", default="medium")

    # ---- 腾讯混元 Hy4（thinking.type + reasoning_effort low/medium/high，默认 high）
    if "tencentmaas" in host or "tokenhub" in host or m.startswith(("hy4", "hunyuan")):
        if m.startswith("hy3"):
            return mk("switch", ["off", "on"], ["thinking.type"],
                      "hy3 系列默认关闭思考", default="on")
        return mk("enum", ["off", "low", "medium", "high"],
                  ["thinking.type", "reasoning_effort"],
                  "thinking 开关 + effort（low/medium/high），默认 high",
                  default="high")

    # ---- Google Gemini（3.x 起只有 thinking_level，且无法关闭）
    if "googleapis" in host or "generativelanguage" in host or m.startswith("gemini"):
        if m.startswith(("gemini-4", "gemini-3")):
            lv = ["minimal", "low", "medium", "high"]
            if any(k in m for k in ("argon", "3.7", "3.8", "3.1-pro")):
                lv = ["low", "medium", "high"]
            return mk("enum", lv, ["thinking_level"],
                      "3.x 起思考常开（不可关闭），用 thinking_level 分档",
                      default="high")
        return mk("budget", ["off", "low", "medium", "high"], ["thinking_budget"],
                  "2.5 及更早用 thinking_budget 数值", default="medium",
                  budgets={"low": 1024, "medium": 8192, "high": 24576})

    # ---- 小米 MiMo（V2.6 起：thinking.type 开关，effort 名目多但强度不分档）
    if "xiaomimimo" in host or "mimo" in host or m.startswith("mimo-"):
        return mk("switch", ["off", "on"], ["thinking.type"],
                  "默认开启；effort 传 none 关闭，其余取值一律等价于开启"
                  "（minimal→low，xhigh/max/ultra→high，实际不分档）",
                  default="on")

    # ---- MiniMax（M3.1 Flash：思考强制开启，五档 effort，禁止设 none）
    if "minimax" in host or m.startswith("minimax"):
        if any(k in m for k in ("m3.1", "m3-1")):
            return mk("enum", ["low", "medium", "high", "xhigh", "max"],
                      ["reasoning_effort"],
                      "思考强制开启，禁止传 none/关闭；默认 max",
                      default="max")
        return mk("enum", ["off", "low", "medium", "high"], ["reasoning_effort"],
                  "M3 及更早：可按 effort 调节", default="medium")

    # ---- Kimi（K3 默认 max 思考强度，low/high 后续开放）
    if "moonshot" in host or "kimi" in host or m.startswith("kimi"):
        if any(k in m for k in ("k3", "k2-thinking", "thinking")):
            return mk("enum", ["low", "high", "max"], ["reasoning_effort"],
                      "K3 默认 max；low/high 逐步开放", default="max")
        return mk("none", [], [], "普通对话模型")

    return mk("unknown", [], [], "未识别的厂商，将用通用探测")


def think_payload(profile, level=None):
    """把统一的档位翻译成该厂商真正的请求参数（档位值必须用英文）。"""
    mode = profile.get("mode")
    lv = level or profile.get("default") or "medium"
    if mode == "enum":
        if "thinking_level" in profile.get("params", []):
            return {"thinking_level": lv}
        out = {}
        if "thinking.type" in profile.get("params", []):
            out["thinking"] = {"type": "disabled" if lv == "off" else "enabled"}
        if lv != "off" or "reasoning_effort" in profile.get("params", []):
            if lv != "off":
                out["reasoning_effort"] = lv
        return out
    if mode == "budget":
        first = (profile.get("params") or [""])[0]
        bud = profile.get("budgets", {}).get(lv, 4096)
        if first == "thinking.budget_tokens":
            if lv == "off":
                return {"thinking": {"type": "disabled"}}
            return {"thinking": {"type": "enabled", "budget_tokens": bud}}
        if first == "thinking_budget":
            # Gemini 2.5：单独一个 thinking_budget，0 表示关闭
            if lv == "off":
                return {"thinking_budget": 0}
            return {"thinking_budget": bud}
        # Qwen 系：开关 + 预算
        if lv == "off":
            return {"enable_thinking": False}
        return {"enable_thinking": True, "thinking_budget": bud}
    if mode == "switch":
        if "thinking.type" in profile.get("params", []):
            return {"thinking": {"type": "disabled" if lv == "off" else "enabled"}}
        return {"enable_thinking": lv != "off"}
    return {}


THINK_Q = ("请一步一步推理：笼子里有鸡和兔共 10 只，腿共 28 条，鸡和兔各几只？"
           "先写出推理过程，再给结论。")


def _extract_think(obj):
    """从响应里找思维链：reasoning_content / reasoning / thinking 块 / reasoning_tokens。"""
    try:
        ch = (obj.get("choices") or [{}])[0]
        msg = ch.get("message") or {}
    except Exception:
        return "", 0
    u = obj.get("usage") or {}
    rt = u.get("reasoning_tokens")
    if rt is None:
        rt = (u.get("completion_tokens_details") or {}).get("reasoning_tokens")
    rt = int(rt or 0)
    for key in ("reasoning_content", "reasoning", "thinking_content", "reasoning_text"):
        v = msg.get(key)
        if isinstance(v, str) and v.strip():
            return key, len(v.strip()), rt
        if isinstance(v, list) and v:
            txt = "".join(str(x.get("text", "")) if isinstance(x, dict) else str(x) for x in v)
            if txt.strip():
                return key, len(txt.strip()), rt
    th = msg.get("thinking")
    if isinstance(th, str) and th.strip():
        return "thinking", len(th.strip()), rt
    if isinstance(th, dict):
        txt = th.get("content") or th.get("text") or ""
        if str(txt).strip():
            return "thinking", len(str(txt).strip()), rt
    if rt:
        return "reasoning_tokens", rt, rt
    return "", 0, 0


def probe_think(base, key, model, timeout=45):
    """按厂商档位模式探测思考能力；被拒就退回一次普通请求。"""
    url = base.rstrip("/") + "/chat/completions"
    prof = resolve_think_mode(base, model)
    extra = think_payload(prof)
    payload = {"model": model, "messages": [{"role": "user", "content": THINK_Q}],
               "max_tokens": 800, "temperature": 0.2}
    payload.update(extra)

    status, body, _ = http_json("POST", url, key, payload, timeout)
    param_ok = status == 200
    if not param_ok and extra:
        payload = {"model": model, "messages": [{"role": "user", "content": THINK_Q}],
                   "max_tokens": 800, "temperature": 0.2}
        status, body, _ = http_json("POST", url, key, payload, timeout)
    if status != 200:
        return {"ok": False, "reason": f"HTTP {status}", "mode": "", "size": 0,
                "rtokens": 0, "params": False, "usage": {}, "profile": prof}

    try:
        obj = json.loads(body)
    except Exception:
        return {"ok": False, "reason": "返回非 JSON", "mode": "", "size": 0,
                "rtokens": 0, "params": param_ok, "usage": {}, "profile": prof}
    mode, size, rtok = _extract_think(obj)
    usage = obj.get("usage") or {}
    if mode:
        return {"ok": True, "mode": mode, "size": size, "rtokens": rtok,
                "params": param_ok, "usage": usage, "profile": prof}
    return {"ok": False, "reason": "无思考内容", "mode": "", "size": 0, "rtokens": rtok,
            "params": param_ok, "usage": usage, "profile": prof}


def test_model(base, key, model, opts, timeout=25, retries=1):
    kind = kind_of(model)
    res = {"model": model, "kind": kind, "status": UNKNOWN, "http": None, "zh": "",
           "detail": "", "latency": None, "usage": None, "tokens": None,
           "stream": None, "tools": None, "special": False}
    if kind in ("image", "audio", "video", "other"):
        res.update(status=SKIP, zh="专用模型，稍后可单独检测", detail="等待专用检测")
        return res

    path = "/embeddings" if kind == "embed" else "/chat/completions"
    url = base.rstrip("/") + path
    tokens = max(1, int(opts.get("tokens") or 1))
    payload = build_payload(kind, model, tokens)
    status, body, ms = None, "", None
    for attempt in range(retries + 1):
        status, body, ms = http_json("POST", url, key, payload, timeout)
        st, detail = classify(status, body)
        if st == DEGRADED and kind == "chat" and re.search(r"max_tokens", detail or "", re.I):
            payload = build_payload(kind, model, tokens, "max_completion_tokens")
            status, body, ms = http_json("POST", url, key, payload, timeout)
            st, detail = classify(status, body)
        if st not in (UNKNOWN, RATE) or attempt == retries:
            break
        time.sleep(1.0 * (attempt + 1))

    res.update(status=st, http=status, detail=detail, latency=round(ms) if ms else None,
               zh=("" if st == OK else zh_reason(status, detail)))
    if st == OK:
        res["tokens"] = parse_tokens(body)
        res["usage"] = bool(res["tokens"])
    elif st == DEGRADED:
        res["tokens"] = parse_tokens(body)

    if kind == "chat" and st in (OK, DEGRADED):
        if opts.get("stream"):
            ok, ttft, why = http_stream(url, key, dict(payload, stream=True, max_tokens=8), timeout)
            res["stream"] = ok
            if ok and ttft:
                res["ttft"] = ttft
            elif why:
                res["stream_why"] = why
        if opts.get("tools"):
            res["tools"] = test_tools(base, key, model, timeout)
    return res


def test_special(base, key, model, timeout):
    """图片生成类模型：发一次最小出图请求。语音/视频仅标注。"""
    kind = kind_of(model)
    res = {"model": model, "kind": kind, "status": SKIP, "http": None, "zh": "",
           "detail": "", "latency": None, "usage": None, "tokens": None,
           "stream": None, "tools": None, "special": True}
    if kind != "image":
        res.update(zh="语音/视频需专用接口，暂不自动检测", detail="需专用接口")
        return res
    url = base.rstrip("/") + "/images/generations"
    payload = {"model": model, "prompt": IMAGE_PROMPT, "n": 1, "size": "512x512"}
    status, body, ms = http_json("POST", url, key, payload, max(30, timeout * 2))
    if status == 200:
        ok = False
        try:
            data = json.loads(body).get("data") or []
            ok = any(isinstance(d, dict) and (d.get("url") or d.get("b64_json")) for d in data)
        except Exception:
            ok = False
        res.update(status=OK if ok else DEGRADED, http=200,
                   zh="" if ok else "接口成功但没有返回图片数据",
                   latency=round(ms) if ms else None)
        return res
    st, detail = classify(status, body)
    res.update(status=st, http=status, detail=detail, zh=zh_reason(status, detail),
               latency=round(ms) if ms else None)
    return res


BALANCE_RULES = [
    ("api.deepseek.com", "https://api.deepseek.com/user/balance", "deepseek"),
    ("api.moonshot.cn", "https://api.moonshot.cn/v1/users/me/balance", "moonshot"),
    ("api.siliconflow.cn", "https://api.siliconflow.cn/v1/user/info", "siliconflow"),
    ("api.siliconflow.com", "https://api.siliconflow.com/v1/user/info", "siliconflow"),
    ("openrouter.ai", "https://openrouter.ai/api/v1/credits", "openrouter"),
    ("api.stepfun.com", "https://api.stepfun.com/v1/accounts", "generic"),
    ("api.minimaxi.com", "https://api.minimaxi.com/v1/token_plan/remains", "generic"),
    ("api.minimax.io", "https://api.minimax.io/v1/token_plan/remains", "generic"),
    ("open.bigmodel.cn", "https://open.bigmodel.cn/api/monitor/usage/quota/limit", "generic"),
]
NO_API_HOSTS = {
    "dashscope.aliyuncs.com": "阿里云百炼没有公开的余额/用量接口，请到「百炼控制台 → 费用与成本」查看",
    "api.openai.com": "OpenAI 普通项目 Key 查不到余额，需要组织级 admin key",
    "api.anthropic.com": "Anthropic 需要管理员密钥才能查用量",
}


def _flatten(obj, prefix="", depth=0, out=None):
    out = [] if out is None else out
    if depth > 2:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float, str)) and str(v).strip() != "":
                out.append((f"{prefix}{k}", v))
            elif isinstance(v, (dict, list)):
                _flatten(v, f"{prefix}{k}.", depth + 1, out)
    elif isinstance(obj, list) and obj:
        _flatten(obj[0], prefix, depth + 1, out)
    return out


def _fmt_num(v):
    try:
        f = float(v)
        return f"{f:,.2f}"
    except Exception:
        return str(v)


def query_balance(base, key, timeout=8):
    """返回 (服务商, 结论行列表, 接口地址)。注：查不到时会说明原因，不假装成功。"""
    host, netloc = "", ""
    try:
        sp = urllib.parse.urlsplit(normalize_base(base))
        host = (sp.hostname or "").lower()
        netloc = sp.netloc or host
    except Exception:
        pass
    if not host:
        return ("未知", ["请先在上方填写接口地址"], "")

    for k, msg in NO_API_HOSTS.items():
        if k in host:
            return ("无公开接口", [msg], "")

    for pat, url, kind in BALANCE_RULES:
        if pat in host:
            status, body, _ = http_json("GET", url, key, None, timeout)
            if status != 200:
                return ("查询失败", [f"HTTP {status} · {zh_reason(status, err_text(body))}"], url)
            try:
                obj = json.loads(body)
            except Exception:
                return ("返回异常", ["接口返回的不是 JSON"], url)
            if kind == "deepseek":
                bins = obj.get("balance_infos") or []
                if bins:
                    b = bins[0]
                    return ("DeepSeek", [f"总余额：{b.get('total_balance')} {b.get('currency', '')}",
                                         f"赠送：{b.get('granted_balance')} ｜ 充值：{b.get('topped_up_balance')}"], url)
            if kind == "moonshot":
                d = obj.get("data") or {}
                return ("Kimi / 月之暗面", [f"可用余额：{_fmt_num(d.get('available_balance'))} 元",
                                            f"代金券：{_fmt_num(d.get('voucher_balance'))} ｜ 现金：{_fmt_num(d.get('cash_balance'))}"], url)
            if kind == "siliconflow":
                d = obj.get("data") or {}
                return ("硅基流动", [f"总余额：{d.get('totalBalance', d.get('balance'))} 元",
                                     f"充值余额：{d.get('chargeBalance')}"], url)
            if kind == "openrouter":
                d = obj.get("data") or {}
                try:
                    left = float(d.get("total_credits", 0)) - float(d.get("total_usage", 0))
                except Exception:
                    left = None
                lines = [f"剩余额度：${left:,.2f}" if left is not None else "剩余额度：—"]
                return ("OpenRouter", lines, url)
            # 通用：列出数字字段
            rows = [(k2, v) for k2, v in _flatten(obj) if isinstance(v, (int, float))]
            return (host, [f"{k2}: {_fmt_num(v)}" for k2, v in rows[:6]] or ["接口返回成功，但没有找到金额字段"], url)

    # 中转站兜底（one-api / new-api 常见路径）
    scheme = "https" if base.lower().startswith("https") else "http"
    for path in ("/api/usage/token", "/api/user/self", "/api/user/info"):
        url = f"{scheme}://{netloc}{path}"
        status, body, _ = http_json("GET", url, key, None, timeout)
        if status == 200:
            try:
                obj = json.loads(body)
            except Exception:
                continue
            if obj.get("success") is False:
                continue
            d = obj.get("data") or {}
            if isinstance(d, dict) and d.get("unlimited_quota"):
                return ("中转站（不限额度）", [f"接口：{path}", "该令牌为无限额度"], url)
            if isinstance(d, dict) and d.get("total_available") is not None:
                return ("中转站",
                        [f"剩余额度：{_fmt_num(d.get('total_available'))}",
                         f"总额度：{_fmt_num(d.get('total_granted'))} ｜ 已用：{_fmt_num(d.get('total_used'))}",
                         f"接口：{path}"], url)
            rows = [(k2, v) for k2, v in _flatten(obj) if isinstance(v, (int, float))]
            if rows:
                return ("中转站", [f"接口：{path}"] + [f"{k2}: {_fmt_num(v)}" for k2, v in rows[:6]], url)

    return ("未识别", ["该服务商没有公开的余额接口，无法查询",
                       "可到服务商控制台查看，或在中转站后台查看令牌额度"], "")


def parse_ver(v):
    nums = re.findall(r"\d+", v or "")
    return tuple(int(x) for x in nums[:3]) or (0,)


def fetch_latest_release(timeout=8):
    url = f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest"
    req = urllib.request.Request(url, headers={
        "User-Agent": "ModelChecker",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    return d.get("tag_name", ""), d.get("html_url", ""), d.get("body", "")


def fetch_model_list(base, key, timeout=25):
    status, body, _ = http_json("GET", base.rstrip("/") + "/models", key, timeout=timeout)
    if status != 200:
        raise RuntimeError(f"获取模型列表失败：HTTP {status} · {zh_reason(status, err_text(body))}")
    try:
        obj = json.loads(body)
    except Exception:
        raise RuntimeError(f"/models 返回的不是合法 JSON（开头 120 字符）：{body[:120]}")
    items = (obj.get("data") or obj.get("models") or []) if isinstance(obj, dict) else []
    out = []
    for it in items:
        if isinstance(it, dict) and it.get("id"):
            out.append(it["id"])
        elif isinstance(it, str):
            out.append(it)
    if not out:
        raise RuntimeError("/models 返回了空列表，请手动填写模型名")
    return out


PRICES = {"by_id": {}, "by_name": {}, "count": 0, "ts": ""}


def norm_key(name):
    x = (name or "").lower()
    x = re.sub(r"-(free|latest|preview)$", "", x)
    x = re.sub(r"-(20\d{6})$", "", x)
    x = re.sub(r"-20\d{2}-\d{2}-\d{2}$", "", x)
    x = re.sub(r"[^a-z0-9]+", "-", x).strip("-")
    return x


def fetch_prices(timeout=10):
    """从 traktoken.com 拉公开价格（美元 / 百万 token）。"""
    status, body, _ = http_json("GET", "https://www.traktoken.com/api/prices", "", None, timeout)
    if status != 200:
        raise RuntimeError(f"HTTP {status}")
    obj = json.loads(body)
    rows = obj.get("data") or []
    by_id, by_name = {}, {}
    for it in rows:
        if not isinstance(it, dict):
            continue
        pin, pout = it.get("input_price"), it.get("output_price")
        if pin is None or pout is None:
            continue
        rec = (float(pin), float(pout), it.get("provider") or "")
        if it.get("id"):
            by_id[norm_key(it["id"])] = rec
        if it.get("name"):
            by_name[norm_key(it["name"])] = rec
    return {"by_id": by_id, "by_name": by_name, "count": len(by_id),
            "ts": time.strftime("%H:%M")}


def price_data(model):
    """返回 (输入价, 输出价) 人民币/百万 token，查不到返回 None。"""
    if not PRICES["by_id"]:
        return None
    k = norm_key(model)
    rec = PRICES["by_id"].get(k) or PRICES["by_name"].get(k)
    if rec is None:
        best = None
        for src in (PRICES["by_id"], PRICES["by_name"]):
            for cand, v in src.items():
                if cand and (cand.startswith(k) or k.startswith(cand)):
                    if best is None or len(cand) > len(best[0]):
                        best = (cand, v)
        rec = best[1] if best else None
    if rec is None:
        return None
    return rec[0] * USD_RATE, rec[1] * USD_RATE


def price_for(model):
    pd = price_data(model)
    if not pd:
        return ""
    return f"输入¥{pd[0]:.1f} / 输出¥{pd[1]:.1f}"


CARWASH_SCENES = [
    ("车停在家楼下", "车在家"),
    ("车就停在小区车位里", "车在车位"),
    ("车已经脏得看不出颜色了，停在院子里", "车在院子"),
    ("车停在自家车库", "车在车库"),
]
CARWASH_DIST = (30, 50, 80, 120, 200, 300)
CARWASH_WEATHER = ["", "，今天还下着小雨", "，外面三十八度高温", "，昨晚刚下过雪"]


def carwash_pick():
    """随机生成一道洗车题，避免模型背固定答案。"""
    scene, tag = random.choice(CARWASH_SCENES)
    dist = random.choice(CARWASH_DIST)
    wx = random.choice(CARWASH_WEATHER)
    q = (f"我想去洗车，洗车店离我家只有 {dist} 米{wx}，{scene}。"
         f"我应该开车过去还是走过去？请直接给结论，一句话说清理由。")
    return q, f"{dist} 米 · {tag}"


def carwash_verdict(text):
    """粗判回答：提到开车=对，只提走路=错。仅作彩蛋，不代表模型能力。"""
    t = (text or "").strip()
    low = t.lower()
    drive = any(k in low for k in ("开车", "驾车", "开过去", "把车开", "drive"))
    walk = any(k in low for k in ("走路", "步行", "走过去", "walk"))
    if any(k in low for k in ("应该开车", "开车过去", "开车去", "把车开", "需要开车", "得开车", "drive the car")):
        return True, "开窍了：要洗的是车 ✅"
    if drive and not walk:
        return True, "提到开车 ✅"
    if drive and walk:
        return True, "提到开车（也说了人走过去）"
    if walk:
        return False, "翻车了：让车主走路回家 🚶"
    return None, "没给出明确结论 🤔"


def ask_carwash(base, key, model, timeout=30):
    url = base.rstrip("/") + "/chat/completions"
    q, tag = carwash_pick()
    payload = {"model": model, "messages": [{"role": "user", "content": q}],
               "max_tokens": 300, "temperature": 0.5}
    status, body, ms = http_json("POST", url, key, payload, timeout)
    if status != 200:
        return {"model": model, "ok": None, "text": f"HTTP {status}", "ms": ms,
                "why": "请求失败", "q": q, "tag": tag}
    try:
        obj = json.loads(body)
        msg = (obj.get("choices") or [{}])[0].get("message") or {}
        text = msg.get("content") or ""
        if not text and msg.get("reasoning_content"):
            text = msg["reasoning_content"]
    except Exception:
        return {"model": model, "ok": None, "text": "返回解析失败", "ms": ms,
                "why": "解析失败", "q": q, "tag": tag}
    ok, why = carwash_verdict(text)
    return {"model": model, "ok": ok, "text": text.strip(), "ms": ms,
            "why": why, "q": q, "tag": tag}


def think_text(r):
    th = r.get("think")
    if th is None:
        return "未检测"
    prof = th.get("profile") or {}
    tail = ""
    if prof.get("mode") in ("switch", "always"):
        tail = f" ｜ 该厂商{THINK_MODES.get(prof['mode'], '')}，无法分档"
    if th.get("ok"):
        mode = th.get("mode") or ""
        size = th.get("size") or 0
        rt = th.get("rtokens") or 0
        if mode == "reasoning_tokens":
            core = f"{size:,} reasoning tokens"
        else:
            core = f"{mode}，{size:,} 字符"
            if rt:
                core += f"，思考 {rt:,} tokens"
        how = "思考参数被接受" if th.get("params") else "参数被拒，模型自带思维链"
        return f"支持（{core}；{how}）{tail}"
    return f"不支持（{th.get('reason') or '无思考内容'}）{tail}"


def cost_of(r):
    """按单价估算本次消耗金额（元）。"""
    t = r.get("tokens")
    pd = price_data(r["model"])
    if not t or not pd:
        return None
    return t["prompt"] / 1e6 * pd[0] + t["completion"] / 1e6 * pd[1]


def M_host(url):
    try:
        u = normalize_base(url)
        if not u:
            return "未填写"
        return urllib.parse.urlsplit(u).hostname or u
    except Exception:
        return "未填写"


def normalize_base(url):
    url = (url or "").strip().rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")]
    if url and not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url


def resource_path(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def scrub_secrets(text):
    """把日志里的密钥类信息打码，避免崩溃日志泄漏 Key。"""
    t = text or ""
    t = re.sub(r"sk-[A-Za-z0-9_\-]{6,}", "sk-***", t)
    t = re.sub(r"(?i)(authorization[^\n]{0,30})", r"\1 <masked>", t)
    t = re.sub(r"(?i)((?:api[_-]?key|token|access_token)=)[^&\s\"']+", r"\1***", t)
    return t


def write_crash(exc, val, tb):
    import traceback
    try:
        d = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "ModelChecker")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "crash.log")
        buf = "".join(traceback.format_exception(exc, val, tb))
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
            f.write(scrub_secrets(buf))
        return p
    except Exception:
        return ""


_BG_SRC = None


def load_bg_source():
    global _BG_SRC
    if _BG_SRC is None:
        try:
            _BG_SRC = Image.open(resource_path("bg.jpg")).convert("RGB")
        except Exception:
            _BG_SRC = False
    return _BG_SRC or None


CARDS = []
_CARD_CACHE = {}


def load_cards():
    global CARDS
    if CARDS:
        return CARDS
    try:
        with open(resource_path(os.path.join("assets", "cards.json")), encoding="utf-8") as f:
            CARDS = json.load(f)
    except Exception:
        CARDS = []
    return CARDS


def card_image(name, local=""):
    key = local or ("card:" + name)
    if key in _CARD_CACHE:
        return _CARD_CACHE[key]
    path = local
    if not path:
        for c in load_cards():
            if c.get("name") == name:
                path = resource_path(os.path.join("assets", "cards", c["file"]))
                break
    im = None
    if path:
        try:
            im = Image.open(path).convert("RGB")
            im.load()
        except Exception:
            im = None
    _CARD_CACHE[key] = im
    return im


_FONT_CACHE = {}
FONT_FILE = os.path.join(os.environ.get("WINDIR", r"C://Windows"), "Fonts", "msyh.ttc")
CHIP_H = 34
CHIP_GAP = 9


def ui_font(size):
    if size not in _FONT_CACHE:
        try:
            _FONT_CACHE[size] = ImageFont.truetype(FONT_FILE, size)
        except Exception:
            _FONT_CACHE[size] = ImageFont.load_default()
    return _FONT_CACHE[size]


def draw_chips(im, labels):
    """把按钮烧进背景：半透明深色胶囊 + 白字，返回可点击矩形。"""
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    font = ui_font(14)
    rects, x2, y = [], im.width - 18, 18
    for name, key in labels:
        tw = d.textlength(name, font=font)
        bw = max(88, int(tw) + 28)
        x1 = x2 - bw
        d.rounded_rectangle((x1, y, x2, y + CHIP_H), radius=10, fill=(11, 16, 25, 122))
        d.text((x1 + bw / 2, y + CHIP_H / 2 + 1), name, font=font, fill=(255, 255, 255, 238), anchor="mm")
        rects.append((x1, y, x2, y + CHIP_H, key))
        y += CHIP_H + CHIP_GAP
    out = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
    return out, rects


def fit_banner(img, w, h, plate_w=400, labels=None):
    """铺满整条横幅（cover，实时跟随窗口宽度），不留任何白边。"""
    ratio = max(w / img.width, h / img.height)
    nw, nh = max(w, int(img.width * ratio) + 1), max(h, int(img.height * ratio) + 1)
    im = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - w) // 2
    top = 0 if nh > h else (nh - h) // 2       # 顶部对齐，优先保住人物头部
    im = im.crop((left, top, left + w, top + h))

    # 标题底板：只在文字区域压暗，其余保持画面原样
    pw = max(180, min(plate_w, w - 32))
    ph = max(96, min(120, h - 18))
    ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(ov).rounded_rectangle((16, 10, 16 + pw, 10 + ph), radius=18,
                                         fill=(10, 15, 23, 165))
    out = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
    return draw_chips(out, labels) if labels else (out, [])


def build_bg_image(w, h, dark=False):
    """整幅横幅：按宽度铺满，纵向对准人物脸部，保持原图亮度。"""
    src = load_bg_source()
    if src is None or w < 10 or h < 10:
        return Image.new("RGB", (max(1, w), max(1, h)), BG)
    ratio = max(w / src.width, h / src.height)
    bw, bh = max(w, int(src.width * ratio) + 2), max(h, int(src.height * ratio) + 2)
    im = src.resize((bw, bh), Image.LANCZOS)
    top = max(0, min(bh - h, int(bh * BANNER_TOP)))
    im = im.crop(((bw - w) // 2, top, (bw - w) // 2 + w, top + h))
    if dark:
        im = Image.blend(im, Image.new("RGB", (w, h), CARD), 0.62)
    # 左侧文字区轻微压浅，右侧与画面本身保持不变
    grad = Image.new("L", (1, w))
    for x in range(w):
        t = min(1.0, x / (w * 0.42))
        grad.putpixel((0, x), int(205 * (1 - t) ** 1.3))
    im = Image.composite(Image.new("RGB", (w, h), BG), im, grad.resize((w, h)))
    # 底部淡出到页面底色
    fade = max(10, int(h * 0.18))
    m2 = Image.new("L", (1, h))
    for y in range(h):
        if y < h - fade:
            m2.putpixel((0, y), 0)
        else:
            m2.putpixel((0, y), int(255 * ((y - (h - fade)) / fade) ** 1.5))
    return Image.composite(Image.new("RGB", (w, h), BG), im, m2.resize((w, h)))


class VScroll(tk.Frame):
    """竖向滚动容器：窗口再矮也不会把里面的控件压扁。"""

    def __init__(self, parent, bg=BG, width=None):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, width=width or 10, height=90)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _wheel(self, e):
        first, last = self.canvas.yview()
        if first <= 0.0 and last >= 1.0:
            return
        self.canvas.yview_scroll(int(-e.delta / 120), "units")


# ---------------------------------------------------------------- 小部件
def center_on(win, parent):
    """把弹窗放到父窗口中间。"""
    try:
        win.update_idletasks()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        w, h = win.winfo_width(), win.winfo_height()
        x = px + max(0, (pw - w) // 2)
        y = py + max(0, (ph - h) // 3)
        win.geometry(f"+{x}+{y}")
    except Exception:
        pass


class Toggle(tk.Frame):
    def __init__(self, parent, text, variable, bg=CARD, font=None):
        super().__init__(parent, bg=bg)
        self.var = variable
        self.canvas = tk.Canvas(self, width=38, height=20, bg=bg, highlightthickness=0, cursor="hand2")
        self.canvas.pack(side="left", pady=1)
        self.label = tk.Label(self, text=text, bg=bg, fg=TEXT, font=font or ("Segoe UI", 9), cursor="hand2")
        self.label.pack(side="left", padx=(7, 0))
        self.canvas.bind("<Button-1>", self._toggle)
        self.label.bind("<Button-1>", self._toggle)
        self.var.trace_add("write", lambda *a: self._render())
        self._render()

    def _toggle(self, _=None):
        self.var.set(not self.var.get())

    def _render(self):
        c = self.canvas
        c.delete("all")
        fill = ACCENT if self.var.get() else KNOB_OFF
        c.create_oval(1, 3, 17, 19, fill=fill, outline=fill)
        c.create_oval(21, 3, 37, 19, fill=fill, outline=fill)
        c.create_rectangle(9, 3, 29, 19, fill=fill, outline=fill)
        x = 22 if self.var.get() else 2
        c.create_oval(x, 4, x + 14, 18, fill="#FFFFFF", outline="#FFFFFF")


class StatCard(tk.Frame):
    def __init__(self, parent, title, color=None, font_family="Segoe UI"):
        super().__init__(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        self.v = tk.Label(self, text="—", bg=CARD, fg=color or TEXT,
                          font=(font_family, 18, "bold"), anchor="w")
        self.v.pack(fill="x", padx=12, pady=(8, 0))
        self.t = tk.Label(self, text=title, bg=CARD, fg=MUTED, font=(font_family, 9), anchor="w")
        self.t.pack(fill="x", padx=12, pady=(1, 8))

    def set(self, text):
        self.v.configure(text=str(text))


class Hero(tk.Canvas):
    def __init__(self, parent, app, height=168):
        super().__init__(parent, height=height, highlightthickness=0, bd=0, bg=BG)
        self.app = app
        self._img = None
        self._job = None
        self._busy = False
        self._chips = []
        self.bind("<Configure>", self._on_resize)
        self.bind("<Button-1>", self._on_click)
        self.bind("<Motion>", self._on_motion)

    def _hit(self, x, y):
        for x1, y1, x2, y2, key in getattr(self, "_chips", []):
            if x1 <= x <= x2 and y1 <= y <= y2:
                return key
        return None

    def _on_motion(self, e):
        try:
            self.configure(cursor="hand2" if self._hit(e.x, e.y) else "")
        except Exception:
            pass

    def _on_click(self, e):
        key = self._hit(e.x, e.y)
        if key == "bg":
            self.app.pick_bg()
        elif key == "theme":
            self.app.toggle_theme()
        elif key == "about":
            self.app.show_about()

    def _on_resize(self, _=None):
        if self._busy:
            return
        # 拖动窗口时只重置计时器，停手后再重绘一次
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
        self._job = self.after(140, self.render)

    def render(self):
        if getattr(self, "_rendering", False):
            return
        self._rendering = True
        try:
            self._render_once()
        finally:
            self._rendering = False

    def _render_once(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 60:
            return
        f = self.app.f
        card = card_image(self.app.card, self.app.local_bg)
        if card is not None:
            # 背景框高度跟随图片原始比例，窗口缩放时不拉伸
            # 严格按图片比例算高度：窗口多宽，图就多高，画面不被放大裁切
            want = max(150, min(300, round(w * card.height / card.width)))
            if abs(want - h) > 2:
                self._busy = True
                try:
                    self.configure(height=want)
                finally:
                    self._busy = False
                h = want
            if getattr(self, "_tw_font", None) is None:
                self._tw_font = tkfont.Font(family=f, size=18, weight="bold")
            plate_w = self._tw_font.measure("AI 模型可用性检测") + 34
            labels = [("背景", "bg"),
                      ("日间模式" if self.app.dark else "夜间模式", "theme"),
                      ("关于", "about")]
            pil, self._chips = fit_banner(card, w, h, plate_w, labels)
            self._img = ImageTk.PhotoImage(pil)
        else:
            labels = [("背景", "bg"),
                      ("日间模式" if self.app.dark else "夜间模式", "theme"),
                      ("关于", "about")]
            pil, self._chips = draw_chips(build_bg_image(w, h, self.app.dark), labels)
            self._img = ImageTk.PhotoImage(pil)
        self.delete("all")
        self.create_image(0, 0, image=self._img, anchor="nw")
        self.create_text(27, 27, anchor="nw", text="AI 模型可用性检测", fill="#0A1018", font=(f, 18, "bold"))
        self.create_text(26, 26, anchor="nw", text="AI 模型可用性检测", fill="#FFFFFF", font=(f, 18, "bold"))
        self.create_text(27, 64, anchor="nw", fill="#0A1018", font=(f, 10),
                         text="连通性 · 模型真实性 · 延迟 · 能力")
        self.create_text(26, 63, anchor="nw", fill="#FFFFFF", font=(f, 10),
                         text="连通性 · 模型真实性 · 延迟 · 能力")
        # 绿色安全标识
        pill = "本地运行 · 密钥不上传"
        pf = tkfont.Font(family=f, size=9)
        pw = pf.measure(pill) + 28
        self.create_polygon(*self._pill_pts(26, 88, 26 + pw, 114, 13), smooth=True,
                            fill=GREEN_BG, outline=GREEN_BG)
        self.create_text(26 + pw / 2, 101, text=pill, fill=GREEN, font=(f, 9), anchor="center")

        self.create_text(w - 18, h - 10, anchor="se", text=f"v{APP_VER}", fill="#E6EDF6", font=(f, 8))

    @staticmethod
    def _pill_pts(x1, y1, x2, y2, r):
        return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
                x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]


class ModelPicker(tk.Toplevel):
    """可搜索、可多选；全选/清空/反选即时生效。"""

    def __init__(self, parent, models, picked, font_family="Segoe UI"):
        super().__init__(parent)
        self.title("选择模型")
        self.configure(bg=BG)
        self.geometry("620x700")
        self.minsize(500, 500)
        self.transient(parent)
        self.grab_set()
        self.f = font_family
        self.result = None
        self.models = list(models)
        self.picked = set(picked) & set(models)
        self._visible = []

        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=18, pady=(16, 8))
        tk.Label(head, text=f"共 {len(self.models)} 个模型", bg=BG, fg=TEXT,
                 font=(self.f, 12, "bold")).pack(anchor="w")
        tk.Label(head, text="Ctrl / Shift 多选，双击单条切换；搜索框支持模糊筛选", bg=BG, fg=MUTED,
                 font=(self.f, 9)).pack(anchor="w", pady=(2, 0))

        box = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        top = tk.Frame(box, bg=CARD)
        top.pack(fill="x", padx=10, pady=(10, 6))
        tk.Label(top, text="搜索", bg=CARD, fg=MUTED, font=(self.f, 9)).pack(side="left")
        self.kw = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.kw, font=(self.f, 10))
        e.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.kw.trace_add("write", lambda *a: self._refill())
        e.focus_set()

        wrap = tk.Frame(box, bg=CARD)
        wrap.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.lb = tk.Listbox(wrap, selectmode="extended", activestyle="none",
                             font=(self.f, 12), bd=0, highlightthickness=0,
                             selectbackground=SEL_BG, selectforeground=TEXT,
                             bg=CARD, fg=TEXT, exportselection=False)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.lb.yview)
        self.lb.configure(yscrollcommand=sb.set)
        self.lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.lb.bind("<<ListboxSelect>>", lambda e: self._count())
        self.lb.bind("<Double-1>", self._toggle_at)

        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", side="bottom", padx=18, pady=(0, 16))
        box.pack(fill="both", expand=True, padx=18, pady=(0, 8))
        for txt, cmd in (("全选", self._all), ("清空", self._none), ("反选", self._invert)):
            ttk.Button(foot, text=txt, style="Ghost.TButton", width=7, command=cmd).pack(side="left", padx=(0, 6))
        self.lbl = tk.Label(foot, text="", bg=BG, fg=MUTED, font=(self.f, 9))
        self.lbl.pack(side="left", padx=(10, 0))
        ttk.Button(foot, text="确定", style="Primary.TButton", width=8, command=self._ok).pack(side="right")
        ttk.Button(foot, text="取消", style="Ghost.TButton", width=7, command=self.destroy).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda e: self.destroy())
        self._refill()
        self._center(parent)

    def _center(self, parent):
        self.update_idletasks()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px + max(0, (pw - w) // 2)}+{py + max(0, (ph - h) // 3)}")

    def _sync_picked(self):
        sel = {self.lb.get(i) for i in self.lb.curselection()}
        for m in self._visible:
            if m in sel:
                self.picked.add(m)
            else:
                self.picked.discard(m)

    def _refill(self, sync=True):
        if sync:
            self._sync_picked()
        kw = self.kw.get().strip().lower()
        self._visible = [m for m in self.models if kw in m.lower()] if kw else list(self.models)
        self.lb.delete(0, "end")
        for m in self._visible:
            self.lb.insert("end", m)
        for i, m in enumerate(self._visible):
            if m in self.picked:
                self.lb.selection_set(i)
        self._count()

    def _count(self):
        self.lbl.configure(text=f"已选 {len(self.picked)} 个")

    def _all(self):
        self._sync_picked()
        self.picked |= set(self._visible)
        self._refill(sync=False)

    def _none(self):
        self.picked.clear()
        self._refill(sync=False)

    def _invert(self):
        self._sync_picked()
        for m in self._visible:
            self.picked.symmetric_difference_update({m})
        self._refill(sync=False)

    def _toggle_at(self, event):
        i = self.lb.nearest(event.y)
        if i < 0 or i >= len(self._visible):
            return
        m = self._visible[i]
        if m in self.picked:
            self.picked.discard(m)
        else:
            self.picked.add(m)
        self._refill(sync=False)

    def _ok(self):
        self._sync_picked()
        self.result = [m for m in self.models if m in self.picked]
        self.destroy()


class TokenReport(tk.Toplevel):
    """Token 消耗小窗，可关闭。"""

    def __init__(self, parent, rows, font_family="Segoe UI"):
        super().__init__(parent)
        self.title("本次用量统计")
        self.configure(bg=BG)
        self.geometry("740x540")
        self.transient(parent)
        self.f = font_family
        self.rows = [r for r in rows if r.get("tokens")]

        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=18, pady=(16, 8))
        total = sum(r["tokens"]["total"] for r in self.rows)
        pt = sum(r["tokens"]["prompt"] for r in self.rows)
        ct = sum(r["tokens"]["completion"] for r in self.rows)
        costs = [c for c in (cost_of(r) for r in self.rows) if c is not None]
        fee = f"· 估算 ¥{sum(costs):.4f}" if costs else "· 部分模型无单价，未估算"
        tk.Label(head, text=f"本次共消耗 {total:,} tokens", bg=BG, fg=TEXT,
                 font=(self.f, 13, "bold")).pack(anchor="w")
        tk.Label(head, text=f"提示 {pt:,} · 生成 {ct:,} {fee} · 数据来自上游返回的 usage",
                 bg=BG, fg=MUTED, font=(self.f, 9)).pack(anchor="w", pady=(2, 0))

        box = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        wrap = tk.Frame(box, bg=CARD)
        wrap.pack(fill="both", expand=True, padx=1, pady=1)
        cols = ("model", "p", "c", "t", "cost")
        self.tv = ttk.Treeview(wrap, columns=cols, show="headings")
        for c, txt, w, anc in (("model", "模型", 340, "w"), ("p", "提示", 80, "e"),
                               ("c", "生成", 80, "e"), ("t", "合计", 90, "e"),
                               ("cost", "估算 ¥", 90, "e")):
            self.tv.heading(c, text=txt)
            self.tv.column(c, width=w, anchor=anc, stretch=(c == "model"))
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=sb.set)
        self.tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for r in sorted(self.rows, key=lambda x: -x["tokens"]["total"]):
            t = r["tokens"]
            c = cost_of(r)
            self.tv.insert("", "end", values=(r["model"], f"{t['prompt']:,}",
                                              f"{t['completion']:,}", f"{t['total']:,}",
                                              f"{c:.4f}" if c is not None else "—"))

        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", side="bottom", padx=18, pady=(0, 16))
        box.pack(fill="both", expand=True, padx=18, pady=(0, 10))
        ttk.Button(foot, text="复制明细", style="Ghost.TButton",
                   command=self._copy).pack(side="left")
        ttk.Button(foot, text="关闭", style="Primary.TButton", width=8,
                   command=self.destroy).pack(side="right")
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(10, lambda: center_on(self, parent))

    def _copy(self):
        lines = ["模型\t提示\t生成\t合计\t估算元"]
        for r in sorted(self.rows, key=lambda x: -x["tokens"]["total"]):
            t = r["tokens"]
            c = cost_of(r)
            lines.append(f"{r['model']}\t{t['prompt']}\t{t['completion']}\t{t['total']}\t"
                         f"{f'{c:.4f}' if c is not None else ''}")
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))


# ---------------------------------------------------------------- 主窗口
class CardPicker(tk.Toplevel):
    """选择内置背景，或从本地选一张图。"""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("选择背景")
        self.configure(bg=BG)
        self.geometry("820x470")
        self.minsize(700, 400)
        self.transient(app)
        self.grab_set()
        self.f = app.f
        self.cards = load_cards()
        self.local = ""
        self._img = None

        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=18, pady=(16, 8))
        tk.Label(head, text="内置背景", bg=BG, fg=TEXT, font=(self.f, 12, "bold")).pack(anchor="w")
        tk.Label(head, text="按原图比例铺满，不拉伸；左侧自动压一层底色，保证标题可读",
                 bg=BG, fg=MUTED, font=(self.f, 9)).pack(anchor="w", pady=(2, 0))

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=18)
        body.columnconfigure(0, minsize=190)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        box = tk.Frame(body, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        box.grid(row=0, column=0, sticky="nsw", padx=(0, 12))
        self.lb = tk.Listbox(box, width=18, font=(self.f, 11), bd=0, highlightthickness=0,
                             selectbackground=SEL_BG, selectforeground=TEXT,
                             bg=CARD, fg=TEXT, activestyle="none", exportselection=False)
        self.lb.pack(fill="both", expand=True, padx=6, pady=6)
        for c in self.cards:
            self.lb.insert("end", c["name"])
        if not self.cards:
            self.lb.insert("end", "默认背景")
        self.lb.bind("<<ListboxSelect>>", lambda e: self._preview())
        self.lb.selection_set(0)

        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="nsew")
        self.pv = tk.Label(right, bg=CARD, fg=MUTED, font=(self.f, 9),
                           highlightbackground=BORDER, highlightthickness=1, text="选择左侧背景预览")
        self.pv.pack(fill="both", expand=True)

        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", side="bottom", padx=18, pady=(10, 16))
        ttk.Button(foot, text="从本地选择图片…", style="Ghost.TButton",
                   command=self._pick_local).pack(side="left")
        ttk.Button(foot, text="应用", style="Primary.TButton", width=8,
                   command=self._apply).pack(side="right")
        ttk.Button(foot, text="取消", style="Ghost.TButton", width=7,
                   command=self.destroy).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda e: self.destroy())
        self.after(10, lambda: center_on(self, app))
        self.after(60, self._preview)

    def _current(self):
        sel = self.lb.curselection()
        if not sel or not self.cards:
            return ""
        return self.cards[sel[0]]["name"]

    def _preview(self):
        name = self._current()
        im = card_image(name) if name else None
        if im is None:
            self.pv.configure(image="", text="默认背景（未选择内置图）")
            return
        pw = max(320, self.pv.winfo_width() - 4)
        ph = max(80, self.pv.winfo_height() - 4)
        r = min(pw / im.width, ph / im.height)
        im2 = im.resize((max(1, int(im.width * r)), max(1, int(im.height * r))), Image.LANCZOS)
        self._img = ImageTk.PhotoImage(im2)
        self.pv.configure(image=self._img, text="")

    def _pick_local(self):
        path = filedialog.askopenfilename(
            title="选择背景图片", filetypes=[("图片", "*.png *.jpg *.jpeg *.webp *.bmp")])
        if not path:
            return
        im = card_image("", path)
        if im is None:
            messagebox.showerror(APP_NAME, "这张图片打不开，换一张试试")
            return
        self.local = path
        pw = max(320, self.pv.winfo_width() - 4)
        ph = max(80, self.pv.winfo_height() - 4)
        r = min(pw / im.width, ph / im.height)
        im2 = im.resize((max(1, int(im.width * r)), max(1, int(im.height * r))), Image.LANCZOS)
        self._img = ImageTk.PhotoImage(im2)
        self.pv.configure(image=self._img, text="")

    def _apply(self):
        if self.local:
            self.app.apply_local(self.local)
        else:
            name = self._current()
            if name:
                self.app.apply_card(name)
        self.destroy()


class EggDialog(tk.Toplevel):
    """检测完成后的小彩蛋：洗车陷阱题。"""

    def __init__(self, app, rows):
        super().__init__(app)
        self.app = app
        self.title("彩蛋：洗车陷阱题")
        self.configure(bg=BG)
        self.geometry("660x460")
        self.transient(app)
        f = app.f

        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=22, pady=(18, 4))
        tk.Label(head, text="🥚 彩蛋：洗车陷阱题 🚗💦", bg=BG, fg=TEXT, font=(f, 14, "bold")).pack(anchor="w")
        tk.Label(head, text="🚿 洗车店 30–300 米随机 · 场景随机，防背答案——要洗的是车，不是人。",
                 bg=BG, fg=MUTED, font=(f, 9)).pack(anchor="w", pady=(3, 0))
        tk.Label(head, text="🎲 随机抽 3 个已连通模型，各自随机题目 · 仅图一乐，不代表模型能力",
                 bg=BG, fg=FAINT, font=(f, 8)).pack(anchor="w", pady=(2, 0))

        body = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        body.pack(fill="both", expand=True, padx=22, pady=(10, 8))
        for i, r in enumerate(rows):
            card = tk.Frame(body, bg=CARD)
            card.pack(fill="x", padx=12, pady=(10 if i == 0 else 6, 0))
            ok = r.get("ok")
            sym, color = ("🎉", GREEN) if ok else (("💤", RED) if ok is False else ("🤔", MUTED))
            tk.Label(card, text=sym, bg=CARD, fg=color, font=(f, 11), width=3).pack(side="left", anchor="n")
            right = tk.Frame(card, bg=CARD)
            right.pack(side="left", fill="x", expand=True)
            tk.Label(right, text=r.get("model", ""), bg=CARD, fg=TEXT,
                     font=(f, 10, "bold")).pack(anchor="w")
            why = r.get("why") or ""
            if r.get("tag"):
                why = f"[{r['tag']}] " + why
            if r.get("ms"):
                why += f" · {r['ms']} ms"
            tk.Label(right, text=why, bg=CARD, fg=color if ok is not None else MUTED,
                     font=(f, 8)).pack(anchor="w")
            snippet = (r.get("text") or "").replace("\n", " ").strip()
            if len(snippet) > 90:
                snippet = snippet[:90] + "…"
            tk.Label(right, text=snippet or "（无内容）", bg=CARD, fg=MUTED, font=(f, 9),
                     wraplength=540, justify="left").pack(anchor="w", pady=(2, 0))

        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", side="bottom", padx=22, pady=(0, 14))
        tk.Label(foot, text="仅对已连通模型生效 · 仅图一乐", bg=BG, fg=FAINT,
                 font=(f, 8)).pack(side="left")
        ttk.Button(foot, text="再来一次", style="Ghost.TButton", width=9,
                   command=self._again).pack(side="right", padx=(0, 8))
        ttk.Button(foot, text="关闭", style="Primary.TButton", width=8,
                   command=self.destroy).pack(side="right")
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(10, lambda: center_on(self, app))

    def _again(self):
        self.destroy()
        self.app.run_egg()


class BalanceDialog(tk.Toplevel):
    """查询当前 Key 的余额 / 额度，查不到就说清楚原因。"""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("余额查询")
        self.configure(bg=BG)
        self.geometry("560x340")
        self.transient(app)
        self.grab_set()
        f = app.f

        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=20, pady=(16, 4))
        self.lbl_title = tk.Label(head, text="查询中…", bg=BG, fg=TEXT, font=(f, 13, "bold"))
        self.lbl_title.pack(anchor="w")
        self.lbl_ep = tk.Label(head, text="", bg=BG, fg=FAINT, font=(f, 8))
        self.lbl_ep.pack(anchor="w", pady=(2, 0))

        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", side="bottom", padx=20, pady=(0, 14))
        ttk.Button(foot, text="重新查询", style="Ghost.TButton", width=10,
                   command=self.refresh).pack(side="left")
        ttk.Button(foot, text="关闭", style="Primary.TButton", width=8,
                   command=self.destroy).pack(side="right")

        box = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        box.pack(fill="both", expand=True, padx=20, pady=(8, 8))
        self.txt = tk.Text(box, wrap="word", bd=0, highlightthickness=0, bg=CARD, fg=TEXT,
                           font=(f, 10), padx=12, pady=10)
        self.txt.pack(fill="both", expand=True)
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(10, lambda: center_on(self, app))
        self.after(30, self.refresh)

    def refresh(self):
        self.lbl_title.configure(text="查询中…")
        self.lbl_ep.configure(text="")
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", "正在请求服务商接口…")
        self.txt.configure(state="disabled")
        self.update_idletasks()
        prov, lines, url = query_balance(self.app.url_var.get(), self.app.key_var.get().strip())
        self.lbl_title.configure(text=prov)
        self.lbl_ep.configure(text=url or "未调用外部接口")
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", "\n".join(lines))
        self.txt.configure(state="disabled")


class PrivacyDialog(tk.Toplevel):
    """把安全设计讲清楚，并告诉用户怎么自己验证；首启时作为使用协议。"""

    def __init__(self, app, agreement=False):
        self.agreement = agreement
        self.accepted = False
        super().__init__(app)
        self.title("使用协议与隐私说明" if agreement else "隐私与安全说明")
        self.configure(bg=BG)
        self.geometry("680x620")
        self.minsize(560, 460)
        self.transient(app)
        f = app.f
        host = M_host(app.url_var.get())

        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=22, pady=(18, 6))
        tk.Label(head, text="使用协议与隐私说明" if agreement else "隐私与安全说明",
                 bg=BG, fg=TEXT, font=(f, 15, "bold")).pack(anchor="w")
        tk.Label(head, text=f"当前接口域名：{host}", bg=BG, fg=MUTED,
                 font=(f, 9)).pack(anchor="w", pady=(3, 0))

        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", side="bottom", padx=22, pady=(0, 16))
        ttk.Button(foot, text="查看源码", style="Ghost.TButton", width=10,
                   command=lambda: webbrowser.open(f"https://github.com/{UPDATE_REPO}")).pack(side="left")
        if agreement:
            ttk.Button(foot, text="同意并继续", style="Primary.TButton", width=12,
                       command=self._accept).pack(side="right")
            ttk.Button(foot, text="退出", style="Ghost.TButton", width=8,
                       command=self.destroy).pack(side="right", padx=(0, 8))
            self.protocol("WM_DELETE_WINDOW", self.destroy)
        else:
            ttk.Button(foot, text="关闭", style="Primary.TButton", width=8,
                       command=self.destroy).pack(side="right")

        box = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        box.pack(fill="both", expand=True, padx=22, pady=(6, 8))
        txt = tk.Text(box, wrap="word", bd=0, highlightthickness=0, bg=CARD, fg=TEXT,
                      font=(f, 9), padx=14, pady=12, spacing1=2, spacing3=4)
        sb = ttk.Scrollbar(box, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        body = """
一、你的 Key 去了哪里
· 只保存在本机内存里，关闭程序即清除。
· 配置文件只保存接口地址、并发、超时、模型列表和壁纸，不保存 Key。
· 发起检测时，Key 只作为 Authorization 头发送给你在上面填写的接口地址，不经过作者服务器。

二、程序会连接哪些地址
· 你填写的接口地址（域名见上方）——检测请求，带 Key。
· https://www.traktoken.com/api/prices —— 拉取公开模型单价，不带 Key。
· https://api.github.com —— 检查是否有新版本，不带 Key。
除此之外没有任何上报、统计或遥测。

三、日志
· 普通运行不写日志。
· 崩溃日志写到 %APPDATA%\\ModelChecker\\crash.log，写入前会把 sk- 开头的密钥、authorization 头、key=/token= 参数打码。

四、风险提示（重要）
· 本软件支持自定义接口地址：Key 会发给你填的那个域名。请确认该地址可信，不要填来路不明的中转站。
· 模型服务商本身会收到你的请求内容，这是任何客户端的共性。

五、你可以自己验证
· 源码完全公开，Key 的读取与发送都在 http_json() 和 start() 里，可逐行核对。
· 用 Fiddler / Wireshark / mitmproxy 抓包，能看到程序只连上面三个域名；HTTPS 内容需要装证书才能解密。
· 校验发布包 SHA-256 与 Release 页面公布的一致。

六、免责声明
· 本软件是本地检测工具，检测结果仅供参考，不构成对任何模型服务商的服务承诺或质量担保。
· 使用本软件即表示你已阅读并同意本说明。因使用本软件产生的一切后果——包括但不限于 API 费用、账号被限流或封禁、数据丢失、业务中断——由使用者自行承担，与本软件作者无关。
· 请遵守你所使用平台的服务条款以及所在地法律法规，不要用于任何违规用途。
"""
        txt.insert("1.0", body.strip())
        txt.configure(state="disabled")

        self.bind("<Escape>", lambda e: self.destroy())
        self.after(10, lambda: center_on(self, app))


    def _accept(self):
        self.accepted = True
        self.destroy()


class AboutDialog(tk.Toplevel):
    """关于：版本、作者联系方式、检查更新。"""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("关于")
        self.configure(bg=BG)
        self.geometry("480x300")
        self.resizable(False, False)
        self.transient(app)
        self.grab_set()
        f = app.f

        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=22, pady=(22, 4))
        tk.Label(head, text="AI 模型检测", bg=BG, fg=TEXT, font=(f, 16, "bold")).pack(anchor="w")
        tk.Label(head, text=f"版本 v{APP_VER}", bg=BG, fg=MUTED, font=(f, 9)).pack(anchor="w", pady=(2, 0))

        body = tk.Frame(self, bg=BG)
        body.pack(fill="x", padx=22, pady=(12, 0))
        row = tk.Frame(body, bg=BG)
        row.pack(anchor="w")
        tk.Label(row, text="联系作者：", bg=BG, fg=MUTED, font=(f, 9)).pack(side="left")
        link = tk.Label(row, text=TELEGRAM, bg=BG, fg=ACCENT, font=(f, 9, "underline"), cursor="hand2")
        link.pack(side="left")
        link.bind("<Button-1>", lambda e: webbrowser.open(TELEGRAM))

        self.status = tk.Label(body, text="", bg=BG, fg=MUTED, font=(f, 9),
                               wraplength=420, justify="left")
        self.status.pack(anchor="w", pady=(14, 0))

        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", side="bottom", padx=22, pady=(0, 18))
        ttk.Button(foot, text="检查更新", style="Ghost.TButton", width=10,
                   command=self.check).pack(side="left")
        ttk.Button(foot, text="关闭", style="Primary.TButton", width=8,
                   command=self.destroy).pack(side="right")
        self.bind("<Escape>", lambda e: self.destroy())

    def _accept(self):
        self.accepted = True
        self.destroy()

    def check(self):
        self.status.configure(text="正在检查最新版本…", fg=MUTED)
        self.update_idletasks()
        try:
            tag, url, _ = fetch_latest_release()
        except urllib.error.HTTPError as e:
            self.status.configure(text="还没有发布版本" if e.code == 404 else f"检查失败：HTTP {e.code}", fg=RED)
            return
        except Exception:
            self.status.configure(text="检查失败：网络不可达", fg=RED)
            return
        if not tag:
            self.status.configure(text="还没有发布版本", fg=MUTED)
            return
        if parse_ver(tag) > parse_ver(APP_VER):
            self.status.configure(text=f"发现新版本 {tag}", fg=GREEN)
            if messagebox.askyesno(APP_NAME, f"发现新版本 {tag}，是否打开下载页？"):
                webbrowser.open(url)
        else:
            self.status.configure(text=f"已是最新版本（{tag}）", fg=GREEN)


class App(tk.Tk):
    def __init__(self, skip_agreement=False):
        super().__init__()
        self._skip_agreement = skip_agreement
        self.title(f"AI 模型可用性检测 v{APP_VER}")
        self.configure(bg=BG)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(1280, sw - 80)}x{min(940, sh - 80)}")
        self.minsize(1100, 760)
        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.run_id = 0
        self._live_rid = None
        self.results = []
        self.all_models = []
        self.selected = []
        self.filter_key = "all"
        self._sort_reverse = {}
        self.total = 0
        self.done_count = 0
        self._closing = False
        self._fit_job = None
        self._running = False
        self._max_model_px = 0
        self._rows_job = None
        self._resizing = False
        self._keybad_shown = False
        self._special_asked = False
        self._tokwin_shown = False
        self.dark = False
        self.card = "Q版"
        self.local_bg = ""
        self._new_ver = ""

        self._init_style()
        self._fcell = tkfont.Font(family=self.f, size=10)
        self._build()
        self._center()
        self._load_config()
        self.after(100, self._drain)
        self.after(60, lambda: self.hero.render())
        threading.Thread(target=self._load_prices, daemon=True).start()
        self.after(3000, self._silent_update_check)
        if not skip_agreement:
            self.after(250, self._ask_agreement)
        ico = resource_path("app.ico")
        if os.path.exists(ico):
            try:
                self.iconbitmap(ico)
            except Exception:
                pass
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- 样式
    def _init_style(self):
        fams = set(tkfont.families())
        self.f = next((x for x in ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI") if x in fams), "Segoe UI")
        f = self.f
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure(".", background=BG, foreground=TEXT, font=(f, 10))
        st.configure("TEntry", fieldbackground=INPUT, bordercolor=BORDER, lightcolor=BORDER,
                     darkcolor=BORDER, insertcolor=TEXT, padding=7)
        st.map("TEntry", bordercolor=[("focus", ACCENT)])
        st.configure("TSpinbox", fieldbackground=INPUT, bordercolor=BORDER, arrowcolor=MUTED, padding=5)
        st.configure("Primary.TButton", background=ACCENT, foreground="#FFFFFF", borderwidth=0,
                     focusthickness=0, padding=(14, 8), font=(f, 10, "bold"))
        st.map("Primary.TButton", background=[("active", ACCENT_DARK), ("disabled", DISABLED)],
               foreground=[("disabled", "#EEF1F6")])
        st.configure("Ghost.TButton", background=CARD, foreground=TEXT, borderwidth=1,
                     bordercolor=BORDER, focusthickness=0, padding=(10, 7), font=(f, 9))
        st.map("Ghost.TButton", background=[("active", CHIP_HOVER), ("disabled", HEAD_BG)],
               foreground=[("disabled", FAINT)])
        st.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT,
                     rowheight=30, borderwidth=0, font=(f, 10))
        st.configure("Treeview.Heading", background=HEAD_BG, foreground=MUTED,
                     relief="flat", borderwidth=0, font=(f, 9, "bold"), padding=(6, 8))
        st.map("Treeview.Heading", background=[("active", CHIP_HOVER)])
        st.map("Treeview", background=[("selected", SEL_BG)], foreground=[("selected", TEXT)])
        st.configure("TProgressbar", background=ACCENT, troughcolor=TROUGH, borderwidth=0,
                     bordercolor=TROUGH, lightcolor=ACCENT, darkcolor=ACCENT, thickness=8)

    def _card(self, parent, pad=12):
        outer = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        inner = tk.Frame(outer, bg=CARD)
        inner.pack(fill="both", expand=True, padx=pad, pady=pad)
        return outer, inner

    # ---------- 布局
    def _build(self):
        f = self.f
        self.hero = Hero(self, self)
        self.hero.pack(fill="x", side="top")

        # 页脚先 pack 到底部，保证内容再高也不会把它挤没
        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", side="bottom", padx=22, pady=(6, 10))

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=22, pady=(8, 0))
        body.columnconfigure(0, minsize=352)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        self._build_left(body)
        self._build_right(body)
        self.status = tk.Label(foot, text="就绪 · 填好地址和 Key，点左侧「开始检测」", bg=BG, fg=MUTED,
                               font=(f, 9), anchor="w")
        self.status.pack(side="left")
        self.lbl_prog = tk.Label(foot, text="", bg=BG, fg=FAINT, font=(f, 9))
        self.lbl_prog.pack(side="right")
        self.progress = ttk.Progressbar(foot, mode="determinate", length=180)
        self.progress.pack(side="right", padx=(12, 10))

    def _build_left(self, parent):
        f = self.f
        left = tk.Frame(parent, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        scroller = VScroll(left, bg=BG)
        scroller.grid(row=0, column=0, sticky="nsew")
        col = scroller.inner

        card, b = self._card(col)
        card.pack(fill="x")
        tk.Label(b, text="接口配置", bg=CARD, fg=TEXT, font=(f, 10, "bold")).pack(anchor="w")
        tk.Label(b, text="API 地址", bg=CARD, fg=MUTED, font=(f, 9)).pack(anchor="w", pady=(8, 2))
        self.url_var = tk.StringVar()
        ttk.Entry(b, textvariable=self.url_var, font=(f, 10)).pack(fill="x")
        self.lbl_host = tk.Label(b, text="目标域名：—", bg=CARD, fg=FAINT, font=(f, 8))
        self.lbl_host.pack(anchor="w", pady=(2, 0))
        self.url_var.trace_add("write", lambda *a: self._update_host())

        tk.Label(b, text="API Key", bg=CARD, fg=MUTED, font=(f, 9)).pack(anchor="w", pady=(8, 2))
        row = tk.Frame(b, bg=CARD)
        row.pack(fill="x")
        self.key_var = tk.StringVar()
        self.ent_key = ttk.Entry(row, textvariable=self.key_var, show="*", font=(f, 10))
        self.ent_key.pack(side="left", fill="x", expand=True)
        self.show_key = tk.BooleanVar(value=False)
        Toggle(row, "显示", self.show_key, bg=CARD, font=(f, 9)).pack(side="left", padx=(8, 0))
        self.show_key.trace_add("write", lambda *a: self.ent_key.configure(show="" if self.show_key.get() else "*"))
        sec = tk.Label(b, text="密钥只存在本机内存 · 隐私与安全说明 →", bg=CARD, fg=ACCENT,
                       font=(f, 8), cursor="hand2")
        sec.pack(anchor="w", pady=(5, 0))
        sec.bind("<Button-1>", lambda e: self.show_privacy())

        opt = tk.Frame(b, bg=CARD)
        opt.pack(fill="x", pady=(8, 0))
        tk.Label(opt, text="并发", bg=CARD, fg=MUTED, font=(f, 9)).pack(side="left")
        self.conc_var = tk.StringVar(value="6")
        ttk.Spinbox(opt, from_=1, to=32, width=3, textvariable=self.conc_var).pack(side="left", padx=(5, 10))
        tk.Label(opt, text="超时(s)", bg=CARD, fg=MUTED, font=(f, 9)).pack(side="left")
        self.timeout_var = tk.StringVar(value="20")
        ttk.Spinbox(opt, from_=5, to=120, width=3, textvariable=self.timeout_var).pack(side="left", padx=(5, 0))

        c2, b2 = self._card(col)
        c2.pack(fill="x", pady=(8, 0))
        tk.Label(b2, text="检测项", bg=CARD, fg=TEXT, font=(f, 10, "bold")).pack(anchor="w")
        self.opt_stream = tk.BooleanVar(value=True)
        self.opt_tools = tk.BooleanVar(value=False)
        self.opt_think = tk.BooleanVar(value=True)
        self.opt_egg = tk.BooleanVar(value=True)
        self.opt_tokenwin = tk.BooleanVar(value=True)
        Toggle(b2, "流式响应（SSE，多 1 次请求）", self.opt_stream, bg=CARD, font=(f, 9)).pack(anchor="w", pady=(7, 0))
        Toggle(b2, "工具调用 function call（多 1 次请求）", self.opt_tools, bg=CARD, font=(f, 9)).pack(anchor="w", pady=(6, 0))
        Toggle(b2, "思考能力 reasoning（多 1 次请求，较慢）", self.opt_think, bg=CARD, font=(f, 9)).pack(anchor="w", pady=(6, 0))
        tkrow = tk.Frame(b2, bg=CARD)
        tkrow.pack(fill="x", pady=(8, 0))
        tk.Label(tkrow, text="输出 token 数", bg=CARD, fg=MUTED, font=(f, 9)).pack(side="left")
        self.tokens_var = tk.StringVar(value="1")
        ttk.Spinbox(tkrow, from_=1, to=512, width=4, textvariable=self.tokens_var).pack(side="left", padx=(6, 6))
        tk.Label(tkrow, text="默认 1", bg=CARD, fg=FAINT, font=(f, 8)).pack(side="left")
        Toggle(b2, "结束后弹出 Token 统计", self.opt_tokenwin, bg=CARD, font=(f, 9)).pack(anchor="w", pady=(7, 0))

        c3, b3 = self._card(col)
        c3.pack(fill="x", pady=(8, 0))
        top = tk.Frame(b3, bg=CARD)
        top.pack(fill="x")
        tk.Label(top, text="检测模型", bg=CARD, fg=TEXT, font=(f, 10, "bold")).pack(side="left")
        self.lbl_sel = tk.Label(top, text="未选择", bg=CARD, fg=FAINT, font=(f, 9))
        self.lbl_sel.pack(side="right")
        btns = tk.Frame(b3, bg=CARD)
        btns.pack(fill="x", pady=(8, 0))
        self.btn_fetch = ttk.Button(btns, text="获取模型列表", style="Ghost.TButton", command=self.fetch_clicked)
        self.btn_fetch.pack(side="left")
        self.btn_pick = ttk.Button(btns, text="选择模型", style="Ghost.TButton",
                                   command=self.open_picker, state="disabled")
        self.btn_pick.pack(side="left", padx=(8, 0))
        tk.Label(b3, text="也可手动输入，每行一个（与所选合并）", bg=CARD, fg=FAINT,
                 font=(f, 8)).pack(anchor="w", pady=(7, 2))
        self.txt_models = tk.Text(b3, width=1, height=2, relief="flat", bg=INPUT, fg=TEXT,
                                  highlightbackground=BORDER, highlightthickness=1,
                                  font=(f, 9), padx=8, pady=5, undo=True, wrap="none")
        self.txt_models.pack(fill="x")

        act = tk.Frame(left, bg=BG)
        act.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.btn_start = ttk.Button(act, text="开始检测", style="Primary.TButton", command=self.start)
        self.btn_start.pack(fill="x")
        self.btn_stop = ttk.Button(act, text="停止", style="Ghost.TButton", command=self.stop, state="disabled")
        self.btn_stop.pack(fill="x", pady=(7, 0))


    def _build_right(self, parent):
        f = self.f
        right = tk.Frame(parent, bg=BG)
        right.grid(row=0, column=1, sticky="nsew")

        stats = tk.Frame(right, bg=BG)
        stats.pack(fill="x")
        for i in range(5):
            stats.columnconfigure(i, weight=1, uniform="s")
        self.c_total = StatCard(stats, "已检测", TEXT, f)
        self.c_ok = StatCard(stats, "可用", GREEN, f)
        self.c_lim = StatCard(stats, "受限", AMBER, f)
        self.c_bad = StatCard(stats, "不可用", RED, f)
        self.c_lat = StatCard(stats, "平均延迟", TEXT, f)
        for i, c in enumerate((self.c_total, self.c_ok, self.c_lim, self.c_bad, self.c_lat)):
            c.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))

        bar = tk.Frame(right, bg=BG)
        bar.pack(fill="x", pady=(12, 6))
        self.chips = {}
        for key, txt in (("all", "全部"), ("ok", "可用"), ("limited", "受限"),
                         ("bad", "不可用"), ("special", "专用")):
            btn = tk.Button(bar, text=txt, bd=0, relief="flat", highlightthickness=0, cursor="hand2",
                            padx=11, pady=5, font=(f, 9), command=lambda k=key: self.set_filter(k))
            btn.pack(side="left", padx=(0, 5))
            self.chips[key] = btn
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *a: self._render())
        tk.Label(bar, text="搜索", bg=BG, fg=MUTED, font=(f, 9)).pack(side="left", padx=(8, 4))
        ttk.Entry(bar, textvariable=self.search_var, width=14, font=(f, 9)).pack(side="left")
        self.btn_egg = ttk.Button(bar, text="🥚 彩蛋", style="Ghost.TButton", width=9,
                                  command=self.run_egg)
        self.btn_egg.pack(side="right")
        self.lbl_price = tk.Label(bar, text="价格加载中…", bg=BG, fg=FAINT, font=(f, 8),
                                  anchor="w", justify="left")
        self.lbl_price.pack(side="left", fill="x", expand=True, padx=(10, 8))
        self._paint_chips()

        bar2 = tk.Frame(right, bg=BG)
        bar2.pack(fill="x", pady=(0, 6))
        # 先占住右侧按钮，窗口变窄时只挤标签，不挤按钮
        # 显示顺序：重试失败 全部重测 只测可用 Token 统计 复制可用 导出 CSV
        self.btn_export = ttk.Button(bar2, text="导出 CSV", style="Ghost.TButton", width=10,
                                     command=self.export_csv, state="disabled")
        self.btn_export.pack(side="right")
        self.btn_copy = ttk.Button(bar2, text="复制可用", style="Ghost.TButton", width=10,
                                   command=self.copy_ok, state="disabled")
        self.btn_copy.pack(side="right", padx=6)
        self.btn_tokwin = ttk.Button(bar2, text="Token 统计", style="Ghost.TButton", width=10,
                                     command=self.show_tokens, state="disabled")
        self.btn_tokwin.pack(side="right")
        self.btn_balance = ttk.Button(bar2, text="余额查询", style="Ghost.TButton", width=9,
                                      command=self.show_balance)
        self.btn_balance.pack(side="right", padx=6)
        self.btn_onlyok = ttk.Button(bar2, text="只测可用", style="Ghost.TButton", width=11,
                                     command=self.retest_ok, state="disabled")
        self.btn_onlyok.pack(side="left")
        self.btn_retryall = ttk.Button(bar2, text="全部重测", style="Ghost.TButton", width=11,
                                       command=self.retest_all, state="disabled")
        self.btn_retryall.pack(side="left", padx=6)
        self.btn_retry = ttk.Button(bar2, text="重试失败项", style="Ghost.TButton", width=11,
                                    command=self.retry_failed, state="disabled")
        self.btn_retry.pack(side="left")

        tcard, tb = self._card(right, pad=1)
        tcard.pack(fill="both", expand=True)
        wrap = tk.Frame(tb, bg=CARD)
        wrap.pack(fill="both", expand=True)
        cols = ("status", "model", "spec", "latency", "cap", "detail", "price")
        self.table = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="extended")
        for c, txt, w, anchor in (("status", "状态", 60, "w"), ("model", "模型", 140, "w"),
                                  ("spec", "上下文/输出", 90, "center"),
                                  ("latency", "延迟", 58, "e"), ("cap", "能力", 164, "w"),
                                  ("detail", "说明", 168, "w"), ("price", "参考价格", 150, "e")):
            self.table.heading(c, text=txt, anchor=anchor, command=lambda cc=c: self._sort(cc))
            self.table.column(c, width=w, anchor=anchor, stretch=False)
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")   # 先占位，窗口再窄也不会把滚动条挤没
        self.table.pack(side="left", fill="both", expand=True)
        self.table.bind("<Configure>", lambda e: self._schedule_fit())
        self.table.bind("<Motion>", self._hover_detail)
        self.table.bind("<Leave>", lambda e: self._hide_tip())
        self._retag()

        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="查看详情", command=self.show_detail)
        self.menu.add_command(label="复制模型名", command=self._copy_selected_name)
        self.menu.add_command(label="复制整行", command=self._copy_selected_row)
        self.table.bind("<Button-3>", self._popup)
        self.table.bind("<Double-1>", self.show_detail)

    # ---------- 主题
    def _retag(self):
        for tag, color in (("ok", GREEN), ("limited", AMBER), ("bad", RED),
                           ("muted", MUTED), ("special", ACCENT)):
            self.table.tag_configure(tag, foreground=color)
        self.table.tag_configure("stripe", background=STRIPE)

    def _paint_chips(self):
        for k, b in self.chips.items():
            on = k == self.filter_key
            b.configure(bg=ACCENT if on else CHIP_BG, fg="#FFFFFF" if on else CHIP_FG,
                        activebackground=ACCENT_DARK if on else CHIP_HOVER,
                        activeforeground="#FFFFFF" if on else TEXT)

    def toggle_theme(self):
        self.dark = not self.dark
        new = dict(DARK if self.dark else LIGHT)
        old = dict(CUR)
        apply_palette(new)
        mapping = {old[k].lower(): new[k] for k in old if old[k].lower() != new[k].lower()}
        self._recolor(self, mapping)
        CUR.update(new)
        self._init_style()
        self._paint_chips()
        self._retag()
        self.hero.render()

    def _recolor(self, w, mapping):
        for opt in ("bg", "background", "highlightbackground"):
            try:
                v = str(w.cget(opt)).lower()
            except Exception:
                continue
            if v in mapping:
                try:
                    w.configure({opt: mapping[v]})
                except Exception:
                    pass
            break
        for opt in ("fg", "foreground"):
            try:
                v = str(w.cget(opt)).lower()
            except Exception:
                continue
            if v in mapping:
                try:
                    w.configure({opt: mapping[v]})
                except Exception:
                    pass
            break
        if isinstance(w, Toggle):
            w._render()
        for c in w.winfo_children():
            self._recolor(c, mapping)

    def _update_host(self):
        try:
            self.lbl_host.configure(text=f"目标域名：{M_host(self.url_var.get())}")
        except Exception:
            pass

    def show_privacy(self):
        dlg = PrivacyDialog(self)
        self.wait_window(dlg)

    def show_about(self):
        dlg = AboutDialog(self)
        self.wait_window(dlg)

    def _silent_update_check(self):
        def work():
            try:
                tag, url, _ = fetch_latest_release(6)
                self.q.put(("update", tag, url))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _ask_agreement(self):
        dlg = PrivacyDialog(self, agreement=True)
        self.wait_window(dlg)
        if not getattr(dlg, "accepted", False):
            self._closing = True
            try:
                self.destroy()
            except Exception:
                pass

    def pick_bg(self):
        dlg = CardPicker(self)
        self.wait_window(dlg)

    def apply_card(self, name):
        self.card = name
        self.local_bg = ""
        self._save_config()
        self.hero.render()
        self.status.configure(text=f"背景已切换：{name}")

    def apply_local(self, path):
        self.local_bg = path
        self.card = ""
        self._save_config()
        self.hero.render()
        self.status.configure(text=f"背景已切换：{os.path.basename(path)}")

    def _center(self):
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        x = (self.winfo_screenwidth() - w) // 2
        y = max(0, (self.winfo_screenheight() - h) // 3)
        self.geometry(f"+{x}+{y}")

    # ---------- 配置
    def _cfg_path(self):
        d = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "ModelChecker")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "config.json")

    def _load_config(self):
        try:
            with open(self._cfg_path(), encoding="utf-8") as fp:
                cfg = json.load(fp)
            self.url_var.set(cfg.get("base_url", ""))
            self.conc_var.set(str(cfg.get("concurrency", 6)))
            self.timeout_var.set(str(cfg.get("timeout", 20)))
            self.tokens_var.set(str(cfg.get("tokens", 1)))
            self.opt_stream.set(bool(cfg.get("stream", True)))
            self.opt_tools.set(bool(cfg.get("tools", False)))
            self.opt_think.set(bool(cfg.get("think", True)))
            self.opt_egg.set(bool(cfg.get("egg", True)))
            self.opt_tokenwin.set(bool(cfg.get("tokenwin", True)))
            self.card = cfg.get("card", "Q版") or ""
            self.local_bg = cfg.get("local_bg", "") or ""
        except Exception:
            pass

    def _save_config(self):
        if os.environ.get("MC_NO_SAVE"):
            return
        try:
            with open(self._cfg_path(), "w", encoding="utf-8") as fp:
                json.dump({"base_url": self.url_var.get().strip(),
                           "concurrency": self.conc_var.get(),
                           "timeout": self.timeout_var.get(),
                           "tokens": self.tokens_var.get(),
                           "stream": self.opt_stream.get(),
                           "tools": self.opt_tools.get(),
                           "think": self.opt_think.get(), "egg": self.opt_egg.get(),
                           "egg": self.opt_egg.get(),
                           "tokenwin": self.opt_tokenwin.get(),
                           "card": self.card,
                           "local_bg": self.local_bg},
                          fp, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ---------- 模型选择
    def _manual_models(self):
        return [ln.strip() for ln in self.txt_models.get("1.0", "end").splitlines()
                if ln.strip() and not ln.strip().startswith("#")]

    def fetch_clicked(self):
        url = normalize_base(self.url_var.get())
        key = self.key_var.get().strip()
        if not url or not key:
            messagebox.showwarning(APP_NAME, "请先填接口地址和 API Key")
            return
        self.url_var.set(url)
        self.btn_fetch.configure(state="disabled")
        self.status.configure(text="正在获取模型列表…")
        timeout = float(self.timeout_var.get() or 20)

        def work():
            try:
                self.q.put(("models", fetch_model_list(url, key, timeout)))
            except Exception as e:
                self.q.put(("error", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def open_picker(self):
        if not self.all_models:
            messagebox.showinfo(APP_NAME, "请先点「获取模型列表」")
            return
        dlg = ModelPicker(self, self.all_models, self.selected, self.f)
        self.wait_window(dlg)
        if dlg.result is not None:
            self.selected = dlg.result
            self.lbl_sel.configure(text=f"已选 {len(self.selected)} 个" if self.selected else "未选择",
                                   fg=TEXT if self.selected else FAINT)

    # ---------- 检测
    def start(self, preset=None):
        url = normalize_base(self.url_var.get())
        key = self.key_var.get().strip()
        if not url:
            messagebox.showwarning(APP_NAME, "请填接口地址，例如 https://api.xxx.com/v1")
            return
        if not key:
            messagebox.showwarning(APP_NAME, "请填 API Key")
            return
        force_all = False
        if not (list(preset) if preset else []) and not self.selected and not self._manual_models():
            if not messagebox.askyesno(APP_NAME, "你还没有选中测试模型，继续则默认选择所有模型！\n\n是否继续？"):
                return
            force_all = True
        self.url_var.set(url)
        self._save_config()
        self.run_id += 1
        rid = self.run_id
        self._live_rid = rid
        self.stop_event.clear()
        self.results = []
        self.total = 0
        self.done_count = 0
        self._keybad_shown = False
        self._special_asked = False
        self._tokwin_shown = False
        self.table.delete(*self.table.get_children())
        for c in (self.c_total, self.c_ok, self.c_lim, self.c_bad, self.c_lat):
            c.set("—")
        self.progress.configure(value=0, maximum=1)
        self.lbl_prog.configure(text="")
        self._set_running(True)
        self.status.configure(text="正在准备…")
        conc = max(1, min(32, int(float(self.conc_var.get() or 6))))
        timeout = float(self.timeout_var.get() or 20)
        opts = {"stream": self.opt_stream.get(), "tools": self.opt_tools.get(),
                "think": self.opt_think.get(), "egg": self.opt_egg.get(),
                "tokens": max(1, int(float(self.tokens_var.get() or 1)))}
        if preset:
            seed = list(preset)
        elif force_all:
            seed = []          # 交给 _run 去 /models 拉全量
        else:
            seed = list(self.selected) + self._manual_models()
        threading.Thread(target=self._run, args=(rid, url, key, seed, conc, timeout, opts, False),
                         daemon=True).start()

    def _set_running(self, running):
        self._running = running
        self.btn_start.configure(state="disabled" if running else "normal")
        self.btn_fetch.configure(state="disabled" if running else "normal")
        self.btn_pick.configure(state="disabled" if running or not self.all_models else "normal")
        self.btn_stop.configure(state="normal" if running else "disabled")
        has = bool(self.results)
        text_models = [r for r in self.results if r.get("kind") in ("chat", "embed")]
        ok_models = [r for r in self.results if r["status"] in (OK, DEGRADED)]
        bad = [r for r in text_models if r["status"] in (MISSING, UNKNOWN, RATE, NO_ACCESS)]
        self.btn_copy.configure(state="disabled" if running else ("normal" if has else "disabled"))
        self.btn_export.configure(state="disabled" if running else ("normal" if has else "disabled"))
        self.btn_egg.configure(state="disabled" if running else "normal")
        self.btn_tokwin.configure(state="disabled" if running else
                                  ("normal" if any(r.get("tokens") for r in self.results) else "disabled"))
        self.btn_retry.configure(state="normal" if (not running and bad) else "disabled")
        self.btn_retryall.configure(state="normal" if (not running and text_models) else "disabled")
        self.btn_onlyok.configure(state="normal" if (not running and ok_models) else "disabled")

    def stop(self):
        self.stop_event.set()
        if self.total == 0:
            self.run_id += 1
            self._live_rid = None
            self._set_running(False)
            self.status.configure(text="已停止")
        else:
            self.status.configure(text="已停止 · 正在整理结果…")

    def _run(self, rid, url, key, seed, conc, timeout, opts, special):
        try:
            models = list(dict.fromkeys(seed))
            if not models and not special:
                models = fetch_model_list(url, key, timeout)
            if not models:
                self.q.put(("error", "没有可检测的模型：请获取模型列表并选择，或手动输入模型名", rid))
                return
            self.q.put(("total", len(models), rid))
            task_q = queue.Queue()
            for m in models:
                task_q.put(m)
            results = []
            lock = threading.Lock()

            def worker():
                while True:
                    if self.stop_event.is_set() or rid != self.run_id:
                        return
                    try:
                        m = task_q.get_nowait()
                    except queue.Empty:
                        return
                    r = test_special(url, key, m, timeout) if special else test_model(url, key, m, opts, timeout, 1)
                    if rid != self.run_id:
                        return
                    with lock:
                        results.append(r)
                        n = len(results)
                    self.q.put(("row", n, r, rid))
                    if opts.get("think") and r["status"] in (OK, DEGRADED) and r["kind"] == "chat":
                        info = probe_think(url, key, r["model"], min(timeout * 2, 60))
                        r["think"] = info
                        u2 = info.get("usage") or {}
                        if u2 and r.get("tokens"):
                            try:
                                r["tokens"]["prompt"] += int(u2.get("prompt_tokens") or 0)
                                r["tokens"]["completion"] += int(u2.get("completion_tokens") or 0)
                                r["tokens"]["total"] = r["tokens"]["prompt"] + r["tokens"]["completion"]
                            except Exception:
                                pass
                        self.q.put(("think", r["model"], info, rid))
                    if r["status"] == KEY_BAD:
                        self.q.put(("keybad", r["detail"], rid))
                        self.stop_event.set()
                        return

            threads = [threading.Thread(target=worker, daemon=True) for _ in range(conc)]
            for t in threads:
                t.start()
            while any(t.is_alive() for t in threads):
                if self.stop_event.is_set() or rid != self.run_id:
                    break
                time.sleep(0.06)
            self.q.put(("done", results, rid, self.stop_event.is_set()))
        except Exception as e:
            self.q.put(("error", f"{type(e).__name__}: {e}", rid))

    def _load_prices(self):
        try:
            self.q.put(("prices", fetch_prices(10)))
        except Exception as e:
            self.q.put(("prices_err", str(e)))

    # ---------- 队列
    def _drain(self):
        try:
            while True:
                try:
                    msg = self.q.get_nowait()
                except queue.Empty:
                    break
                kind = msg[0]

                if kind == "update":
                    tag, url = msg[1], msg[2]
                    if tag and parse_ver(tag) > parse_ver(APP_VER):
                        self._new_ver = tag
                        self.status.configure(text=f"发现新版本 {tag}，点右上角「关于」查看")
                    continue

                if kind == "egg":
                    self.btn_egg.configure(state="normal")
                    self.status.configure(text="彩蛋结束 · 仅图一乐，不代表模型能力")
                    if not self._closing and msg[1]:
                        EggDialog(self, msg[1])
                    continue

                if kind == "think":
                    model, info = msg[1], msg[2]
                    for r in self.results:
                        if r["model"] == model:
                            r["think"] = info
                            break
                    self._refresh_row(model)
                    continue

                if kind == "prices":
                    PRICES.update(msg[1])
                    self.lbl_price.configure(
                        text=f"价格 {PRICES['count']} 个 · 每百万 token · 按 1:{USD_RATE} 折算，仅供参考")
                    self._render()
                    continue
                if kind == "prices_err":
                    self.lbl_price.configure(text="价格获取失败（traktoken.com 不可达），不影响检测")
                    continue

                if kind == "models":
                    models = msg[1]
                    self.all_models = models
                    self.btn_fetch.configure(state="normal")
                    self.btn_pick.configure(state="normal")
                    self.status.configure(text=f"已获取 {len(models)} 个模型，点「选择模型」挑选要检测的")
                    self.after(20, self.open_picker)
                    continue

                if kind == "error":
                    rid_err = msg[2] if len(msg) >= 3 else None
                    if rid_err is not None and rid_err != self.run_id:
                        continue
                    self._set_running(False)
                    self.status.configure(text="已中止")
                    self.after(10, lambda m=msg[1]: messagebox.showerror(APP_NAME, m))
                    continue

                if kind == "row":
                    rid = msg[3]
                elif kind in ("total", "keybad", "done"):
                    rid = msg[2]
                else:
                    rid = None
                if rid is not None and rid != self.run_id:
                    continue
                if kind == "row" and rid != self._live_rid:
                    continue

                if kind == "total":
                    self.total = msg[1]
                    self.progress.configure(maximum=max(1, self.total), value=0)
                    self.lbl_prog.configure(text=f"0 / {self.total}")
                    self.status.configure(text=f"正在检测 {self.total} 个模型…")
                elif kind == "row":
                    r = msg[2]
                    self.results.append(r)
                    self.done_count = msg[1]
                    self._insert_row(r)
                    self._update_stats()
                    self.progress.configure(value=self.done_count)
                    self.lbl_prog.configure(text=f"{self.done_count} / {self.total}")
                elif kind == "keybad":
                    if not self._keybad_shown:
                        self._keybad_shown = True
                        self.after(10, lambda d=msg[1]: messagebox.showerror(
                            APP_NAME, f"API Key 无效（HTTP 401）：{d}\n\n检测已中止。"))
                elif kind == "done":
                    self._live_rid = None
                    self._on_done(msg[3])
        finally:
            if not self._closing:
                self.after(100, self._drain)

    def _on_done(self, stopped):
        self.stop_event.clear()
        self._set_running(False)
        self._auto_sort()
        self._update_stats()
        ok = sum(1 for r in self.results if r["status"] == OK)
        lim = sum(1 for r in self.results if r["status"] == DEGRADED)
        bad = sum(1 for r in self.results if r["status"] in (MISSING, NO_ACCESS))
        other = len(self.results) - ok - lim - bad
        head = "已停止" if stopped else "检测完成"
        self.status.configure(text=f"{head} · 共 {len(self.results)} 个：可用 {ok}，受限 {lim}，"
                                   f"不可用 {bad}，其他 {other}")
        self.lbl_prog.configure(text=f"{len(self.results)} / {self.total or len(self.results)}")
        if not stopped:
            self.progress.configure(value=self.progress["maximum"])
            self._maybe_token_report()
            self._maybe_special()

    def _maybe_token_report(self):
        rows = [r for r in self.results if r.get("tokens")]
        self.btn_tokwin.configure(state="normal" if rows else "disabled")
        if rows and self.opt_tokenwin.get() and not self._tokwin_shown:
            self._tokwin_shown = True
            self.show_tokens()

    def run_egg(self):
        ok_models = [r for r in self.results
                     if r["status"] == OK and r.get("kind") == "chat"]
        if not ok_models:
            messagebox.showinfo(APP_NAME, "还没有已连通的对话模型，先跑一次检测吧")
            return
        key = self.key_var.get().strip()
        if not key:
            messagebox.showwarning(APP_NAME, "请先填写 API Key")
            return
        ok_models.sort(key=lambda x: x.get("latency") or 10 ** 9)
        picks = [r["model"] for r in ok_models[:3]]
        url = normalize_base(self.url_var.get())
        self.btn_egg.configure(state="disabled")
        self.status.configure(text=f"彩蛋进行中：{len(picks)} 个模型正在思考洗车问题…")

        def work():
            rows = []
            for m in picks:
                try:
                    rows.append(ask_carwash(url, key, m, 30))
                except Exception as e:
                    rows.append({"model": m, "ok": None, "text": str(e), "ms": 0,
                                 "why": "请求异常", "q": "", "tag": ""})
            self.q.put(("egg", rows, self.run_id))

        threading.Thread(target=work, daemon=True).start()

    def show_balance(self):
        if not self.key_var.get().strip():
            messagebox.showwarning(APP_NAME, "请先填写 API Key")
            return
        dlg = BalanceDialog(self)
        self.wait_window(dlg)

    def show_tokens(self):
        rows = [r for r in self.results if r.get("tokens")]
        if not rows:
            messagebox.showinfo(APP_NAME, "本次没有拿到 usage 数据（上游未返回 token 统计）")
            return
        TokenReport(self, rows, self.f)

    def _maybe_special(self):
        if self._special_asked:
            return
        pending = [r["model"] for r in self.results if r["status"] == SKIP and r["kind"] == "image"]
        others = [r for r in self.results if r["status"] == SKIP and r["kind"] in ("audio", "video")]
        if not pending:
            return
        self._special_asked = True
        msg = (f"文本 / 向量模型检测完毕。\n\n还有 {len(pending)} 个图片生成类模型未检测，是否继续？\n"
               f"每个模型会发起 1 次最小出图请求（512×512），可能产生少量费用。")
        if others:
            msg += f"\n另有 {len(others)} 个语音/视频模型需专用接口，暂不自动检测。"
        if not messagebox.askyesno(APP_NAME, msg):
            self.status.configure(text=self.status.cget("text") + f" · 已跳过 {len(pending)} 个图片模型")
            return
        self.run_id += 1
        rid = self.run_id
        self._live_rid = rid
        self.stop_event.clear()
        self.results = [r for r in self.results if not (r["status"] == SKIP and r["kind"] == "image")]
        self.done_count = 0
        self.total = len(pending)
        self.progress.configure(maximum=max(1, len(pending)), value=0)
        self._set_running(True)
        self.status.configure(text=f"正在检测 {len(pending)} 个图片模型…")
        self.table.delete(*self.table.get_children())
        for r in self.results:
            self._insert_row(r)
        self._update_stats()
        conc = max(1, min(16, int(float(self.conc_var.get() or 6))))
        timeout = float(self.timeout_var.get() or 20)
        threading.Thread(target=self._run, args=(rid, normalize_base(self.url_var.get()),
                                                 self.key_var.get().strip(), pending, conc,
                                                 timeout, {}, True), daemon=True).start()

    # ---------- 表格
    def _cap_text(self, r):
        parts = []
        if r["kind"] == "image":
            parts.append("图片生成")
        elif r["kind"] == "audio":
            parts.append("语音")
        elif r["kind"] == "video":
            parts.append("视频")
        if r.get("stream") is not None:
            parts.append("流式✓" if r["stream"] else "流式✗")
        if r.get("tools") is not None:
            parts.append("工具✓" if r["tools"] else "工具✗")
        th = r.get("think")
        if th is not None and r["kind"] == "chat":
            if th.get("ok"):
                parts.append("思考✓")
            else:
                parts.append("思考✗")
        if r.get("usage") is not None and r["kind"] in ("chat", "embed"):
            parts.append("计量✓" if r["usage"] else "计量✗")
        return " ".join(parts) if parts else "—"

    def _detail_text(self, r):
        if r["status"] == OK:
            return f"流式未通过：{r['stream_why']}" if r.get("stream_why") else ""
        zh = r.get("zh") or zh_reason(r["http"], r.get("detail", ""))
        return f"{r['http']} · {zh}" if r["http"] else zh

    def _style(self, r):
        _, tag = BADGE.get(r["status"], ("?", "muted"))
        if r.get("special") and tag != "bad":
            tag = "special"
        return tag

    def _refresh_row(self, model):
        for r in self.results:
            if r["model"] != model:
                continue
            iid = f"r{id(r)}"
            if self.table.exists(iid):
                sym, _ = BADGE.get(r["status"], ("?", "muted"))
                lat = f"{r['latency']} ms" if r.get("latency") is not None else "—"
                self.table.item(iid, values=(f"{sym} {r['status']}",
                                             self._fit(r["model"], self._model_width() + 16),
                                             spec_text(r["model"]), lat,
                                             self._cap_text(r),
                                             self._fit(self._detail_text(r), self._detail_width()),
                                             price_for(r["model"]) or "—"))
            break

    def _insert_row(self, r):
        if not self._match(r):
            return
        iid = f"r{id(r)}"
        if self.table.exists(iid):
            return
        sym, _ = BADGE.get(r["status"], ("?", "muted"))
        lat = f"{r['latency']} ms" if r.get("latency") is not None else "—"
        stripe = len(self.table.get_children()) % 2 == 1
        self.table.insert("", "end", iid=iid,
                          values=(f"{sym} {r['status']}", self._fit(r["model"], self._model_width() + 16),
                                  spec_text(r["model"]), lat,
                                  self._cap_text(r), self._fit(self._detail_text(r), self._detail_width()),
                                  price_for(r["model"]) or "—"),
                          tags=(self._style(r), "stripe") if stripe else (self._style(r),))

    def _match(self, r):
        kw = self.search_var.get().strip().lower()
        if kw and kw not in r["model"].lower():
            return False
        k = self.filter_key
        if k == "ok":
            return r["status"] == OK
        if k == "limited":
            return r["status"] == DEGRADED
        if k == "bad":
            return r["status"] in (MISSING, NO_ACCESS, RATE, UNKNOWN, KEY_BAD)
        if k == "special":
            return r["kind"] in ("image", "audio", "video")
        return True

    def _render(self):
        self.table.delete(*self.table.get_children())
        for r in self.results:
            if self._match(r):
                sym, _ = BADGE.get(r["status"], ("?", "muted"))
                lat = f"{r['latency']} ms" if r.get("latency") is not None else "—"
                self.table.insert("", "end", iid=f"r{id(r)}",
                                  values=(f"{sym} {r['status']}",
                                          self._fit(r["model"], self._model_width() + 16),
                                          spec_text(r["model"]), lat,
                                          self._cap_text(r),
                                          self._fit(self._detail_text(r), self._detail_width()),
                                          price_for(r["model"]) or "—"),
                                  tags=(self._style(r),))

    def _fit(self, text, px):
        try:
            if self._fcell.measure(text) <= px:
                return text
            while text and self._fcell.measure(text + "…") > px:
                text = text[:-1]
            return text + "…"
        except Exception:
            return text

    def _model_width(self):
        try:
            return max(60, self.table.column("model", "width") - 12)
        except Exception:
            return 170

    def _schedule_fit(self):
        if getattr(self, "_fit_job", None):
            return
        self._fit_job = self.after(160, self._do_fit)

    def _do_fit(self):
        self._fit_job = None
        if getattr(self, "_running", False):
            return
        self._fit_columns()

    def _fit_columns(self, event=None):
        """按表格实际宽度分配列宽，窗口变窄也不会把最后一列挤出去。"""
        try:
            total = self.table.winfo_width() - 14
        except Exception:
            return
        if total < 320:
            return
        w_status, w_spec, w_lat, w_cap, w_price = 60, 90, 58, 164, 150
        rest = total - w_status - w_spec - w_lat - w_cap - w_price
        # 模型列按最长名称给宽度，保证显示全称
        longest = getattr(self, "_max_model_px", 0)
        w_model = max(112, min(220, longest + 20))
        if w_model > rest - 120:
            w_model = max(120, rest - 120)
        w_detail = max(120, rest - w_model - 8)
        try:
            old_m = self.table.column("model", "width")
            old_d = self.table.column("detail", "width")
        except Exception:
            old_m = old_d = 0
        for col, wd in (("status", w_status), ("spec", w_spec), ("latency", w_lat),
                        ("cap", w_cap), ("price", w_price), ("model", w_model),
                        ("detail", w_detail)):
            try:
                if abs(self.table.column(col, "width") - wd) < 2:
                    continue
            except Exception:
                pass
            self.table.column(col, width=wd)
        # 列宽变化后延迟重排，避免拖动窗口时反复重建整张表
        if abs(old_m - w_model) > 8 or abs(old_d - w_detail) > 8:
            self._resizing = True
            if getattr(self, "_rows_job", None):
                try:
                    self.after_cancel(self._rows_job)
                except Exception:
                    pass
            self._rows_job = self.after(220, self._refill_rows)

    def _refill_rows(self):
        self._rows_job = None
        self._resizing = False
        self._render()

    def _detail_width(self):
        try:
            total = self.table.winfo_width()
            fixed = sum(self.table.column(c, "width") for c in
                        ("status", "spec", "model", "latency", "cap", "price"))
            return max(110, total - fixed - 12)
        except Exception:
            return 280

    def set_filter(self, key):
        self.filter_key = key
        self._paint_chips()
        self._render()

    def _update_stats(self):
        total = len(self.results)
        try:
            self._max_model_px = max((self._fcell.measure(r["model"]) for r in self.results), default=0)
        except Exception:
            pass
        ok = sum(1 for r in self.results if r["status"] == OK)
        lim = sum(1 for r in self.results if r["status"] == DEGRADED)
        bad = sum(1 for r in self.results if r["status"] in (MISSING, NO_ACCESS, RATE, UNKNOWN, KEY_BAD))
        lat = [r["latency"] for r in self.results if r.get("latency")]
        self.c_total.set(total or "—")
        self.c_ok.set(ok)
        self.c_lim.set(lim)
        self.c_bad.set(bad)
        self.c_lat.set(f"{round(sum(lat) / len(lat))} ms" if lat else "—")
        self._schedule_fit()

    def _auto_sort(self):
        def key(r):
            lat = r.get("latency")
            special = 1 if r.get("kind") in ("image", "audio", "video") else 0
            return (GROUP.get(r["status"], 3), special,
                    lat if lat is not None else 10 ** 9, r["model"])
        self.results.sort(key=key)
        self._render()

    def _sort(self, col):
        rows = [(self.table.set(iid, col), iid) for iid in self.table.get_children()]
        rev = self._sort_reverse.get(col, False)
        if col == "status":
            rows.sort(key=lambda t: GROUP.get(t[0].split(" ", 1)[-1], 9), reverse=rev)
        elif col == "latency":
            rows.sort(key=lambda t: int(t[0].split()[0]) if t[0].endswith("ms") else 10 ** 9, reverse=rev)
        elif col == "price":
            def pv(t):
                try:
                    return float(re.sub(r"[^0-9.]", "", t[0].split("/")[0]))
                except Exception:
                    return 10 ** 9
            rows.sort(key=pv, reverse=rev)
        else:
            rows.sort(key=lambda t: t[0].lower(), reverse=rev)
        for i, (_, iid) in enumerate(rows):
            self.table.move(iid, "", i)
        self._sort_reverse[col] = not rev

    def _hover_detail(self, e):
        if getattr(self, "_resizing", False):
            return
        col = self.table.identify_column(e.x)
        row = self.table.identify_row(e.y)
        if col != "#5" or not row:
            self._hide_tip()
            return
        r = self._sel_result(row)
        if not r:
            return
        text = self._detail_text(r)
        if not text or len(text) < 6:
            self._hide_tip()
            return
        if getattr(self, "_tip_key", None) == (row, text):
            return
        self._hide_tip()
        top = tk.Toplevel(self)
        top.wm_overrideredirect(True)
        tk.Label(top, text=text, bg="#111827", fg="#F9FAFB", font=(self.f, 9),
                 padx=10, pady=6, wraplength=560, justify="left").pack()
        top.geometry(f"+{e.x_root + 14}+{e.y_root + 18}")
        self._tipwin = top
        self._tip_key = (row, text)

    def _hide_tip(self):
        w = getattr(self, "_tipwin", None)
        if w is not None:
            try:
                w.destroy()
            except Exception:
                pass
            self._tipwin = None
        self._tip_key = None

    def _popup(self, event):
        iid = self.table.identify_row(event.y)
        if iid:
            if iid not in self.table.selection():
                self.table.selection_set(iid)
            self.menu.tk_popup(event.x_root, event.y_root)

    def _sel_result(self, iid):
        try:
            want = int(iid[1:])
        except Exception:
            return None
        for r in self.results:
            if id(r) == want:
                return r
        return None

    def show_detail(self, event=None):
        sel = self.table.selection()
        if not sel:
            return
        r = self._sel_result(sel[0])
        if not r:
            return
        top = tk.Toplevel(self)
        top.title("模型详情")
        top.configure(bg=BG)
        top.geometry("640x460")
        top.transient(self)
        body = tk.Frame(top, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        txt = tk.Text(body, wrap="word", bd=0, highlightthickness=0, bg=CARD, fg=TEXT,
                      font=(self.f, 10), padx=14, pady=12)
        txt.pack(fill="both", expand=True)

        def cap(v, yes="支持", no="不支持"):
            return "未检测" if v is None else (yes if v else no)

        tok = r.get("tokens")
        toktxt = f"{tok['prompt']} + {tok['completion']} = {tok['total']}" if tok else "—"
        txt.insert("1.0", "\n".join([
            f"模型：{r['model']}",
            f"状态：{r['status']}",
            f"类型：{r['kind']}",
            f"HTTP：{r['http'] if r['http'] is not None else '—'}",
            f"延迟：{str(r['latency']) + ' ms' if r.get('latency') else '—'}",
            f"流式：{cap(r.get('stream'))}",
            f"工具调用：{cap(r.get('tools'))}",
            f"思考能力：{think_text(r)}",
            f"上下文 / 最大输出：{spec_text(r['model'])} tokens",
            f"usage 计量：{cap(r.get('usage'), '有', '无')}",
            f"Token（提示+生成=合计）：{toktxt}",
            f"参考价格（¥/百万 token）：{price_for(r['model']) or '未收录'}",
            "",
            "原因（中文）：",
            self._detail_text(r) or "（无）",
            "",
            "上游原始返回：",
            r.get("detail") or "（无）",
        ]))
        txt.configure(state="disabled")
        bar = tk.Frame(top, bg=BG)
        bar.pack(fill="x", side="bottom", padx=16, pady=(0, 16))
        body.pack(fill="both", expand=True, padx=16, pady=16)
        ttk.Button(bar, text="复制模型名", style="Ghost.TButton",
                   command=lambda: self._clip(r["model"])).pack(side="left")
        ttk.Button(bar, text="关闭", style="Primary.TButton", width=8,
                   command=top.destroy).pack(side="right")
        top.bind("<Escape>", lambda e: top.destroy())

    def _clip(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status.configure(text="已复制到剪贴板")

    def _copy_selected_name(self):
        sel = self.table.selection()
        if not sel:
            return
        names = [self.table.set(i, "model") for i in sel]
        self.clipboard_clear()
        self.clipboard_append("\n".join(names))
        self.status.configure(text=f"已复制 {len(names)} 个模型名")

    def _copy_selected_row(self):
        sel = self.table.selection()
        if not sel:
            return
        lines = ["\t".join(self.table.set(i, c) for c in ("status", "model", "latency", "cap", "detail"))
                 for i in sel]
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
        self.status.configure(text="已复制")

    def copy_ok(self):
        ok = [r["model"] for r in self.results if r["status"] in (OK, DEGRADED)]
        if not ok:
            messagebox.showinfo(APP_NAME, "还没有可用的模型")
            return
        self.clipboard_clear()
        self.clipboard_append("\n".join(sorted(ok)))
        self.status.configure(text=f"已复制 {len(ok)} 个可用模型到剪贴板")

    def retry_failed(self):
        bad = [r["model"] for r in self.results
               if r["status"] in (MISSING, UNKNOWN, RATE, NO_ACCESS) and r.get("kind") in ("chat", "embed")]
        if not bad:
            messagebox.showinfo(APP_NAME, "没有需要重试的对话模型")
            return
        self.status.configure(text=f"只重试 {len(bad)} 个失败项…")
        self.start(preset=bad)

    def retest_all(self):
        models = [r["model"] for r in self.results if r.get("kind") in ("chat", "embed")]
        if not models:
            messagebox.showinfo(APP_NAME, "还没有可重测的模型")
            return
        self.status.configure(text=f"全部重测 {len(models)} 个模型…")
        self.start(preset=models)

    def retest_ok(self):
        models = [r["model"] for r in self.results if r["status"] in (OK, DEGRADED)]
        if not models:
            messagebox.showinfo(APP_NAME, "当前没有可用模型可重测")
            return
        self.status.configure(text=f"只重测 {len(models)} 个可用模型…")
        self.start(preset=models)

    def export_csv(self):
        if not self.results:
            messagebox.showinfo(APP_NAME, "还没有结果可以导出")
            return
        path = filedialog.asksaveasfilename(
            title="保存检测结果", defaultextension=".csv",
            initialfile=time.strftime("model_check_%Y%m%d_%H%M.csv"),
            filetypes=[("CSV 文件", "*.csv"), ("JSON 文件", "*.json")])
        if not path:
            return
        try:
            if path.lower().endswith(".json"):
                with open(path, "w", encoding="utf-8") as fp:
                    json.dump({"base_url": self.url_var.get(),
                               "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                               "results": self.results}, fp, ensure_ascii=False, indent=2)
            else:
                with open(path, "w", encoding="utf-8-sig", newline="") as fp:
                    w = csv.writer(fp)
                    w.writerow(["状态", "模型", "类型", "HTTP", "延迟(ms)", "上下文/输出", "流式", "工具调用", "思考", "usage",
                                "提示token", "生成token", "合计token", "说明", "参考价格", "接口地址"])
                    for r in self.results:
                        t = r.get("tokens") or {}
                        w.writerow([r["status"], r["model"], r["kind"], r["http"] or "",
                                    r.get("latency") or "",
                                    "" if r.get("stream") is None else ("支持" if r["stream"] else "不支持"),
                                    "" if r.get("tools") is None else ("支持" if r["tools"] else "不支持"),
                                    think_text(r),
                                    "" if r.get("usage") is None else ("有" if r["usage"] else "无"),
                                    t.get("prompt", ""), t.get("completion", ""), t.get("total", ""),
                                    self._detail_text(r), price_for(r["model"]) or "", self.url_var.get()])
            self.status.configure(text=f"已导出：{path}")
        except Exception as e:
            messagebox.showerror(APP_NAME, f"导出失败：{e}")

    def _on_close(self):
        self._closing = True
        self.stop_event.set()
        self.run_id += 1
        self._save_config()
        self.destroy()

    def report_callback_exception(self, exc, val, tb):
        log = write_crash(exc, val, tb)
        try:
            messagebox.showerror(APP_NAME, f"操作出错：{val}\n\n日志：{log}")
        except Exception:
            pass


def selftest():
    out_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.getcwd()
    out = os.path.join(out_dir, "selftest.txt")
    try:
        app = App(skip_agreement=True)
        app.withdraw()
        app.update()
        r = test_model("http://127.0.0.1:9/v1", "k", "gpt-4o",
                       {"stream": False, "tools": False, "tokens": 1}, 1.5, 0)
        zh = zh_reason(402, "insufficient balance")
        with open(out, "w", encoding="utf-8") as f:
            f.write(f"GUI_OK tk={tk.TkVersion} probe={r['status']} zh={zh} bg={bool(load_bg_source())}\n")
        app.destroy()
    except Exception as e:
        with open(out, "w", encoding="utf-8") as f:
            f.write(f"FAIL {type(e).__name__}: {e}\n")
        raise


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    if "--selftest" in sys.argv:
        selftest()
        return

    def hook(t, v, tb):
        log = write_crash(t, v, tb)
        try:
            messagebox.showerror(APP_NAME, f"程序启动失败：{v}\n\n日志：{log}")
        except Exception:
            pass

    sys.excepthook = hook
    try:
        App().mainloop()
    except Exception:
        hook(*sys.exc_info())


if __name__ == "__main__":
    main()
