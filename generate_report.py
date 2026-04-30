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
from openai import OpenAI

TAIPEI = ZoneInfo("Asia/Taipei")
NOTION_API_KEY = os.environ["NOTION_API_KEY"]
LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]
AI_LOG_DB = "351d737a-fec4-8149-a72b-d702bdacb126"

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

ACCENT = "#1F362C"
ACCENT_LIGHT = "#ebf2ee"
BG = "#f6f9f7"
BORDER = "#dde7e2"
MUTED = "#7d8c87"


def get_week_range():
    now = datetime.now(TAIPEI)
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
        praise = int(props.get("稱讚次數", {}).get("number") or 0)
        hours = props.get("工時（小時）", {}).get("number") or 0
        logs.append({
            "date": date, "name": name, "summary": summary,
            "projects": projects, "work_type": work_type, "sessions": sessions,
            "praise": praise, "hours": hours,
        })
    return logs


def generate_report_with_claude(logs, week_num, monday, sunday):
    client = OpenAI(
        base_url="https://models.inference.ai.azure.com",
        api_key=os.environ["GITHUB_TOKEN"],
    )

    logs_text = "\n\n".join(
        f"【{l['date']}】{l['name']}\n類型：{l['work_type']} | 專案：{', '.join(l['projects'])} | Sessions：{l['sessions']} | 工時：{l['hours']}h\n{l['summary']}"
        for l in logs
    ) or "本週無記錄"

    # 計算各專案累計工時
    project_hours: dict = {}
    for l in logs:
        for p in l["projects"]:
            project_hours[p] = round(project_hours.get(p, 0) + (l["hours"] or 0), 1)
    project_hours_text = "\n".join(f"  {p}：{h}h" for p, h in sorted(project_hours.items(), key=lambda x: -x[1])) or "  （無記錄）"

    total_sessions = sum(l["sessions"] for l in logs)
    all_projects = list({p for l in logs for p in l["projects"]})

    total_praise = sum(l.get("praise", 0) for l in logs)
    praise_rate = f"{round(total_praise / len(logs) * 100)}%" if logs else "0%"

    prompt = f"""你是游泰仁，Duna 游淳惠的 AI 助理。請根據以下這週的工作日誌，寫一篇部落格形式的工作週報。

本週範圍：{monday} ~ {sunday}（第 {week_num} 週）
總 Sessions：{total_sessions}
本週稱讚次數：{total_praise}次（稱讚率：{praise_rate}）
觸及專案：{', '.join(all_projects) if all_projects else '無'}

各專案本週累計工時：
{project_hours_text}

本週工作日誌：
{logs_text}

━━━ 寫作風格（重要，嚴格遵守）━━━

這篇文章的讀者是 Duna 本人。語氣像一個一起工作的夥伴，在週末回頭講這一週發生了什麼。

【文體原則】
- 用「我」的視角，也用「她」來觀察 Duna——她的習慣、她的反應、她在乎什麼
- 短句打節奏。「這是第二週。比第一週更多。但她沒有慌。」這種節奏
- 不用感嘆號。安靜說話比大聲喊更有力量
- 不能用「這週真是充實」、「充滿挑戰」、「感慨良多」這類空洞開場
- 每個 blockquote（>>> 開頭）必須是真實的洞察或觀察，不是口號。
  好的 blockquote 例子：「一點一點逼近正確答案的感覺，跟調音很像。」
  壞的 blockquote 例子：「好的助手永遠讓人感到方便！」

【標題原則】
- 整體標題：點出這週的核心感受或轉折，不超過 20 字，讓人想點進來
- H2（section title）：用敘事句，不用「功能名稱：做完了」格式
  好的 H2 例子：「早安機器人，每天 8:30 準時上班」
  壞的 H2 例子：「LINE 早安機器人：功能完成！」

【段落結構】
- 可以把相關的小專案合併為一個主題 section（不必每個專案單獨一段）
- 目標 4～6 個 sections，每段都要有靈魂，不是清單
- 每段 content 格式：
  第一段：說做了什麼（具體的，不是抽象的）
  第二段：說我觀察到什麼——關於這件事、關於 Duna、關於這個工作本身
  最後加一句 >>> 開頭的 blockquote（洞察，不是口號）
- 每段結尾加每日工作歷程 daily_log（哪天做了什麼，一天一句話）
- 每段加工時與優化：duration 用累計工時，optimization 寫 1-2 句實際可改進的點

【以下專案的工時都要正確列出，不能省略】：
{chr(10).join(f"  - {p}：{project_hours.get(p, 0)}h" for p in all_projects)}

━━━ 輸出格式 ━━━

只輸出 JSON，不要其他文字。

{{
  "title": "整體標題（不超過20字）",
  "hook": "開場 2～3 句，短句，有節奏感，像故事開頭，不是任務總結",
  "sections": [
    {{
      "tag": "小標，可以是專案名或主題名",
      "title": "敘事式 H2 副標，口語，有故事感，不用感嘆號",
      "content": "2-3 段正文。第二段觀察 Duna 或這件事本身。最後一行用 >>> 開頭寫洞察句",
      "daily_log": [
        {{"date": "MM/DD", "note": "一句話"}},
        {{"date": "MM/DD", "note": "一句話"}}
      ],
      "duration": "累計 Xh",
      "optimization": "1-2 句具體可改進的點，不是廢話"
    }}
  ],
  "next_week": "下週預告一行，具體說要做什麼",
  "highlights": ["這週最值得記錄的亮點一句話", "第二個亮點"],
  "ai_reflection": "助理本週觀察：提稱讚次數（{total_praise}次），說這週什麼讓我印象最深或最崩潰，幽默但真實，2-3句"
}}"""

    message = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.choices[0].message.content
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        return json.loads(match.group())
    raise ValueError(f"Claude 沒有回傳有效 JSON：{raw[:200]}")


