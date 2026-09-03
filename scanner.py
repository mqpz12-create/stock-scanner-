import os
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
        log("[!] 텔레그램 토큰 또는 Chat ID 누락")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    
    max_len = 3500
    msg_chunks = [message[i:i+max_len] for i in range(0, len(message), max_len)]
    
    for chunk in msg_chunks:
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
start_str = (datetime.today() - timedelta(days=550)).strftime("%Y-%m-%d")

log(f"[*] {today_str} 정통 Mansfield RS + 수급 매동 스캔 시작...")

# 1. 코스피 벤치마크
try:
    df_kospi = fdr.DataReader('KS11', start_str)
    kospi_close = df_kospi['Close']
except Exception:
    kospi_close = None

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

def make_vol_bar(ratio_pct):
    filled = int(round(min(ratio_pct / 100.0, 1.0) * 10))
    return "■" * filled + "□" * (10 - filled)

# 네이버 투자자별 매매동향(외인/기관 최근 5일 순매수) 수집 함수
def get_investor_trend(code):
    try:
        url = f"https://finance.naver.com/item/frgn.naver?code={code}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=5)
        dfs = pd.read_html(res.text)
        
        # 외인/기관 순매수 테이블 (통상 2번째 테이블)
        df_inv = dfs[2].dropna(subset=['날짜'])
        df_inv = df_inv.iloc[:5] # 최근 5거래일
        
        inst_net = pd.to_numeric(df_inv['기관'].astype(str).str.replace(',', ''), errors='coerce').sum()
        frgn_net = pd.to_numeric(df_inv['외국인'].astype(str).str.replace(',', ''), errors='coerce').sum()
        
        if inst_net > 0 and frgn_net > 0:
            return "🔥 외인·기관 쌍끌이 순매수", 15
        elif inst_net > 0 and frgn_net <= 0:
            return "⭐️ 기관 순매수 유입", 10
        elif frgn_net > 0 and inst_net <= 0:
            return "💎 외국인 순매수 유입", 10
        elif inst_net < 0 and frgn_net < 0:
            return "⚠️ 개인 홀로 매수 (외인·기관 동반 매도)", 0
        else:
            return "⚪️ 수급 공방 (중립)", 5
    except Exception:
        return "⚪️ 수급 데이터 집계 중", 5

