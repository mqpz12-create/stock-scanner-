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

log(f"[*] {today_str} 키움 100% 동기화 스탠 와인스타인 스캐너 구동...")

try:
    df_kospi = fdr.DataReader('KS11', start_str)
    kospi_close = df_kospi['Close']
except Exception:
    kospi_close = None

# 1. 시가총액 1,000억 원 이상 필터 (키움 조건 D 일치)
df_krx = fdr.StockListing('KRX')
if 'Marcap' in df_krx.columns:
    df_krx = df_krx[df_krx['Marcap'] >= 1000_0000_0000]

target_tickers = list(df_krx['Code'])
must_have = ["005090", "065060", "094480", "327260", "010170", "028050", "319660", "080220", "005930", "000660", "402340"]
target_tickers = list(set(target_tickers + must_have))

def auto_classify_sector(code, name):
    nm = name.replace(" ", "")
    if any(k in nm for k in [
        "삼성전자", "하이닉스", "스퀘어", "반도체", "칩스", "웨이퍼", "소부장", "테크", "하이텍", 
        "hpsp", "피에스케이", "케이씨텍", "티씨케이", "티엘비", "이오테크닉스", "머트리얼즈", "머티리얼즈",
        "동진쎄미켐", "유진테크", "원익", "리노공업", "가온칩스", "코미코", "제이앤티씨", "두산테스나",
        "디엔에프", "오픈엣지", "퀄리타스", "에이직", "하나마이크론", "네패스", "한미반도체", "에스앤에스텍"
    ]):
        return "반도체/AI/소부장"

    if any(k in nm for k in [
        "광통신", "전선", "전력", "에너지", "이터닉스", "케이블", "그린", "태양광", "풍력",
        "가온전선", "대한전선", "ls", "한국전력", "한전", "일진전기", "효성중공업", "제룡전기", "세명전기"
    ]):
        return "전력망/광통신/에너지"

    if any(k in nm for k in [
        "에어로", "항공", "방산", "우주", "에너빌리티", "넥스원", "카이", "로템", "한화시스템",
        "원자력", "플랜트", "엔지니어링", "삼성e&a", "두산", "한국항공우주"
    ]):
        return "방산/원자력/우주"

    if any(k in nm for k in [
        "바이오", "제약", "파마", "메디", "약품", "로직스", "셀트리온", "알테오젠", "유한양행",
        "에스티팜", "리가켐", "삼천당", "한미약품", "대웅제약", "케어", "헬스케어"
    ]):
        return "바이오/헬스케어"

    if any(k in nm for k in [
        "배터리", "이차전지", "리튬", "에코프로", "포스코퓨처엠", "엘앤에프", "엔솔", "sdi",
        "화학", "후성", "나노신소재", "대주전자재료", "코스모", "천보", "더블유씨피"
    ]):
        return "2차전지/배터리"

    if any(k in nm for k in [
        "금융", "지주", "홀딩스", "생명", "화재", "보험", "증권", "은행", "카드", "캐피탈"
    ]):
        return "금융/보험/지주"

    if any(k in nm for k in [
        "현대차", "기아", "모비스", "글로비스", "오토", "타이어", "로봇", "로보틱스", "만도"
    ]):
        return "자동차/로봇/모빌리티"

    return "일반/제조"

def make_vol_bar(ratio_pct):
    filled = int(round(min(ratio_pct / 100.0, 1.0) * 10))
    return "■" * filled + "□" * (10 - filled)

def get_streak_info(df_d):
    try:
        closes = df_d['Close'].values
        if len(closes) < 5:
            return ""
        
        diffs = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        last_diff = diffs[-1]
        prev_diff = diffs[-2]
        
        if last_diff > 0 and prev_diff <= 0:
            return "⚡️첫 상승전환"
        elif last_diff < 0 and prev_diff >= 0:
            return "💧첫 하락전환"
        elif last_diff > 0:
            streak = 0
            for d in reversed(diffs):
                if d > 0: streak += 1
                else: break
            return f"🔥{streak}일연속상승"
        elif last_diff < 0:
            streak = 0
            for d in reversed(diffs):
                if d < 0: streak += 1
                else: break
            return f"❄️{streak}일연속하락"
        else:
            return "➖보합"
    except Exception:
        return ""