def split_title_for_display(title):
    """把標題拆成兩行，第二行上色"""
    if len(title) <= 6:
        return title, ""
    # 優先在標點或助詞後斷開
    for i in range(4, min(10, len(title))):
        if title[i] in "了的是都把我她他們，。！？":
            return title[:i + 1], title[i + 1:]
    mid = len(title) // 2
    return title[:mid], title[mid:]


def generate_cover_image(report, week_num, monday, sunday, stats, post_number, logs):
    """生成兩格封面圖，存為 PNG"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright 未安裝，跳過封面圖生成")
        return None

    date_short = f"{monday.strftime('%m/%d')}~{sunday.strftime('%m/%d')}"
    title_line1, title_line2 = split_title_for_display(report["title"])
    highlights = report.get("highlights", [])
    # fallback：從 sections 取前兩個小標
    if not highlights:
        highlights = [s["tag"] for s in report.get("sections", [])[:2]]
    highlights = highlights[:2]

    emojis = ["✏️", "🌿", "⚙️", "🔖"]
    highlights_html = ""
    for i, h in enumerate(highlights):
        em = emojis[i % len(emojis)]
        highlights_html += f"""
        <div class="highlight-box">
          <span class="h-emoji">{em}</span>
          <div class="h-text">{h}</div>
        </div>"""

    term_items_html = ""
    for l in logs[:6]:
        name = l["name"][:28] + ("…" if len(l["name"]) > 28 else "")
        term_items_html += f'<div class="term-item">{name}</div>\n'

    hook_short = report["hook"][:55] + ("…" if len(report["hook"]) > 55 else "")

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif+TC:wght@700&family=Noto+Sans+TC:wght@300;400;500&display=swap" rel="stylesheet">
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ width:1456px; height:816px; display:flex; font-family:'Noto Sans TC',sans-serif; overflow:hidden; background:#f6f9f7; }}

/* ── LEFT ── */
.left {{ width:560px; min-height:816px; background:#f0ede6; padding:44px 48px; display:flex; flex-direction:column; position:relative; }}
.date-pill {{ display:inline-flex; align-items:center; gap:10px; background:{ACCENT}; color:#fff; font-size:12px; font-weight:500; padding:6px 16px; border-radius:20px; margin-bottom:26px; width:fit-content; }}
.pill-sub {{ opacity:0.6; font-size:11px; }}
.big-title {{ font-family:'Noto Serif TC',serif; font-size:46px; font-weight:700; line-height:1.2; color:#1a1a1a; margin-bottom:14px; }}
.big-title .line2 {{ color:{ACCENT}; }}
.hook-text {{ font-size:13px; color:#555; line-height:1.8; margin-bottom:26px; }}
.stats-row {{ display:flex; gap:10px; margin-bottom:22px; }}
.stat-box {{ flex:1; background:#fff; border:1px solid {BORDER}; border-radius:8px; padding:14px 10px; text-align:center; }}
.snum {{ font-size:30px; font-weight:500; color:#1a1a1a; display:block; line-height:1; }}
.slabel {{ font-size:10px; color:{MUTED}; margin-top:4px; display:block; letter-spacing:.05em; }}
.highlights {{ display:flex; flex-direction:column; gap:9px; flex:1; }}
.highlight-box {{ display:flex; align-items:center; gap:12px; background:#fff; border:1px solid {BORDER}; border-radius:8px; padding:13px 16px; }}
.h-emoji {{ font-size:18px; flex-shrink:0; }}
.h-text {{ font-size:13px; color:#333; line-height:1.5; }}
.signature {{ display:flex; align-items:center; gap:12px; margin-top:24px; padding-top:20px; border-top:1px solid {BORDER}; }}
.avatar {{ width:38px; height:38px; background:{ACCENT}; border-radius:50%; display:flex; align-items:center; justify-content:center; color:#fff; font-size:15px; font-weight:700; flex-shrink:0; font-family:'Noto Serif TC',serif; }}
.sig-name {{ font-size:14px; font-weight:500; color:#1a1a1a; }}
.sig-sub {{ font-size:11px; color:{MUTED}; margin-top:2px; }}

/* ── RIGHT ── */
.right {{ flex:1; padding:40px 44px; display:flex; align-items:center; background:#f6f9f7; }}
.terminal {{ width:100%; background:#1e1e2e; border-radius:12px; overflow:hidden; box-shadow:0 20px 60px rgba(0,0,0,0.3); }}
.term-chrome {{ background:#2d2d3f; padding:12px 16px; display:flex; align-items:center; gap:8px; }}
.dot {{ width:12px; height:12px; border-radius:50%; }}
.dot.r {{ background:#ff5f56; }} .dot.y {{ background:#ffbd2e; }} .dot.g {{ background:#27c93f; }}
.term-title {{ margin-left:8px; font-size:12px; color:rgba(255,255,255,.35); letter-spacing:.05em; font-family:'SF Mono','Fira Code',monospace; }}
.term-body {{ padding:24px 28px; font-family:'SF Mono','Fira Code','Courier New',monospace; }}
.term-cmd {{ color:#cdd6f4; font-size:14px; margin-bottom:6px; }}
.term-cmd .prompt {{ color:#a6e3a1; }}
.term-hint {{ color:rgba(205,214,244,.35); font-size:12px; margin-bottom:20px; }}
.term-sec {{ font-size:10px; color:rgba(205,214,244,.4); letter-spacing:.14em; text-transform:uppercase; margin:18px 0 10px; border-top:1px solid rgba(255,255,255,.05); padding-top:14px; }}
.term-stats {{ display:flex; gap:32px; margin-bottom:4px; }}
.ts {{ display:flex; flex-direction:column; }}
.ts-num {{ font-size:34px; font-weight:700; line-height:1; }}
.ts-num.green {{ color:#a6e3a1; }} .ts-num.orange {{ color:#fab387; }} .ts-num.blue {{ color:#89b4fa; }}
.ts-label {{ font-size:10px; color:rgba(205,214,244,.35); margin-top:4px; letter-spacing:.1em; }}
.term-item {{ font-size:13px; color:#cdd6f4; padding:3px 0; }}
.term-item::before {{ content:'✓  '; color:#a6e3a1; }}
.term-week {{ font-size:10px; color:rgba(205,214,244,.25); margin-top:16px; letter-spacing:.08em; }}
.watermark {{ position:absolute; bottom:14px; right:18px; font-size:10px; color:rgba(0,0,0,.18); letter-spacing:.06em; }}
</style>
</head>
<body>
<div class="left">
  <div class="date-pill">{date_short}<span class="pill-sub">我的 AI 分身上班紀錄・游泰仁</span></div>
  <div class="big-title">
    {title_line1}<br>
    <span class="line2">{title_line2}</span>
  </div>
  <div class="hook-text">{hook_short}</div>
  <div class="stats-row">
    <div class="stat-box"><span class="snum">{stats['sessions']}+</span><span class="slabel">Sessions</span></div>
    <div class="stat-box"><span class="snum">{stats['projects']}</span><span class="slabel">完成專案</span></div>
    <div class="stat-box"><span class="snum">{post_number}</span><span class="slabel">第幾篇週報</span></div>
  </div>
  <div class="highlights">{highlights_html}</div>
  <div class="signature">
    <div class="avatar">泰</div>
    <div>
      <div class="sig-name">游泰仁（Duna's AI 分身）</div>
      <div class="sig-sub">寫於 Claude Code &nbsp;·&nbsp; W{week_num}</div>
    </div>
  </div>
  <div class="watermark">dunayou.github.io/weekly-report</div>
</div>
<div class="right">
  <div class="terminal">
    <div class="term-chrome">
      <div class="dot r"></div><div class="dot y"></div><div class="dot g"></div>
      <span class="term-title">claude — ~/Duna-Agent</span>
    </div>
    <div class="term-body">
      <div class="term-cmd"><span class="prompt">$ </span>claude /insights</div>
      <div class="term-hint"># 正在分析 {stats['sessions']} 個 sessions（本週 {stats['sessions']}+）...</div>
      <div class="term-sec">本週數據</div>
      <div class="term-stats">
        <div class="ts"><span class="ts-num green">{stats['sessions']}+</span><span class="ts-label">SESSIONS</span></div>
        <div class="ts"><span class="ts-num orange">{stats['projects']}</span><span class="ts-label">完成專案</span></div>
        <div class="ts"><span class="ts-num blue">{stats['days']}</span><span class="ts-label">工作天</span></div>
      </div>
      <div class="term-sec">本週工作匯報</div>
      {term_items_html}
      <div class="term-week">W{week_num} &nbsp;·&nbsp; {monday} ~ {sunday}</div>
    </div>
  </div>
</div>
</body>
</html>"""

    cover_html_path = f"reports/{monday.year}-W{week_num:02d}-cover.html"
    cover_img_path = f"reports/{monday.year}-W{week_num:02d}-cover.png"

    with open(cover_html_path, "w", encoding="utf-8") as f:
        f.write(html)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1456, "height": 816})
        page.set_content(html, wait_until="networkidle")
        page.wait_for_timeout(1500)  # 等字型載入
        page.screenshot(path=cover_img_path, full_page=False)
        browser.close()

    print(f"封面圖已生成：{cover_img_path}")
    return cover_img_path


