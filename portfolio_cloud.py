# -*- coding: utf-8 -*-
"""
每日持仓资产自动刷新 - 云端版 (GitHub Actions)
- 读取 portfolio_config.json (含全部持仓 / combos组合 / 目标 / 定投计划)
- 拉取最新净值/行情 -> 计算市值/盈亏/总资产/分类占比/目标偏离
- 生成 HTML 日报 daily/YYYY-MM-DD_持仓日报.html + 根目录 index.html 导航页
- 微信推送日报摘要 (Server酱, key 取环境变量 SERVERCHAN_KEY 或配置文件 serverchan_key)
- 提交 last_prices.json / daily / index.html 回仓库
运行: python portfolio_cloud.py

说明: 本脚本为云端自包含版本, 不依赖本地 Excel / 图片生成 / 站点探测。
"""
import json
import os
import re
import sys
import io
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "portfolio_config.json")
CACHE_PATH = os.path.join(BASE, "last_prices.json")
DAILY_DIR = os.path.join(BASE, "daily")

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BEIJING = timezone(timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


# ============ 资产大类 + 行业属性映射 ============
CLASSIFY = {
    "006493": ("固收类", "债券"), "007562": ("固收类", "债券"), "004388": ("固收类", "债券"),
    "160622": ("固收类", "债券"), "003547": ("固收类", "债券"),
    "008163": ("权益类", "红利"), "014528": ("权益类", "宽基"), "110022": ("权益类", "消费"),
    "159938": ("权益类", "医药医疗"), "162412": ("权益类", "医药医疗"),
    "00700": ("权益类", "港股"), "03690": ("权益类", "港股"),
    "000216": ("商品类", "黄金"),
    "003474": ("货币类", "货币"), "013003": ("货币类", "货币"), "024890": ("货币类", "货币"),
    "000051": ("权益类", "宽基"), "000478": ("权益类", "宽基"), "000968": ("权益类", "宽基"),
    "001051": ("权益类", "宽基"), "001052": ("权益类", "宽基"), "022424": ("权益类", "宽基"),
    "110020": ("权益类", "宽基"), "161017": ("权益类", "宽基"),
    "009051": ("权益类", "红利"), "020602": ("权益类", "红利"), "021550": ("权益类", "红利"),
    "100032": ("权益类", "红利"),
    "000369": ("权益类", "医药医疗"), "000727": ("权益类", "医药医疗"),
    "001180": ("权益类", "医药医疗"), "002708": ("权益类", "医药医疗"), "012323": ("权益类", "医药医疗"),
    "000071": ("权益类", "港股"), "006327": ("权益类", "港股"), "012348": ("权益类", "港股"),
    "164906": ("权益类", "港股"),
    "019524": ("权益类", "海外"), "050025": ("权益类", "海外"),
    "002286": ("固收类", "债券"), "003376": ("固收类", "债券"), "004419": ("固收类", "债券"),
    "006484": ("固收类", "债券"), "007169": ("固收类", "债券"), "019518": ("固收类", "债券"),
    "100050": ("固收类", "债券"), "110027": ("固收类", "债券"),
    "008401": ("权益类", "海外"), "017641": ("权益类", "海外"), "019305": ("权益类", "海外"),
    "096001": ("权益类", "海外"), "021778": ("权益类", "海外"), "016452": ("权益类", "海外"),
}
COMBO_CLASSIFY = {"qieman_nasdaq_long": ("权益类", "海外")}

CATEGORY = {
    "006493": "债券", "007562": "债券", "004388": "债券", "160622": "债券", "003547": "债券",
    "002286": "债券", "003376": "债券", "004419": "债券", "006484": "债券", "007169": "债券",
    "019518": "债券", "100050": "债券", "110027": "债券",
    "00700": "港股", "03690": "港股", "000071": "港股", "012348": "港股",
    "006327": "港股", "164906": "港股",
    "016452": "纳指", "019524": "纳指", "021778": "纳指", "019441": "纳指",
    "050025": "标普500", "017641": "标普500", "019305": "标普500",
    "096001": "标普500", "008401": "标普500",
    "159938": "医药", "162412": "医药", "012323": "医药", "000369": "医药",
    "000727": "医药", "001180": "医药", "002708": "医药",
    "110022": "消费", "000248": "消费", "004424": "消费", "011309": "消费", "519915": "消费",
    "008163": "红利", "009051": "红利", "020602": "红利", "021550": "红利", "100032": "红利",
    "014528": "宽基", "000051": "宽基", "000478": "宽基", "000968": "宽基",
    "001051": "宽基", "001052": "宽基", "022424": "宽基", "110020": "宽基", "161017": "宽基",
    "000216": "黄金",
}
COMBO_CATEGORY = {"qieman_nasdaq_long": "纳指"}
CATEGORY_ORDER = ["债券", "港股", "纳指", "标普500", "医药", "消费", "红利", "宽基", "黄金", "货币"]
CATEGORY_EMOJI = {
    "债券": "🏦", "港股": "🇭🇰", "纳指": "🇺🇸", "标普500": "🗽", "医药": "💊",
    "消费": "🛒", "红利": "💰", "宽基": "📈", "黄金": "🥇", "货币": "💵",
}


def http_get(url, timeout=10):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_funds(codes):
    """腾讯基金净值接口(主源): 返回 {code: (单位净值, 净值日期)}"""
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


def fetch_fund_legacy(code):
    """天天基金 fundgz 接口(兜底): 返回 (单位净值, 净值日期)"""
    url = "http://fundgz.1234567.com.cn/js/%s.js?rt=%d" % (code, int(datetime.now().timestamp()))
    try:
        txt = http_get(url, timeout=8)
        m = re.search(r"\{.*\}", txt)
        if not m:
            raise ValueError("no data")
        d = json.loads(m.group(0))
        nav = d.get("dwjz")
        date = d.get("jzrq")
        if nav is None or nav == "":
            raise ValueError("empty nav")
        return float(nav), date
    except Exception:
        return None, None


def fetch_qt(codes):
    """腾讯行情接口, 返回 {code: 现价}"""
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


# ============ 历史快照 + ASCII 走势曲线 ============
HISTORY_PATH = os.path.join(BASE, "history.json")


def load_history():
    if os.path.exists(HISTORY_PATH):
        try:
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_history(total, pnl, today):
    hist = load_history()
    hist[today] = {"total": round(total, 2), "pnl": round(pnl, 2)}
    try:
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def sparkline(values, width=None):
    if not values:
        return ""
    blocks = "▁▂▃▄▅▆▇█"
    lo, hi = min(values), max(values)
    if hi == lo:
        return blocks[len(blocks) // 2] * len(values)
    span = hi - lo
    out = []
    for v in values:
        ratio = (v - lo) / span
        idx = int(round(ratio * (len(blocks) - 1)))
        idx = max(0, min(len(blocks) - 1, idx))
        out.append(blocks[idx])
    return "".join(out)


def short_name(name, limit=14):
    name = (name.replace("交易型开放式指数证券投资基金", "ETF")
                .replace("证券投资基金", "")
                .replace("指数型", "")
                .replace("发起式联接基金", "")
                .replace("联接基金", "")
                .replace("（QDII）", "(QD)")
                .replace("人民币", "")
                .replace("型证券", ""))
    if len(name) > limit:
        return name[:limit - 1] + "…"
    return name


def generate_health(alloc, sector_val, total_assets, total_pnl, total_pnl_pct):
    equity = alloc.get("权益类", 0)
    fixed = alloc.get("固收类", 0)
    money = alloc.get("货币类", 0)
    commodity = alloc.get("商品类", 0)
    equity_pct = equity / total_assets * 100 if total_assets else 0
    money_pct = money / total_assets * 100 if total_assets else 0
    commodity_pct = commodity / total_assets * 100 if total_assets else 0

    comments, suggestions = [], []
    if equity_pct >= 75:
        comments.append(f"权益类占比 {equity_pct:.0f}%，明显偏高，组合波动与回撤风险较大。")
        suggestions.append("建议逐步降低权益仓位，增配债券等稳健资产，把回撤控制在可承受范围。")
    elif equity_pct >= 60:
        comments.append(f"权益类占比 {equity_pct:.0f}%，属于积极型配置。")
        suggestions.append("可保持当前仓位，但需注意单一行业不宜过重，避免过度集中。")
    elif equity_pct >= 30:
        comments.append(f"权益类占比 {equity_pct:.0f}%，股债配置较为均衡。")
    else:
        comments.append(f"权益类占比 {equity_pct:.0f}%，配置偏保守，收益弹性有限。")
        suggestions.append("可适度提升权益仓位，用长期视角换取更高收益空间。")

    if money_pct < 1:
        comments.append("现金与货币仓位几乎为零，缺乏流动性缓冲。")
        suggestions.append("建议保留 3%~5% 现金，应对市场回调时的补仓机会或临时用款需求。")
    elif money_pct > 20:
        comments.append(f"现金与货币占比 {money_pct:.0f}%，闲置资金偏多，可能拖累整体收益。")

    equity_sectors = {k: v for k, v in sector_val.items() if k not in ("债券", "货币")}
    if equity_sectors:
        top_name = max(equity_sectors, key=equity_sectors.get)
        top_val = equity_sectors[top_name]
        top_pct_total = top_val / total_assets * 100 if total_assets else 0
        if top_pct_total >= 15:
            comments.append(f"权益行业集中度偏高：{top_name}占总资产 {top_pct_total:.0f}%。")
            suggestions.append(f"关注 {top_name} 板块的集中风险，可考虑向其他行业分散。")
        elif top_pct_total >= 10:
            comments.append(f"权益行业中 {top_name}占比最高（占总资产 {top_pct_total:.0f}%），需留意集中度。")

    if total_pnl < 0:
        comments.append(f"组合整体浮亏 {abs(total_pnl):,.0f} 元（{total_pnl_pct*100:+.1f}%）。")
        suggestions.append("浮亏标的可持有观察、以定投摊薄成本，避免在低点恐慌性割肉。")
    else:
        comments.append(f"组合整体浮盈 {total_pnl:,.0f} 元（{total_pnl_pct*100:+.1f}%）。")

    if 0 < commodity_pct < 5:
        comments.append(f"黄金等商品配置 {commodity_pct:.1f}%，起到一定对冲作用。")
    return comments, suggestions


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
    files = sorted((f for f in os.listdir(DAILY_DIR) if f.endswith(".html")), reverse=True)
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
    combos = cfg.get("combos", [])
    cash = cfg.get("cash", 0)
    fx = cfg.get("fx_hkd_cny", 0.92)
    targets = cfg.get("targets", {})
    tg_funds = targets.get("funds", {})
    tg_cat = targets.get("category", {})
    tg_ac = targets.get("asset_class", {})
    baseline_gap = float(targets.get("baseline_gap", 0) or 0)

    cache = load_cache()
    now = datetime.now(BEIJING)
    today = now.strftime("%Y-%m-%d")
    errors = []
    rows = []

    cn_codes = [h["code"] for h in holdings if h["market"] == "cn"]
    qt = fetch_qt(["sz" + c if c.startswith(("0", "1", "3")) else "sh" + c for c in cn_codes])
    hk_codes = ["hk" + h["code"] for h in holdings if h["market"] == "hk"]
    hk_qt = fetch_qt(hk_codes) if hk_codes else {}
    fund_codes = [h["code"] for h in holdings if h["market"] == "fund"]
    fund_qt = fetch_funds(fund_codes)

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
        px_raw = None

        if market == "money":
            price = 1.0
            pdate = today
        elif market == "fund":
            res = fund_qt.get(code)
            if res:
                px_raw, pdate = res
                price = px_raw
            else:
                px_raw, pdate = fetch_fund_legacy(code)
                price = px_raw
        elif market == "hk":
            qkey = "hk" + code
            px_raw = hk_qt.get(qkey)
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
            "group": h.get("group", "main"),
        })

    # ============ combos 组合处理 ============
    combo_rows = []
    for cb in combos:
        comps = cb.get("components", [])
        cb_cost = sum(c["shares"] * c["cost"] for c in comps)
        comp_qt = fetch_funds([c["code"] for c in comps]) if comps else {}
        dyn_value = 0.0
        for c in comps:
            nav = comp_qt.get(c["code"], (None, ""))
            px = nav[0] if nav and nav[0] else c["cost"]
            dyn_value += c["shares"] * px
        cb_value = dyn_value
        cb_pnl = cb_value - cb_cost
        cb_pct = cb_pnl / cb_cost if cb_cost else 0
        combo_rows.append({
            "name": cb.get("name", "组合"),
            "code": cb.get("id", "combo"),
            "market": "combo",
            "shares": None, "cost": None, "price": None, "date": today,
            "cost_val": cb_cost, "value": cb_value, "pnl": cb_pnl,
            "pnl_pct": cb_pct, "day_delta": 0,
            "group": cb.get("group", "且慢"),
            "detail": comps,
            "daily_invest": cb.get("daily_invest", 0),
            "last_sync": cb.get("last_sync", ""),
        })
        total_cost += cb_cost
        total_value += cb_value

    total_assets = total_value + cash
    total_pnl = total_value - total_cost
    total_pnl_pct = total_pnl / max(total_cost, 1)

    save_cache(cache)
    os.makedirs(DAILY_DIR, exist_ok=True)

    # ============ 分类统计 ============
    for r in rows:
        if r["market"] == "money":
            ac, sec = "货币类", "货币"
        else:
            ac, sec = CLASSIFY.get(r["code"], ("权益类", "其他"))
        r["asset_class"] = ac
        r["sector"] = sec
        r["category"] = CATEGORY.get(r["code"], sec if sec != "海外" else "其他")
        r["target"] = tg_funds.get(r["code"])
    for r in combo_rows:
        ac, sec = COMBO_CLASSIFY.get(r["code"], ("权益类", "海外"))
        r["asset_class"] = ac
        r["sector"] = sec
        r["category"] = COMBO_CATEGORY.get(r["code"], "纳指")
        r["target"] = tg_funds.get(r["code"])

    alloc, sector_val = {}, {}
    for r in rows + combo_rows:
        alloc[r["asset_class"]] = alloc.get(r["asset_class"], 0) + r["value"]
        sector_val[r["sector"]] = sector_val.get(r["sector"], 0) + r["value"]
    if cash > 0:
        alloc["货币类"] = alloc.get("货币类", 0) + cash

    comments, suggestions = generate_health(alloc, sector_val, total_assets, total_pnl, total_pnl_pct)

    # ============ 控制台摘要 ============
    lines = []
    lines.append(f"【持仓日报 {today}】总资产 {fmt_num(total_assets)} 元 | 持仓市值 {fmt_num(total_value)} | 现金 {fmt_num(cash)}")
    lines.append(f"持仓盈亏 {fmt_num(total_pnl)} 元 ({total_pnl_pct*100:+.2f}%) | 较上次刷新 {fmt_num(day_change)} 元")
    lines.append("-" * 46)
    for r in sorted(rows + combo_rows, key=lambda x: x["value"], reverse=True):
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

    def row_tr(r):
        if r.get("market") == "combo":
            return (
                f"<tr><td><b>{r['name']}</b><div class='sub code'>组合</div></td>"
                f"<td>-</td><td>-</td><td><b>-</b><div class='sub'>{r['date']}</div></td>"
                f"<td><b>{fmt_num(r['value'])}</b></td>"
                f"<td>{money_html(r['pnl'])}</td>"
                f"<td>{pct_html(r['pnl_pct'])}</td>"
                f"<td>{money_html(r['day_delta'])}</td></tr>"
            )
        return (
            f"<tr><td>{r['name']}<div class='sub code'>{r['code']}</div></td>"
            f"<td>{fmt_num(r['shares'])}</td><td>{fmt_num(r['cost'], 4)}</td>"
            f"<td><b>{fmt_num(r['price'], 4)}</b><div class='sub'>{r['date']}</div></td>"
            f"<td>{fmt_num(r['value'])}</td><td>{money_html(r['pnl'])}</td>"
            f"<td>{pct_html(r['pnl_pct'])}</td><td>{money_html(r['day_delta'])}</td></tr>"
        )

    all_rows = sorted(rows + combo_rows, key=lambda x: -x["value"])
    ac_order = ["权益类", "固收类", "商品类", "货币类"]
    ac_trs = ""
    for ac in ac_order:
        v = alloc.get(ac, 0)
        if v <= 0:
            continue
        p = v / total_assets * 100 if total_assets else 0
        ac_trs += f"<tr><td>{ac}</td><td>{fmt_num(v)}</td><td>{p:.1f}%</td></tr>"

    sec_trs = ""
    for sec, v in sorted(sector_val.items(), key=lambda x: -x[1]):
        p = v / total_assets * 100 if total_assets else 0
        sec_trs += f"<tr><td>{sec}</td><td>{fmt_num(v)}</td><td>{p:.1f}%</td></tr>"

    plans = cfg.get("plans", [])
    plans_trs = ""
    for pl in plans:
        paused = pl.get("status") == "paused"
        status_txt = '<span style="color:#b25e09">已暂停</span>' if paused else '<span style="color:#0a9d4e">运行中</span>'
        plans_trs += (
            f"<tr><td>{pl['name']}</td><td>{pl.get('code','')}</td>"
            f"<td>{pl.get('cycle','')}</td><td><b>{pl.get('amount','')}</b></td><td>{status_txt}</td></tr>"
        )

    err_html = ""
    if errors:
        err_html = "<div class='warn'>⚠️ " + "；".join(errors) + "</div>"

    cat_rows_map = {}
    for r in all_rows:
        cat = r.get("category", "其他")
        cat_rows_map.setdefault(cat, []).append(r)

    # 目标偏离度
    cat_tgt_map = {}
    dev_trs = ""
    total_gap_abs = 0.0
    if tg_cat:
        for cat in CATEGORY_ORDER:
            grp = cat_rows_map.get(cat)
            if not grp:
                continue
            cur_v = sum(r["value"] for r in grp)
            tgt_v = float(tg_cat.get(cat, 0) or 0)
            gap = cur_v - tgt_v
            rate = gap / tgt_v if tgt_v else 0
            total_gap_abs += abs(gap)
            cat_tgt_map[cat] = (tgt_v, gap, rate)
            if abs(rate) <= 0.05:
                st = "<span style='color:#0a9d4e'>已达标</span>"
            elif abs(rate) <= 0.15:
                st = "<span style='color:#b25e09'>有偏离</span>"
            else:
                st = "<span style='color:#e0242f'>偏离较大</span>"
            gcls = "need-buy" if gap < 0 else ("need-sell" if gap > 0 else "flat")
            gsign = "+" if gap > 0 else ""
            dev_trs += (
                f"<tr><td>{CATEGORY_EMOJI.get(cat,'📁')} {cat}</td>"
                f"<td>{fmt_num(cur_v)}</td><td>{fmt_num(tgt_v)}</td>"
                f"<td><span class='{gcls}'>{gsign}{gap:,.0f}</span></td>"
                f"<td><span class='{gcls}'>{rate*100:+.1f}%</span></td><td>{st}</td></tr>"
            )
    progress = 0.0
    if baseline_gap > 0 and tg_cat:
        progress = max(0.0, min(1.0, (baseline_gap - total_gap_abs) / baseline_gap))
    dev_html = ""
    if tg_cat:
        dev_html = (
            "<div class='card'>"
            f"<div style='font-weight:600;margin-bottom:4px'>🎯 目标偏离度 · {targets.get('plan_name','目标方案')}</div>"
            f"<div class='sub' style='margin-bottom:8px'>制定于 {targets.get('plan_date','')} · "
            f"基准缺口 {fmt_num(baseline_gap)} 元 · 当前剩余缺口 {fmt_num(total_gap_abs)} 元</div>"
            f"<div style='font-size:13px;margin-bottom:4px'>调仓进度 <b>{progress*100:.1f}%</b></div>"
            f"<div class='bar'><i style='width:{progress*100:.1f}%'></i></div>"
            "<table style='margin-top:12px'>"
            "<tr><th>分类</th><th>当前市值</th><th>目标市值</th><th>偏离金额</th><th>偏离率</th><th>状态</th></tr>"
            f"{dev_trs}"
            "</table>"
            "<div class='sub' style='margin-top:8px'>偏离金额为负（蓝）表示需要买入，为正（橙）表示需要卖出</div>"
            "</div>"
        )

    comments_li = "".join(f"<li>{c}</li>" for c in comments)
    suggestions_li = "".join(f"<li>{s}</li>" for s in suggestions)

    group_html = ""
    for cat in CATEGORY_ORDER:
        grp = cat_rows_map.get(cat)
        if not grp:
            continue
        grp_sorted = sorted(grp, key=lambda x: -x["value"])
        grp_value = sum(r["value"] for r in grp_sorted)
        grp_pnl = sum(r["pnl"] for r in grp_sorted)
        grp_cost = sum(r["cost_val"] for r in grp_sorted)
        grp_pct = grp_pnl / grp_cost if grp_cost else 0
        emoji = CATEGORY_EMOJI.get(cat, "📁")
        trs = "".join(row_tr(r) for r in grp_sorted)
        combo_detail = ""
        for r in grp_sorted:
            if r.get("market") == "combo":
                detail = r.get("detail", [])
                stale_note = ""
                sync = r.get("last_sync", "")
                daily = float(r.get("daily_invest", 0) or 0)
                if sync and daily > 0:
                    try:
                        d0 = datetime.strptime(sync, "%Y-%m-%d").date()
                        days = (datetime.strptime(today, "%Y-%m-%d").date() - d0).days
                    except Exception:
                        days = 0
                    if days >= 3:
                        est = days * daily * 22 / 30.0
                        stale_note = (
                            f"<div style='margin-top:8px;background:#fff7e8;border-left:3px solid #f0c987;"
                            f"padding:8px 10px;border-radius:6px;font-size:12.5px;color:#8a5a00'>"
                            f"⚠️ 份额快照同步于 <b>{sync}</b>（{days} 天前），期间按 {daily:,.0f}/交易日 定投估算约 "
                            f"<b>{est:,.0f} 元</b>尚未计入本表，实际以且慢 APP 为准。</div>"
                        )
                comp_rows = []
                for c in detail:
                    comp_rows.append(
                        f"<tr><td>{c['name']}</td><td>{c['code']}</td>"
                        f"<td>{fmt_num(c['shares'], 2)}</td><td>{fmt_num(c['cost'], 4)}</td></tr>"
                    )
                combo_detail += (
                    f"<details style='margin-top:10px'><summary style='font-size:13px;color:#3b6fd4'>"
                    f"查看 {len(detail)} 只纳指成分基金明细</summary>"
                    f"<table style='margin-top:8px'><tr><th>名称</th><th>代码</th><th>份额</th><th>单位成本</th></tr>"
                    f"{''.join(comp_rows)}</table>{stale_note}</details>"
                )
        gap_txt = ""
        if cat in cat_tgt_map:
            _t, _g, _r = cat_tgt_map[cat]
            gcls = "need-buy" if _g < 0 else ("need-sell" if _g > 0 else "flat")
            gsign = "+" if _g > 0 else ""
            gap_txt = (f" · 🎯目标 {fmt_num(_t)} "
                       f"<span class='{gcls}'>({gsign}{_g:,.0f})</span>")
        group_html += (
            f"<details class='cat-group' open>"
            f"<summary>{emoji} {cat} <span class='tag'>{len(grp_sorted)}只</span> "
            f"<span style='color:#1f2329'>市值 {fmt_num(grp_value)} 元</span> "
            f"· 盈亏 {money_html(grp_pnl)} ({pct_html(grp_pct)}){gap_txt}</summary>"
            f"<table style='margin-top:10px'>"
            f"<tr><th>标的</th><th>份额</th><th>成本</th><th>最新价</th><th>市值</th><th>盈亏</th><th>收益率</th><th>较上次</th></tr>"
            f"{trs}</table>{combo_detail}</details>"
        )

    # 资产收益曲线(基于每日快照)
    save_history(total_assets, total_pnl, today)
    hist = load_history()
    hdates = sorted(hist.keys())
    trend_html = ""
    if len(hdates) >= 2:
        hvals = [hist[d]["total"] for d in hdates]
        sp = sparkline(hvals)
        lo, hi = min(hvals), max(hvals)
        first_v, last_v = hvals[0], hvals[-1]
        chg = last_v - first_v
        chg_pct = chg / first_v * 100 if first_v else 0
        chg_color = "#e0242f" if chg >= 0 else "#0a9d4e"
        chg_sign = "+" if chg >= 0 else ""
        li = "".join(
            f"<div style='font-size:12px;color:#5a6272;padding:2px 0'>{d} {sp[i]} {v/10000:.1f}万</div>"
            for i, (d, v) in enumerate(zip(hdates, hvals))
        )
        trend_html = (
            "<div class='card'><div style='font-weight:600;margin-bottom:4px'>📈 资产收益曲线</div>"
            f"<div class='sub' style='margin-bottom:6px'>近 {len(hdates)} 次快照 · 当前 {last_v:,.0f} 元 · "
            f"区间 <span style='color:{chg_color};font-weight:600'>{chg_sign}{chg:,.0f} 元 ({chg_sign}{chg_pct:.2f}%)</span></div>"
            f"<div style='font-family:ui-monospace,Consolas,monospace;line-height:1.5'>{li}</div></div>"
        )

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
.need-buy{{color:#2f5597}}.need-sell{{color:#974706}}
.bar{{height:8px;background:#eef0f3;border-radius:4px;overflow:hidden}}
.bar>i{{display:block;height:100%;background:#3b6fd4}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{padding:8px 6px;text-align:right;border-bottom:1px solid #f0f1f4}}
th{{color:#8a919f;font-weight:500;font-size:12px}}
td:first-child,th:first-child{{text-align:left}}
.warn{{background:#fff7e6;color:#b25e09;border:1px solid #ffd591;border-radius:8px;padding:10px 12px;font-size:13px;margin-bottom:14px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.tag{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;background:#eef3ff;color:#3b6fd4}}
details>summary{{cursor:pointer;font-weight:600;color:#1f2329;list-style:none}}
details>summary::-webkit-details-marker{{display:none}}
details[open]>summary::before{{content:"▾ "}}
details>summary::before{{content:"▸ "}}
details.cat-group{{border:1px solid #eef0f3;border-radius:10px;padding:10px 14px;margin-bottom:10px;background:#fafbfc}}
details.cat-group>summary{{font-size:15px}}
.code{{font-family:ui-monospace,Consolas,monospace;color:#8a919f;font-size:11px}}
ul.health{{padding-left:18px;margin:8px 0}}
ul.health li{{margin:4px 0;font-size:13px;line-height:1.5}}
.sugg{{background:#f0f9f0;border-radius:8px;padding:12px;margin-top:8px}}
.sugg li{{color:#2f7d3a}}
@keyframes fadeIn{{from{{opacity:0}}to{{opacity:1}}}}
@media(max-width:640px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body>
<div class="card">
  <h1>📊 我的持仓日报</h1>
  <div class="date">{today} 云端自动刷新 · 数据来源: 腾讯行情/基金净值</div>
  <div class="big">{fmt_num(total_assets)}<span style="font-size:14px;color:#8a919f;margin-left:4px">元</span></div>
  <div class="sub">持仓盈亏 {money_html(total_pnl)} ({pct_html(total_pnl_pct)}) · 较上次刷新 {money_html(day_change)} 元</div>
</div>
{err_html}
<div class="card">
  <div style="font-weight:600;margin-bottom:10px">🩺 持仓健康度评语</div>
  <ul class="health">{comments_li}</ul>
  <div class="sugg"><b>💡 操作建议</b><ul class="health">{suggestions_li}</ul></div>
</div>
{dev_html}
<div class="grid">
  <div class="card"><div style="font-weight:600;margin-bottom:10px">📊 资产大类（权益属性）</div>
    <table><tr><th>大类</th><th>金额</th><th>占比</th></tr>{ac_trs}
      <tr><td><b>合计</b></td><td><b>{fmt_num(total_assets)}</b></td><td><b>100%</b></td></tr></table>
  </div>
  <div class="card"><div style="font-weight:600;margin-bottom:10px">🏭 行业属性分布</div>
    <table><tr><th>行业</th><th>金额</th><th>占比</th></tr>{sec_trs}</table>
  </div>
</div>
<div class="card">
  <div style="font-weight:600;margin-bottom:10px">📦 持仓概览</div>
  <table>
    <tr><td>现金余额</td><td><b>{fmt_num(cash)}</b></td><td>-</td></tr>
    <tr><td>持仓总数</td><td colspan="2"><b>{len(rows)} 项 + 纳指长期组合</b></td></tr>
    <tr><td>定投计划</td><td colspan="2"><b>{len(plans)} 条（运行中 {sum(1 for pl in plans if pl.get('status')!='paused')} 条）</b></td></tr>
  </table>
</div>
<div class="card">
  <div style="font-weight:600;margin-bottom:10px">📋 持仓明细（按分类） <span class="tag">{len(rows)}项 + 纳指长期组合</span></div>
  {group_html}
</div>
{plans_html if (plans_html := (f'''<div class="card">
  <div style="font-weight:600;margin-bottom:10px">⏰ 定投计划</div>
  <table><tr><th>名称</th><th>标的</th><th>周期</th><th>金额(元)</th><th>状态</th></tr>{plans_trs}</table>
</div>''' if plans else "")) else ""}
{trend_html}
<div style="color:#8a919f;font-size:11px;text-align:center;padding:10px 0 20px">
  自动生成于 {now.strftime('%Y-%m-%d %H:%M')} (北京时间) · 仅供个人参考, 不构成投资建议<br>
  修改持仓请在 portfolio_config.json 中调整份额/成本
</div>
</body></html>"""

    daily_path = os.path.join(DAILY_DIR, f"{today}_持仓日报.html")
    with open(daily_path, "w", encoding="utf-8") as f:
        f.write(html)
    write_index(total_assets, today)

    # ============ 微信推送 (Server酱) ============
    sckey = os.environ.get("SERVERCHAN_KEY", "") or cfg.get("serverchan_key", "")
    if sckey:
        sign = "📈" if day_change >= 0 else "📉"
        plines = [
            f"【{today} 持仓日报】", "",
            f"💰 总资产  {fmt_num(total_assets)} 元",
            f"📊 总盈亏  {total_pnl:+,.0f} 元 ({total_pnl_pct*100:+.2f}%)",
            f"   成本 {fmt_num(total_cost)} → 现值 {fmt_num(total_assets)}",
            f"{sign} 较上次 {day_change:+,.0f} 元",
        ]
        if tg_cat and baseline_gap > 0:
            plines.append(f"🎯 调仓进度 {progress*100:.1f}%  (剩余缺口 {total_gap_abs:,.0f} 元)")

        def _disp_width(s):
            return sum(2 if ord(ch) > 0x2E80 else 1 for ch in s)

        def _pnl_line(r):
            nm = short_name(r["name"], 12)
            pad = " " * max(1, 24 - _disp_width(nm))
            icon = "📈" if r["pnl"] >= 0 else "📉"
            return f"   {icon} {nm}{pad}{r['pnl']:>+9,.0f} {r['pnl_pct']*100:>+6.2f}%"

        all_r = rows + combo_rows
        ranked = sorted(all_r, key=lambda r: r["pnl"])
        losers = [r for r in ranked if r["pnl"] < 0]
        winners = [r for r in ranked if r["pnl"] > 0][::-1]
        plines.append("")
        plines.append(f"💹 盈亏明细(共{len(all_r)}项, 亏{len(losers)}/赚{len(winners)})")
        show_n = 6
        if losers:
            for r in losers[:show_n]:
                plines.append(_pnl_line(r))
            if len(losers) > show_n:
                rest = sum(r["pnl"] for r in losers[show_n:])
                plines.append(f"   … 其余{len(losers)-show_n}只亏损合计 {rest:+,.0f} 元")
        if winners:
            if losers:
                plines.append("   " + "─" * 26)
            for r in winners[:show_n]:
                plines.append(_pnl_line(r))
            if len(winners) > show_n:
                rest = sum(r["pnl"] for r in winners[show_n:])
                plines.append(f"   … 其余{len(winners)-show_n}只盈利合计 {rest:+,.0f} 元")

        cat_agg = {}
        for r in all_r:
            c = r.get("category") or ("货币" if r.get("market") == "money" else "其他")
            a = cat_agg.setdefault(c, {"v": 0.0, "p": 0.0})
            a["v"] += r["value"]
            a["p"] += r["pnl"]
        if cat_agg:
            plines.append("")
            plines.append("📦 分类小计")
            for c, a in sorted(cat_agg.items(), key=lambda kv: -kv[1]["v"]):
                base = a["v"] - a["p"]
                pct = a["p"] / base * 100 if base else 0
                icon = "📈" if a["p"] >= 0 else "📉"
                cpad = " " * max(1, 12 - _disp_width(c))
                plines.append(f"   {icon} {c}{cpad}{a['v']:>9,.0f} {a['p']:>+9,.0f} ({pct:+.2f}%)")

        if tg_cat and cat_tgt_map:
            needs = []
            for cat, (tgt_v, gap, rate) in cat_tgt_map.items():
                if abs(rate) <= 0.05 or abs(gap) < 5000:
                    continue
                need_buy = gap < 0
                needs.append((abs(rate), cat, gap, need_buy))
            needs.sort(reverse=True)
            if needs:
                plines.append("")
                plines.append("📌 偏离最大:")
                for _, cat, gap, nb in needs[:3]:
                    act = "加仓" if nb else "减仓"
                    plines.append(f"  · {cat}: {'+' if gap>0 else ''}{gap:,.0f} 元 ({act})")

        if errors:
            plines.append("")
            plines.append("⚠️ " + "; ".join(errors))

        desp = "\n".join(plines)
        title = "持仓日报 %s 总资产%.1f万 盈亏%+.2f%%" % (
            today[5:].replace("-", "/"), total_assets / 10000, total_pnl_pct * 100
        )
        ok, msg = serverchan_push(sckey, title, desp)
        print(("微信推送: 成功" if ok else "微信推送: 失败 - ") + ("" if ok else msg))
    else:
        print("微信推送: 未配置 SERVERCHAN_KEY, 跳过")

    print(summary_text)
    print("\nHTML日报: " + daily_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
