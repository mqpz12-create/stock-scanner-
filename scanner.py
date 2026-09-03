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

log(f"[*] {today_str} 한국거래소 공식 업종(Sector) 연동 스캔 시작...")

# 1. KOSPI 벤치마크
try:
    df_kospi = fdr.DataReader('KS11', start_str)
    kospi_close = df_kospi['Close']
except Exception:
    kospi_close = None

# 2. 거래소 공식 업종(KRX-DESC) 마스터 테이블 로드
try:
    df_desc = fdr.StockListing('KRX-DESC')
    # Symbol(종목코드), Sector(공식업종), Industry(주요제품)
    code_to_sector = dict(zip(df_desc['Symbol'], df_desc['Sector'].fillna('')))
    code_to_ind = dict(zip(df_desc['Symbol'], df_desc['Industry'].fillna('')))
except Exception as e:
    log(f"[!] KRX-DESC 로드 실패: {e}")
    code_to_sector = {}
    code_to_ind = {}

# 3. 시가총액 상위 종목 수집
df_krx = fdr.StockListing('KRX')
if 'Marcap' in df_krx.columns:
    df_krx = df_krx[df_krx['Marcap'] >= 500_0000_0000]

target_tickers = list(df_krx.sort_values(by='Marcap', ascending=False)['Code'].head(400))
must_have = ["005090", "065060", "094480", "327260", "010170", "028050", "319660", "080220"]
target_tickers = list(set(target_tickers + must_have))

# 거래소 공식 표준산업분류 기반 6대 실전 섹터 판정 함수
def classify_official_sector(code, name):
    sec = code_to_sector.get(code, "")
    ind = code_to_ind.get(code, "")
    full_text = f"{sec} {ind} {name}".lower()

    if any(k in full_text for k in ["반도체", "전자부품", "집적회로", "다이오드", "웨이퍼"]):
        return "반도체/소부장"
    elif any(k in full_text for k in ["절연선", "케이블", "광통신", "전기 가스", "증기", "발전", "전력", "에너지"]):
        return "전력인프라/광통신"
    elif any(k in full_text for k in ["의약품", "의약물질", "바이오", "의료", "병원"]):
        return "바이오/헬스케어"
    elif any(k in full_text for k in ["특수 목적용 기계", "무기", "항공", "우주", "선박", "철도", "플랜트"]):
        return "기계/플랜트/방산"
    elif any(k in full_text for k in ["축전지", "일차전지", "화학물질", "배터리", "석유정제"]):
        return "화학/배터리"
    elif any(k in full_text for k in ["금융업", "신탁업", "지주회사", "보험", "증권"]):
        return "금융/지주"
    
    # 그 외 일반 제조
    return "일반/제조"

def make_vol_bar(ratio_pct):
    filled = int(round(min(ratio_pct / 100.0, 1.0) * 10))
    return "■" * filled + "□" * (10 - filled)

def get_investor_trend(code):
    try:
        url = f"https://m.stock.naver.com/api/stock/{code}/trend?pageSize=5&page=1"
        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)',
            'Referer': 'https://m.stock.naver.com/'
        }
        res = requests.get(url, headers=headers, timeout=4)
        if res.status_code != 200:
            return "⚪️ 수급 공방 (중립)", 3
            
        data = res.json()
        trends = data.get('message', []) if isinstance(data, dict) and 'message' in data else data
        if not trends or len(trends) == 0:
            return "⚪️ 수급 공방 (중립)", 3

        inst_sum = 0
        frgn_sum = 0
        for item in trends[:5]:
            inst_sum += int(str(item.get('institutionPureBuyQuant', '0')).replace(',', ''))
            frgn_sum += int(str(item.get('foreignerPureBuyQuant', '0')).replace(',', ''))

        if inst_sum > 0 and frgn_sum > 0:
            return f"🔥 외인·기관 쌍끌이 (+{inst_sum+frgn_sum:,}주)", 6
        elif inst_sum > 0 and frgn_sum <= 0:
            return f"⭐️ 기관 집중 매집 (+{inst_sum:,}주)", 5
        elif frgn_sum > 0 and inst_sum <= 0:
            return f"💎 외국인 집중 매집 (+{frgn_sum:,}주)", 5
        elif inst_sum < 0 and frgn_sum < 0:
            return "⚠️ 개인 홀로 매수 (외인·기관 동반 매도)", 0
        else:
            return "⚪️ 수급 공방 (중립)", 3
    except Exception:
        return "⚪️ 수급 공방 (중립)", 3