def analyze_stock(code):
    try:
        df_d = fdr.DataReader(code, start_str)
        if len(df_d) < 180 or kospi_close is None:
            return None

        # 키움 일치형 주봉 30주선
        df_w = df_d.resample('W-FRI').agg({
            'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
        }).dropna()

        if len(df_w) < 35:
            return None

        df_w['SMA30'] = df_w['Close'].rolling(30).mean()
        current_price = int(df_d['Close'].iloc[-1])
        sma30 = df_w['SMA30'].iloc[-1]
        prev_sma30 = df_w['SMA30'].iloc[-5]

        # 1. 30주선 우상향/보합
        if np.isnan(sma30) or np.isnan(prev_sma30) or sma30 < (prev_sma30 * 0.995):
            return None

        # 2. 30주선 풀백 구간 (-3% ~ +8%)
        disp = (current_price / sma30) * 100.0
        if not (97.0 <= disp <= 108.0):
            return None

        # 3. 거래량 절벽 정밀 계산
        today_vol = float(df_d['Volume'].iloc[-1])
        prev_vol = float(df_d['Volume'].iloc[-2])
        vol_ratio_prev = (today_vol / prev_vol * 100.0) if prev_vol > 0 else 100.0
        vol_sma20 = df_d['Volume'].rolling(20).mean().iloc[-1]
        vol_ratio_sma20 = (today_vol / vol_sma20 * 100.0) if vol_sma20 > 0 else 100.0

        if vol_ratio_sma20 > 90.0:
            return None

        # 4. 정통 52주 Mansfield Relative Strength
        df_rs = pd.DataFrame({'stock': df_d['Close'], 'kospi': kospi_close}).dropna()
        if len(df_rs) >= 120:
            rs_line = df_rs['stock'] / df_rs['kospi']
            window = min(len(df_rs), 250)
            rs_sma = rs_line.rolling(window, min_periods=60).mean()
            m_rs = ((rs_line.iloc[-1] / rs_sma.iloc[-1]) - 1.0) * 100.0
        else:
            m_rs = 0.0

        if m_rs >= 15.0:
            rs_tag = f"🔥 시장 압도적 초강세 (Mansfield +{m_rs:.1f})"
        elif m_rs >= 5.0:
            rs_tag = f"🚀 지수 대비 강세 (Mansfield +{m_rs:.1f})"
        elif m_rs >= 0.0:
            rs_tag = f"🟢 지수 대비 우위 (Mansfield +{m_rs:.1f})"
        else:
            rs_tag = f"⚪️ 시장 하회 흐름 (Mansfield {m_rs:.1f})"

        # 5. 차트 수렴 패턴
        recent_5 = df_d.iloc[-5:]
        range_5 = (recent_5['High'].max() - recent_5['Low'].min()) / current_price * 100.0
        recent_20 = df_d.iloc[-20:]
        range_20 = (recent_20['High'].max() - recent_20['Low'].min()) / current_price * 100.0
        
        is_flag = False
        if range_5 <= 6.0 and vol_ratio_sma20 <= 65.0:
            pattern_tag = "상승깃발형 (변동폭 축소+거래량 절벽)"
            is_flag = True
        elif range_5 < (range_20 * 0.45):
            pattern_tag = "삼각수렴 지지 (에너지 응축)"
        else:
            pattern_tag = "30주선 안정 지지"

        # 6. 매매동향(수급) 분석 실행 (조건 통과 종목에만 호출하여 속도 유지)
        investor_tag, investor_score = get_investor_trend(code)

        # 종합 스코어링 (100점 만점)
        score = 0
        # Mansfield RS (35점)
        if m_rs >= 15.0: score += 35
        elif m_rs >= 5.0: score += 28
        elif m_rs >= 0.0: score += 20
        else: score += 8

        # 30주선 지지 완성도 (25점)
        if 99.5 <= disp <= 102.5: score += 25
        elif 98.0 <= disp <= 104.5: score += 18
        else: score += 10

        # 거래량 마름 (15점)
        if vol_ratio_sma20 <= 45.0: score += 15
        elif vol_ratio_sma20 <= 65.0: score += 11
        else: score += 6

        # 수급 점수 (15점)
        score += investor_score

        # 패턴 점수 (10점)
        if is_flag: score += 10
        elif "삼각수렴" in pattern_tag: score += 7
        else: score += 3

        name_match = df_krx[df_krx['Code'] == code]
        name = name_match['Name'].iloc[0] if not name_match.empty else code
        name = name.replace("[", "").replace("]", "").replace("*", "")
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
            "vol_today": int(today_vol),
            "vol_ratio_prev": round(vol_ratio_prev, 1),
            "vol_ratio_sma20": round(vol_ratio_sma20, 1),
            "tag": tag,
            "pattern": pattern_tag,
            "rs": rs_tag,
            "m_rs": m_rs,
            "investor": investor_tag,
            "score": score
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

msg = f"📊 [{today_str} 스탠 와인스타인 실전 입체 리포트]\n"
msg += f"• 조건 충족 종목수: 총 {len(results)}개\n"

