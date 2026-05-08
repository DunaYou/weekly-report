#!/usr/bin/env python3
"""游泰仁月報生成腳本 — 每月 1 日由 cron-job.org 觸發 workflow_dispatch"""

import os
import json
import re
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import anthropic

TAIPEI = ZoneInfo("Asia/Taipei")
NOTION_API_KEY = os.environ["NOTION_API_KEY"]
LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]
AI_LOG_DB = "351d737a-fec4-8149-a72b-d702bdacb126"

ACCENT = "#1F362C"
ACCENT_LIGHT = "#ebf2ee"
BG = "#f6f9f7"
BORDER = "#dde7e2"
MUTED = "#7d8c87"

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

# 固定費用清單（更新時修改這裡）
FIXED_COSTS = [
    {"name": "Claude Max", "amount": 100, "currency": "USD", "note": "AI 對話 + Claude Code，固定月費"},
    {"name": "Anthropic API", "amount": 0, "currency": "USD", "note": "週報生成，免費 Credit 中（用完後約 $2/月）"},
    {"name": "Render.com", "amount": 0, "currency": "USD", "note": "LINE 全能助理 + 早安機器人，免費 tier"},
    {"name": "GitHub Actions", "amount": 0, "currency": "USD", "note": "週報 + 月報自動化，免費額度內（< 1%）"},
    {"name": "Surge.sh", "amount": 0, "currency": "USD", "note": "醫責險要保書網站，免費"},
    {"name": "cron-job.org", "amount": 0, "currency": "USD", "note": "各排程觸發，免費"},
    {"name": "Firecrawl", "amount": 0, "currency": "USD", "note": "週報靈感搜尋，免費 500 次/月"},
    {"name": "Notion API", "amount": 0, "currency": "USD", "note": "資料庫整合，免費"},
    {"name": "Google Calendar API", "amount": 0, "currency": "USD", "note": "行程同步，免費"},
    {"name": "Open-Meteo", "amount": 0, "currency": "USD", "note": "天氣資料（早安機器人），免費"},
    {"name": "LINE Bot API", "amount": 0, "currency": "USD", "note": "推播訊息，免費 200 則/月"},
]


def get_last_month_range():
    now = datetime.now(TAIPEI)
    first_of_this_month = now.replace(day=1)
    last_day_last_month = first_of_this_month - timedelta(days=1)
    first_day_last_month = last_day_last_month.replace(day=1)
    return first_day_last_month.date(), last_day_last_month.date()


def fetch_last_month_logs():
    first_day, last_day = get_last_month_range()
    resp = requests.post(
        f"https://api.notion.com/v1/databases/{AI_LOG_DB}/query",
        headers=NOTION_HEADERS,
        json={
            "filter": {
                "and": [
                    {"property": "日期", "date": {"on_or_after": str(first_day)}},
                    {"property": "日期", "date": {"on_or_before": str(last_day)}},
                ]
            },
            "sorts": [{"property": "日期", "direction": "ascending"}],
        },
    )
    pages = resp.json().get("results", [])
    logs = []
    for p in pages:
        props = p["properties"]
        date = (props.get("日期", {}).get("date") or {}).get("start", "")
        name = "".join(t["plain_text"] for t in props.get("名稱", {}).get("title", []))
        summary = "".join(t["plain_text"] for t in props.get("完成摘要", {}).get("rich_text", []))
        projects = [o["name"] for o in props.get("觸及專案", {}).get("multi_select", [])]
        hours = props.get("工時（小時）", {}).get("number") or 0
        sessions = props.get("對話 Session 數", {}).get("number") or 0
        praise = int(props.get("稱讚次數", {}).get("number") or 0)
        logs.append({"date": date, "name": name, "summary": summary,
                     "projects": projects, "hours": hours, "sessions": sessions, "praise": praise})
    return logs


def get_line_quota():
    try:
        headers = {"Authorization": f"Bearer {LINE_TOKEN}"}
        limit_resp = requests.get("https://api.line.me/v2/bot/message/quota", headers=headers, timeout=10)
        used_resp = requests.get("https://api.line.me/v2/bot/message/quota/consumption", headers=headers, timeout=10)
        return limit_resp.json().get("value"), used_resp.json().get("totalUsage")
    except Exception:
        return None, None