def analyze_stock(code):
    try:
        df_d = fdr.DataReader(code, start_str)
        if len(df_d) < 180 or kospi_close is None:
            return None

        # 키움 일치 주봉 30주선 (W-FRI)
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

        if m_rs >= 40.0:
            rs_tag = f"👑 시장 최상위 슈퍼스톡 (Mansfield +{m_rs:.1f})"
        elif m_rs >= 20.0:
            rs_tag = f"🔥 시장 압도적 주도주 (Mansfield +{m_rs:.1f})"
        elif m_rs >= 5.0:
            rs_tag = f"🚀 지수 대비 강세 돌파 (Mansfield +{m_rs:.1f})"
        elif m_rs >= 0.0:
            rs_tag = f"🟢 지수 대비 우위/견조 (Mansfield +{m_rs:.1f})"
        else:
            rs_tag = f"⚪️ 지수 하회 흐름 (Mansfield {m_rs:.1f})"

        # 5. 패턴
        recent_5 = df_d.iloc[-5:]
        range_5 = (recent_5['High'].max() - recent_5['Low'].min()) / current_price * 100.0
        recent_20 = df_d.iloc[-20:]
        range_20 = (recent_20['High'].max() - recent_20['Low'].min()) / current_price * 100.0
        
        pattern_score = 1
        if range_5 <= 6.0 and vol_ratio_sma20 <= 65.0:
            pattern_tag = "상승깃발형 (변동폭 축소+거래량 절벽)"
            pattern_score = 4
        elif range_5 < (range_20 * 0.45):
            pattern_tag = "삼각수렴 지지 (에너지 응축)"
            pattern_score = 3
        else:
            pattern_tag = "30주선 안정 지지"

        # 6. 수급 분석
        investor_tag, investor_score = get_investor_trend(code)

        name_match = df_krx[df_krx['Code'] == code]
        name = name_match['Name'].iloc[0] if not name_match.empty else code
        name = name.replace("[", "").replace("]", "").replace("*", "")
        
        # 거래소 공식 업종 기반 분류
        sector = classify_official_sector(code, name)

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
            "pattern_score": pattern_score,
            "rs": rs_tag,
            "m_rs": m_rs,
            "investor": investor_tag,
            "investor_score": investor_score
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

    # 섹터별 평균 RS 계산
    sec_rs_mean = df_res.groupby('sector')['m_rs'].mean().to_dict()

    final_results = []
    for _, r in df_res.iterrows():
        score = 0
        # 1) Mansfield RS (30점)
        if r['m_rs'] >= 40.0: score += 30
        elif r['m_rs'] >= 20.0: score += 25
        elif r['m_rs'] >= 5.0: score += 20
        elif r['m_rs'] >= 0.0: score += 14
        else: score += 0

        # 2) 섹터 프리미엄 (15점)
        sec_mean = sec_rs_mean.get(r['sector'], 0.0)
        if r['sector'] in ["반도체/소부장", "전력인프라/광통신", "기계/플랜트/방산"] or sec_mean >= 15.0:
            score += 15
            sec_badge = "🔥 핵심주도섹터"
        elif r['sector'] != "일반/제조":
            score += 10
            sec_badge = "🟢 테마섹터"
        else:
            score += 5
            sec_badge = "⚪️ 개별주"

        # 3) 30주선 지지 완성도 (25점)
        if 99.0 <= r['disp'] <= 102.5: score += 25
        elif 98.0 <= r['disp'] <= 104.5: score += 18
        else: score += 10

        # 4) 거래량 마름 (20점)
        if r['vol_ratio_sma20'] <= 45.0: score += 20
        elif r['vol_ratio_sma20'] <= 65.0: score += 15
        else: score += 8

        # 5) 수급 & 패턴 보너스 (10점)
        score += r['investor_score'] + r['pattern_score']

        r_dict = dict(r)
        r_dict['score'] = score
        r_dict['sec_badge'] = sec_badge
        final_results.append(r_dict)

    df_res = pd.DataFrame(final_results)

    # 1. 주도 섹터 대장주 선별
    df_themed = df_res[df_res['sector'] != "일반/제조"]
    top_sector_leader = None
    if not df_themed.empty:
        top_sector_leader = df_themed.sort_values(by=['score', 'm_rs'], ascending=[False, False]).iloc[0]

    # 2. 독자 돌파 개별 초강세주 (Mansfield RS 1위)
    df_indie = df_res[df_res['code'] != (top_sector_leader['code'] if top_sector_leader is not None else "")]
    indie_alpha = None
    if not df_indie.empty:
        indie_alpha = df_indie.sort_values(by=['m_rs', 'score'], ascending=[False, False]).iloc[0]

    msg += "\n🔥 [TODAY'S HIGHLIGHT : 최우선 관심주]\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    if top_sector_leader is not None:
        bar_t = make_vol_bar(top_sector_leader['vol_ratio_sma20'])
        chart_url_t = f"https://m.stock.naver.com/domestic/stock/{top_sector_leader['code']}/total"
        msg += f"🏆 주도섹터 최우선 대장주 ({top_sector_leader['sec_badge']})\n"
        msg += f"▶ {top_sector_leader['name']} ({top_sector_leader['price']:,}원) [{top_sector_leader['sector']} | 와인스타인 {top_sector_leader['score']}점]\n"
        msg += f"   - 수급주체: {top_sector_leader['investor']}\n"
        msg += f"   - 상대강도: {top_sector_leader['rs']}\n"
        msg += f"   - 패턴: {top_sector_leader['pattern']}\n"
        msg += f"   - 30주선: {top_sector_leader['sma30']:,}원 (이격: {top_sector_leader['disp']}%)\n"
        msg += f"   - 거래량: 20일이평비 {top_sector_leader['vol_ratio_sma20']}% [{bar_t}] (전일비: {top_sector_leader['vol_ratio_prev']}%)\n"
        msg += f"   - 모바일차트: {chart_url_t}\n\n"

    if indie_alpha is not None:
        bar_i = make_vol_bar(indie_alpha['vol_ratio_sma20'])
        chart_url_i = f"https://m.stock.naver.com/domestic/stock/{indie_alpha['code']}/total"
        msg += f"⚡️ 독자 돌파 개별 초강세주 (Mansfield RS 1위)\n"
        msg += f"▶ {indie_alpha['name']} ({indie_alpha['price']:,}원) [{indie_alpha['sector']} | 와인스타인 {indie_alpha['score']}점]\n"
        msg += f"   - 수급주체: {indie_alpha['investor']}\n"
        msg += f"   - 상대강도: {indie_alpha['rs']}\n"
        msg += f"   - 패턴: {indie_alpha['pattern']}\n"
        msg += f"   - 30주선: {indie_alpha['sma30']:,}원 (이격: {indie_alpha['disp']}%)\n"
        msg += f"   - 거래량: 20일이평비 {indie_alpha['vol_ratio_sma20']}% [{bar_i}]\n"
        msg += f"   - 모바일차트: {chart_url_i}\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"

    # 전체 리스트 브리핑 (점수 높은 순 정렬)
    df_sorted = df_res.sort_values(by=["score", "m_rs"], ascending=[False, False])
    for sec, grp in df_sorted.groupby("sector", sort=False):
        msg += f"\n📁 [{sec}] ({len(grp)}개)\n"
        for _, r in grp.iterrows():
            bar = make_vol_bar(r['vol_ratio_sma20'])
            chart_url = f"https://m.stock.naver.com/domestic/stock/{r['code']}/total"
            msg += f"• {r['name']} ({r['price']:,}원) [{r['score']}점 | {r['tag']}]\n"
            msg += f"   - 수급: {r['investor']}\n"
            msg += f"   - 상대강도: {r['rs']}\n"
            msg += f"   - 패턴: {r['pattern']}\n"
            msg += f"   - 30주선: {r['sma30']:,}원 (이격: {r['disp']}%) | 20일이평비: {r['vol_ratio_sma20']}% [{bar}]\n"
            msg += f"   - 일봉거래: {r['vol_today']:,}주 (전일비: {r['vol_ratio_prev']}%)\n"
            msg += f"   - 차트보기: {chart_url}\n"
else:
    msg += "오늘 조건을 충족하는 종목이 없습니다."

send_telegram(msg)
log("[*] 거래소 공식 업종 기반 발송 완료")