if results:
    df_res = pd.DataFrame(results)

    # 1. 주도 섹터 대장주 (포착 종목이 많은 섹터의 최고점주)
    df_themed = df_res[df_res['sector'] != "일반/기타"]
    top_sector_leader = None
    if not df_themed.empty:
        leading_sec = df_themed['sector'].value_counts().index[0]
        top_sector_leader = df_themed[df_themed['sector'] == leading_sec].sort_values(by=['score', 'm_rs'], ascending=[False, False]).iloc[0]

    # 2. 독자 돌파 개별 초강세주 (Mansfield RS 1위)
    df_indie = df_res[df_res['code'] != (top_sector_leader['code'] if top_sector_leader is not None else "")]
    indie_alpha = None
    if not df_indie.empty:
        indie_alpha = df_indie.sort_values(by=['m_rs', 'score'], ascending=[False, False]).iloc[0]

    msg += "\n🔥 [TODAY'S HIGHLIGHT : 최우선 관심주]\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    if top_sector_leader is not None:
        bar_t = make_vol_bar(top_sector_leader['vol_ratio_sma20'])
        msg += f"🏆 주도섹터 최우선 대장주\n"
        msg += f"▶ {top_sector_leader['name']} ({top_sector_leader['price']:,}원) [{top_sector_leader['sector']} | 와인스타인 {top_sector_leader['score']}점]\n"
        msg += f"   - 수급주체: {top_sector_leader['investor']}\n"
        msg += f"   - 상대강도: {top_sector_leader['rs']}\n"
        msg += f"   - 패턴: {top_sector_leader['pattern']}\n"
        msg += f"   - 30주선: {top_sector_leader['sma30']:,}원 (이격: {top_sector_leader['disp']}%)\n"
        msg += f"   - 거래량: 20일이평비 {top_sector_leader['vol_ratio_sma20']}% [{bar_t}] (전일비: {top_sector_leader['vol_ratio_prev']}%)\n"
        msg += f"   - 차트: https://m.stock.naver.com/item/{top_sector_leader['code']}\n\n"

    if indie_alpha is not None:
        bar_i = make_vol_bar(indie_alpha['vol_ratio_sma20'])
        msg += f"⚡️ 독자 돌파 개별 초강세주 (Mansfield RS 1위)\n"
        msg += f"▶ {indie_alpha['name']} ({indie_alpha['price']:,}원) [{indie_alpha['sector']} | 와인스타인 {indie_alpha['score']}점]\n"
        msg += f"   - 수급주체: {indie_alpha['investor']}\n"
        msg += f"   - 상대강도: {indie_alpha['rs']}\n"
        msg += f"   - 패턴: {indie_alpha['pattern']}\n"
        msg += f"   - 30주선: {indie_alpha['sma30']:,}원 (이격: {indie_alpha['disp']}%)\n"
        msg += f"   - 거래량: 20일이평비 {indie_alpha['vol_ratio_sma20']}% [{bar_i}]\n"
        msg += f"   - 차트: https://m.stock.naver.com/item/{indie_alpha['code']}\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"

    # 전체 종목 리스트 브리핑 (수급주체 라인 필수 포함)
    df_sorted = df_res.sort_values(by=["sector", "m_rs"], ascending=[True, False])
    for sec, grp in df_sorted.groupby("sector"):
        msg += f"\n📁 [{sec}] ({len(grp)}개)\n"
        for _, r in grp.iterrows():
            bar = make_vol_bar(r['vol_ratio_sma20'])
            msg += f"• {r['name']} ({r['price']:,}원) [{r['score']}점 | {r['tag']}]\n"
            msg += f"   - 수급: {r['investor']}\n"
            msg += f"   - 상대강도: {r['rs']}\n"
            msg += f"   - 패턴: {r['pattern']}\n"
            msg += f"   - 30주선: {r['sma30']:,}원 (이격: {r['disp']}%) | 20일이평비: {r['vol_ratio_sma20']}% [{bar}]\n"
            msg += f"   - 일봉거래: {r['vol_today']:,}주 (전일비: {r['vol_ratio_prev']}%)\n"
            msg += f"   - 차트: https://m.stock.naver.com/item/{r['code']}\n"
else:
    msg += "오늘 조건을 충족하는 종목이 없습니다."

send_telegram(msg)
log("[*] 텔레그램 발송 완료")
