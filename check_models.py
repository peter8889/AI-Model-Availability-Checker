#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量探测 OpenAI 兼容 API：哪些模型真的能用，哪些不能。

只依赖 Python 标准库（3.7+），无需 pip install。

用法示例:
    # 1) 先拉取模型清单，再逐个实测
    python check_models.py --base-url https://api.xxx.com/v1 --api-key sk-xxx --discover

    # 2) 自己给一份候选列表
    python check_models.py --base-url https://api.xxx.com/v1 --api-key sk-xxx \
        --models gpt-4o,gpt-4o-mini,claude-sonnet-4 --concurrency 8

    # 3) 列表在文件里，一行一个
    python check_models.py --base-url https://api.xxx.com/v1 --api-key sk-xxx \
        --models-file models.txt --only-failed

key 也可以走环境变量 OPENAI_API_KEY。
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------------------------------------------------------- 结果分类
OK = "可用"
MISSING = "不可用"           # 模型不存在 / 没有渠道
NO_ACCESS = "无权限"         # 模型存在，但当前 key 访问不了
DEGRADED = "可用(受限)"      # 返回 400，但报错内容与参数有关，说明模型是通的
RATE = "限流"                # 429，需重试
UNKNOWN = "未知"             # 5xx / 超时 / 网络错误
KEY_BAD = "key失效"          # 401，全局问题
SKIP = "跳过"                # 图片/语音等会产生费用的接口

RANK = {MISSING: 0, NO_ACCESS: 1, UNKNOWN: 2, RATE: 3, DEGRADED: 4, KEY_BAD: 5, SKIP: 6, OK: 7}

MISSING_PAT = re.compile(
    r"model[_ ]?not[_ ]?found|no such model|does not exist|invalid model|unknown model"
    r"|not a valid model|model .{0,40}(not exist|not available|not supported|not found)"
    r"|无可用渠道|没有可用渠道|模型不存在|不存在的模型|无效的模型|模型.*?(不可用|不存在|未找到|未开通)",
    re.I,
)
NO_ACCESS_PAT = re.compile(r"permission|not allowed|not authorized|access denied|无权|无权限|未授权|禁止访问", re.I)
PARAM_PAT = re.compile(r"max_tokens|max_completion_tokens|temperature|unsupported (value|parameter)|invalid_request_error", re.I)
RATE_PAT = re.compile(r"rate.?limit|too many requests|限流|请求过于频繁|quota|余额|insufficient", re.I)


