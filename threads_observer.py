#!/usr/bin/env python3
"""Threads 生態觀察腳本 v2 — 每天 09:30 台北時間由 cron-job.org 觸發 workflow_dispatch"""

import os, json, re, time, requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from playwright.sync_api import sync_playwright

# ── 設定 ───────────────────────────────────────────────────────────────────
KEYWORDS = [
    "診所", "診所開業", "診所經營", "診所行銷", "診所日常",
    "醫師", "醫療糾紛", "醫美",
    "報稅", "稅務規劃", "現金流",
    "勞基法", "勞資糾紛",
    "會計師", "會計師日常",
    "律師",
    "開業", "診所倒閉", "醫師薪水",
]

ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
LINE_TOKEN         = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID       = os.environ.get("LINE_USER_ID", "")
GH_PAT             = os.environ.get("GH_PAT", "")
THREADS_COOKIES    = os.environ.get("THREADS_COOKIES", "")   # JSON 字串
THREADS_USER       = os.environ.get("THREADS_USER", "panda.1050009")
THREADS_PASS       = os.environ.get("THREADS_PASS", "duna102206011")

REPO    = "DunaYou/weekly-report"
BRANCH  = "main"
TZ      = timezone(timedelta(hours=8))

# ── 爬蟲 ───────────────────────────────────────────────────────────────────

def login_threads(page):
    """用帳密登入 Threads，或用 cookie 跳過登入"""
    if THREADS_COOKIES:
        try:
            cookies = json.loads(THREADS_COOKIES)
            page.context.add_cookies(cookies)
            page.goto("https://www.threads.com/", wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            if "threads.com" in page.url and "login" not in page.url:
                print("cookie 登入成功")
                return True
        except Exception as e:
            print(f"cookie 登入失敗: {e}")

    # 帳密登入
    page.goto("https://www.threads.com/login", wait_until="domcontentloaded")
    page.wait_for_timeout(2000)

    # 找 email / username 欄位
    try:
        page.fill('input[name="username"], input[type="text"]', THREADS_USER)
        page.fill('input[name="password"], input[type="password"]', THREADS_PASS)
        page.keyboard.press("Enter")
        page.wait_for_timeout(4000)
        print(f"帳密登入後頁面: {page.url}")
        return "login" not in page.url
    except Exception as e:
        print(f"帳密登入失敗: {e}")
        return False


EXTRACT_JS = """() => {
  const results = [];
  document.querySelectorAll('time').forEach(timeEl => {
    const datetime = timeEl.getAttribute('datetime') || timeEl.innerText;
    let node = timeEl;
    for (let i = 0; i < 15; i++) {
      node = node.parentElement;
      if (!node) break;
      const userLinks = node.querySelectorAll('a[href^="/@"]');
      if (userLinks.length > 0 && (node.innerText||'').length > 50) {
        const username = userLinks[0].getAttribute('href').replace('/@','').split('?')[0];
        const txt = node.innerText || '';
        const numMatches = txt.match(/\\b\\d[\\d,]*\\b/g) || [];
        const nums = numMatches.map(n => parseInt(n.replace(/,/g,''))).filter(n => n > 0 && n < 9999999);
        const likes = nums.length > 0 ? Math.max(...nums) : 0;
        const lines = txt.split('\\n').map(l=>l.trim()).filter(l=>
          l.length > 5 &&
          !l.match(/^@/) &&
          !l.match(/^\\d+$/) &&
          !['更多','回覆','轉發','喜歡','分享','建立'].includes(l)
        );
        const postText = lines.join(' ').slice(0, 300);
        if (postText.length > 10 && username) {
          results.push({ username, text: postText, likes, time: datetime });
        }
        break;
      }
    }
  });
  const seen = new Set();
  return JSON.stringify(results.filter(p => {
    const key = p.username + p.text.slice(0,20);
    if (seen.has(key)) return false;
    seen.add(key); return true;
  }));
}"""


def scrape_keyword(page, keyword, scroll_times=2):
    """搜尋一個關鍵字，滾動後回傳帖子列表"""
    url = f"https://www.threads.com/search?q={requests.utils.quote(keyword)}&serp_type=default"
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)

    posts = []
    for _ in range(scroll_times + 1):
        raw = page.evaluate(EXTRACT_JS)
        batch = json.loads(raw)
        posts.extend(batch)
        page.keyboard.press("End")
        page.wait_for_timeout(1800)

    # 去重
    seen = set()
    unique = []
    for p in posts:
        key = p["username"] + p["text"][:20]
        if key not in seen:
            seen.add(key)
            p["keyword"] = keyword
            unique.append(p)

    print(f"  {keyword}: {len(unique)} 篇")
    return unique


