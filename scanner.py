import os
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pykrx import stock

def send_telegram(message):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[!] 텔레그램 환경변수 누락")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    requests.post(url, json=payload)

today = datetime.today().strftime("%Y%m%d")
# 150거래일(30주) 확보를 위해 300일 전부터 일봉 데이터 수집
start_date = (datetime.today() - timedelta(days=320)).strftime("%Y%m%d")

print(f"[*] {today} 30주선(150일선) 스크리닝 가동...")

kospi = stock.get_market_ticker_list(today, market="KOSPI")
kosdaq = stock.get_market_ticker_list(today, market="KOSDAQ")
tickers = kospi + kosdaq

SECTORS = {
    "005090": "에너지/전력망", "065060": "에너지/전력망", "094480": "에너지/전력망",
    "267260": "신재생/에너지", "009830": "신재생/태양광", "051910": "신재생/화학",
    "327260": "반도체/광통신", "010140": "반도체/광통신", "000660": "반도체/AI", "005930": "반도체/AI",
    "105560": "금융/지주", "055550": "금융/지주", "086790": "금융/지주", "005830": "금융/보험",
    "028050": "플랜트/건설", "000210": "플랜트/건설", "028670": "인프라/해운",
    "003030": "철강/소재", "012330": "자동차/부품", "005380": "자동차/부품"
}

results = []

for t in tickers:
    try:
        # 일봉 데이터 한 번에 수집 (주봉 30주선 = 일봉 150일선)
        df = stock.get_market_ohlcv_by_date(start_date, today, t)
        if len(df) < 160:
            continue
        
        cp = df['종가'].iloc[-1]
        if cp < 1000:
            continue

        # 30주선(150일 단순이동평균) 계산
        df['SMA150'] = df['종가'].rolling(150).mean()
        s30 = df['SMA150'].iloc[-1]
        p_s30 = df['SMA150'].iloc[-20] # 4주(20거래일) 전 30주선

        # 1. 30주선 우상향 또는 보합 검증
        if np.isnan(s30) or np.isnan(p_s30) or s30 < (p_s30 * 0.995):
            continue

        # 2. 30주선 풀백 구간 (-3% ~ +8% 이내 지지)
        disp = (cp / s30) * 100.0
        if not (97.0 <= disp <= 108.0):
            continue

        # 3. 거래량 절벽 검증 (20일 평균 대비 90% 이하)
        v_avg = df['거래량'].iloc[-21:-1].mean()
        t_vol = df['거래량'].iloc[-1]
        v_rat = t_vol / v_avg if v_avg > 0 else 1.0
        if v_rat > 0.90:
            continue

        # 최근 5거래일 연기금 순매수
        df_net = stock.get_market_net_purchases_of_equities_by_ticker(
            df.index[-5].strftime("%Y%m%d"), today, "ALL", t
        )
        p_net = df_net.loc[t, "연기금"] if t in df_net.index else 0

        name = stock.get_market_ticker_name(t)
        sec = SECTORS.get(t, "기타/제조")

        results.append({
            "name": name,
            "sector": sec,
            "price": int(cp),
            "disp": round(disp, 1),
            "v_rat": round(v_rat, 2),
            "p_net": int(p_net)
        })

    except Exception:
        continue

# 텔레그램 메시지 구성
msg = f"📊 *[{today} 스탠 와인스타인 30주선 A포인트 리포트]*\n"
msg += f"- 시장 전체 스캔 포착: *{len(results)}개*\n"
msg += "--------------------------------------\n"

if results:
    df_res = pd.DataFrame(results).sort_values(by=["sector", "v_rat"])
    for sec, grp in df_res.groupby("sector"):
        msg += f"\n📁 *[{sec}]* ({len(grp)}개)\n"
        for _, r in grp.head(5).iterrows():
            p_sign = "+" if r['p_net'] > 0 else ""
            msg += f"• *{r['name']}* (`{r['price']:,}원`)\n"
            msg += f"   - 30주선 이격: `{r['disp']}%` | 거래량: 평소의 `{int(r['v_rat']*100)}%`\n"
            msg += f"   - 연기금 5일: `{p_sign}{r['p_net']:,}주`\n"
else:
    msg += "오늘 30주선 풀백 조건을 만족하는 종목이 없습니다."

send_telegram(msg)