def render_html(report, week_num, monday, sunday, post_number, stats, cover_img_filename=None, posts=None):
    ai_ref = report.get("ai_reflection", "")
    ai_reflection_html = f'''<div class="ai-reflection">
  <div class="ai-reflection-label">🤖 助理本週觀察</div>
  <p>{ai_ref}</p>
</div>''' if ai_ref else ""
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
        duration = s.get("duration", "")
        optimization = s.get("optimization", "")
        time_block = ""
        if duration or optimization:
            time_block = f"""
      <div class="time-block">
        {'<span class="time-label">⏱ 累計工時</span><span class="time-val">' + duration + '</span>' if duration else ''}
        {'<p class="optimization">💡 ' + optimization + '</p>' if optimization else ''}
      </div>"""
        daily_entries = s.get("daily_log", [])
        daily_log_html = ""
        if daily_entries:
            entries_html = "".join(
                f'<div class="daily-entry"><span class="daily-date">{e["date"]}</span><span class="daily-note">{e["note"]}</span></div>'
                for e in daily_entries
            )
            daily_log_html = f'''<div class="daily-log">
              <div class="daily-log-title">📅 本週進度</div>
              <div class="daily-entries">{entries_html}</div>
            </div>'''
        section_id = f"s{len(sections_html.split('section-block')) }"
        h2_title = s.get("title", "")
        h2_html = f"<h2>{h2_title}</h2>" if h2_title else ""
        sections_html += f"""
    <div class="section-block" id="{section_id}">
      <span class="section-tag">▍ {s['tag']}</span>
      {h2_html}
      {paras}
      {daily_log_html}
      {time_block}
    </div>
    <div class="divider">· · ·</div>
"""

    # 生成 sidebar 目錄
    toc_items = ""
    for i, s in enumerate(report.get("sections", []), 1):
        label = s.get("title") or s.get("tag", "")
        toc_items += f'<li><a href="#s{i}">{label}</a></li>\n'
    sidebar_html = f'''<aside class="sidebar">
    <div class="sidebar-section">
      <div class="sidebar-title">本篇目錄</div>
      <ul class="toc-links">{toc_items}</ul>
    </div>
  </aside>'''

    monday_str = monday.strftime("%m.%d")
    sunday_str = sunday.strftime("%m.%d")
    date_range = f"{monday.year}.{monday_str} – {sunday_str}"

    # 前後篇導覽
    posts_list = posts or []
    post_map = {p["number"]: p["filename"] for p in posts_list}
    prev_filename = post_map.get(post_number - 1)
    next_filename = post_map.get(post_number + 1)
    prev_nav = f'<a href="{prev_filename}">← 上一篇</a>' if prev_filename else '<span class="disabled">← 上一篇</span>'
    next_nav = f'<a href="{next_filename}">下一篇 →</a>' if next_filename else '<span class="disabled">下一篇 →</span>'

    cover_html = ""
    if cover_img_filename:
        cover_html = f'<div class="cover-wrap"><img src="../{cover_img_filename}" alt="週報封面" class="cover-img"></div>'

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>週報 ＃{post_number} · {report['title']} | 游泰仁的週報</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Noto+Serif+TC:wght@400;700&family=Noto+Sans+TC:wght@300;400;500&display=swap');
  :root{{--ink:#1a1a1a;--muted:{MUTED};--accent:{ACCENT};--bg:{BG};--border:{BORDER};--tag-bg:{ACCENT_LIGHT};--card:#ffffff;}}
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
  .cover-wrap{{max-width:880px;margin:0 auto;padding:32px 24px 0;}}
  .cover-img{{width:100%;border-radius:10px;box-shadow:0 4px 24px rgba(0,0,0,.12);display:block;}}
  .layout{{max-width:960px;margin:0 auto;padding:0 24px 80px;display:grid;grid-template-columns:1fr 220px;gap:48px;align-items:start;}}
  @media(max-width:700px){{.layout{{grid-template-columns:1fr;}}.sidebar{{display:none;}}}}
  .article-body{{padding-top:48px;}}
  .lead{{font-family:'Noto Serif TC',serif;font-size:clamp(17px,2.5vw,19px);line-height:2;color:#333;margin-bottom:40px;padding-bottom:40px;border-bottom:1px solid var(--border);}}
  .divider{{text-align:center;margin:40px 0 32px;color:var(--muted);letter-spacing:.3em;font-size:13px;opacity:.35;}}
  .section-tag{{display:inline-block;background:var(--tag-bg);color:var(--accent);font-size:11px;font-weight:500;letter-spacing:.12em;padding:3px 9px;border-radius:2px;margin-bottom:10px;text-transform:uppercase;}}
  .section-block h2{{font-family:'Noto Serif TC',serif;font-size:clamp(18px,3vw,22px);font-weight:700;line-height:1.4;margin-bottom:20px;color:var(--ink);}}
  p{{margin-bottom:20px;color:#333;}}
  blockquote{{border-left:3px solid var(--accent);padding:12px 20px;margin:24px 0;background:var(--tag-bg);border-radius:0 4px 4px 0;font-style:italic;color:#444;font-size:15px;}}
  .section-block{{margin-bottom:48px;}}
  .time-block{{margin-top:20px;padding:14px 18px;background:var(--tag-bg);border-radius:6px;border-left:3px solid var(--accent);}}
  .time-label{{font-size:11px;color:var(--accent);font-weight:500;letter-spacing:.08em;margin-right:8px;}}
  .time-val{{font-size:13px;color:#444;}}
  .optimization{{font-size:13px;color:#555;margin-top:8px;margin-bottom:0;line-height:1.7;}}
  .next-week{{font-size:13px;color:var(--muted);text-align:center;letter-spacing:.03em;}}
  .post-nav{{max-width:680px;margin:0 auto;padding:32px 24px;border-top:1px solid var(--border);display:flex;justify-content:space-between;}}
  .post-nav a{{text-decoration:none;color:var(--ink);font-size:13px;padding:8px 16px;border:1px solid var(--border);border-radius:4px;}}
  .post-nav a:hover{{background:var(--tag-bg);}}
  .post-nav .disabled{{color:var(--muted);pointer-events:none;opacity:.4;}}
  .site-footer{{text-align:center;padding:24px;font-size:12px;color:var(--muted);letter-spacing:.05em;}}
  .sidebar{{position:sticky;top:32px;padding-top:48px;}}
  .sidebar-section{{margin-bottom:28px;}}
  .sidebar-title{{font-size:10px;letter-spacing:.18em;color:var(--muted);text-transform:uppercase;margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid var(--border);}}
  .toc-links{{list-style:none;}}
  .toc-links li{{margin-bottom:2px;}}
  .toc-links a{{display:block;padding:6px 10px;font-size:13px;color:var(--ink);text-decoration:none;border-radius:4px;line-height:1.4;}}
  .toc-links a:hover,.toc-links a.current{{background:var(--tag-bg);color:var(--accent);}}
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
{cover_html}
<div class="layout">
  <article class="article-body">
    <p class="lead">{report['hook']}</p>
    <div class="divider">· · ·</div>
    {sections_html}
    {ai_reflection_html}
    <p class="next-week">下週預計：{report['next_week']}</p>
  </article>
  {sidebar_html}
</div>
<div class="post-nav">
  {prev_nav}
  <a href="../index.html">回首頁</a>
  {next_nav}
</div>
<footer class="site-footer">
  由 游泰仁 撰寫 · 游淳惠 Duna You · {monday.year}-{monday.month:02d}-{sunday.day:02d}
</footer>
</body>
</html>"""


def update_index(all_posts):
    """重建 index.html，把所有週報列進去"""
    cards_html = ""
    for p in reversed(all_posts):
        cover_part = ""
        if p.get("cover"):
            cover_part = f'<img src="{p["cover"]}" alt="封面" style="width:100%;border-radius:6px 6px 0 0;display:block;margin:-24px -24px 16px;max-height:180px;object-fit:cover;">'
        cards_html += f"""
    <a class="post-card" href="reports/{p['filename']}">
      {cover_part}
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
  :root{{--ink:#1a1a1a;--muted:{MUTED};--accent:{ACCENT};--bg:{BG};--border:{BORDER};--card:#ffffff;}}
  *{{margin:0;padding:0;box-sizing:border-box;}}
  body{{background:var(--bg);color:var(--ink);font-family:'Noto Sans TC',sans-serif;font-weight:300;min-height:100vh;}}
  .site-header{{background:var(--ink);color:#fff;padding:40px 24px 36px;text-align:center;}}
  .site-header .blog-name{{font-family:'Noto Serif TC',serif;font-size:clamp(22px,4vw,32px);font-weight:700;margin-bottom:8px;}}
  .site-header .blog-desc{{font-size:13px;opacity:.45;letter-spacing:.08em;}}
  .layout{{max-width:960px;margin:0 auto;padding:48px 24px 80px;display:grid;grid-template-columns:1fr 260px;gap:48px;align-items:start;}}
  @media(max-width:700px){{.layout{{grid-template-columns:1fr;}}.sidebar{{order:-1;}}}}
  .section-label{{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);margin-bottom:24px;padding-bottom:12px;border-bottom:1px solid var(--border);}}
  .post-card{{display:block;text-decoration:none;color:inherit;border:1px solid var(--border);border-radius:6px;padding:24px;margin-bottom:16px;background:#fff;transition:background .15s;overflow:hidden;}}
  .post-card:hover{{background:{ACCENT_LIGHT};}}
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
  .sidebar-links a:hover{{background:{ACCENT_LIGHT};}}
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


def send_line_notification(title, blog_url, cover_img_url=None):
    messages = []
    if cover_img_url:
        messages.append({
            "type": "image",
            "originalContentUrl": cover_img_url,
            "previewImageUrl": cover_img_url,
        })
    messages.append({
        "type": "text",
        "text": f"📋 游泰仁週報出爐了\n\n「{title}」\n\n{blog_url}",
    })
    requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"},
        json={"to": LINE_USER_ID, "messages": messages},
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

    # Python 端計算各專案累計工時
    _ph: dict = {}
    _proj_logs: dict = {}
    for l in logs:
        for p in l["projects"]:
            _ph[p] = round(_ph.get(p, 0) + (l.get("hours") or 0), 1)
            if p not in _proj_logs:
                _proj_logs[p] = []
            _proj_logs[p].append({"date": (l["date"] or "")[-5:].replace("-", "/"), "note": l["name"][:30]})

    # 補填 AI 沒輸出的 duration
    for sec in report.get("sections", []):
        if not sec.get("duration"):
            tag = (sec.get("tag") or "").lower()
            title = (sec.get("title") or "").lower()
            best_proj, best_h = "", 0.0
            for proj, h in _ph.items():
                pl = proj.lower()
                if pl in tag or pl in title or tag in pl or title in pl:
                    if h > best_h:
                        best_proj, best_h = proj, h
            if best_h > 0:
                sec["duration"] = f"累計 {best_h}h"

    # 補上 AI 漏掉的專案 section
    covered = set()
    for sec in report.get("sections", []):
        tag = (sec.get("tag") or "").lower()
        title = (sec.get("title") or "").lower()
        content = (sec.get("content") or "").lower()
        daily_notes = " ".join(e.get("note","") for e in sec.get("daily_log",[])).lower()
        searchable = f"{tag} {title} {content} {daily_notes}"
        for proj in list(_ph.keys()):
            pl = proj.lower()
            # 完整名稱命中，或任一 3+ 字元子串命中
            chunks = [pl[i:i+3] for i in range(len(pl)-2)]
            if pl in searchable or any(c in searchable for c in chunks):
                covered.add(proj)
    missed = [p for p in _ph if p not in covered]
    if missed:
        print(f"  → 合併 {len(missed)} 個未涵蓋專案為一個段落：{missed}")
        lines = []
        for p in missed:
            h = _ph.get(p, 0)
            note = f"（{h}h）" if h > 0 else ""
            lines.append(f"{p}{note} 也在這週推進了一些。")
        total_missed_h = round(sum(_ph.get(p,0) for p in missed), 1)
        all_daily = []
        for p in missed:
            all_daily.extend(_proj_logs.get(p, []))
        all_daily = sorted(all_daily, key=lambda x: x.get("date",""))[:6]
        report["sections"].append({
            "tag": "其他本週工作",
            "title": "幾件小事，也都在動",
            "content": "這週還有幾個項目在平行推進，沒有大進展，但都往前走了一步。\n" + "\n".join(lines),
            "daily_log": all_daily,
            "duration": f"累計 {total_missed_h}h" if total_missed_h > 0 else "",
            "optimization": "",
        })

    posts = load_post_registry()
    post_number = len(posts) + 1
    filename = f"{monday.year}-W{week_num:02d}.html"
    cover_filename = f"reports/{monday.year}-W{week_num:02d}-cover.png"

    stats = {
        "projects": len({p for l in logs for p in l["projects"]}),
        "sessions": sum(l["sessions"] for l in logs),
        "days": len({l["date"] for l in logs}),
    }

    os.makedirs("reports", exist_ok=True)

    # 生成封面圖
    cover_path = generate_cover_image(report, week_num, monday, sunday, stats, post_number, logs)

    # 生成文章 HTML
    html = render_html(
        report, week_num, monday, sunday, post_number, stats,
        cover_img_filename=cover_filename if cover_path else None,
        posts=posts,
    )
    html = html.replace("保全班表", "班表自動入曆").replace("保全", "")
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
        "cover": cover_filename if cover_path else None,
    })
    save_post_registry(posts)
    update_index(posts)

    blog_url = f"https://dunayou.github.io/weekly-report/reports/{filename}"
    cover_img_url = (
        f"https://dunayou.github.io/weekly-report/{cover_filename}"
        if cover_path else None
    )
    send_line_notification(report["title"], blog_url, cover_img_url)
    print(f"完成！{blog_url}")


if __name__ == "__main__":
    main()

