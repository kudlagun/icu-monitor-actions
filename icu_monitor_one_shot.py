#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# One-shot monitor for GitHub Actions: fetch -> diff -> notify -> save state -> exit

import os, re, json, sys
from pathlib import Path
import requests
from bs4 import BeautifulSoup

# -------- Config from env --------
PORTAL_URLS   = [u.strip() for u in os.getenv("PORTAL_URLS","https://course-reg.icu.ac.jp/reg/prereg_clist/GEN.html").split(",") if u.strip()]
COURSE_CODES  = [c.strip().upper() for c in os.getenv("COURSE_CODES","").split(",") if c.strip()]
CODE_PREFIXES = [p.strip().upper() for p in os.getenv("CODE_PREFIXES","").split(",") if p.strip()]
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL","").strip()
INITIAL_NOTIFY = os.getenv("INITIAL_NOTIFY","0").lower() not in ("0","false","")

STATE_PATH = Path("state.json")
HEADERS = {"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}

def passes_filter(code: str) -> bool:
    if COURSE_CODES:
        return code in COURSE_CODES
    if CODE_PREFIXES:
        return any(code.startswith(p) for p in CODE_PREFIXES)
    return True

def extract_int(s: str):
    m = re.search(r"(\d+)", s or "")
    return int(m.group(1)) if m else None

def parse_page(html: str):
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if not tds: continue
        if tr.get("valign") != "top" and len(tds) < 7: continue
        code_text = (tds[1].get_text(" ", strip=True) or "").strip()
        m = re.search(r"\b([A-Z]{3}\d{3})\b", code_text)
        if not m: continue
        code = m.group(1).upper()
        seats = extract_int(tds[-1].get_text(" ", strip=True))
        if seats is None: continue
        out[code] = {"open": seats > 0, "seats": seats}
    return out

def fetch_all():
    merged = {}
    for url in PORTAL_URLS:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        if not r.encoding: r.encoding = "utf-8"
        merged.update(parse_page(r.text))
    return merged

def notify(msg: str):
    print(msg, flush=True)
    if DISCORD_WEBHOOK_URL:
        try:
            requests.post(DISCORD_WEBHOOK_URL, json={"content": msg}, timeout=10).raise_for_status()
        except Exception as e:
            print(f"[WARN] Discord 发送失败: {e}", file=sys.stderr)

def main():
    latest = fetch_all()
    latest = {k:v for k,v in latest.items() if passes_filter(k)}

    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            state = {"courses": {}}
    else:
        state = {"courses": {}}

    prev_courses = state.get("courses", {})
    changed = False
    first_run = (len(prev_courses) == 0)

    # 初次运行：保存基线，是否通知由 INITIAL_NOTIFY 决定
    if first_run:
        state["courses"] = latest
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        if INITIAL_NOTIFY:
            for code, info in latest.items():
                notify(f"📌 初始 {code}: open={info['open']}, LEFT={info['seats']}")
        else:
            print("Initialized baseline (no notifications).")
        return

    # 对比变化
    for code, info in latest.items():
        prev = prev_courses.get(code)
        if not prev:
            prev_courses[code] = info
            changed = True
            notify(f"🆕 新出现课程 {code}: open={info['open']}, LEFT={info['seats']}")
            continue

        # 开关变化
        if prev.get("open") != info["open"]:
            prev_courses[code] = info
            changed = True
            if info["open"]:
                notify(f"✅ 课程 {code} 现在『可选』！LEFT {prev.get('seats')}→{info['seats']}")
            else:
                notify(f"⛔ 课程 {code} 已关闭/满员。LEFT {prev.get('seats')}→{info['seats']}")
            continue

        # 座位数变化
        if prev.get("seats") != info["seats"]:
            prev_courses[code] = info
            changed = True
            notify(f"↔️ 课程 {code} 座位变化：LEFT {prev.get('seats')}→{info['seats']}")

    # 消失的课程（只提示一次）
    for code in list(prev_courses.keys()):
        if passes_filter(code) and code not in latest:
            if not prev_courses[code].get("_gone_notified"):
                prev_courses[code]["_gone_notified"] = True
                changed = True
                notify(f"⚠️ 未在当前页面集合中找到课程 {code}（可能换学期/列表）。")

    if changed:
        state["courses"] = prev_courses
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print("No changes.")

if __name__ == "__main__":
    main()
