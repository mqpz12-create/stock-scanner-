import os
import sys
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import FinanceDataReader as fdr

def log(text):
    print(text, flush=True)

def send_telegram(message):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log("[!] 텔레그램 환경변수 누락")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        log(f"[!] 텔레그램 전송 에러: {e}")

today_str = datetime.today().strftime("%Y-%m-%d")
# 30주선 확보를 위해 60주(약 420일) 전 일봉부터 수집
start_str = (datetime.today() - timedelta(days=450)).strftime("%Y-%m-%d")

log(f"[*] {today_str} 기준 MTS 일치형 주봉 30주선 스캔 시작...")

df_krx = fdr.StockListing('KRX')
if 'Marcap' in df_krx.columns:
    df_krx = df_krx[df_krx['Marcap'] >= 500_0000_0000]

target_tickers = list(df_krx.sort_values(by='Marcap', ascending=False)['Code'].head(400))
must_have = ["005090", "065060", "094480", "327260", "010140", "028050"]
target_tickers = list(set(target_tickers + must_have))

SECTORS = {
    "005090": "에너지/전력망", "065060": "에너지/전력망", "094480": "에너지/전력망",
    "267260": "신재생/에너지", "009830": "신재생/태양광", "051910": "신재생/화학",
    "327260": "반도체/광통신", "010140": "반도체/광통신", "000660": "반도체/AI", "005930": "반도체/AI",
    "105560": "금융/지주", "055550": "금융/지주", "086790": "금융/지주", "005830": "금융/보험",
    "028050": "플랜트/건설", "000210": "플랜트/건설", "028670": "해운/인프라",
    "012330": "자동차", "005380": "자동차", "003030": "철강/소재"
}

def analyze_stock(code):
    try:
        df_d = fdr.DataReader(code, start_str)
        if len(df_d) < 150:
            return None

        # 1. HTS/MTS와 동일한 주봉(Weekly) 캔들 생성 (금요일 마감 기준)
        df_w = df_d.resample('W-FRI').agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last',
            'Volume': 'sum'
        }).dropna()

        if len(df_w) < 35:
            return None

        # 2. 주봉 30주선(SMA 30) 및 20주선 정밀 계산
        df_w['SMA30'] = df_w['Close'].rolling(30).mean()
        
        current_price = int(df_d['Close'].iloc[-1])
        sma30 = df_w['SMA30'].iloc[-1]
        prev_sma30 = df_w['SMA30'].iloc[-5] # 4~5주 전 30주선

        # 필터 1: 30주선 우상향 또는 수평 지지 (하향 추세 배제)
        if np.isnan(sma30) or np.isnan(prev_sma30) or sma30 < (prev_sma30 * 0.995):
            return None

        # 필터 2: 30주선 풀백 구간 (-3% ~ +8% 지지)
        disp = (current_price / sma30) * 100.0
        if not (97.0 <= disp <= 108.0):
            continue_flag = False
        else:
            continue_flag = True
            
        if not continue_flag:
            return None

        # 필터 3: 일봉 거래량 절벽 (최근 20거래일 평균 대비 90% 이하 수렴)
        v_avg = df_d['Volume'].iloc[-21:-1].mean()
        t_vol = df_d['Volume'].iloc[-1]
        vol_ratio = (t_vol / v_avg) if v_avg > 0 else 1.0
        if vol_ratio > 0.90:
            return None

        name_match = df_krx[df_krx['Code'] == code]
        name = name_match['Name'].iloc[0] if not name_match.empty else code
        sector = SECTORS.get(code, "일반/제조")

        return {
            "code": code,
            "name": name,
            "sector": sector,
            "price": current_price,
            "sma30": int(round(sma30)),
            "disp": round(disp, 1),
            "vol_ratio": round(vol_ratio, 2)
        }
    except Exception:
        return None

results = []
log(f"[*] 총 {len(target_tickers)}개 종목 MTS 동기화 주봉 스캔 중...")

with ThreadPoolExecutor(max_workers=15) as executor:
    future_to_code = {executor.submit(analyze_stock, code): code for code in target_tickers}
    for future in as_completed(future_to_code):
        res = future.result()
        if res:
            results.append(res)

log(f"[*] 분석 완료. 포착 종목수: {len(results)}개")

msg = f"📊 *[{today_str} MTS 동기화 30주선 A포인트 리포트]*\n"
msg += f"- 실전 주봉 30주선 포착: *{len(results)}개*\n"
msg += "--------------------------------------\n"

if results:
    df_res = pd.DataFrame(results).sort_values(by=["sector", "vol_ratio"])
    for sec, grp in df_res.groupby("sector"):
        msg += f"\n📁 *[{sec}]* ({len(grp)}개)\n"
        for _, r in grp.head(5).iterrows():
            msg += f"• *{r['name']}* (`{r['price']:,}원`)\n"
            msg += f"   - 주봉 30주선: `{r['sma30']:,}원` (이격: `{r['disp']}%`)\n"
            msg += f"   - 일봉 거래량비: 평소의 `{int(r['vol_ratio']*100)}%`\n"
else:
    msg += "오늘 주봉 30주선 지지 조건을 충족하는 종목이 없습니다."

send_telegram(msg)
log("[*] 텔레그램 전송 완료!")