def http_json(method, url, key, payload=None, timeout=25):
    """发请求，永远返回 (status|None, body_text)，不抛异常。"""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    if key:
        req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(4000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read(4000).decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body
    except Exception as e:  # 超时、DNS、TLS、连接重置……
        return None, f"{type(e).__name__}: {e}"


def err_text(body):
    """从各种错误结构里抠出人类可读的一句话。"""
    try:
        obj = json.loads(body)
    except Exception:
        return re.sub(r"\s+", " ", body)[:180]
    if isinstance(obj, dict):
        for path in (("error", "message"), ("error", "msg"), ("message",), ("msg",), ("detail",), ("error",)):
            cur = obj
            for p in path:
                cur = cur.get(p) if isinstance(cur, dict) else None
                if cur is None:
                    break
            if isinstance(cur, str) and cur:
                return re.sub(r"\s+", " ", cur)[:180]
    return re.sub(r"\s+", " ", body)[:180]


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
        if PARAM_PAT.search(txt):
            return DEGRADED, err_text(txt)
        return DEGRADED, err_text(txt)
    if status is None:
        return UNKNOWN, err_text(txt)
    if 500 <= status < 600:
        return UNKNOWN, err_text(txt)
    return UNKNOWN, f"HTTP {status} {err_text(txt)}"


def kind_of(model):
    m = model.lower()
    if re.search(r"embed|bge[-_]|gte[-_]|text-embedding|rerank|jina", m):
        return "embed"
    if re.search(r"dall-e|imagen|flux|sd[-_]?xl|midjourney|stable-diffusion|image|kolors", m):
        return "image"
    if re.search(r"whisper|tts|speech|asr|audio|voice", m):
        return "audio"
    if re.search(r"sora|veo|kling|runway|video|hailuo", m):
        return "video"
    if re.search(r"moderation|guard", m):
        return "other"
    return "chat"


def build_payload(kind, model, variant="max_tokens"):
    if kind == "embed":
        return {"model": model, "input": "hi"}
    if variant == "max_completion_tokens":
        return {"model": model,
                "messages": [{"role": "user", "content": "hi"}],
                "max_completion_tokens": 1, "stream": False}
    return {"model": model,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1, "temperature": 0, "stream": False}


def probe(base, key, model, timeout, retries):
    kind = kind_of(model)
    if kind in ("image", "audio", "video", "other"):
        return {"model": model, "kind": kind, "status": SKIP, "http": None,
                "detail": "需专用接口且可能计费，未自动测试"}

    path = "/embeddings" if kind == "embed" else "/chat/completions"
    url = base.rstrip("/") + path
    status, body = None, ""
    for attempt in range(retries + 1):
        status, body = http_json("POST", url, key, build_payload(kind, model), timeout)
        res, detail = classify(status, body)
        # 有些推理模型（o1/o3/gpt-5 系）不认 max_tokens，换参数再试一次
        if res == DEGRADED and kind == "chat" and re.search(r"max_tokens", detail or "", re.I):
            status, body = http_json("POST", url, key,
                                     build_payload(kind, model, "max_completion_tokens"), timeout)
            res, detail = classify(status, body)
        if res not in (UNKNOWN, RATE) or attempt == retries:
            break
        time.sleep(1.5 * (attempt + 1))
    return {"model": model, "kind": kind, "status": res, "http": status, "detail": detail}


def list_models(base, key, timeout):
    url = base.rstrip("/") + "/models"
    status, body = http_json("GET", url, key, timeout=timeout)
    if status != 200:
        sys.exit(f"[×] 拉取 /models 失败: HTTP {status} {err_text(body)}\n"
                 f"    检查 base-url 是否带 /v1、key 是否有效。")
    try:
        obj = json.loads(body)
        items = obj.get("data") or obj.get("models") or []
        return [it["id"] for it in items if isinstance(it, dict) and it.get("id")]
    except Exception as e:
        sys.exit(f"[×] /models 返回不是预期 JSON: {e}\n{body[:300]}")


def main():
    ap = argparse.ArgumentParser(description="批量探测 OpenAI 兼容 API 的模型可用性")
    ap.add_argument("--base-url", required=True, help="例如 https://api.xxx.com/v1")
    ap.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", ""), help="默认读 OPENAI_API_KEY")
    ap.add_argument("--models", default="", help="逗号分隔的候选模型，如 a,b,c")
    ap.add_argument("--models-file", default="", help="候选模型文件，一行一个")
    ap.add_argument("--discover", action="store_true", help="先请求 /models 拉清单")
    ap.add_argument("--concurrency", type=int, default=6, help="并发数，默认 6，别太高以免被限流")
    ap.add_argument("--timeout", type=float, default=25, help="单请求超时秒数")
    ap.add_argument("--retries", type=int, default=1, help="5xx/429 重试次数")
    ap.add_argument("--only-failed", action="store_true", help="只显示非「可用」的结果")
    ap.add_argument("--out", default="model_check_report.json", help="JSON 报告路径")
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if args.models_file:
        with open(args.models_file, encoding="utf-8") as f:
            models += [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    if args.discover:
        models = list_models(args.base_url, args.api_key, args.timeout) + models
    models = list(dict.fromkeys(models))  # 去重保序
    if not models:
        sys.exit("[×] 没有候选模型：加 --discover 或 --models/--models-file")

    print(f"[i] 目标 {args.base_url} ｜ 候选 {len(models)} 个 ｜ 并发 {args.concurrency}\n")
    t0 = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        futs = {pool.submit(probe, args.base_url, args.api_key, m, args.timeout, args.retries): m
                for m in models}
        for i, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            results.append(r)
            if r["status"] == KEY_BAD:
                sys.exit(f"[×] key 无效（HTTP 401）：{r['detail']}")
            print(f"  [{i}/{len(models)}] {r['status']:<8} {r['model']}", flush=True)

    results.sort(key=lambda r: (RANK.get(r["status"], 9), r["model"]))
    print("\n" + "=" * 78)
    for r in results:
        if args.only_failed and r["status"] == OK:
            continue
        http = r["http"] if r["http"] is not None else "-"
        line = f"{r['status']:<10} {str(http):<5} {r['model']}"
        if r["status"] != OK and r["detail"]:
            line += f"\n{'':<16}└─ {r['detail']}"
        print(line)

    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("=" * 78)
    print("汇总: " + " ｜ ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: RANK.get(kv[0], 9))))
    print(f"耗时 {time.time() - t0:.1f}s")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"base_url": args.base_url, "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "counts": counts, "results": results}, f, ensure_ascii=False, indent=2)
    print(f"报告已写入 {args.out}")

    ok = [r["model"] for r in results if r["status"] in (OK, DEGRADED)]
    if ok:
        print("\n可用模型（每行一个，可直接粘进配置）:")
        print("\n".join(ok))


if __name__ == "__main__":
    main()