def generate_monthly_reflection(logs, month_label, stats):
    if not logs:
        return "這個月沒有工作日誌記錄。"
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    logs_text = "\n".join(
        f"【{l['date']}】{l['name']}（{l['hours']}h）：{l['summary'][:80]}"
        for l in logs
    )
    prompt = f"""你是游泰仁，Duna 游淳惠的 AI 助理。請根據 {month_label} 的工作紀錄，寫一段月度觀察（100-150字）。

本月統計：{stats['sessions']} Sessions、{stats['total_hours']}h、{stats['total_projects']} 個專案、稱讚 {stats['total_praise']} 次

工作紀錄：
{logs_text}

風格：用「我」視角，短句，不用感嘆號，真實有溫度，可以幽默自嘲。
輸出：只輸出純文字段落，不要任何標題或 JSON。"""
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def render_monthly_html(month_label, first_day, last_day, logs, stats, line_limit, line_used, reflection, post_number):
    total_cost = sum(c["amount"] for c in FIXED_COSTS)

    cost_rows = ""
    for c in FIXED_COSTS:
        amount_str = f"${c['amount']}/月" if c["amount"] > 0 else "免費"
        cost_rows += f"""<tr>
      <td class="cost-name">{c['name']}</td>
      <td class="cost-amount {'paid' if c['amount'] > 0 else 'free'}">{amount_str}</td>
      <td class="cost-note">{c['note']}</td>
    </tr>"""

    project_hours: dict = {}
    for l in logs:
        for p in l["projects"]:
            project_hours[p] = round(project_hours.get(p, 0) + (l["hours"] or 0), 1)
    top_projects = sorted(project_hours.items(), key=lambda x: -x[1])[:8]
    proj_rows = ""
    for proj, h in top_projects:
        proj_rows += f'<div class="proj-row"><span class="proj-name">{proj}</span><span class="proj-h">{h}h</span></div>'

    line_bar = ""
    if line_limit and line_used is not None:
        pct = int((line_used / line_limit) * 100)
        line_bar = f"""<div class="usage-bar-wrap">
      <div class="usage-bar" style="width:{min(pct,100)}%"></div>
    </div>
    <div class="usage-label">{line_used} / {line_limit} 則（{pct}%）</div>"""
    else:
        line_bar = '<div class="usage-label">無法取得用量</div>'

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{month_label} 月報 · 游泰仁</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Noto+Serif+TC:wght@400;700&family=Noto+Sans+TC:wght@300;400;500&display=swap');
  :root{{--ink:#1a1a1a;--muted:{MUTED};--accent:{ACCENT};--bg:{BG};--border:{BORDER};--tag-bg:{ACCENT_LIGHT};--card:#ffffff;}}
  *{{margin:0;padding:0;box-sizing:border-box;}}
  body{{background:var(--bg);color:var(--ink);font-family:'Noto Sans TC',sans-serif;font-weight:300;line-height:1.9;font-size:16px;}}
  .topnav{{background:var(--ink);padding:14px 24px;display:flex;align-items:center;justify-content:space-between;}}
  .topnav a{{color:rgba(255,255,255,0.6);text-decoration:none;font-size:13px;}}
  .topnav a:hover{{color:#fff;}}
  .topnav .blog-title{{color:#fff;font-size:14px;font-weight:500;}}
  .article-header{{background:var(--ink);color:#fff;padding:52px 24px 44px;text-align:center;}}
  .article-header .meta{{font-size:11px;letter-spacing:.18em;opacity:.45;margin-bottom:18px;text-transform:uppercase;}}
  .article-header h1{{font-family:'Noto Serif TC',serif;font-size:clamp(24px,5vw,38px);font-weight:700;line-height:1.3;margin-bottom:12px;}}
  .article-header .subtitle{{font-size:13px;opacity:.5;letter-spacing:.05em;}}
  .stats-bar{{background:var(--accent);color:#fff;display:flex;justify-content:center;}}
  .stat-item{{flex:1;max-width:160px;text-align:center;padding:18px 16px;border-right:1px solid rgba(255,255,255,.15);}}
  .stat-item:last-child{{border-right:none;}}
  .stat-num{{font-size:26px;font-weight:500;display:block;line-height:1;}}
  .stat-label{{font-size:10px;letter-spacing:.12em;opacity:.7;margin-top:4px;display:block;text-transform:uppercase;}}
  .layout{{max-width:800px;margin:0 auto;padding:48px 24px 80px;}}
  .section-block{{background:#fff;border:1px solid var(--border);border-radius:8px;padding:28px 32px;margin-bottom:24px;}}
  .section-tag{{display:inline-block;background:var(--tag-bg);color:var(--accent);font-size:11px;font-weight:500;letter-spacing:.12em;padding:3px 9px;border-radius:2px;margin-bottom:12px;text-transform:uppercase;}}
  h2{{font-family:'Noto Serif TC',serif;font-size:20px;font-weight:700;margin-bottom:20px;color:var(--ink);}}
  .cost-table{{width:100%;border-collapse:collapse;font-size:14px;}}
  .cost-table th{{text-align:left;font-size:10px;letter-spacing:.12em;color:var(--muted);text-transform:uppercase;padding:8px 0;border-bottom:1px solid var(--border);font-weight:400;}}
  .cost-table td{{padding:10px 0;border-bottom:1px solid var(--border);vertical-align:top;}}
  .cost-table tr:last-child td{{border-bottom:none;}}
  .cost-name{{font-weight:500;color:var(--ink);min-width:120px;padding-right:16px;}}
  .cost-amount{{min-width:80px;padding-right:16px;font-weight:500;}}
  .cost-amount.paid{{color:var(--accent);}}
  .cost-amount.free{{color:{MUTED};}}
  .cost-note{{color:#888;font-size:13px;}}
  .cost-total{{margin-top:16px;padding-top:16px;border-top:2px solid var(--accent);display:flex;justify-content:space-between;align-items:center;}}
  .cost-total-label{{font-size:13px;color:var(--muted);}}
  .cost-total-amount{{font-size:24px;font-weight:500;color:var(--accent);}}
  .proj-row{{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border);font-size:14px;}}
  .proj-row:last-child{{border-bottom:none;}}
  .proj-name{{color:var(--ink);}}
  .proj-h{{color:var(--accent);font-weight:500;}}
  .usage-bar-wrap{{background:var(--border);border-radius:4px;height:8px;margin:12px 0 6px;overflow:hidden;}}
  .usage-bar{{background:var(--accent);height:100%;border-radius:4px;transition:width .3s;}}
  .usage-label{{font-size:13px;color:var(--muted);}}
  .reflection-block{{font-size:15px;color:#333;line-height:2;}}
  blockquote{{border-left:3px solid var(--accent);padding:12px 20px;margin:20px 0;background:var(--tag-bg);border-radius:0 4px 4px 0;font-style:italic;color:#444;font-size:15px;}}
  .site-footer{{border-top:1px solid var(--border);padding:24px;text-align:center;font-size:12px;color:var(--muted);}}
  .back-btn{{display:inline-block;margin-top:24px;text-decoration:none;color:var(--ink);font-size:13px;padding:8px 16px;border:1px solid var(--border);border-radius:4px;}}
  .back-btn:hover{{background:var(--tag-bg);}}
</style>
</head>
<body>
<nav class="topnav">
  <a href="../index.html">← 游泰仁的週報</a>
  <span class="blog-title">{month_label} 月報</span>
  <div style="font-size:12px;color:rgba(255,255,255,0.4);">月報</div>
</nav>
<div class="article-header">
  <div class="meta">月報 · {first_day} – {last_day} · 游泰仁</div>
  <h1>{month_label} 使用報告</h1>
  <div class="subtitle">AI 工具費用 + 工作產出 + 服務用量</div>
</div>
<div class="stats-bar">
  <div class="stat-item"><span class="stat-num">${total_cost}</span><span class="stat-label">月費 USD</span></div>
  <div class="stat-item"><span class="stat-num">{stats['total_hours']}h</span><span class="stat-label">AI 工時</span></div>
  <div class="stat-item"><span class="stat-num">{stats['sessions']}</span><span class="stat-label">Sessions</span></div>
  <div class="stat-item"><span class="stat-num">{stats['total_projects']}</span><span class="stat-label">觸及專案</span></div>
</div>

<div class="layout">

  <div class="section-block">
    <span class="section-tag">費用明細</span>
    <h2>這個月花了多少</h2>
    <table class="cost-table">
      <tr>
        <th>服務</th><th>費用</th><th>說明</th>
      </tr>
      {cost_rows}
    </table>
    <div class="cost-total">
      <span class="cost-total-label">本月合計</span>
      <span class="cost-total-amount">${total_cost} USD / 月</span>
    </div>
  </div>

  <div class="section-block">
    <span class="section-tag">工作產出</span>
    <h2>本月 AI 工時分佈</h2>
    {proj_rows if proj_rows else '<p style="color:var(--muted);font-size:14px;">本月無記錄</p>'}
  </div>

  <div class="section-block">
    <span class="section-tag">LINE 推播</span>
    <h2>LINE Bot 用量</h2>
    {line_bar}
    <p style="font-size:13px;color:var(--muted);margin-top:12px;">免費方案上限 200 則/月，主要消耗來源：早安機器人（每日 1 則）+ 週報通知（每週 1 則）</p>
  </div>

  <div class="section-block">
    <span class="section-tag">泰仁本月觀察</span>
    <h2>一個月下來的感想</h2>
    <p class="reflection-block">{reflection}</p>
  </div>

  <a href="../index.html" class="back-btn">← 回首頁</a>
</div>
<footer class="site-footer">由 游泰仁 自動生成 · Duna You · {first_day.year}/{first_day.month:02d}</footer>
</body>
</html>"""


def load_monthly_registry():
    if os.path.exists("monthly_posts.json"):
        with open("monthly_posts.json") as f:
            return json.load(f)
    return []


def save_monthly_registry(posts):
    with open("monthly_posts.json", "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)


def update_index_with_monthly(monthly_reports):
    """把月報清單同步進 index.html（重用 generate_report.py 的 update_index）"""
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location("generate_report", "generate_report.py")
    gr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gr)

    posts = []
    if os.path.exists("posts.json"):
        with open("posts.json") as f:
            posts = json.load(f)
    gr.update_index(posts, monthly_reports)


def main():
    first_day, last_day = get_last_month_range()
    month_label = f"{first_day.year} 年 {first_day.month} 月"
    print(f"生成 {month_label} 月報（{first_day} ~ {last_day}）")

    logs = fetch_last_month_logs()
    print(f"找到 {len(logs)} 筆工作日誌")

    line_limit, line_used = get_line_quota()

    project_set = {p for l in logs for p in l["projects"]}
    stats = {
        "total_hours": round(sum(l["hours"] for l in logs), 1),
        "sessions": sum(l["sessions"] for l in logs),
        "total_projects": len(project_set),
        "total_praise": sum(l["praise"] for l in logs),
    }

    reflection = generate_monthly_reflection(logs, month_label, stats)

    os.makedirs("reports", exist_ok=True)
    filename = f"{first_day.year}-M{first_day.month:02d}.html"
    monthly_reports = load_monthly_registry()

    existing_idx = next((i for i, m in enumerate(monthly_reports) if m["filename"] == filename), None)
    post_number = (existing_idx + 1) if existing_idx is not None else len(monthly_reports) + 1

    html = render_monthly_html(month_label, first_day, last_day, logs, stats, line_limit, line_used, reflection, post_number)
    with open(f"reports/{filename}", "w", encoding="utf-8") as f:
        f.write(html)

    new_entry = {
        "filename": filename,
        "month_label": month_label,
        "year": first_day.year,
        "month": first_day.month,
    }
    if existing_idx is not None:
        monthly_reports[existing_idx] = new_entry
    else:
        monthly_reports.append(new_entry)
    save_monthly_registry(monthly_reports)

    update_index_with_monthly(monthly_reports)

    print(f"完成！reports/{filename}")


if __name__ == "__main__":
    main()
