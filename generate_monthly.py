#!/usr/bin/env python3
"""游泰仁月報生成腳本 — 每月 1 日由 cron-job.org 觸發 workflow_dispatch"""

import os
import json
import requests
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
import anthropic

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

GH_PAT_EXPIRY = date(2099, 1, 1)        # 永不到期，更新 PAT 時記得改這行
ANTHROPIC_FREE_CREDIT = 5.00            # 初始免費額度 USD（手動更新）
ANTHROPIC_CREDIT_START = date(2026, 5, 1)  # 免費 Credit 發放日


def get_last_month_range():
    now = datetime.now(TAIPEI)
    first_of_this = now.replace(day=1)
    last_day = first_of_this - timedelta(days=1)
    first_day = last_day.replace(day=1)
    return first_day.date(), last_day.date()


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
    logs = []
    for p in resp.json().get("results", []):
        props = p["properties"]
        date_val = (props.get("日期", {}).get("date") or {}).get("start", "")
        name = "".join(t["plain_text"] for t in props.get("名稱", {}).get("title", []))
        projects = [o["name"] for o in props.get("觸及專案", {}).get("multi_select", [])]
        hours = props.get("工時（小時）", {}).get("number") or 0
        sessions = props.get("對話 Session 數", {}).get("number") or 0
        logs.append({"date": date_val, "name": name, "projects": projects,
                     "hours": hours, "sessions": sessions})
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
        f"【{l['date']}】{l['name']}（{l['hours']}h）"
        for l in logs[:20]
    )
    prompt = f"""你是游泰仁，Duna 游淳惠的 AI 助理。請根據 {month_label} 的工作紀錄，寫一段月度觀察（80-120字）。

本月統計：{stats['sessions']} Sessions、{stats['total_hours']}h、{stats['total_projects']} 個專案

工作紀錄：
{logs_text}

風格：用「我」視角，短句，不用感嘆號，真實有溫度，可以幽默自嘲。
輸出：只輸出純文字段落，不要標題或 JSON。"""
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def render_monthly_html(month_label, first_day, last_day, logs, stats,
                         line_limit, line_used, reflection):
    now = datetime.now(TAIPEI)
    report_date = f"{now.year} 年 {now.month} 月 {now.day} 日"
    period = f"{first_day.year} / {first_day.month:02d} / {first_day.day:02d} ～ {last_day.year} / {last_day.month:02d} / {last_day.day:02d}"

    # LINE 用量計算
    line_used_str = str(line_used) if line_used is not None else "—"
    line_limit_str = str(line_limit) if line_limit else "200"
    line_remain = (line_limit - line_used) if (line_limit and line_used is not None) else None
    line_remain_str = str(line_remain) if line_remain is not None else "—"
    line_pct = int((line_used / line_limit) * 100) if (line_limit and line_used) else 0

    # GH_PAT 到期
    today = datetime.now(TAIPEI).date()
    pat_days = (GH_PAT_EXPIRY - today).days
    pat_note = "永不到期，無需處理" if pat_days > 3650 else (
        f"還有 {pat_days} 天，請盡快更新！" if pat_days > 0 else "已過期！請立即更新！"
    )
    pat_class = "val-green" if pat_days > 3650 else ("val-orange" if pat_days > 30 else "val-orange")

    # Anthropic 剩餘估算（每月約 $2，粗估）
    months_elapsed = (today.year - ANTHROPIC_CREDIT_START.year) * 12 + (today.month - ANTHROPIC_CREDIT_START.month)
    credit_remaining_est = max(0.0, ANTHROPIC_FREE_CREDIT - months_elapsed * 2)
    months_remaining = int(credit_remaining_est / 2) if credit_remaining_est > 0 else 0

    # 摘要卡：費用儀表板邏輯
    if credit_remaining_est > 0:
        current_spend_val = "$0"
        current_spend_sub = "所有 API 服務皆在免費額度內"
        future_spend_val = "NT$65"
        future_spend_sub = f"約 {months_remaining} 個月後｜Anthropic 免費額度用完"
        credit_months_str = f"可再撐約 {months_remaining} 個月"
    else:
        current_spend_val = "~US$2"
        current_spend_sub = "Anthropic API 按量計費中"
        future_spend_val = "NT$65"
        future_spend_sub = "Anthropic API 每月約 US$2，按量計費"
        credit_months_str = "已用完，按量計費"

    # 本月工時分佈（取前 8 個專案）
    project_hours: dict = {}
    for l in logs:
        for p in l["projects"]:
            project_hours[p] = round(project_hours.get(p, 0) + (l["hours"] or 0), 1)
    top_projects = sorted(project_hours.items(), key=lambda x: -x[1])[:8]
    proj_rows = ""
    for proj, h in top_projects:
        proj_rows += f"""<tr>
      <td>{proj}</td>
      <td><span class="val-highlight">{h}h</span></td>
      <td><span class="val-muted">{round(h / stats['total_hours'] * 100) if stats['total_hours'] else 0}%</span></td>
    </tr>"""
    if not proj_rows:
        proj_rows = '<tr><td colspan="3" style="color:#aaa;text-align:center;padding:16px;">本月無記錄</td></tr>'

    # 近期注意事項（動態）
    alerts = []
    if pat_days <= 365 and pat_days != 0:
        alerts.append({
            "date": str(GH_PAT_EXPIRY),
            "item": "GH_PAT 到期",
            "desc": "GitHub → Settings → Developer settings → PAT 更新，並同步到 GitHub Secret 和 Render 環境變數",
            "urgent": pat_days <= 30,
        })
    if credit_remaining_est < 2:
        alerts.append({
            "date": "即將用完",
            "item": "Anthropic 免費額度",
            "desc": f"剩餘估計 US${credit_remaining_est:.2f}，建議儲值 US$5-10",
            "urgent": True,
        })

    alert_rows = ""
    for a in alerts:
        date_class = "val-orange" if a["urgent"] else ""
        alert_rows += f"""<tr>
      <td><span class="{date_class}">{a['date']}</span></td>
      <td>{a['item']}</td>
      <td>{a['desc']}</td>
    </tr>"""
    if not alert_rows:
        alert_rows = '<tr><td colspan="3" style="color:#2e7d32;font-weight:600;padding:12px 20px;">✓ 本月無需處理的事項</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>游泰仁系統 — {month_label} 外部服務用量報告</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: "Helvetica Neue", "PingFang TC", "Microsoft JhengHei", sans-serif;
    background: #f7f9f8;
    color: #1a1a1a;
    padding: 48px;
    font-size: 14px;
    line-height: 1.6;
  }}
  .header {{
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    border-bottom: 2.5px solid #1F362C;
    padding-bottom: 16px;
    margin-bottom: 32px;
  }}
  .header-left h1 {{ font-size: 22px; font-weight: 700; color: #1F362C; letter-spacing: 0.5px; }}
  .header-left p {{ font-size: 12px; color: #7d8c87; margin-top: 4px; }}
  .header-right {{ text-align: right; font-size: 12px; color: #7d8c87; }}
  .header-right .date {{ font-size: 14px; font-weight: 600; color: #1F362C; }}
  .summary-row {{ display: flex; gap: 16px; margin-bottom: 32px; }}
  .summary-card {{
    flex: 1; background: white; border-radius: 10px;
    border: 1px solid #dde7e2; padding: 20px; text-align: center;
  }}
  .summary-card .label {{
    font-size: 11px; color: #7d8c87; text-transform: uppercase;
    letter-spacing: 0.8px; margin-bottom: 8px;
  }}
  .summary-card .amount {{ font-size: 26px; font-weight: 700; color: #1F362C; }}
  .summary-card .sub {{ font-size: 11px; color: #7d8c87; margin-top: 4px; }}
  .summary-card.highlight {{ background: #1F362C; }}
  .summary-card.highlight .label,
  .summary-card.highlight .sub {{ color: #a8c4b8; }}
  .summary-card.highlight .amount {{ color: #ffffff; }}
  .section {{
    background: white; border-radius: 10px;
    border: 1px solid #dde7e2; margin-bottom: 20px; overflow: hidden;
  }}
  .section-header {{
    display: flex; align-items: center; gap: 10px;
    padding: 16px 20px; border-bottom: 1px solid #f0f4f2;
  }}
  .section-icon {{
    width: 32px; height: 32px; border-radius: 8px; background: #eef4f1;
    display: flex; align-items: center; justify-content: center; font-size: 16px;
  }}
  .section-title {{ font-size: 15px; font-weight: 600; color: #1F362C; }}
  .section-badge {{
    margin-left: auto; padding: 3px 10px;
    border-radius: 20px; font-size: 11px; font-weight: 600;
  }}
  .badge-free {{ background: #e8f5e9; color: #2e7d32; }}
  .badge-paid {{ background: #fff3e0; color: #e65100; }}
  .section table {{ width: 100%; border-collapse: collapse; }}
  .section table th {{
    background: #f7f9f8; padding: 10px 20px; text-align: left;
    font-size: 11px; font-weight: 600; color: #7d8c87;
    text-transform: uppercase; letter-spacing: 0.5px;
    border-bottom: 1px solid #eef2f0;
  }}
  .section table td {{
    padding: 11px 20px; border-bottom: 1px solid #f5f8f6;
    font-size: 13px; vertical-align: top;
  }}
  .section table tr:last-child td {{ border-bottom: none; }}
  .section table td:first-child {{ color: #7d8c87; width: 36%; }}
  .val-highlight {{ font-weight: 600; color: #1F362C; }}
  .val-green {{ color: #2e7d32; font-weight: 600; }}
  .val-orange {{ color: #e65100; font-weight: 600; }}
  .val-muted {{ color: #aaa; font-size: 12px; }}
  .note-box {{
    background: #fff8e1; border-left: 3px solid #ffc107;
    border-radius: 0 8px 8px 0; padding: 10px 16px;
    margin: 12px 20px 16px; font-size: 12px; color: #5d4037; line-height: 1.7;
  }}
  .reflection-box {{
    background: #eef4f1; border-left: 3px solid #1F362C;
    border-radius: 0 8px 8px 0; padding: 12px 16px;
    margin: 12px 20px 16px; font-size: 13px; color: #333; line-height: 1.8;
  }}
  .footer {{
    margin-top: 32px; padding-top: 16px;
    border-top: 1px solid #dde7e2;
    display: flex; justify-content: space-between;
    font-size: 11px; color: #bbb;
  }}
  .back-link {{
    display: inline-block; margin-bottom: 24px;
    font-size: 13px; color: #7d8c87; text-decoration: none;
  }}
  .back-link:hover {{ color: #1F362C; }}
</style>
</head>
<body>

<a href="../index.html" class="back-link">← 回游泰仁週報</a>

<div class="header">
  <div class="header-left">
    <h1>游泰仁系統 — 外部服務用量報告</h1>
    <p>涵蓋：早安機器人、LINE 全能助理、週報系統、AI 特助、醫責險要保書</p>
  </div>
  <div class="header-right">
    <div class="date">{report_date}</div>
    <div>統計期間：{period}</div>
  </div>
</div>

<div class="summary-row">
  <div class="summary-card highlight">
    <div class="label">目前每月支出</div>
    <div class="amount">{current_spend_val}</div>
    <div class="sub">{current_spend_sub}</div>
  </div>
  <div class="summary-card">
    <div class="label">預估月費（調整後）</div>
    <div class="amount">{future_spend_val}</div>
    <div class="sub">{future_spend_sub}</div>
  </div>
  <div class="summary-card">
    <div class="label">LINE 推播本月用量</div>
    <div class="amount">{line_used_str} 則</div>
    <div class="sub">上限 {line_limit_str} 則｜剩餘 {line_remain_str} 則</div>
  </div>
  <div class="summary-card">
    <div class="label">Anthropic 剩餘額度</div>
    <div class="amount">US${credit_remaining_est:.2f}</div>
    <div class="sub">{credit_months_str}</div>
  </div>
</div>

<!-- Claude Max -->
<div class="section">
  <div class="section-header">
    <div class="section-icon">🧠</div>
    <div class="section-title">Claude Max（Anthropic 訂閱）</div>
    <span class="section-badge badge-paid">付費 $100/月</span>
  </div>
  <table>
    <tr><th>項目</th><th>數值</th></tr>
    <tr><td>方案</td><td><span class="val-highlight">Claude Max $100/月</span></td></tr>
    <tr><td>使用工具</td><td>Claude Code（CLI + VSCode 擴充）</td></tr>
    <tr><td>主要模型</td><td>claude-sonnet-4-6（日常）/ claude-opus-4-7（高難度任務）</td></tr>
    <tr><td>本月 Sessions</td><td><span class="val-highlight">{stats['sessions']} 個</span></td></tr>
    <tr><td>費用</td><td><span class="val-orange">$100 USD / 月（固定）</span></td></tr>
  </table>
</div>

<!-- LINE Messaging API -->
<div class="section">
  <div class="section-header">
    <div class="section-icon">💬</div>
    <div class="section-title">LINE Messaging API</div>
    <span class="section-badge badge-free">免費</span>
  </div>
  <table>
    <tr><th>項目</th><th>數值</th></tr>
    <tr><td>Bot 名稱</td><td><span class="val-highlight">淳の每日助理 (@320kziqv)</span></td></tr>
    <tr><td>本月配額（上限）</td><td><span class="val-highlight">{line_limit_str} 則</span> push message</td></tr>
    <tr><td>本月已用</td><td><span class="val-highlight">{line_used_str} 則</span>　<span class="val-muted">({line_pct}%)</span></td></tr>
    <tr><td>剩餘配額</td><td><span class="val-green">{line_remain_str} 則</span></td></tr>
    <tr><td>穩定月用量預估</td><td>早安 1 則/天 × 30 天 + 週報 4 則 + 月報 1 則 ＝ 約 <span class="val-highlight">35 則/月</span></td></tr>
    <tr><td>對話回覆（reply）</td><td>不計入配額，完全免費</td></tr>
    <tr><td>費用</td><td><span class="val-green">$0</span>｜用量遠低於 200 則上限</td></tr>
  </table>
  <div class="note-box">⚠️ GH_PAT 狀態：<span class="{pat_class}">{pat_note}</span>　LINE 全能助理 AI 對話功能依賴此 Token。</div>
</div>

<!-- Anthropic API -->
<div class="section">
  <div class="section-header">
    <div class="section-icon">🤖</div>
    <div class="section-title">Anthropic API（週報生成）</div>
    <span class="section-badge badge-paid">付費（目前吃免費額度）</span>
  </div>
  <table>
    <tr><th>項目</th><th>數值</th></tr>
    <tr><td>使用場景</td><td>週報自動生成（claude-sonnet-4-6）</td></tr>
    <tr><td>免費 Credit grant</td><td>US$5.00（2026/5/1 發放，到期 2027/5/2）</td></tr>
    <tr><td>剩餘額度（估）</td><td><span class="val-green">US${credit_remaining_est:.2f}</span>　<span class="val-muted">（請至 console.anthropic.com 確認實際數字）</span></td></tr>
    <tr><td>免費額度用完後月費</td><td><span class="val-orange">約 US$2 / 月</span>，依實際呼叫次數計費</td></tr>
    <tr><td>費用</td><td><span class="val-green">$0</span>（免費額度期間）</td></tr>
    <tr><td>Token 用量</td><td><span class="val-muted">請至 console.anthropic.com → Usage 查看實際數字</span></td></tr>
  </table>
  <div class="note-box">📌 此處剩餘額度為估算值（按每月 $2 計）。實際數字請至 console.anthropic.com → Usage 確認。</div>
</div>

<!-- GitHub -->
<div class="section">
  <div class="section-header">
    <div class="section-icon">⚙️</div>
    <div class="section-title">GitHub（Actions + Pages + Models）</div>
    <span class="section-badge badge-free">免費</span>
  </div>
  <table>
    <tr><th>項目</th><th>數值</th></tr>
    <tr><td>週報系統</td><td>DunaYou/weekly-report（每週日 12:00 執行）</td></tr>
    <tr><td>LINE 全能助理</td><td>DunaYou/line-assistant（Render 部署）</td></tr>
    <tr><td>本月預估 Actions 用量</td><td>週報 4 次 + 月報 1 次 ≈ 約 <span class="val-highlight">1.5 分鐘/月</span></td></tr>
    <tr><td>免費額度</td><td>2,000 分鐘 / 月</td></tr>
    <tr><td>GitHub Models (gpt-4o-mini)</td><td>LINE 全能助理 AI 對話，免費</td></tr>
    <tr><td>GH_PAT 狀態</td><td><span class="{pat_class}">{pat_note}</span></td></tr>
    <tr><td>費用</td><td><span class="val-green">$0</span>｜用量不到免費額度的 1%</td></tr>
  </table>
</div>

<!-- 其他免費服務 -->
<div class="section">
  <div class="section-header">
    <div class="section-icon">🔧</div>
    <div class="section-title">其他服務（全部免費）</div>
    <span class="section-badge badge-free">免費</span>
  </div>
  <table>
    <tr><th>服務</th><th>用途</th><th>費用</th></tr>
    <tr><td>Render.com</td><td>LINE 全能助理 Flask 伺服器（Free Tier）</td><td><span class="val-green">$0</span></td></tr>
    <tr><td>cron-job.org</td><td>早安 08:30 觸發 + 週報 + 月報 + Render 防休眠 ping</td><td><span class="val-green">$0</span></td></tr>
    <tr><td>Surge.sh</td><td>醫責險要保書（fbmmiclient / fbmmiensure）</td><td><span class="val-green">$0</span></td></tr>
    <tr><td>Firecrawl</td><td>週報「值得偷學的案例」搜尋（500 次/月免費）</td><td><span class="val-green">$0</span></td></tr>
    <tr><td>Notion API</td><td>待辦、客戶、靈感庫、工作日誌等資料庫讀寫</td><td><span class="val-green">$0</span></td></tr>
    <tr><td>Open-Meteo</td><td>早安機器人台北天氣</td><td><span class="val-green">$0</span></td></tr>
    <tr><td>Google Calendar API</td><td>早安行程、全能助理查行程</td><td><span class="val-green">$0</span></td></tr>
  </table>
</div>

<!-- 本月 AI 工時分佈 -->
<div class="section">
  <div class="section-header">
    <div class="section-icon">📊</div>
    <div class="section-title">本月 AI 工時分佈</div>
    <span class="section-badge badge-free">工作紀錄</span>
  </div>
  <table>
    <tr><th>專案</th><th>工時</th><th>佔比</th></tr>
    {proj_rows}
  </table>
  <div class="reflection-box">{reflection}</div>
</div>

<!-- 近期注意事項 -->
<div class="section">
  <div class="section-header">
    <div class="section-icon">📌</div>
    <div class="section-title">近期需要注意的事</div>
  </div>
  <table>
    <tr><th>截止日</th><th>項目</th><th>說明</th></tr>
    {alert_rows}
  </table>
</div>

<div class="footer">
  <div>游泰仁 AI 特助系統 — 自動生成報告</div>
  <div>盈爍財務顧問 × 瑞爍品牌顧問</div>
</div>

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
    import importlib.util
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
    }

    reflection = generate_monthly_reflection(logs, month_label, stats)

    os.makedirs("reports", exist_ok=True)
    filename = f"{first_day.year}-M{first_day.month:02d}.html"
    monthly_reports = load_monthly_registry()
    existing_idx = next((i for i, m in enumerate(monthly_reports) if m["filename"] == filename), None)

    html = render_monthly_html(month_label, first_day, last_day, logs, stats,
                                line_limit, line_used, reflection)
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