def get_all_posts():
    all_posts = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.new_page()

        ok = login_threads(page)
        if not ok:
            print("登入失敗，嘗試繼續（可能是未登入的公開結果）")

        for kw in KEYWORDS:
            try:
                posts = scrape_keyword(page, kw)
                all_posts.extend(posts)
                time.sleep(1)
            except Exception as e:
                print(f"  {kw} 失敗: {e}")

        # 儲存當前 cookie（供下次使用）
        cookies = ctx.cookies()
        browser.close()

    return all_posts, cookies


# ── Claude 分析 ────────────────────────────────────────────────────────────

def analyze_with_claude(posts):
    if not ANTHROPIC_API_KEY:
        return _mock_analysis(posts)

    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    posts_text = "\n\n".join([
        f"[{i+1}] 帳號:{p['username']} 關鍵字:{p['keyword']} 讚:{p['likes']} 時間:{p['time']}\n{p['text'][:250]}"
        for i, p in enumerate(posts[:80])
    ])

    today = datetime.now(TZ).strftime("%Y-%m-%d")

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system="你只能輸出純 JSON，不能有任何 markdown 或說明文字。",
        messages=[{
            "role": "user",
            "content": f"""以下是今天（{today}）從 Threads 搜尋到的貼文，請分析後回傳 JSON：

{posts_text}

請回傳以下格式的純 JSON（不要有 markdown code block）：
{{
  "total_scraped": 數字,
  "top15": [
    {{
      "rank": 1,
      "username": "帳號名",
      "keyword": "關鍵字",
      "identity": "醫師/一般人/律師/會計師/診所顧問 等",
      "title": "10字內貼文標題（你自己下的）",
      "likes": 讚數,
      "time": "HH:MM",
      "strategy": "20-40字，這篇為什麼有效、如何複製它的流量策略"
    }}
  ],
  "hot_topics": "50字以內今日最熱議題摘要",
  "best_time": "最佳發文時段建議（一句話）",
  "account_suggestion": "給醫師/診所顧問帳號的明天發文建議（50字）",
  "copy_idea": "明天就能直接複製的貼文格式（50字）",
  "identity_stats": {{"醫師": 0, "一般人": 0, "律師": 0, "會計師": 0, "診所顧問": 0}},
  "keyword_stats": {{"診所": 0, "診所開業": 0}},
  "time_slots": [
    {{"slot": "早晨", "range": "06–09", "count": 0, "avg_likes": 0}},
    {{"slot": "上午", "range": "09–12", "count": 0, "avg_likes": 0}},
    {{"slot": "午後", "range": "12–15", "count": 0, "avg_likes": 0}},
    {{"slot": "下午", "range": "15–18", "count": 0, "avg_likes": 0}},
    {{"slot": "晚間", "range": "18–22", "count": 0, "avg_likes": 0}},
    {{"slot": "深夜", "range": "22–06", "count": 0, "avg_likes": 0}}
  ]
}}

請根據實際資料填入數字和內容。top15 選讚數最高或最有策略學習價值的 15 篇（不夠就選全部）。"""
        }]
    )

    raw = resp.content[0].text.strip()
    # 移除 markdown
    raw = re.sub(r'^```json\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # bracket 配對
        start = raw.find('{')
        if start >= 0:
            depth, end = 0, -1
            for i, c in enumerate(raw[start:]):
                if c == '{': depth += 1
                elif c == '}': depth -= 1
                if depth == 0:
                    end = start + i + 1
                    break
            if end > start:
                return json.loads(raw[start:end])
        return _mock_analysis(posts)


def _mock_analysis(posts):
    """Claude 不可用時的假資料"""
    return {
        "total_scraped": len(posts),
        "top15": [{"rank": i+1, "username": p["username"], "keyword": p["keyword"],
                   "identity": "一般人", "title": p["text"][:15],
                   "likes": p["likes"], "time": "09:00",
                   "strategy": "需要 Claude API 才能分析"} for i, p in enumerate(posts[:15])],
        "hot_topics": "需要 Claude API 才能分析",
        "best_time": "需要 Claude API 才能分析",
        "account_suggestion": "需要 Claude API",
        "copy_idea": "需要 Claude API",
        "identity_stats": {},
        "keyword_stats": {},
        "time_slots": []
    }


# ── HTML 產生 ──────────────────────────────────────────────────────────────

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Noto+Serif+TC:wght@400;700&family=Noto+Sans+TC:wght@300;400;500&display=swap');
:root{--ink:#1a1a1a;--accent:#1F362C;--bg:#f6f9f7;--border:#dde7e2;--card:#ffffff;}
*{margin:0;padding:0;box-sizing:border-box;}
body{background:var(--bg);color:var(--ink);font-family:'Noto Sans TC',sans-serif;font-weight:300;min-height:100vh;}
.top-nav{background:#1a2a23;padding:10px 24px;display:flex;align-items:center;gap:12px;}
.top-nav a{color:#7ecba1;font-size:13px;text-decoration:none;}
.top-nav .sep{color:#3d5248;font-size:12px;}
.main{max-width:760px;margin:0 auto;padding:40px 24px 80px;}
.page-title{font-family:'Noto Serif TC',serif;font-size:24px;font-weight:700;margin-bottom:6px;}
.page-sub{font-size:13px;color:#7d8c87;margin-bottom:28px;}
.section-label{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:#7d8c87;margin-bottom:16px;padding-bottom:10px;border-bottom:1px solid var(--border);}
.card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:24px;margin-bottom:20px;}
.badge{display:inline-block;background:#e8f5ee;border:1px solid #b2d8c4;border-radius:20px;padding:3px 10px;font-size:12px;color:#1F362C;margin:2px;}
.site-footer{border-top:1px solid var(--border);padding:20px;text-align:center;font-size:12px;color:#7d8c87;}
table{width:100%;border-collapse:collapse;}
th{padding:8px 12px;text-align:left;font-size:10px;color:#7d8c87;font-weight:500;background:#f6f9f7;}
td{padding:9px 12px;font-size:12px;border-bottom:1px solid #f0f4f2;}
tr:nth-child(even){background:#fafcfb;}
.kw-tag{background:#f0faf4;border-radius:3px;padding:2px 6px;font-size:11px;}
.strategy{color:#3d6b57;}
"""


def build_html(date_str, analysis, posts):
    mm_dd = datetime.strptime(date_str, "%Y-%m-%d").strftime("%-m/%-d")
    total = analysis.get("total_scraped", len(posts))
    top15 = analysis.get("top15", [])
    kw_cnt = len([k for k in KEYWORDS if any(p["keyword"] == k for p in posts)])
    kw_stats = analysis.get("keyword_stats", {})
    kw_str = " · ".join([f"{k}（{v}）" for k, v in sorted(kw_stats.items(), key=lambda x: -x[1]) if v > 0][:8])
    id_stats = analysis.get("identity_stats", {})
    time_slots = analysis.get("time_slots", [])

    # 身份 badges
    id_badges = " ".join([f'<span class="badge">{k} <strong>{v}</strong></span>' for k, v in id_stats.items() if v > 0])

    # 時段長條圖
    max_avg = max((s.get("avg_likes", 0) for s in time_slots), default=1) or 1
    time_rows = ""
    for s in time_slots:
        pct = int(s.get("avg_likes", 0) / max_avg * 80) if max_avg else 4
        pct = max(pct, 4)
        time_rows += f"""<tr>
  <td style="white-space:nowrap;"><span style="font-size:12px;color:#333;">{s['slot']}</span><span style="font-size:10px;color:#bbb;margin-left:5px;">{s['range']}</span></td>
  <td style="color:#7d8c87;">{s['count']} 篇</td>
  <td><div style="background:#1F362C;height:10px;border-radius:3px;width:{pct}%;min-width:4px;"></div></td>
  <td style="color:#1F362C;font-weight:600;">{s['avg_likes']}</td>
</tr>"""

    # top15 rows
    top_rows = ""
    for p in top15:
        top_rows += f"""<tr>
  <td style="text-align:center;color:#1F362C;font-weight:600;white-space:nowrap;">#{p['rank']}</td>
  <td><span class="kw-tag">#{p['keyword']}</span></td>
  <td style="color:#555;white-space:nowrap;">{p['identity']}</td>
  <td style="color:#333;">{p['title']}</td>
  <td class="strategy">{p['strategy']}</td>
</tr>"""

    # 精選帖子詳細
    post_cards = ""
    for p in posts:
        if any(t.get("username") == p["username"] and t.get("title","") in p["text"][:50] for t in top15):
            post_cards += f"""<div class="card" style="margin-bottom:12px;">
  <div style="font-size:11px;color:#7d8c87;margin-bottom:6px;"><strong style="color:#1F362C;">@{p['username']}</strong> · #{p['keyword']} · {p.get('time','')}</div>
  <p style="font-size:13px;color:#555;line-height:1.8;">{p['text'][:400]}</p>
</div>"""

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Threads 觀察 {date_str} · 游泰仁</title>
<style>{CSS}</style>
</head>
<body>
<div class="top-nav">
  <a href="../">← 泰仁週報首頁</a>
  <span class="sep">›</span>
  <a href="./">Threads 每日觀察</a>
</div>
<div class="main">
  <div class="page-title">Threads 觀察 · {mm_dd}</div>
  <div class="page-sub">從 {len(KEYWORDS)} 個關鍵字精選 {len(top15)} 篇</div>
  <div class="section-label">統整分析</div>

<div class="card">
  <div class="section-label">今日統計</div>
  <div style="display:flex;gap:28px;margin-bottom:16px;flex-wrap:wrap;">
    <div><div style="font-size:32px;font-weight:700;color:#1F362C;">{len(top15)}</div><div style="font-size:11px;color:#7d8c87;">精選篇數</div></div>
    <div><div style="font-size:32px;font-weight:700;color:#1F362C;">{total}</div><div style="font-size:11px;color:#7d8c87;">原始抓取</div></div>
    <div><div style="font-size:32px;font-weight:700;color:#1F362C;">{kw_cnt}</div><div style="font-size:11px;color:#7d8c87;">關鍵字覆蓋</div></div>
  </div>
  <div style="margin-bottom:8px;font-size:11px;font-weight:600;color:#7d8c87;text-transform:uppercase;letter-spacing:.1em;">身份分佈</div>
  <div style="margin-bottom:10px;">{id_badges}</div>
  <div style="font-size:11px;color:#7d8c87;">熱門關鍵字：{kw_str}</div>
</div>

<div class="card">
  <div style="margin-bottom:10px;"><div style="font-size:11px;font-weight:600;color:#1F362C;margin-bottom:4px;">🔥 今日熱門主題</div><div style="font-size:13px;color:#333;">{analysis.get('hot_topics','')}</div></div>
  <div style="margin-bottom:10px;"><div style="font-size:11px;font-weight:600;color:#1F362C;margin-bottom:4px;">🕐 最佳發文時段</div><div style="font-size:13px;color:#333;">{analysis.get('best_time','')}</div></div>
  <div style="margin-bottom:10px;"><div style="font-size:11px;font-weight:600;color:#1F362C;margin-bottom:4px;">🎯 帳號發文建議</div><div style="font-size:13px;color:#333;">{analysis.get('account_suggestion','')}</div></div>
  <div><div style="font-size:11px;font-weight:600;color:#1F362C;margin-bottom:4px;">⚡ 明天就能複製</div><div style="font-size:13px;color:#333;">{analysis.get('copy_idea','')}</div></div>
</div>

<div class="card" style="padding:0;overflow:hidden;">
  <div style="padding:14px 20px;border-bottom:1px solid #dde7e2;">
    <span style="font-size:11px;font-weight:600;letter-spacing:.1em;color:#7d8c87;text-transform:uppercase;">發文時段 × 平均讚數</span>
  </div>
  <table><thead><tr>
    <th>時段</th><th>帖數</th><th>平均讚數</th><th></th>
  </tr></thead><tbody>{time_rows}</tbody></table>
</div>

<div class="card" style="padding:0;overflow:hidden;margin-bottom:32px;">
  <div style="padding:14px 20px;border-bottom:1px solid #dde7e2;">
    <span style="font-size:11px;font-weight:600;letter-spacing:.1em;color:#7d8c87;text-transform:uppercase;">快速總覽</span>
  </div>
  <div style="overflow-x:auto;">
  <table><thead><tr>
    <th>#</th><th>關鍵字</th><th>身份</th><th>標題</th><th>策略學習點</th>
  </tr></thead><tbody>{top_rows}</tbody></table>
  </div>
</div>

<div class="section-label">精選帖子</div>
{post_cards}

</div>
<div class="site-footer">游泰仁 Threads 每日觀察 · {date_str} · <a href="./" style="color:#5a8a72;">觀察總覽</a></div>
</body>
</html>"""


# ── GitHub Push ────────────────────────────────────────────────────────────

def gh_put(path, content, message, sha=None):
    url = f"https://api.github.com/repos/{REPO}/contents/{path}"
    import base64
    headers = {"Authorization": f"token {GH_PAT}", "Accept": "application/vnd.github.v3+json"}
    body = {"message": message, "content": base64.b64encode(content.encode()).decode(), "branch": BRANCH}
    if sha:
        body["sha"] = sha
    r = requests.put(url, headers=headers, json=body)
    if r.status_code not in (200, 201):
        print(f"  gh_put 失敗 {path}: {r.status_code} {r.text[:200]}")
    return r


def get_sha(path):
    url = f"https://api.github.com/repos/{REPO}/contents/{path}"
    headers = {"Authorization": f"token {GH_PAT}"}
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        return r.json().get("sha")
    return None


def push_report(date_str, html, accounts_data, cookies_json=None):
    path = f"threads/{date_str}.html"
    sha = get_sha(path)
    gh_put(path, html, f"threads: {date_str} daily report", sha)
    print(f"  ✓ 推送報告 {path}")

    # 更新 accounts.json
    acc_sha = get_sha("threads/accounts.json")
    gh_put("threads/accounts.json", json.dumps(accounts_data, ensure_ascii=False, indent=2),
           f"threads: update accounts for {date_str}", acc_sha)

    # 更新 index
    _update_index(date_str)

    # 更新 cookies（若有）
    if cookies_json:
        _update_gh_secret_hint(cookies_json)


def _update_index(date_str):
    try:
        url = f"https://api.github.com/repos/{REPO}/contents/threads/index.html"
        headers = {"Authorization": f"token {GH_PAT}"}
        r = requests.get(url, headers=headers)
        import base64
        if r.status_code == 200:
            old = base64.b64decode(r.json()["content"]).decode()
            sha = r.json()["sha"]
        else:
            old, sha = "", None
        mm_dd = datetime.strptime(date_str, "%Y-%m-%d").strftime("%-m/%-d")
        new_entry = f'<li><a href="{date_str}.html" class="day-link"><span class="day-badge">{mm_dd}</span></a></li>'
        if date_str in old:
            print("  index 已有此日期，跳過")
            return
        new_html = old.replace("</ul>", f"{new_entry}\n</ul>", 1) if "</ul>" in old else old + new_entry
        gh_put("threads/index.html", new_html, f"threads: update index for {date_str}", sha)
    except Exception as e:
        print(f"  index 更新失敗: {e}")


def _update_gh_secret_hint(cookies_json):
    # 只印出提示，不能直接更新 Secret（需要 GitHub Actions API）
    print(f"  ⚠️  提示：如需更新 THREADS_COOKIES secret，請到 GitHub Settings > Secrets 更新")


# ── LINE 通知 ──────────────────────────────────────────────────────────────

def send_line(date_str, top3):
    if not LINE_TOKEN or not LINE_USER_ID:
        return
    url_report = f"https://dunayou.github.io/weekly-report/threads/{date_str}.html"
    titles = "\n".join([f"  #{p['rank']} {p.get('title','')}" for p in top3])
    msg = f"📊 Threads 每日觀察 {date_str}\n\n今日精選 TOP 3：\n{titles}\n\n👉 {url_report}"
    requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"},
        json={"to": LINE_USER_ID, "messages": [{"type": "text", "text": msg}]}
    )
    print("  ✓ LINE 通知已發送")


# ── 主流程 ─────────────────────────────────────────────────────────────────

def main():
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    print(f"=== Threads 觀察 {today} ===")

    print("1. 爬蟲中...")
    all_posts, cookies = get_all_posts()
    print(f"   共抓取 {len(all_posts)} 篇")

    print("2. Claude 分析中...")
    analysis = analyze_with_claude(all_posts)
    top15 = analysis.get("top15", [])

    print("3. 生成 HTML...")
    html = build_html(today, analysis, all_posts)

    # 更新 accounts 追蹤
    acc_path = Path(__file__).parent / "threads" / "accounts.json"
    try:
        accounts = json.loads(acc_path.read_text()) if acc_path.exists() else {}
    except Exception:
        accounts = {}
    for p in all_posts:
        uname = p["username"]
        if uname not in accounts:
            accounts[uname] = {"dates": [], "identities": [], "keywords": []}
        if today not in accounts[uname]["dates"]:
            accounts[uname]["dates"].append(today)
        kw = p.get("keyword", "")
        if kw and kw not in accounts[uname]["keywords"]:
            accounts[uname]["keywords"].append(kw)

    print("4. Push 到 GitHub...")
    push_report(today, html, accounts, json.dumps(cookies))

    print("5. LINE 通知...")
    send_line(today, top15[:3])

    print("=== 完成 ===")


if __name__ == "__main__":
    main()
