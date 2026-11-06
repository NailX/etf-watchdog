# -*- coding: utf-8 -*-
"""自建汪汪队ETF跟踪器 — 数据管道
数据源: 腾讯 qt.gtimg.cn(实时) + 新浪 quotes.sina.cn(K线), 全免费公开接口
用法: python3 update_data.py            # 更新快照
      python3 update_data.py --push     # 更新并(有变化时)git commit+push
cron: */2 9-15 * * 1-5  (交易时段每2分钟)
"""
import os, sys, json, time, urllib.request, datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, "data", "cache.json")
SNAP = os.path.join(ROOT, "data", "snapshot.js")
LASTPUSH = os.path.join(ROOT, "data", "last_push.json")

ETF_LIST = [
    ("510300", "沪深300"), ("510310", "沪深300"), ("510050", "上证50"),
    ("510330", "沪深300"), ("159919", "沪深300"), ("512100", "中证1000"),
    ("588080", "科创50"), ("159915", "创业板指"), ("510500", "中证500"),
    ("159845", "中证1000"), ("560010", "中证1000"), ("510180", "上证180"),
    ("159629", "中证1000"), ("510230", "上证180金融"), ("159922", "中证500"),
    ("512500", "中证500"), ("515800", "中证800"), ("159952", "创业板指"),
    ("588050", "科创50"), ("159901", "深证100"), ("588000", "科创50"),
    ("512050", "中证A500"), ("159338", "中证A500"), ("159339", "中证A500"),
]
HUIJIN = {"510300": 735.13, "510310": 558.94, "510050": 487.59, "510330": 420.28,
          "159919": 354.98, "512100": 221.69, "588080": 216.07, "159915": 170.20,
          "510500": 142.17, "159845": 137.26, "560010": 105.50, "510180": 52.32,
          "159629": 36.34, "510230": 25.64, "159922": 23.11, "512500": 20.51,
          "515800": 13.08, "159952": 10.91, "588050": 9.70, "159901": 7.19}
ETF300 = {"510300", "159919", "510310", "510330", "510050"}

def get(url, gbk=False, timeout=15, retries=2):
    for i in range(retries + 1):
        try:
            raw = urllib.request.urlopen(url, timeout=timeout).read()
            return raw.decode("gbk" if gbk else "utf-8", "ignore")
        except Exception:
            if i == retries: raise
            time.sleep(2)

def mkt(code): return ("sh" if code[0] in "58" else "sz") + code

def load_cache():
    try: return json.load(open(CACHE))
    except Exception: return {"date": "", "avg20": {}, "shape": {}}

def sina_kline(code, scale, datalen):
    u = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_x=/CN_MarketDataService.getKLineData"
         f"?symbol={mkt(code)}&scale={scale}&ma=no&datalen={datalen}")
    txt = get(u)
    s = txt[txt.index("(") + 1:txt.rindex(")")]
    return json.loads(s)

def refresh_daily(code, today):
    """20日均额: 新浪日K 量×均价, 取今日之前20个交易日"""
    ks = sina_kline(code, 240, 26)
    rows = [k for k in ks if k["day"] < today][-20:]
    if len(rows) < 15: return 0.0
    amts = [float(k["volume"]) * (float(k["open"]) + float(k["high"]) + float(k["low"]) + float(k["close"])) / 4 / 1e8 for k in rows]
    return round(sum(amts) / len(amts), 2)

def refresh_shape(code, today):
    """日内成交额分布: 近20个完整交易日, 各5分钟时点累计占全天比例均值"""
    ks = sina_kline(code, 5, 1023)
    days = {}
    for k in ks:
        d, hhmm = k["day"][:10], k["day"][11:16]
        if d >= today: continue
        days.setdefault(d, []).append((hhmm, float(k.get("amount") or 0)))
    days = sorted(days.items())[-20:]
    if len(days) < 10: return []
    acc = {}
    for d, bars in days:
        tot = sum(a for _, a in bars) or 1
        c = 0.0
        for hhmm, a in bars:
            c += a
            acc.setdefault(hhmm, []).append(c / tot)
    return [[h, round(sum(v) / len(v), 4)] for h, v in sorted(acc.items())]

def refresh_cache(today):
    c = load_cache()
    if c.get("date") == today and len(c.get("avg20", {})) >= len(ETF_LIST) - 2:
        return c
    print(f"[cache] 刷新 20日均额+日内分布 {today} ...", flush=True)
    for i, (code, _) in enumerate(ETF_LIST):
        try:
            c["avg20"][code] = refresh_daily(code, today)
            c["shape"][code] = refresh_shape(code, today)
        except Exception as e:
            print(f"[cache] {code} 失败: {e}")
        time.sleep(0.3)
    c["date"] = today
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(c, open(CACHE, "w"))
    return c

