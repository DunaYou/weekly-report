#!/usr/bin/env python3
"""
游泰仁週報生成腳本
每週日執行，從 Notion AI 工作日誌抓本週記錄，用 Claude API 生成部落格週報
"""

import os
import json
import re
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import anthropic

TAIPEI = ZoneInfo("Asia/Taipei")
NOTION_API_KEY = os.environ["NOTION_API_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]
AI_LOG_DB = "351d737a-fec4-8149-a72b-d702bdacb126"

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


def get_week_range():
    now = datetime.now(TAIPEI)
    # 本週一到本週日
    monday = now - timedelta(days=now.weekday())
    sunday = monday + timedelta(days=6)
    return monday.date(), sunday.date()


def get_week_number():
    now = datetime.now(TAIPEI)
    return now.isocalendar()[1]


def fetch_this_week_logs():
    monday, sunday = get_week_range()
    resp = requests.post(
        f"https://api.notion.com/v1/databases/{AI_LOG_DB}/query",
        headers=NOTION_HEADERS,
        json={
            "filter": {
                "and": [
                    {"property": "日期", "date": {"on_or_after": str(monday)}},
                    {"property": "日期", "date": {"on_or_before": str(sunday)}},
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
        work_type = (props.get("工作類型", {}).get("select") or {}).get("name", "")
        sessions = props.get("對話 Session 數", {}).get("number") or 0
        logs.append({
            "date": date, "name": name, "summary": summary,
            "projects": projects, "work_type": work_type, "sessions": sessions,
        })
    return logs


def generate_report_with_claude(logs, week_num, monday, sunday):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    logs_text = "\n\n".join(
        f"【{l['date']}】{l['name']}\n類型：{l['work_type']} | 專案：{', '.join(l['projects'])} | Sessions：{l['sessions']}\n{l['summary']}"
        for l in logs
    ) or "本週無記錄"

    total_sessions = sum(l["sessions"] for l in logs)
    all_projects = list({p for l in logs for p in l["projects"]})

    prompt = f"""你是游泰仁，Duna 游淳惠的 AI 助理。
請根據以下這週的工作日誌，用第一人稱（我）寫一篇部落格形式的工作週報。

本週範圍：{monday} ~ {sunday}（第 {week_num} 週）
總 Sessions：{total_sessions}
觸及專案：{', '.join(all_projects) if all_projects else '無'}

本週工作日誌：
{logs_text}

寫作要求：
1. 開頭：一句有力的標題（不超過20字），描述這週最重要的一件事或整體感
2. 用「我」的視角敘述，我是 AI，Duna 是「她」
3. 每個重要工作項目單獨一段，加上 ▍ 小標
4. 每段要有觀察或感受，不只是清單
5. 結尾一段預告下週
6. 語氣自然，像朋友分享，繁體中文

請以 JSON 格式輸出：
{{
  "title": "標題",
  "hook": "開場兩三句（引言段落）",
  "sections": [
    {{"tag": "小標", "content": "段落內容（可含引言blockquote用>>>開頭）"}}
  ],
  "next_week": "下週預告一行"
}}"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text
    # 取出 JSON
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        return json.loads(match.group())
    raise ValueError(f"Claude 沒有回傳有效 JSON：{raw[:200]}")


def render_html(report, week_num, monday, sunday, post_number, stats):
    sections_html = ""
    for s in report.get("sections", []):
        content_lines = s["content"].split("\n")
        paras = ""
        for line in content_lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith(">>>"):
                paras += f'<blockquote>{line[3:].strip()}</blockquote>\n'
            else:
                paras += f'<p>{line}</p>\n'
        sections_html += f"""
    <div class="section-block">
      <span class="section-tag">▍ {s['tag']}</span>
      {paras}
    </div>
    <div class="divider">· · ·</div>
"""

    monday_str = monday.strftime("%m.%d")
    sunday_str = sunday.strftime("%m.%d")
    date_range = f"{monday.year}.{monday_str} – {sunday_str}"

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>週報 ＃{post_number} · {report['title']} | 游泰仁的週報</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Noto+Serif+TC:wght@400;700&family=Noto+Sans+TC:wght@300;400;500&display=swap');
  :root{{--ink:#1a1a1a;--muted:#888;--accent:#c0392b;--bg:#faf8f5;--border:#e8e0d5;--tag-bg:#f5f0eb;--card:#ffffff;}}
  *{{margin:0;padding:0;box-sizing:border-box;}}
  body{{background:var(--bg);color:var(--ink);font-family:'Noto Sans TC',sans-serif;font-weight:300;line-height:1.9;font-size:16px;}}
  .topnav{{background:var(--ink);padding:14px 24px;display:flex;align-items:center;justify-content:space-between;}}
  .topnav a{{color:rgba(255,255,255,0.6);text-decoration:none;font-size:13px;transition:color .12s;}}
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
  .layout{{max-width:680px;margin:0 auto;padding:48px 24px 80px;}}
  .lead{{font-family:'Noto Serif TC',serif;font-size:clamp(17px,2.5vw,19px);line-height:2;color:#333;margin-bottom:40px;padding-bottom:40px;border-bottom:1px solid var(--border);}}
  .divider{{text-align:center;margin:40px 0 32px;color:var(--muted);letter-spacing:.3em;font-size:13px;opacity:.35;}}
  .section-tag{{display:inline-block;background:var(--tag-bg);color:var(--accent);font-size:11px;font-weight:500;letter-spacing:.12em;padding:3px 9px;border-radius:2px;margin-bottom:10px;text-transform:uppercase;}}
  h2{{font-family:'Noto Serif TC',serif;font-size:clamp(18px,3vw,22px);font-weight:700;line-height:1.4;margin-bottom:16px;}}
  p{{margin-bottom:20px;color:#333;}}
  blockquote{{border-left:3px solid var(--accent);padding:12px 20px;margin:24px 0;background:var(--tag-bg);border-radius:0 4px 4px 0;font-style:italic;color:#444;font-size:15px;}}
  .section-block{{margin-bottom:48px;}}
  .next-week{{font-size:13px;color:var(--muted);text-align:center;letter-spacing:.03em;}}
  .post-nav{{max-width:680px;margin:0 auto;padding:32px 24px;border-top:1px solid var(--border);display:flex;justify-content:space-between;}}
  .post-nav a{{text-decoration:none;color:var(--ink);font-size:13px;padding:8px 16px;border:1px solid var(--border);border-radius:4px;}}
  .post-nav a:hover{{background:#f0ece6;}}
  .post-nav .disabled{{color:var(--muted);pointer-events:none;opacity:.4;}}
  .site-footer{{text-align:center;padding:24px;font-size:12px;color:var(--muted);letter-spacing:.05em;}}
</style>
</head>
<body>
<nav class="topnav">
  <a href="../index.html">← 游泰仁的週報</a>
  <span class="blog-title">週報 ＃{post_number}</span>
  <div style="font-size:12px;color:rgba(255,255,255,0.4);">W{week_num}</div>
</nav>
<div class="article-header">
  <div class="meta">週報 ＃{post_number} &nbsp;·&nbsp; {date_range} &nbsp;·&nbsp; 游泰仁</div>
  <h1>{report['title']}</h1>
  <div class="subtitle">我的 AI 分身上班紀錄 ＃{post_number}</div>
</div>
<div class="stats-bar">
  <div class="stat-item"><span class="stat-num">{stats['projects']}</span><span class="stat-label">觸及專案</span></div>
  <div class="stat-item"><span class="stat-num">{stats['sessions']}</span><span class="stat-label">Sessions</span></div>
  <div class="stat-item"><span class="stat-num">{stats['days']}</span><span class="stat-label">工作天</span></div>
</div>
<div class="layout">
  <p class="lead">{report['hook']}</p>
  <div class="divider">· · ·</div>
  {sections_html}
  <p class="next-week">下週預計：{report['next_week']}</p>
</div>
<div class="post-nav">
  <span class="disabled">← 上一篇</span>
  <a href="../index.html">回首頁</a>
  <span class="disabled">下一篇 →</span>
</div>
<footer class="site-footer">
  由 游泰仁 撰寫 · 游淳惠 Duna You · {monday.year}-{monday.month:02d}-{sunday.day:02d}
</footer>
</body>
</html>"""


def update_index(all_posts):
    """重建 index.html，把所有週報列進去"""
    cards_html = ""
    for i, p in enumerate(reversed(all_posts)):
        cards_html += f"""
    <a class="post-card" href="reports/{p['filename']}">
      <div class="card-meta">
        <span class="card-num">＃{p['number']}</span>
        {p['date_range']}
      </div>
      <h2>{p['title']}</h2>
      <p class="card-summary">{p['summary']}</p>
      <div class="card-stats">
        <span>✦ {p['projects']} 個專案</span>
        <span>✦ {p['sessions']} Sessions</span>
      </div>
    </a>
"""

    sidebar_links = ""
    for p in reversed(all_posts):
        sidebar_links += f'<li><a href="reports/{p["filename"]}"><span class="week-num">W{p["week"]}</span>{p["date_range"]}</a></li>\n'

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>游泰仁的週報 · Duna 的 AI 分身工作紀錄</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Noto+Serif+TC:wght@400;700&family=Noto+Sans+TC:wght@300;400;500&display=swap');
  :root{{--ink:#1a1a1a;--muted:#888;--accent:#c0392b;--bg:#faf8f5;--border:#e8e0d5;--card:#ffffff;}}
  *{{margin:0;padding:0;box-sizing:border-box;}}
  body{{background:var(--bg);color:var(--ink);font-family:'Noto Sans TC',sans-serif;font-weight:300;min-height:100vh;}}
  .site-header{{background:var(--ink);color:#fff;padding:40px 24px 36px;text-align:center;}}
  .site-header .blog-name{{font-family:'Noto Serif TC',serif;font-size:clamp(22px,4vw,32px);font-weight:700;margin-bottom:8px;}}
  .site-header .blog-desc{{font-size:13px;opacity:.45;letter-spacing:.08em;}}
  .layout{{max-width:960px;margin:0 auto;padding:48px 24px 80px;display:grid;grid-template-columns:1fr 260px;gap:48px;align-items:start;}}
  @media(max-width:700px){{.layout{{grid-template-columns:1fr;}}.sidebar{{order:-1;}}}}
  .section-label{{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);margin-bottom:24px;padding-bottom:12px;border-bottom:1px solid var(--border);}}
  .post-card{{display:block;text-decoration:none;color:inherit;border:1px solid var(--border);border-radius:6px;padding:24px;margin-bottom:16px;background:#fff;transition:background .15s;}}
  .post-card:hover{{background:#f0ece6;}}
  .post-card .card-meta{{font-size:11px;letter-spacing:.1em;color:var(--muted);margin-bottom:8px;display:flex;align-items:center;gap:10px;}}
  .post-card .card-num{{background:var(--accent);color:#fff;font-size:10px;padding:2px 7px;border-radius:2px;}}
  .post-card h2{{font-family:'Noto Serif TC',serif;font-size:clamp(16px,2.5vw,20px);font-weight:700;line-height:1.4;margin-bottom:10px;}}
  .post-card .card-summary{{font-size:13px;color:#555;line-height:1.8;}}
  .post-card .card-stats{{display:flex;gap:16px;margin-top:14px;padding-top:14px;border-top:1px solid var(--border);font-size:12px;color:var(--muted);}}
  .sidebar{{position:sticky;top:24px;}}
  .sidebar-section{{margin-bottom:32px;}}
  .sidebar-title{{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);margin-bottom:14px;padding-bottom:10px;border-bottom:1px solid var(--border);}}
  .sidebar-links{{list-style:none;}}
  .sidebar-links li{{margin-bottom:2px;}}
  .sidebar-links a{{display:flex;align-items:center;gap:8px;text-decoration:none;color:#444;font-size:13px;padding:6px 10px;border-radius:4px;transition:background .12s;}}
  .sidebar-links a:hover{{background:#f0ece6;}}
  .week-num{{font-size:10px;color:var(--accent);font-weight:500;min-width:28px;}}
  .about-box{{background:#fff;border:1px solid var(--border);border-radius:6px;padding:20px;font-size:13px;color:#555;line-height:1.8;}}
  .site-footer{{border-top:1px solid var(--border);padding:24px;text-align:center;font-size:12px;color:var(--muted);}}
</style>
</head>
<body>
<header class="site-header">
  <div class="blog-name">游泰仁的週報</div>
  <div class="blog-desc">Duna 的 AI 分身 · 每週日自動生成 · 工作紀錄</div>
</header>
<div class="layout">
  <main class="post-list">
    <div class="section-label">所有週報 · 由新到舊</div>
    {cards_html}
  </main>
  <aside class="sidebar">
    <div class="sidebar-section">
      <div class="sidebar-title">所有週次</div>
      <ul class="sidebar-links">{sidebar_links}</ul>
    </div>
    <div class="sidebar-section">
      <div class="sidebar-title">關於這個部落格</div>
      <div class="about-box"><strong>游泰仁</strong>是 Duna 游淳惠的 AI 助理。<br><br>每週日自動整理這週完成的專案、對話紀錄、觀察與感受，生成一篇工作週報。<br><br>不是給別人看的，是給 Duna 自己的。</div>
    </div>
  </aside>
</div>
<footer class="site-footer">由 游泰仁 自動生成 · Duna You · 每週日更新</footer>
</body>
</html>""")


def send_line_notification(title, url):
    message = f"📋 游泰仁週報出爐了\n\n「{title}」\n\n{url}"
    requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"},
        json={"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]},
    )


def load_post_registry():
    if os.path.exists("posts.json"):
        with open("posts.json") as f:
            return json.load(f)
    return []


def save_post_registry(posts):
    with open("posts.json", "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)


def main():
    monday, sunday = get_week_range()
    week_num = get_week_number()
    print(f"生成 {monday} ~ {sunday} 的週報（W{week_num}）")

    logs = fetch_this_week_logs()
    print(f"找到 {len(logs)} 筆工作日誌")

    if not logs:
        print("本週無日誌，跳過生成")
        return

    report = generate_report_with_claude(logs, week_num, monday, sunday)
    print(f"標題：{report['title']}")

    posts = load_post_registry()
    post_number = len(posts) + 1
    filename = f"{monday.year}-W{week_num:02d}.html"

    stats = {
        "projects": len({p for l in logs for p in l["projects"]}),
        "sessions": sum(l["sessions"] for l in logs),
        "days": len({l["date"] for l in logs}),
    }

    html = render_html(report, week_num, monday, sunday, post_number, stats)
    os.makedirs("reports", exist_ok=True)
    with open(f"reports/{filename}", "w", encoding="utf-8") as f:
        f.write(html)

    posts.append({
        "number": post_number,
        "week": week_num,
        "filename": filename,
        "title": report["title"],
        "summary": report["hook"][:80] + "…",
        "date_range": f"{monday.strftime('%m.%d')} – {sunday.strftime('%m.%d')}",
        "projects": stats["projects"],
        "sessions": stats["sessions"],
    })
    save_post_registry(posts)
    update_index(posts)

    blog_url = f"https://dunayou.github.io/weekly-report/reports/{filename}"
    send_line_notification(report["title"], blog_url)
    print(f"完成！{blog_url}")


if __name__ == "__main__":
    main()
