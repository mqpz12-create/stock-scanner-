import os
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import FinanceDataReader as fdr

def log(text):
    print(text, flush=True)

# 텔레그램 안전 전송 (파싱 에러가 없는 일반 텍스트 모드)
def send_telegram(message):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log("[!] 텔레그램 토큰 또는 Chat ID 누락")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    
    max_len = 3500
    msg_chunks = [message[i:i+max_len] for i in range(0, len(message), max_len)]
    
    for chunk in msg_chunks:
        # parse_mode를 빼서 괄호/퍼센트 충돌을 원천 차단
        payload = {
            "chat_id": chat_id, 
            "text": chunk, 
            "disable_web_page_preview": True
        }
        try:
            res = requests.post(url, json=payload, timeout=10)
            log(f"[*] 텔레그램 응답 코드: {res.status_code}")
        except Exception as e:
            log(f"[!] 전송 에러: {e}")

today_str = datetime.today().strftime("%Y-%m-%d")
start_str = (datetime.today() - timedelta(days=450)).strftime("%Y-%m-%d")

log(f"[*] {today_str} 스탠 와인스타인 실전 입체 스캔 시작...")

# 코스피 벤치마크 (상대강도 RS용)
try:
    df_kospi = fdr.DataReader('KS11', start_str)
    kospi_ret = (df_kospi['Close'].iloc[-1] / df_kospi['Close'].iloc[-20]) - 1.0
except Exception:
    kospi_ret = 0.0

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

def make_vol_bar(ratio):
    filled = int(round(min(ratio, 1.0) * 10))
    return "■" * filled + "□" * (10 - filled)

def analyze_stock(code):
    try:
        df_d = fdr.DataReader(code, start_str)
        if len(df_d) < 150:
            return None

        # 키움 MTS 일치 주봉 생성
        df_w = df_d.resample('W-FRI').agg({
            'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
        }).dropna()

        if len(df_w) < 35:
            return None

        df_w['SMA30'] = df_w['Close'].rolling(30).mean()
        current_price = int(df_d['Close'].iloc[-1])
        sma30 = df_w['SMA30'].iloc[-1]
        prev_sma30 = df_w['SMA30'].iloc[-5]

        # 1. 30주선 우상향/보합 확인
        if np.isnan(sma30) or np.isnan(prev_sma30) or sma30 < (prev_sma30 * 0.995):
            return None

        # 2. 30주선 풀백 구간 (-3% ~ +8%)
        disp = (current_price / sma30) * 100.0
        if not (97.0 <= disp <= 108.0):
            return None

        # 3. 거래량 절벽 검증 (20일 평균 대비 90% 이하)
        v_avg = df_d['Volume'].iloc[-21:-1].mean()
        t_vol = df_d['Volume'].iloc[-1]
        vol_ratio = (t_vol / v_avg) if v_avg > 0 else 1.0
        if vol_ratio > 0.90:
            return None

        # 4. 차트 패턴 분석
        recent_5 = df_d.iloc[-5:]
        range_5 = (recent_5['High'].max() - recent_5['Low'].min()) / current_price * 100.0
        recent_20 = df_d.iloc[-20:]
        range_20 = (recent_20['High'].max() - recent_20['Low'].min()) / current_price * 100.0
        
        if range_5 <= 6.0 and vol_ratio <= 0.60:
            pattern_tag = "상승깃발형 (변동폭 축소+거래량 절벽)"
        elif range_5 < (range_20 * 0.45):
            pattern_tag = "삼각수렴 지지 (에너지 응축)"
        else:
            pattern_tag = "30주선 안정 지지"

        # 5. 상대강도 RS
        stock_ret_20 = (current_price / df_d['Close'].iloc[-20]) - 1.0
        rs_diff = (stock_ret_20 - kospi_ret) * 100.0
        if rs_diff >= 5.0:
            rs_tag = f"지수 대비 초강세 (+{rs_diff:.1f}%)"
        elif rs_diff >= 0.0:
            rs_tag = f"지수 대비 견조 (+{rs_diff:.1f}%)"
        else:
            rs_tag = f"지수 연동 흐름 ({rs_diff:.1f}%)"

        name_match = df_krx[df_krx['Code'] == code]
        name = name_match['Name'].iloc[0] if not name_match.empty else code
        sector = SECTORS.get(code, "일반/기타")

        if 99.0 <= disp <= 103.0:
            tag = "30주선 초밀착"
        elif disp < 99.0:
            tag = "일시 언더슈팅"
        else:
            tag = "30주선 위 지지"

        return {
            "code": code,
            "name": name,
            "sector": sector,
            "price": current_price,
            "sma30": int(round(sma30)),
            "disp": round(disp, 1),
            "vol_ratio": round(vol_ratio, 2),
            "tag": tag,
            "pattern": pattern_tag,
            "rs": rs_tag
        }
    except Exception:
        return None

results = []
log(f"[*] 총 {len(target_tickers)}개 종목 분석 중...")

with ThreadPoolExecutor(max_workers=15) as executor:
    future_to_code = {executor.submit(analyze_stock, code): code for code in target_tickers}
    for future in as_completed(future_to_code):
        res = future.result()
        if res:
            results.append(res)

log(f"[*] 분석 완료. 포착 종목수: {len(results)}개")

# 일반 텍스트 포맷 (특수문자 에러 원천 방지)
msg = f"📊 [{today_str} 스탠 와인스타인 30주선 실전 리포트]\n"
msg += f"• 조건 충족 종목수: 총 {len(results)}개\n"
msg += "────────────────────\n"

if results:
    df_res = pd.DataFrame(results).sort_values(by=["sector", "vol_ratio"])
    for sec, grp in df_res.groupby("sector"):
        msg += f"\n📁 [{sec}] ({len(grp)}개)\n"
        for _, r in grp.iterrows():
            bar = make_vol_bar(r['vol_ratio'])
            pct = int(r['vol_ratio'] * 100)
            
            msg += f"▶ {r['name']} ({r['price']:,}원) [{r['tag']}]\n"
            msg += f"   - 패턴: {r['pattern']}\n"
            msg += f"   - 상대강도: {r['rs']}\n"
            msg += f"   - 30주선: {r['sma30']:,}원 (이격도: {r['disp']}%)\n"
            msg += f"   - 거래량: 평소의 {pct}% [{bar}]\n"
            msg += f"   - 차트보기: https://m.stock.naver.com/item/{r['code']}\n"
else:
    msg += "오늘 주봉 30주선 지지 조건을 충족하는 종목이 없습니다."

send_telegram(msg)
log("[*] 텔레그램 전송 루틴 종료")