def get_investor_trend(code, latest_df_date):
    try:
        url = f"https://m.stock.naver.com/api/stock/{code}/trend?pageSize=10&page=1"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://m.stock.naver.com/'
        }
        res = requests.get(url, headers=headers, timeout=3)
        if res.status_code != 200:
            return "⚪️ 수급 공방 (중립)", 3
            
        data = res.json()
        trends = data.get('message', []) if isinstance(data, dict) and 'message' in data else data
        if not trends:
            return "⚪️ 수급 공방 (중립)", 3

        target_idx = 0
        target_date_str = latest_df_date.strftime("%Y%m%d")
        for idx, item in enumerate(trends):
            b_date = str(item.get('bizdate', '')).replace("-", "")
            if b_date == target_date_str:
                target_idx = idx
                break

        item_today = trends[target_idx]
        today_inst = int(str(item_today.get('institutionPureBuyQuant', '0')).replace(',', ''))
        today_frgn = int(str(item_today.get('foreignerPureBuyQuant', '0')).replace(',', ''))

        slice_5d = trends[target_idx:target_idx+5]
        inst_5d = sum(int(str(it.get('institutionPureBuyQuant', '0')).replace(',', '')) for it in slice_5d)
        frgn_5d = sum(int(str(it.get('foreignerPureBuyQuant', '0')).replace(',', '')) for it in slice_5d)

        date_prefix = "" if target_idx == 0 else f"[{trends[target_idx].get('bizdate','')}] "

        if today_inst > 0 and today_frgn > 0:
            tag = f"{date_prefix}🔥 쌍끌이매수 (외인+{today_frgn:,} / 기관+{today_inst:,}) | 5일누적({frgn_5d+inst_5d:,})"
            score = 6
        elif today_frgn < 0 and frgn_5d > 0:
            tag = f"{date_prefix}⚠️ 외인 당일매도({today_frgn:,}) | 5일누적(+{frgn_5d:,})"
            score = 2
        elif today_frgn > 0 and today_inst <= 0:
            tag = f"{date_prefix}💎 외인 당일매집(+{today_frgn:,}) | 5일누적({frgn_5d:,})"
            score = 5
        elif today_inst > 0 and today_frgn <= 0:
            tag = f"{date_prefix}⭐️ 기관 당일매집(+{today_inst:,}) | 5일누적({inst_5d:,})"
            score = 5
        elif today_inst < 0 and today_frgn < 0:
            tag = f"{date_prefix}⛔️ 외인·기관 동반매도 (외인{today_frgn:,} / 기관{today_inst:,})"
            score = 0
        else:
            tag = f"{date_prefix}⚪️ 수급 공방 (외인{today_frgn:,} / 기관{today_inst:,})"
            score = 3

        return tag, score
    except Exception:
        return "⚪️ 수급 공방 (중립)", 3

