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
start_w = (datetime.today() - timedelta(days=480)).strftime("%Y%m%d")
start_d = (datetime.today() - timedelta(days=50)).strftime("%Y%m%d")

print(f"[*] {today} 전종목 전수 스크리닝 시작...")

# 1. 코스피 / 코스닥 전종목 수집
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

# 전종목 대상 순회 (시총/거래량 유효 종목 중심)
for t in tickers:
    try:
        # 일봉 데이터 30일치 먼저 확인
        df_d = stock.get_market_ohlcv_by_date(start_d, today, t)
        if len(df_d) < 22:
            continue
        
        # 관리종목/동전주 배제 (최근 종가 1,000원 이상, 당일 거래대금 최소 확인)
        cp = df_d['종가'].iloc[-1]
        if cp < 1000:
            continue

        # 주봉 30주선(SMA 30) 수집
        df_w = stock.get_market_ohlcv_by_date(start_w, today, t, "m")
        if len(df_w) < 32:
            continue

        df_w['SMA30'] = df_w['종가'].rolling(30).mean()
        s30 = df_w['SMA30'].iloc[-1]
        p_s30 = df_w['SMA30'].iloc[-4] # 3~4주 전 대비

        # [필터 1] 30주선 기울기 우상향 또는 수평 (하향 추세는 완전 배제)
        if np.isnan(s30) or np.isnan(p_s30) or s30 < (p_s30 * 0.995):
            continue

        # [필터 2] 30주선 풀백 구간 (-3% ~ +7% 이내 밀착 지지)
        disp = (cp / s30) * 100.0
        if not (97.0 <= disp <= 107.0):
            continue

        # [필터 3] 일봉 거래량 수렴/절벽 확인 (20일 평균 거래량 대비 80% 이하)
        v_avg = df_d['거래량'].iloc[-21:-1].mean()
        t_vol = df_d['거래량'].iloc[-1]
        v_rat = t_vol / v_avg if v_avg > 0 else 1.0
        
        if v_rat > 0.80:
            continue

        # 최근 5거래일 연기금/기관 순매수 수집
        df_net = stock.get_market_net_purchases_of_equities_by_ticker(
            df_d.index[-5].strftime("%Y%m%d"), today, "ALL", t
        )
        p_net = df_net.loc[t, "연기금"] if t in df_net.index else 0
        inst_net = df_net.loc[t, "기관합계"] if t in df_net.index else 0

        name = stock.get_market_ticker_name(t)
        sec = SECTORS.get(t, "일반/제조")

        results.append({
            "name": name,
            "sector": sec,
            "price": int(cp),
            "disp": round(disp, 1),
            "v_rat": round(v_rat, 2),
            "p_net": int(p_net),
            "inst_net": int(inst_net)
        })

    except Exception:
        continue

# 텔레그램 발송 메시지 구성
msg = f"📊 *[{today} 스탠 와인스타인 30주선 A포인트 리포트]*\n"
msg += f"- 전체 시장 스캔 포착: *{len(results)}개*\n"
msg += "--------------------------------------\n"

if results:
    df = pd.DataFrame(results).sort_values(by=["sector", "v_rat"])
    # 섹터별로 그룹핑하여 출력
    for sec, grp in df.groupby("sector"):
        msg += f"\n📁 *[{sec}]* ({len(grp)}종목)\n"
        for _, r in grp.head(5).iterrows(): # 섹터별 최대 5개 압축
            p_sign = "+" if r['p_net'] > 0 else ""
            msg += f"• *{r['name']}* (`{r['price']:,}원`)\n"
            msg += f"   - 30주선 이격: `{r['disp']}%` | 거래량: 평소의 `{int(r['v_rat']*100)}%`\n"
            msg += f"   - 연기금 5일: `{p_sign}{r['p_net']:,}주`\n"
else:
    msg += "오늘 30주선 풀백 조건을 충족하는 종목이 없습니다."

send_telegram(msg)
