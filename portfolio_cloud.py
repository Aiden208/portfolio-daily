# -*- coding: utf-8 -*-
"""
每日持仓资产刷新 - 云端版 (GitHub Actions)
- 从公开行情源拉取各标的最新价格/净值
- 计算市值/盈亏/总资产
- 生成 HTML 日报到 daily/ 目录 + 根目录 index.html 导航页
- 微信推送日报摘要 (Server酱, key 从环境变量 SERVERCHAN_KEY 或配置文件读取)
运行: python portfolio_cloud.py
"""
import json
import os
import re
import sys
import io
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "portfolio_config.json")
CACHE_PATH = os.path.join(BASE, "last_prices.json")
DAILY_DIR = os.path.join(BASE, "daily")

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

CST = timezone(timedelta(hours=8))  # 北京时间
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def http_get(url, timeout=10):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_funds(codes):
    """腾讯基金净值接口: 返回 {code: (单位净值, 净值日期)}"""
    if not codes:
        return {}
    url = "https://qt.gtimg.cn/q=" + ",".join("jj" + c for c in codes)
    try:
        txt = http_get(url, timeout=8)
    except Exception:
        return {}
    out = {}
    for line in txt.strip().split(";"):
        line = line.strip()
        if "=" not in line:
            continue
        head, body = line.split("=", 1)
        body = body.strip().strip('"')
        fields = body.split("~")
        if len(fields) > 8:
            key = head.strip().replace("v_jj", "")
            try:
                nav = float(fields[5]) if fields[5] else None
            except ValueError:
                nav = None
            if nav and nav > 0:
                out[key] = (nav, fields[8])
    return out


def fetch_qt(codes):
    """腾讯行情接口, 返回 {code: 现价}"""
    if not codes:
        return {}
    url = "https://qt.gtimg.cn/q=" + ",".join(codes)
    try:
        txt = http_get(url, timeout=8)
        out = {}
        for line in txt.strip().split(";"):
            line = line.strip()
            if "=" not in line:
                continue
            head, body = line.split("=", 1)
            body = body.strip().strip('"')
            fields = body.split("~")
            if len(fields) > 3:
                key = head.strip().replace("v_", "")
                try:
                    out[key] = float(fields[3])
                except ValueError:
                    out[key] = None
        return out
    except Exception:
        return {}


def load_cache():
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cache(cache):
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def fmt_num(v, digits=0):
    if v is None:
        return "-"
    if digits == 0:
        return f"{v:,.0f}"
    return f"{v:,.{digits}f}"


def http_post(url, data, timeout=10):
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def serverchan_push(key, title, desp):
    try:
        data = urllib.parse.urlencode({"title": title, "desp": desp}).encode("utf-8")
        txt = http_post("https://sctapi.ftqq.com/%s.send" % key, data)
        d = json.loads(txt)
        if d.get("code") == 0:
            return True, "ok"
        return False, str(d.get("message", txt))
    except Exception as e:
        return False, str(e)


def write_index(total_assets, today):
    files = sorted(
        (f for f in os.listdir(DAILY_DIR) if f.endswith(".html")), reverse=True
    )
    items = "".join(
        f'<li><a href="daily/{f}">{f.replace(".html", "")}</a></li>' for f in files
    )
    index = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>我的持仓日报</title><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;background:#f5f6fa;color:#1f2329;padding:24px;max-width:520px;margin:0 auto}}
