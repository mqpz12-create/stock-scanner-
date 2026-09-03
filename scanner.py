import os
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import FinanceDataReader as fdr

def send_telegram(message):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[!] 텔레그램 환경변수 누락")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    requests.post(url, json=payload)

today_str = datetime.today().strftime("%Y-%m-%d")
start_str = (datetime.today() - timedelta(days=365)).strftime("%Y-%m-%d")

print(f"[*] {today_str} 기준 한국 시장 전수 스크리닝 시작 (엔진: FinanceDataReader)...")

df_krx = fdr.StockListing('KRX')
print(f"[*] 총 {len(df_krx)}개 상장 종목 확보 완료.")

if 'Marcap' in df_krx.columns:
    df_krx = df_krx[df_krx['Marcap'] >= 500_0000_0000]

SECTORS = {
    "005090": "에너지/전력망", "065060": "에너지/전력망", "094480": "에너지/전력망",
    "267260": "신재생/에너지", "009830": "신재생/태양광", "051910": "신재생/화학",
    "327260": "반도체/광통신", "010140": "반도체/광통신", "000660": "반도체/AI", "005930": "반도체/AI",
    "105560": "금융/지주", "055550": "금융/지주", "086790": "금융/지주", "005830": "금융/보험",
    "028050": "플랜트/건설", "000210": "플랜트/건설", "028670": "해운/인프라",
    "012330": "자동차", "005380": "자동차", "003030": "철강/소재"
}

results = []

target_tickers = list(df_krx.sort_values(by='Marcap', ascending=False)['Code'].head(500))
must_have = ["005090", "065060", "094480", "327260", "010140", "028050"]
target_tickers = list(set(target_tickers + must_have))

print(f"[*] 핵심 500개 종목 30주선(150일선) & 거래량 풀백 정밀 계산 중...")

for code in target_tickers:
    try:
        df = fdr.DataReader(code, start_str)
        if len(df) < 140:
            continue

        close_price = int(df['Close'].iloc[-1])
        if close_price < 1000:
            continue

        df['SMA150'] = df['Close'].rolling(150, min_periods=100).mean()
        sma150 = df['SMA150'].iloc[-1]
        prev_sma150 = df['SMA150'].iloc[-20]

        if np.isnan(sma150) or np.isnan(prev_sma150) or sma150 < (prev_sma150 * 0.99):
            continue

        disp = (close_price / sma150) * 100.0
        if not (96.0 <= disp <= 109.0):
            continue

        v_avg = df['Volume'].iloc[-21:-1].mean()
        t_vol = df['Volume'].iloc[-1]
        vol_ratio = (t_vol / v_avg) if v_avg > 0 else 1.0
        if vol_ratio > 0.95:
            continue

        name_match = df_krx[df_krx['Code'] == code]
        name = name_match['Name'].iloc[0] if not name_match.empty else code
        sector = SECTORS.get(code, "일반/제조")

        results.append({
            "code": code,
            "name": name,
            "sector": sector,
            "price": close_price,
            "sma150": int(sma150),
            "disp": round(disp, 1),
            "vol_ratio": round(vol_ratio, 2)
        })

    except Exception:
        continue

msg = f"📊 *[{today_str} 스탠 와인스타인 30주선 A포인트 리포트]*\n"
msg += f"- 포착 종목수: *{len(results)}개*\n"
msg += "--------------------------------------\n"

if results:
    df_res = pd.DataFrame(results).sort_values(by=["sector", "vol_ratio"])
    for sec, grp in df_res.groupby("sector"):
        msg += f"\n📁 *[{sec}]* ({len(grp)}개)\n"
        for _, r in grp.head(5).iterrows():
            msg += f"• *{r['name']}* (`{r['price']:,}원`)\n"
            msg += f"   - 30주선: `{r['sma150']:,}원` (이격: `{r['disp']}%`)\n"
            msg += f"   - 거래량 마름: 평소의 `{int(r['vol_ratio']*100)}%`\n"
else:
    msg += "오늘 30주선 풀백 및 거래량 절벽 조건을 만족하는 종목이 없습니다."

send_telegram(msg)