def tencent_quotes():
    q = ",".join(mkt(c) for c, _ in ETF_LIST)
    txt = get(f"https://qt.gtimg.cn/q={q}", gbk=True)
    out = {}
    for line in txt.strip().split(";"):
        line = line.strip()
        if "=" not in line or "~" not in line: continue
        code = line[line.index("_") + 1:line.index("=")][2:]
        f = line[line.index('"') + 1:line.rindex('"')].split("~")
        def num(i, d=0.0):
            try: return float(f[i])
            except Exception: return d
        shares = None
        for j in range(60, min(len(f), 80)):
            try:
                v = float(f[j])
                if v > 1e8: shares = v; break
            except Exception: pass
        out[code] = {"name": f[1], "price": num(3), "prev_close": num(4), "pct": num(32),
                     "outer": num(7), "inner": num(8), "amount_yi": num(37) / 1e4,
                     "shares": shares}
    return out

def shape_at(shape, tmin):
    pts = [(int(h[:2]) * 60 + int(h[3:]), v) for h, v in shape]
    if not pts: return None
    if tmin <= pts[0][0]: return pts[0][1]
    if tmin >= pts[-1][0]: return pts[-1][1]
    for i in range(1, len(pts)):
        if tmin <= pts[i][0]:
            t0, v0 = pts[i-1]; t1, v1 = pts[i]
            return v0 + (v1 - v0) * (tmin - t0) / (t1 - t0)
    return pts[-1][1]

def main():
    now = datetime.datetime.now()
    today = now.strftime("%Y%m%d")
    cache = refresh_cache(now.strftime("%Y-%m-%d"))
    quotes = tencent_quotes()
    tmin = now.hour * 60 + now.minute
    eff = min(tmin, 690) if tmin < 780 else min(tmin, 900)
    items, tot_net, tot_amt, base_sum, n300 = [], 0.0, 0.0, 0.0, 0.0
    for code, idx in ETF_LIST:
        q = quotes.get(code)
        if not q: continue
        base = cache["avg20"].get(code, 0.0)
        sp = shape_at(cache["shape"].get(code, []), eff) if tmin < 900 else 1.0
        oi = q["outer"] + q["inner"]
        net = (q["outer"] - q["inner"]) / oi * q["amount_yi"] if oi > 0 else 0.0
        mult = q["amount_yi"] / (base * sp) if base > 0 and sp and sp > 0.02 else 0.0
        tot_net += net; tot_amt += q["amount_yi"]; base_sum += base
        if code in ETF300: n300 += net
        items.append({"code": code, "name": q["name"], "index": idx, "price": q["price"],
                      "pct": q["pct"], "amount_yi": round(q["amount_yi"], 1), "avg20_yi": base,
                      "mult": round(mult, 2), "net": round(net, 1),
                      "shares_yi": round(q["shares"] / 1e8, 1) if q["shares"] else None,
                      "huijin_yi": HUIJIN.get(code)})
    mult_w = tot_amt / base_sum if base_sum else 0
    lv = 2 if (tot_net >= 90 or n300 >= 50) else (1 if (tot_net >= 40 or n300 >= 25 or (mult_w >= 1.8 and tot_net >= 15)) else 0)
    verdict = {2: "🔴 大概率上班了", 1: "🟠 疑似上班", 0: "⚪ 未上班/观望"}[lv]
    snap = {"generated_at": now.strftime("%Y-%m-%d %H:%M:%S"), "trade_date": today,
            "verdict_level": lv, "verdict": verdict, "total_amount_yi": round(tot_amt, 1),
            "total_net": round(tot_net, 1), "mult_weighted": round(mult_w, 2), "net300": round(n300, 1),
            "items": items}
    os.makedirs(os.path.dirname(SNAP), exist_ok=True)
    js = "window.SNAPSHOT = " + json.dumps(snap, ensure_ascii=False) + ";\n"
    open(SNAP, "w").write(js)
    print(f"[{now.strftime('%H:%M')}] {verdict} | 净买{tot_net:+.1f}亿 300系{n300:+.1f}亿 量能{mult_w:.2f}x | {len(items)}只", flush=True)
    if "--push" in sys.argv and try_push(js, now):
        pass

def try_push(js, now):
    import subprocess
    try:
        last = json.load(open(LASTPUSH))
    except Exception:
        last = {}
    import hashlib
    h = hashlib.md5(js.encode()).hexdigest()
    changed = last.get("hash") != h
    due = now.timestamp() - last.get("t", 0) > 300
    if not changed or not due:
        return False
    subprocess.run(["git", "-C", ROOT, "add", "data/snapshot.js"], check=True)
    if subprocess.run(["git", "-C", ROOT, "diff", "--cached", "--quiet"]).returncode == 0:
        return False
    subprocess.run(["git", "-C", ROOT, "commit", "-m", f"snapshot {now.strftime('%m%d %H:%M')}"], check=True)
    subprocess.run(["git", "-C", ROOT, "push"], check=True)
    json.dump({"hash": h, "t": now.timestamp()}, open(LASTPUSH, "w"))
    print("[push] 已推送 GitHub Pages", flush=True)
    return True

if __name__ == "__main__":
    main()
