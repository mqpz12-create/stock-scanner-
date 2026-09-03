import os
import time
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

today_dt = datetime.today()
target_date = today_dt.strftime("%Y%m%d")

# 최신 영업일 확인 (휴일/장마감 미반영 대비)
for i in range(7):
    chk_date = (today_dt - timedelta(days=i)).strftime("%Y%m%d")
    df_chk = stock.get_market_ohlcv_by_ticker(chk_date, market="KOSPI")
    if not df_chk.empty and df_chk['거래량'].sum() > 0:
        target_date = chk_date
        break

print(f"[*] 기준 영업일: {target_date} (코스피+코스닥 전 종목 전수 스캔 시작)")

# 30주선(150거래일) 계산용 기간 설정
start_date = (datetime.strptime(target_date, "%Y%m%d") - timedelta(days=365)).strftime("%Y%m%d")

# 1. 한국거래소 전 종목(KOSPI + KOSDAQ) 티커 전수 확보
tickers_kospi = stock.get_market_ticker_list(target_date, market="KOSPI")
tickers_kosdaq = stock.get_market_ticker_list(target_date, market="KOSDAQ")
all_tickers = tickers_kospi + tickers_kosdaq

# 2. 당일 전 종목 시세/거래량 벌크(한 번에) 수집 -> 차단 방지 및 초고속 연산
df_today_kospi = stock.get_market_ohlcv_by_ticker(target_date, market="KOSPI")
df_today_kosdaq = stock.get_market_ohlcv_by_ticker(target_date, market="KOSDAQ")
df_today_all = pd.concat([df_today_kospi, df_today_kosdaq])

# 시가총액 데이터도 한 번에 수집 (잡주/초소형주 필터용)
df_cap_kospi = stock.get_market_cap_by_ticker(target_date, market="KOSPI")
df_cap_kosdaq = stock.get_market_cap_by_ticker(target_date, market="KOSDAQ")
df_cap_all = pd.concat([df_cap_kospi, df_cap_kosdaq])

# 주요 테마/섹터 매핑 사전
SECTOR_MAP = {
    "005090": "에너지/전력망", "065060": "에너지/전력망", "094480": "에너지/전력망",
    "267260": "신재생/태양광", "009830": "신재생/태양광", "051910": "신재생/화학",
    "327260": "반도체/광통신", "010140": "반도체/광통신", "000660": "반도체/AI", "005930": "반도체/AI",
    "105560": "금융/지주", "055550": "금융/지주", "086790": "금융/지주", "005830": "금융/보험",
    "028050": "플랜트/인프라", "000210": "플랜트/건설", "028670": "해운/물류",
    "012330": "자동차", "005380": "자동차", "003030": "철강/소재"
}

results = []
scan_count = 0
total_len = len(all_tickers)

print(f"[*] 시장 전체 {total_len}개 종목 스탠 와인스타인 30주선 전수 필터링 중...")

for ticker in all_tickers:
    scan_count += 1
    try:
        # 시총 300억 미만 동전주/극단적 잡주만 1차 컷 (오류 방지)
        if ticker in df_cap_all.index:
            cap = df_cap_all.loc[ticker, "시가총액"]
            if cap < 300_0000_0000:
                continue

        # 당일 종가 및 거래량 확인
        if ticker not in df_today_all.index:
            continue
            
        today_close = int(df_today_all.loc[ticker, "종가"])
        today_vol = int(df_today_all.loc[ticker, "거래량"])
        if today_close < 1000 or today_vol == 0:
            continue

        # 개별 종목 과거 1년 일봉 데이터 가져오기 (30주선 산출)
        df_hist = stock.get_market_ohlcv_by_date(start_date, target_date, ticker)
        if len(df_hist) < 120:
            continue

        # 주봉 30주선 = 일봉 150일선 (최소 90일 데이터만 있어도 연산)
        df_hist['SMA150'] = df_hist['종가'].rolling(150, min_periods=90).mean()
        s30 = df_hist['SMA150'].iloc[-1]
        p_s30 = df_hist['SMA150'].iloc[-20] # 약 1달(20거래일) 전 30주선

        # 조건 1: 30주선 우상향 또는 수평 지지 (하향 추세는 완전 탈락)
        if np.isnan(s30) or np.isnan(p_s30) or s30 < (p_s30 * 0.985):
            continue

        # 조건 2: 30주선 풀백 구간 (-5% ~ +10% 이내 지지권)
        disparity = (today_close / s30) * 100.0
        if not (95.0 <= disparity <= 110.0):
            continue

        # 조건 3: 일봉 거래량 절벽 (최근 20일 평균 대비 100% 이하 = 거래량 안정)
        v_avg = df_hist['거래량'].iloc[-21:-1].mean()
        vol_ratio = (today_vol / v_avg) if v_avg > 0 else 1.0
        if vol_ratio > 1.05:
            continue

        # 조건 통과 종목: 최근 5거래일 연기금/기관 수급 확인
        df_net = stock.get_market_net_purchases_of_equities_by_ticker(
            df_hist.index[-5].strftime("%Y%m%d"), target_date, "ALL", ticker
        )
        p_net = int(df_net.loc[ticker, "연기금"]) if ticker in df_net.index else 0
        inst_net = int(df_net.loc[ticker, "기관합계"]) if ticker in df_net.index else 0

        name = stock.get_market_ticker_name(ticker)
        sec = SECTOR_MAP.get(ticker, "일반/제조")

        results.append({
            "ticker": ticker,
            "name": name,
            "sector": sec,
            "price": today_close,
            "s30": int(s30),
            "disp": round(disparity, 1),
            "v_rat": round(vol_ratio, 2),
            "p_net": p_net,
            "inst_net": inst_net
        })

    except Exception:
        continue

# 텔레그램 메시지 구성
msg = f"📊 *[{target_date} 전 종목 와인스타인 30주선 A포인트 리포트]*\n"
msg += f"- 시장 전체({total_len}개) 전수 조사 포착: *{len(results)}개 종목*\n"
msg += "--------------------------------------\n"

if results:
    df_res = pd.DataFrame(results).sort_values(by=["sector", "v_rat"])
    for sec, grp in df_res.groupby("sector"):
        msg += f"\n📁 *[{sec}]* ({len(grp)}개)\n"
        for _, r in grp.head(5).iterrows():
            p_sign = "+" if r['p_net'] > 0 else ""
            msg += f"• *{r['name']}* (`{r['price']:,}원`)\n"
            msg += f"   - 30주선: `{r['s30']:,}원` (이격: `{r['disp']}%`)\n"
            msg += f"   - 거래량비: `{r['v_rat']}배` | 연기금 5일: `{p_sign}{r['p_net']:,}주`\n"
else:
    msg += f"{target_date} 기준 전 종목 전수 조사 결과 일치하는 종목이 없습니다."

send_telegram(msg)