h1{{font-size:20px;margin-bottom:6px}} p{{color:#8a919f;font-size:13px;margin-bottom:16px}}
ul{{list-style:none}} li{{background:#fff;border-radius:10px;margin-bottom:8px;box-shadow:0 1px 3px rgba(0,0,0,.05)}}
a{{display:block;padding:14px 16px;color:#3b6fd4;text-decoration:none;font-weight:600}}
a:active{{background:#eef3ff}}
</style></head><body>
<h1>📊 持仓日报</h1>
<p>最新总资产 {fmt_num(total_assets)} 元 · 更新于 {today} · 点击日期查看当日完整日报</p>
<ul>{items}</ul>
</body></html>"""
    with open(os.path.join(BASE, "index.html"), "w", encoding="utf-8") as f:
        f.write(index)


def main():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    holdings = cfg["holdings"]
    cash = cfg.get("cash", 0)
    fx = cfg.get("fx_hkd_cny", 0.86)

    cache = load_cache()
    today = datetime.now(CST).strftime("%Y-%m-%d")
    errors = []
    rows = []

    cn_codes = [h["code"] for h in holdings if h["market"] == "cn"]
    hk_codes = [h["code"] for h in holdings if h["market"] == "hk"]
    qt = fetch_qt(["sz" + c if c.startswith(("0", "1", "3")) else "sh" + c for c in cn_codes])
    hk_qt = fetch_qt(["hk" + c for c in hk_codes])
    fund_qt = fetch_funds([h["code"] for h in holdings if h["market"] == "fund"])

    total_cost = 0.0
    total_value = 0.0
    day_change = 0.0

    for h in holdings:
        code = h["code"]
        market = h["market"]
        shares = h["shares"]
        cost = h["cost"]
        price = None
        pdate = ""

        if market == "fund":
            res = fund_qt.get(code)
            if res:
                price, pdate = res
        elif market == "hk":
            px_raw = hk_qt.get("hk" + code)
            if px_raw is not None:
                price = px_raw * fx
                pdate = today
        else:
            prefix = "sz" if code.startswith(("0", "1", "3")) else "sh"
            px_raw = qt.get(prefix + code)
            if px_raw is not None:
                price = px_raw
                pdate = today

        if price is None:
            cached = cache.get(code)
            if cached:
                price = cached["price"]
                pdate = cached.get("date", "缓存")
                errors.append(f"{h['name']} 拉取失败, 沿用 {pdate} 价格 {price}")
            else:
                price = cost
                errors.append(f"{h['name']} 拉取失败, 暂按成本价")

        cost_val = shares * cost
        value = shares * price
        pnl = value - cost_val
        pnl_pct = pnl / cost_val if cost_val else 0
        prev = cache.get(code)
        day_delta = value - prev["value"] if prev and "value" in prev else 0.0
        day_change += day_delta

        total_cost += cost_val
        total_value += value

        cache[code] = {"price": price, "date": pdate, "value": value, "cost": cost_val}
        rows.append({
            "name": h["name"], "code": code, "market": market,
            "shares": shares, "cost": cost, "price": price, "date": pdate,
            "cost_val": cost_val, "value": value, "pnl": pnl,
            "pnl_pct": pnl_pct, "day_delta": day_delta,
        })

    total_assets = total_value + cash
    total_pnl = total_value - total_cost

    save_cache(cache)
    os.makedirs(DAILY_DIR, exist_ok=True)

    # ============ 控制台摘要 ============
    lines = []
    lines.append(f"【持仓日报 {today}】总资产 {fmt_num(total_assets)} 元 | 持仓市值 {fmt_num(total_value)} | 现金 {fmt_num(cash)}")
    lines.append(f"持仓盈亏 {fmt_num(total_pnl)} 元 ({total_pnl/max(total_cost,1)*100:+.2f}%) | 较上次刷新 {fmt_num(day_change)} 元")
    lines.append("-" * 46)
    for r in sorted(rows, key=lambda x: x["value"], reverse=True):
        lines.append(f"{r['name']:<22} {fmt_num(r['value'])} 元 {r['pnl']/max(r['cost_val'],1)*100:+.2f}%")
    if errors:
        lines.append("-" * 46)
        for e in errors:
            lines.append("[!] " + e)
    summary_text = "\n".join(lines)

    # ============ HTML 日报 ============
    def pct_html(v):
        cls = "up" if v > 0 else ("down" if v < 0 else "flat")
        sign = "+" if v > 0 else ""
        return f'<span class="{cls}">{sign}{v*100:.2f}%</span>'

    def money_html(v, prefix=""):
        cls = "up" if v > 0 else ("down" if v < 0 else "flat")
        sign = "+" if v > 0 else ""
        return f'<span class="{cls}">{prefix}{sign}{v:,.0f}</span>'

    trs = []
    for r in sorted(rows, key=lambda x: x["value"], reverse=True):
        trs.append(
            f"<tr><td>{r['name']}</td><td>{fmt_num(r['shares'])}</td><td>{fmt_num(r['cost'], 4)}</td>"
            f"<td><b>{fmt_num(r['price'], 4)}</b><div class='sub'>{r['date']}</div></td>"
            f"<td>{fmt_num(r['value'])}</td><td>{money_html(r['pnl'])}</td>"
            f"<td>{pct_html(r['pnl_pct'])}</td><td>{money_html(r['day_delta'])}</td></tr>"
        )

    asset_alloc = [
        ("固收+现金", sum(r["value"] for r in rows if r["market"] == "fund" and r["code"] in ("006493", "007562", "004388", "160622", "003547")) + cash),
        ("红利", sum(r["value"] for r in rows if r["code"] == "008163")),
        ("医药医疗", sum(r["value"] for r in rows if r["code"] in ("159938", "162412"))),
        ("海外权益", sum(r["value"] for r in rows if r["code"] == "00700")),
        ("商品黄金", sum(r["value"] for r in rows if r["code"] == "000216")),
        ("其他权益", sum(r["value"] for r in rows if r["code"] in ("014528", "110022"))),
    ]
    alloc_trs = ""
    for name, v in asset_alloc:
        p = v / total_assets * 100 if total_assets else 0
        alloc_trs += f"<tr><td>{name}</td><td>{fmt_num(v)}</td><td>{p:.1f}%</td></tr>"

    equity_val = asset_alloc[1][1] + asset_alloc[2][1] + asset_alloc[3][1] + asset_alloc[5][1]
    fixed_val = asset_alloc[0][1]

    err_html = ""
    if errors:
        err_html = "<div class='warn'>⚠️ " + "；".join(errors) + "</div>"

    now_str = datetime.now(CST).strftime("%Y-%m-%d %H:%M")
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>持仓日报 {today}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;background:#f5f6fa;color:#1f2329;padding:16px}}
.card{{background:#fff;border-radius:12px;padding:18px;margin-bottom:14px;box-shadow:0 1px 4px rgba(0,0,0,.06)}}
h1{{font-size:20px;margin-bottom:4px}} .date{{color:#8a919f;font-size:13px}}
.big{{font-size:30px;font-weight:700;margin:8px 0 4px}}
.sub{{color:#8a919f;font-size:12px}}
.up{{color:#e0242f}}.down{{color:#0a9d4e}}.flat{{color:#8a919f}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{padding:8px 6px;text-align:right;border-bottom:1px solid #f0f1f4}}
th{{color:#8a919f;font-weight:500;font-size:12px}}
td:first-child,th:first-child{{text-align:left}}
.warn{{background:#fff7e6;color:#b25e09;border:1px solid #ffd591;border-radius:8px;padding:10px 12px;font-size:13px;margin-bottom:14px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.tag{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;background:#eef3ff;color:#3b6fd4}}
</style></head><body>
<div class="card">
  <h1>📊 我的持仓日报</h1>
  <div class="date">{today} 云端自动刷新 · 数据来源: 腾讯行情/基金净值</div>
  <div class="big">{fmt_num(total_assets)}<span style="font-size:14px;color:#8a919f;margin-left:4px">元</span></div>
  <div class="sub">持仓盈亏 {money_html(total_pnl)} ({pct_html(total_pnl/max(total_cost,1))}) · 较上次刷新 {money_html(day_change)} 元</div>
</div>
{err_html}
<div class="grid">
  <div class="card"><div style="font-weight:600;margin-bottom:10px">💼 资产结构</div>
    <table>
      <tr><th>类别</th><th>金额</th><th>占比</th></tr>
      {alloc_trs}
      <tr><td><b>合计</b></td><td><b>{fmt_num(total_assets)}</b></td><td><b>100%</b></td></tr>
    </table>
  </div>
  <div class="card"><div style="font-weight:600;margin-bottom:10px">💰 现金与仓位</div>
    <table>
      <tr><td>现金余额</td><td><b>{fmt_num(cash)}</b></td></tr>
      <tr><td>持仓市值</td><td><b>{fmt_num(total_value)}</b></td></tr>
      <tr><td>权益类占比</td><td>{equity_val/total_assets*100:.1f}%</td></tr>
      <tr><td>固收+现金占比</td><td>{fixed_val/total_assets*100:.1f}%</td></tr>
    </table>
  </div>
</div>
<div class="card">
  <div style="font-weight:600;margin-bottom:10px">📋 持仓明细 <span class="tag">按市值排序</span></div>
  <table>
    <tr><th>标的</th><th>份额</th><th>成本价</th><th>最新价</th><th>市值(元)</th><th>盈亏(元)</th><th>收益率</th><th>较上次</th></tr>
    {''.join(trs)}
  </table>
</div>
<div style="color:#8a919f;font-size:11px;text-align:center;padding:10px 0 20px">
  云端自动生成于 {now_str} (北京时间) · 仅供个人参考, 不构成投资建议
</div>
</body></html>"""

    daily_path = os.path.join(DAILY_DIR, f"{today}.html")
    with open(daily_path, "w", encoding="utf-8") as f:
        f.write(html)
    write_index(total_assets, today)

    # ============ 微信推送 (Server酱) ============
    sckey = os.environ.get("SERVERCHAN_KEY", "") or cfg.get("serverchan_key", "")
    site_url = cfg.get("site_url", "")
    if sckey:
        best = max(rows, key=lambda x: x["pnl_pct"])
        worst = min(rows, key=lambda x: x["pnl_pct"])
        title = "持仓日报 %s 总资产%.1f万" % (
            today[5:].replace("-", "/"), total_assets / 10000
        )
        desp = (
            "**总资产** %s 元 (现金 %s)\n\n" % (fmt_num(total_assets), fmt_num(cash))
            + "**持仓盈亏** {} 元 ({:+.2f}%) | 较上次刷新 {} 元\n\n".format(
                format(total_pnl, "+,.0f"),
                total_pnl / max(total_cost, 1) * 100,
                format(day_change, "+,.0f"),
            )
            + "**表现最佳** %s %+.2f%%\n\n" % (best["name"], best["pnl_pct"] * 100)
            + "**表现最弱** %s %+.2f%%\n\n" % (worst["name"], worst["pnl_pct"] * 100)
        )
        if errors:
            desp += "**⚠️ 提醒** " + "; ".join(errors) + "\n\n"
        if site_url:
            desp += "[📊 查看完整日报](%s)" % site_url
        ok, msg = serverchan_push(sckey, title, desp)
        print(("微信推送: 成功" if ok else "微信推送: 失败 - ") + ("" if ok else msg))
    else:
        print("微信推送: 未配置 SERVERCHAN_KEY, 跳过")

    print(summary_text)
    print("\nHTML日报: " + daily_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
