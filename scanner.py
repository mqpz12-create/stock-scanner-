import os
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pykrx import stock

# 텔레그램 전송 함수
def send_telegram(message):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[!] 텔레그램 토큰 또는 Chat ID가 설정되지 않았습니다.")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    requests.post(url, json=payload)

today = datetime.today().strftime("%Y%m%d")
start_w = (datetime.today() - timedelta(days=450)).strftime("%Y%m%d")
start_d = (datetime.today() - timedelta(days=60)).strftime("%Y%m%d")

kospi = stock.get_market_ticker_list(today, market="KOSPI")
kosdaq = stock.get_market_ticker_list(today, market="KOSDAQ")
tickers = kospi + kosdaq

SECTORS = {
    "005090": "에너지/전력망", "065060": "에너지/전력망", "094480": "에너지/전력망",
    "267260": "신재생/에너지", "009830": "신재생/태양광", "051910": "신재생/화학",
    "327260": "반도체/광통신", "010140": "반도체/광통신", "000660": "반도체/AI", "005930": "반도체/AI",
    "105560": "금융/지주", "055550": "금융/지주", "086790": "금융/지주", "005830": "금융/보험",
    "028050": "플랜트/인프라", "000210": "플랜트/인프라", "028670": "해운/인프라"
}

results = []
# 시총 및 주요 종목군 400개 대상 퀀트 분석
for t in tickers[:400]:
    try:
        df_w = stock.get_market_ohlcv_by_date(start_w, today, t, "m")
        if len(df_w) < 35: continue
        df_w['SMA30'] = df_w['종가'].rolling(30).mean()
        cp, s30, p_s30 = df_w['종가'].iloc[-1], df_w['SMA30'].iloc[-1], df_w['SMA30'].iloc[-5]
        if s30 <= p_s30: continue
        disp = (cp / s30) * 100
        if not (98.0 <= disp <= 105.0): continue

        df_d = stock.get_market_ohlcv_by_date(start_d, today, t)
        if len(df_d) < 22: continue
        v_avg = df_d['거래량'].iloc[-21:-1].mean()
        t_vol = df_d['거래량'].iloc[-1]
        v_rat = t_vol / v_avg if v_avg > 0 else 1.0
        if v_rat > 0.55: continue

        # 최근 5일 연기금 순매수
        df_net = stock.get_market_net_purchases_of_equities_by_ticker(
            df_d.index[-5].strftime("%Y%m%d"), today, "ALL", t
        )
        p_net = df_net.loc[t, "연기금"] if t in df_net.index else 0

        results.append({
            "name": stock.get_market_ticker_name(t),
            "sector": SECTORS.get(t, "일반/기타"),
            "price": int(cp),
            "disp": round(disp, 1),
            "v_rat": round(v_rat, 2),
            "p_net": int(p_net)
        })
    except:
        continue

# 텔레그램 메시지 조립
msg = f"📊 *[{today} 스탠 와인스타인 30주선 A포인트 리포트]*\n"
msg += f"- 포착 종목수: {len(results)}개\n"
msg += "--------------------------------------\n"

if results:
    df = pd.DataFrame(results).sort_values(by=["sector", "v_rat"])
    for sec, grp in df.groupby("sector"):
        msg += f"\n📁 *[{sec}]*\n"
        for _, r in grp.iterrows():
            msg += f"• *{r['name']}* | {r['price']:,}원\n"
            msg += f"   - 30주선 이격: `{r['disp']}%` | 거래량비: `{r['v_rat']}배`\n"
            msg += f"   - 연기금 5일: `{r['p_net']:,}주`\n"
else:
    msg += "오늘 30주선 풀백 및 거래량 절벽 조건을 만족하는 종목이 없습니다."

send_telegram(msg)