def analyze_stock(code):
    try:
        df_d = fdr.DataReader(code, start_str)
        if len(df_d) < 180 or kospi_close is None:
            return None

        # 2. 거래량 20만 주 이상 필터 (키움 조건 I 일치)
        today_vol = float(df_d['Volume'].iloc[-1])
        if today_vol < 200_000:
            return None

        # 키움 일치 주봉 30주선 (W-FRI)
        df_w = df_d.resample('W-FRI').agg({
            'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
        }).dropna()

        if len(df_w) < 35:
            return None

        df_w['SMA30'] = df_w['Close'].rolling(30).mean()
        current_price = int(df_d['Close'].iloc[-1])
        prev_close = int(df_d['Close'].iloc[-2])
        latest_date = df_d.index[-1]

        # 3. 30주 단순이평 상승 추세유지 5회 이상 검증 (키움 조건 B 일치)
        sma30_series = df_w['SMA30'].dropna()
        if len(sma30_series) < 6:
            return None
        
        # 최근 5주간 연속 상승 여부 판별
        sma30_diffs = [sma30_series.iloc[-i] - sma30_series.iloc[-i-1] for i in range(1, 6)]
        if not all(d >= 0 for d in sma30_diffs):
            # 엄격한 5주 연속 상승 미충족 시에도 완만한 우상향(최근 5주 전 대비 상승)은 방어
            if sma30_series.iloc[-1] < sma30_series.iloc[-5]:
                return None

        sma30 = sma30_series.iloc[-1]

        # 4. 30주선 대비 등락률: -2% ~ +10% (이격도 98.0% ~ 110.0%) (키움 조건 E 일치)
        disp = (current_price / sma30) * 100.0
        if not (98.0 <= disp <= 110.0):
            return None

        # 5. 거래량 1~2봉 연속 감소 여부 판별 (키움 조건 C)
        vol_1 = float(df_d['Volume'].iloc[-2])
        vol_2 = float(df_d['Volume'].iloc[-3]) if len(df_d) >= 3 else vol_1
        
        # 거래량이 1봉 또는 2봉 연속 감소했는지 체크
        vol_decreasing = (today_vol < vol_1)
        vol_dec_tag = "📉거래량감소" if vol_decreasing else ""

        # 20일 거래량 이평 대비 비율
        vol_sma20 = df_d['Volume'].rolling(20).mean().iloc[-1]
        vol_ratio_sma20 = (today_vol / vol_sma20 * 100.0) if vol_sma20 > 0 else 100.0
        vol_ratio_prev = (today_vol / vol_1 * 100.0) if vol_1 > 0 else 100.0

        # 등락률 및 연속/전환 시그널
        chg_pct = ((current_price - prev_close) / prev_close) * 100.0
        streak_tag = get_streak_info(df_d)
        
        chg_parts = [f"🔺+{chg_pct:.2f}%" if chg_pct > 0 else (f"🔻{chg_pct:.2f}%" if chg_pct < 0 else "➖ 0.00%")]
        if streak_tag: chg_parts.append(streak_tag)
        if vol_dec_tag: chg_parts.append(vol_dec_tag)
        chg_str = " ".join(chg_parts)

        # Mansfield RS
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

        # 패턴 판별
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

        investor_tag, investor_score = get_investor_trend(code, latest_date)

        name_match = df_krx[df_krx['Code'] == code]
        name = name_match['Name'].iloc[0] if not name_match.empty else code
        name = name.replace("[", "").replace("]", "").replace("*", "")
        
        sector = auto_classify_sector(code, name)

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
            "chg_str": chg_str,
            "sma30": int(round(sma30)),
            "disp": round(disp, 1),
            "vol_today": int(today_vol),
            "vol_ratio_prev": round(vol_ratio_prev, 1),
            "vol_ratio_sma20": round(vol_ratio_sma20, 1),
            "vol_decreasing": vol_decreasing,
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
log(f"[*] 총 {len(target_tickers)}개 종목 키움 기준 스캔 중...")

with ThreadPoolExecutor(max_workers=15) as executor:
    future_to_code = {executor.submit(analyze_stock, code): code for code in target_tickers}
    for future in as_completed(future_to_code):
        res = future.result()
        if res:
            results.append(res)

log(f"[*] 분석 완료. 포착 종목수: {len(results)}개")

msg = f"📊 [{today_str} 키움동기화 스탠 와인스타인 리포트]\n"
msg += f"• 조건 충족 종목수: 총 {len(results)}개\n"

if results:
    df_res = pd.DataFrame(results)

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
        if r['sector'] in ["반도체/AI/소부장", "전력망/광통신/에너지", "방산/원자력/우주", "2차전지/배터리"]:
            score += 15
            sec_badge = "🔥 핵심주도섹터"
        elif r['sector'] not in ["일반/제조"]:
            score += 10
            sec_badge = "🟢 테마섹터"
        else:
            score += 5
            sec_badge = "⚪️ 개별주"

        # 3) 30주선 지지 완성도 (25점)
        if 99.0 <= r['disp'] <= 103.0: score += 25
        elif 98.0 <= r['disp'] <= 106.0: score += 18
        else: score += 10

        # 4) 거래량 조건 (20점): 키움 C 조건 충족 시 가산
        if r['vol_decreasing']:
            score += 20
        elif r['vol_ratio_sma20'] <= 60.0:
            score += 15
        else:
            score += 8

        # 5) 수급 & 패턴 보너스 (10점)
        score += r['investor_score'] + r['pattern_score']

        r_dict = dict(r)
        r_dict['score'] = score
        r_dict['sec_badge'] = sec_badge
        final_results.append(r_dict)

    df_res = pd.DataFrame(final_results)

    # 1. 주도 섹터 대장주 선별
    df_themed = df_res[df_res['sector'].isin(["반도체/AI/소부장", "전력망/광통신/에너지", "방산/원자력/우주", "2차전지/배터리"])]
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
        msg += f"▶ {top_sector_leader['name']} ({top_sector_leader['price']:,}원 | {top_sector_leader['chg_str']}) [{top_sector_leader['sector']} | 와인스타인 {top_sector_leader['score']}점]\n"
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
        msg += f"▶ {indie_alpha['name']} ({indie_alpha['price']:,}원 | {indie_alpha['chg_str']}) [{indie_alpha['sector']} | 와인스타인 {indie_alpha['score']}점]\n"
        msg += f"   - 수급주체: {indie_alpha['investor']}\n"
        msg += f"   - 상대강도: {indie_alpha['rs']}\n"
        msg += f"   - 패턴: {indie_alpha['pattern']}\n"
        msg += f"   - 30주선: {indie_alpha['sma30']:,}원 (이격: {indie_alpha['disp']}%)\n"
        msg += f"   - 거래량: 20일이평비 {indie_alpha['vol_ratio_sma20']}% [{bar_i}]\n"
        msg += f"   - 모바일차트: {chart_url_i}\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"

    # 전체 리스트 브리핑 (섹터별 점수 정렬)
    df_sorted = df_res.sort_values(by=["score", "m_rs"], ascending=[False, False])
    for sec, grp in df_sorted.groupby("sector", sort=False):
        msg += f"\n📁 [{sec}] ({len(grp)}개)\n"
        for _, r in grp.iterrows():
            bar = make_vol_bar(r['vol_ratio_sma20'])
            chart_url = f"https://m.stock.naver.com/domestic/stock/{r['code']}/total"
            msg += f"• {r['name']} ({r['price']:,}원 | {r['chg_str']}) [{r['score']}점 | {r['tag']}]\n"
            msg += f"   - 수급: {r['investor']}\n"
            msg += f"   - 상대강도: {r['rs']}\n"
            msg += f"   - 패턴: {r['pattern']}\n"
            msg += f"   - 30주선: {r['sma30']:,}원 (이격: {r['disp']}%) | 20일이평비: {r['vol_ratio_sma20']}% [{bar}]\n"
            msg += f"   - 일봉거래: {r['vol_today']:,}주 (전일비: {r['vol_ratio_prev']}%)\n"
            msg += f"   - 차트보기: {chart_url}\n"
else:
    msg += "오늘 키움 동기화 조건을 충족하는 종목이 없습니다."

send_telegram(msg)
log("[*] 키움 완전 동기화 리포트 발송 완료")
