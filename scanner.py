import os
import requests
import pandas as pd
import numpy as np
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup

def log(text):
    print(text, flush=True)

def send_telegram(message):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log("[!] 텔레그램 토큰 또는 Chat ID 누락")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    
    # 텔레그램 메시지 길이 제한(4,096자) 대응: 3,000자 단위 안전 분할
    max_len = 3000
    msg_chunks = [message[i:i+max_len] for i in range(0, len(message), max_len)]
    
    for idx, chunk in enumerate(msg_chunks):
        payload = {
            "chat_id": chat_id, 
            "text": chunk, 
            "disable_web_page_preview": True
        }
        try:
            res = requests.post(url, json=payload, timeout=10)
            log(f"[*] 텔레그램 [{idx+1}/{len(msg_chunks)}] 응답 코드: {res.status_code}")
        except Exception as e:
            log(f"[!] 전송 에러: {e}")

today_str = datetime.today().strftime("%Y-%m-%d")
log(f"[*] {today_str} 100점 만점 개별주 VCP 스캐너 가동...")

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148',
    'Referer': 'https://m.stock.naver.com/'
})

# ============================================================
# 1. 네이버 당일 주도 테마 TOP 10 수집
# ============================================================
top_themes = []
try:
    url = "https://finance.naver.com/sise/theme.naver?&page=1"
    res = session.get(url, timeout=5)
    if res.status_code == 200:
        soup = BeautifulSoup(res.content.decode('euc-kr', 'replace'), 'html.parser')
        rows = soup.find_all('tr')
        for tr in rows:
            name_td = tr.find('td', class_='col_type1')
            rate_td = tr.find('td', class_='col_type2')
            if name_td and rate_td:
                a_tag = name_td.find('a')
                span_tag = rate_td.find('span')
                if a_tag and span_tag:
                    t_name = a_tag.text.strip()
                    t_rate_str = span_tag.text.strip().replace('%', '').replace('+', '').replace(',', '')
                    try:
                        top_themes.append((t_name, float(t_rate_str)))
                    except ValueError:
                        pass
        top_themes.sort(key=lambda x: x[1], reverse=True)
        top_themes = top_themes[:10]
except Exception as e:
    log(f"[!] 테마 수집 실패: {e}")

# ============================================================
# 2. 전종목 리스트 API 고속 확보 (ETF/ETN/인덱스 전수 차단)
# ============================================================
code_to_name = {}

# 국내 모든 ETF 운용사 브랜드 및 파생/지수 키워드 전수 차단
EXCLUDE_KEYWORDS = [
    'KODEX', 'TIGER', 'ACE', 'KBSTAR', 'RISE', 'SOL', 'PLUS', 'HANARO', 
    'KOSEF', 'KIWOOM', 'TIMEFOLIO', '히어로즈', 'WOORI', 'WON', '하나로', 
    'ARIRANG', 'FOCUS', 'TRUSTON', 'UNIONE', 'ETF', 'ETN', '인버스', 
    '레버리지', '선물', '스팩', '리츠', '채권', 'TR', '200', '코스피', '코스닥'
]

def get_market_tickers(market_type):
    items = []
    # 코스피/코스닥 각 25페이지 (총 약 3,000개 수집 후 필터링)
    for page in range(1, 26):
        api_url = f"https://m.stock.naver.com/api/stocks/marketValue/{market_type}?page={page}&pageSize=60"
        try:
            r = session.get(api_url, timeout=4)
            if r.status_code != 200:
                break
            data = r.json()
            stocks = data.get('stocks', [])
            if not stocks:
                break
            for s in stocks:
                cd = str(s.get('itemCode', '')).strip().zfill(6)
                nm = str(s.get('stockName', '')).strip()
                if not cd or not nm:
                    continue
                
                # ETF 브랜드, 우선주, 스팩 철저 배제
                if any(k in nm for k in EXCLUDE_KEYWORDS) or nm.endswith('우') or nm.endswith('우B') or nm.endswith('3우C'):
                    continue
                items.append((cd, nm))
        except Exception:
            break
    return items

log("[*] 코스피/코스닥 순수 개별주 취득 중...")
with ThreadPoolExecutor(max_workers=2) as exec_ticker:
    f_kospi = exec_ticker.submit(get_market_tickers, "KOSPI")
    f_kosdaq = exec_ticker.submit(get_market_tickers, "KOSDAQ")
    
    for cd, nm in f_kospi.result() + f_kosdaq.result():
        code_to_name[cd] = nm

target_tickers = list(code_to_name.keys())
log(f"[*] 유효 스캔 대상 순수 상장 보통주: {len(target_tickers)}개 확보 완료")

# ============================================================
# 3. 캔들 수집 파이프라인
# ============================================================
def get_daily_candle(code, count=400):
    code_str = str(code).strip().zfill(6)
    url = f"https://fchart.stock.naver.com/sise.nhn?symbol={code_str}&timeframe=day&count={count}&requestType=0"
    try:
        r = session.get(url, timeout=3.5)
        if r.status_code != 200:
            return None
        lines = r.text.split('\n')
        rows = []
        for l in lines:
            if 'item data=' in l:
                val = l.split('"')[1].split('|')
                rows.append({
                    'Date': pd.to_datetime(val[0]),
                    'Open': float(val[1]),
                    'High': float(val[2]),
                    'Low': float(val[3]),
                    'Close': float(val[4]),
                    'Volume': float(val[5])
                })
        if not rows:
            return None
        df = pd.DataFrame(rows)
        df.set_index('Date', inplace=True)
        return df
    except Exception:
        return None

try:
    df_kospi = get_daily_candle("069500", count=400)
    kospi_close = df_kospi['Close'] if df_kospi is not None else None
except Exception:
    kospi_close = None

# ============================================================
# 4. 분석 보조 함수
# ============================================================
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
        
        if last_diff > 0 and prev_diff <= 0: return "⚡️첫 상승전환"
        elif last_diff < 0 and prev_diff >= 0: return "💧첫 하락전환"
        elif last_diff > 0:
            streak = sum(1 for _ in filter(lambda x: x > 0, reversed(diffs)))
            return f"🔥{streak}일연속상승"
        elif last_diff < 0:
            streak = sum(1 for _ in filter(lambda x: x < 0, reversed(diffs)))
            return f"❄️{streak}일연속하락"
        else: return "➖보합"
    except Exception:
        return ""

def get_investor_trend(code, latest_df_date):
    try:
        code_str = str(code).strip().zfill(6)
        url = f"https://m.stock.naver.com/api/stock/{code_str}/trend?pageSize=10&page=1"
        res = session.get(url, timeout=2.0)
        if res.status_code != 200:
            return "⚪️ 수급 확인불가", 0
            
        data = res.json()
        trends = data.get('message', []) if isinstance(data, dict) and 'message' in data else data
        if not trends:
            return "⚪️ 수급 확인불가", 0

        target_idx = 0
        target_date_str = latest_df_date.strftime("%Y%m%d")
        for idx, item in enumerate(trends):
            b_date = str(item.get('bizdate', '')).replace("-", "")
            if b_date == target_date_str:
                target_idx = idx
                break

        slice_5d = trends[target_idx:target_idx+5]
        inst_5d = sum(int(str(it.get('institutionPureBuyQuant', '0')).replace(',', '')) for it in slice_5d)
        frgn_5d = sum(int(str(it.get('foreignerPureBuyQuant', '0')).replace(',', '')) for it in slice_5d)
        
        indiv_list = []
        for it in slice_5d:
            if 'individualPureBuyQuant' in it:
                indiv_list.append(int(str(it.get('individualPureBuyQuant', '0')).replace(',', '')))
            else:
                f_q = int(str(it.get('foreignerPureBuyQuant', '0')).replace(',', ''))
                i_q = int(str(it.get('institutionPureBuyQuant', '0')).replace(',', ''))
                indiv_list.append(-(f_q + i_q))
        
        indiv_5d = sum(indiv_list)
        smart_money_5d = frgn_5d + inst_5d

        if indiv_5d < 0 and smart_money_5d > 0:
            tag = f"💎 [스마트머니 장악] 개인 5일 누적 매도({indiv_5d:,}주) | 외인·기관 흡수(+{smart_money_5d:,}주)"
            score = 10
        elif indiv_5d < 0:
            tag = f"💎 [개미 이탈] 개인 5일 누적 매도({indiv_5d:,}주) | 잠재매물 감소"
            score = 6
        elif inst_5d < 0 and frgn_5d > 0:
            tag = f"⚠️ [수급 공방/기관출회] 외인(+{frgn_5d:,}) vs 기관(-{abs(inst_5d):,}) | 핑퐁주의"
            score = -5
        elif indiv_5d > 0:
            tag = f"⚠️ [개미 유입] 개인 5일 누적 매수(+{indiv_5d:,}주) | 매물 저항 부담"
            score = -10
        else:
            tag = "⚪️ 수급 중립 공방"
            score = 0

        return tag, score
    except Exception:
        return "⚪️ 수급 확인불가", 0

def analyze_stock(code):
    try:
        code = str(code).strip().zfill(6)
        df_d = get_daily_candle(code, count=360)
        if df_d is None or len(df_d) < 180:
            return None

        today_vol = float(df_d['Volume'].iloc[-1])
        if today_vol < 100_000:
            return None

        df_w = df_d.resample('W-FRI').agg({
            'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
        }).dropna()

        if len(df_w) < 35:
            return None

        df_w['SMA5'] = df_w['Close'].rolling(5).mean()
        df_w['SMA30'] = df_w['Close'].rolling(30).mean()

        current_price = int(df_d['Close'].iloc[-1])
        prev_close = int(df_d['Close'].iloc[-2])
        latest_date = df_d.index[-1]

        sma30_series = df_w['SMA30'].dropna()
        if len(sma30_series) < 8:
            return None

        # 와인스타인: 30주선 우상향/바닥 다지기 필터
        is_sma30_uptrend = (sma30_series.iloc[-1] >= sma30_series.iloc[-3] * 0.998)
        if not is_sma30_uptrend:
            return None

        sma30 = sma30_series.iloc[-1]
        disp = (current_price / sma30) * 100.0
        if not (97.0 <= disp <= 115.0):
            return None

        # ============================================================
        # 일봉 기준 30일 이평선 이격도 필터 (-4% ~ +6%)
        # ============================================================
        sma30_daily = df_d['Close'].rolling(30).mean().iloc[-1]
        if pd.isna(sma30_daily):
            return None
        
        disp_daily_30 = (current_price / sma30_daily) * 100.0
        if not (96.0 <= disp_daily_30 <= 106.0):  # -4% (96.0) ~ +6% (106.0)
            return None

        sma5_val = df_w['SMA5'].iloc[-1]
        is_above_w5 = (current_price >= sma5_val)
        is_near_w5 = (not is_above_w5) and (current_price >= sma5_val * 0.975)

        vol_sma50 = df_d['Volume'].rolling(50).mean().iloc[-1]
        vol_ratio_sma50 = (today_vol / vol_sma50 * 100.0) if vol_sma50 > 0 else 100.0
        vol_50_under = (today_vol < vol_sma50)

        vol_1 = float(df_d['Volume'].iloc[-2])
        vol_ratio_prev = (today_vol / vol_1 * 100.0) if vol_1 > 0 else 100.0

        recent_20 = df_d.iloc[-20:]
        recent_10 = df_d.iloc[-10:]
        recent_5 = df_d.iloc[-5:]

        range_20 = (recent_20['High'].max() - recent_20['Low'].min()) / current_price * 100.0
        range_10 = (recent_10['High'].max() - recent_10['Low'].min()) / current_price * 100.0
        range_5 = (recent_5['High'].max() - recent_5['Low'].min()) / current_price * 100.0

        high_20d_idx = recent_20['High'].values.argmax()
        is_flag_shape = (high_20d_idx < 15) and (recent_5['High'].max() < recent_20['High'].max())
        is_contracting = (range_20 >= range_10) and (range_10 >= range_5)

        pattern_score = 4
        if is_above_w5 and (is_flag_shape or is_contracting) and range_5 <= 8.0 and vol_50_under:
            pattern_tag = f"🚩 주봉5주선 위 완벽VCP (진폭 {range_5:.1f}% | 핸들수축)"
        elif (is_flag_shape or is_contracting) and range_5 <= 10.0:
            if is_above_w5:
                pattern_tag = f"⚡️ 주봉5주선 지지 깃발형 (5일 진폭 {range_5:.1f}%)"
            else:
                pattern_tag = f"🛡 30주선 지지/첫반등 (5일 진폭 {range_5:.1f}% | 5주선 매물저항)"
        elif range_5 <= 7.0:
            pattern_tag = f"🌀 단기 초미세 수렴 (5일 진폭 {range_5:.1f}%)"
        else:
            pattern_tag = f"30주선 지지 채널 (5일 진폭 {range_5:.1f}%)"

        pivot_high = int(recent_10['High'].max())
        dist_to_pivot = ((pivot_high - current_price) / current_price) * 100.0

        if is_above_w5 and vol_ratio_sma50 <= 50.0 and range_5 <= 8.0 and dist_to_pivot <= 3.0:
            buy_trigger_str = f"🚨 [슈팅직전 셋업완료] 피벗 {pivot_high:,}원 돌파 시 즉시발사 (선취매 유효구간)"
        elif is_above_w5 and dist_to_pivot <= 4.0 and vol_50_under:
            buy_trigger_str = f"🎯 돌파매수 대기 (10일 피벗 {pivot_high:,}원 돌파 시)"
        elif is_above_w5:
            buy_trigger_str = f"⏳ 베이스 수축 진행 (피벗 {pivot_high:,}원 | 이격 +{dist_to_pivot:.1f}%)"
        elif is_near_w5:
            buy_trigger_str = f"⚡️ 5주선 돌파 임박 (머리 위 5주선: {int(round(sma5_val)):,}원 | 이격 {((sma5_val-current_price)/current_price*100):.1f}%)"
        else:
            buy_trigger_str = f"⛔️ 5주선 매물저항 구간 (머리 위 5주선: {int(round(sma5_val)):,}원)"

        m_rs_long, m_rs_short = 0.0, 0.0
        if kospi_close is not None:
            df_rs = pd.DataFrame({'stock': df_d['Close'], 'kospi': kospi_close}).dropna()
            if len(df_rs) >= 120:
                rs_line = df_rs['stock'] / df_rs['kospi']
                w_long = min(len(df_rs), 250)
                rs_sma_long = rs_line.rolling(w_long, min_periods=60).mean()
                m_rs_long = ((rs_line.iloc[-1] / rs_sma_long.iloc[-1]) - 1.0) * 100.0

                rs_sma_short = rs_line.rolling(60, min_periods=30).mean()
                m_rs_short = ((rs_line.iloc[-1] / rs_sma_short.iloc[-1]) - 1.0) * 100.0

        if m_rs_long >= 20.0 and m_rs_short > 0:
            rs_tag = f"👑 장단기 듀얼 슈퍼스톡 (RS 장기+{m_rs_long:.1f} / 단기+{m_rs_short:.1f})"
        elif m_rs_long > 0 and m_rs_short > 0:
            rs_tag = f"🔥 장단기 동반 우상향 (RS 장기+{m_rs_long:.1f} / 단기+{m_rs_short:.1f})"
        elif m_rs_long > 0:
            rs_tag = f"🟢 장기 추세 우위 (RS 장기+{m_rs_long:.1f} / 단기{m_rs_short:+.1f})"
        else:
            rs_tag = f"⚪️ 지수 동행/하회 (RS 장기{m_rs_long:+.1f} / 단기{m_rs_short:+.1f})"

        investor_tag, investor_score = get_investor_trend(code, latest_date)

        chg_pct = ((current_price - prev_close) / prev_close) * 100.0
        streak_tag = get_streak_info(df_d)
        chg_parts = [f"🔺+{chg_pct:.2f}%" if chg_pct > 0 else (f"🔻{chg_pct:.2f}%" if chg_pct < 0 else "➖ 0.00%")]
        if streak_tag: chg_parts.append(streak_tag)
        if vol_50_under: chg_parts.append("📉50일거래하회")
        chg_str = " ".join(chg_parts)

        name = code_to_name.get(code, code)
        name = name.replace("[", "").replace("]", "").replace("*", "")

        if 99.0 <= disp <= 103.0:
            tag = "30주선 초밀착"
        elif disp < 99.0:
            tag = "일시 언더슈팅"
        else:
            tag = "30주선 위 지지"

        return {
            "code": code,
            "name": name,
            "price": current_price,
            "chg_str": chg_str,
            "sma30": int(round(sma30)),
            "sma30_daily": int(round(sma30_daily)),
            "sma5_w": int(round(sma5_val)),
            "is_above_w5": is_above_w5,
            "is_near_w5": is_near_w5,
            "disp": round(disp, 1),
            "disp_daily_30": round(disp_daily_30, 1),
            "vol_today": int(today_vol),
            "vol_ratio_prev": round(vol_ratio_prev, 1),
            "vol_ratio_sma50": round(vol_ratio_sma50, 1),
            "vol_50_under": vol_50_under,
            "tag": tag,
            "pattern_tag": pattern_tag,
            "buy_trigger_str": buy_trigger_str,
            "rs": rs_tag,
            "m_rs_long": m_rs_long,
            "m_rs_short": m_rs_short,
            "investor": investor_tag
        }
    except Exception:
        return None

# ============================================================
# 5. 전종목 병렬 고속 스캔 실행
# ============================================================
results = []
log(f"[*] 총 {len(target_tickers)}개 순수 상장주 정밀 스캔 시작...")

with ThreadPoolExecutor(max_workers=25) as executor:
    future_to_code = {executor.submit(analyze_stock, code): code for code in target_tickers}
    for future in as_completed(future_to_code):
        res = future.result()
        if res:
            results.append(res)

log(f"[*] 전종목 분석 완료! 최종 조건 통과 종목: {len(results)}개")

# ============================================================
# 6. 채점 및 텔레그램 리포트 생성 (100점 만점 균등 배점)
# ============================================================
msg = f"📊 [{today_str} 와인스타인 순수 개별주 VCP 리포트]\n"
msg += f"• 조건 충족 종목수: 총 {len(results)}개\n\n"

if top_themes:
    msg += "🔥 [당일 네이버 시장 주도 테마 TOP 10]\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    for idx, (t_name, t_rate) in enumerate(top_themes):
        msg += f"{idx+1}. {t_name} (+{t_rate:.2f}%)\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n\n"

if results:
    df_res = pd.DataFrame(results)

    final_results = []
    for _, r in df_res.iterrows():
        score = 0
        
        # 1. 상대강도 추세 (최대 25점)
        if r['m_rs_long'] >= 20.0 and r['m_rs_short'] > 0: 
            score += 25
        elif r['m_rs_long'] > 0 and r['m_rs_short'] > 0: 
            score += 20
        elif r['m_rs_long'] > 0: 
            score += 10

        # 2. 주봉 30주선 이격도 (최대 25점)
        if 99.0 <= r['disp'] <= 103.0: 
            score += 25
        elif 103.0 < r['disp'] <= 107.0: 
            score += 15
        elif 98.0 <= r['disp'] < 99.0: 
            score += 10
        else: 
            score += 5

        # 3. 일봉 30일선 이격도 (최대 25점)
        if 99.0 <= r['disp_daily_30'] <= 102.0: 
            score += 25
        elif (102.0 < r['disp_daily_30'] <= 104.0) or (97.0 <= r['disp_daily_30'] < 99.0): 
            score += 15
        else: 
            score += 5

        # 4. 거래량 수축도 (최대 25점)
        if r['vol_ratio_sma50'] <= 45.0: 
            score += 25
        elif r['vol_ratio_sma50'] <= 75.0: 
            score += 15
        elif r['vol_50_under']: 
            score += 5

        r_dict = dict(r)
        r_dict['score'] = score
        final_results.append(r_dict)

    df_res = pd.DataFrame(final_results)
    # 총점(score) 1순위, 장기 상대강도(m_rs_long) 2순위로 내림차순 정렬
    df_sorted = df_res.sort_values(by=["score", "m_rs_long"], ascending=[False, False]).reset_index(drop=True)
    
    # 상위 30개 출력 적용
    df_top = df_sorted.head(30)

    msg += f"📋 [포착 종목 셋업 랭킹 TOP {len(df_top)}] (전체 {len(df_sorted)}개 중)\n"
    for idx, r in df_top.iterrows():
        rank = idx + 1
        bar = make_vol_bar(r['vol_ratio_sma50'])
        w5_mark = "🟢5주선위(상방열림)" if r['is_above_w5'] else ("⚡️5주선돌파임박" if r['is_near_w5'] else "🟡5주선아래(매물저항)")
        
        # 100점 만점 기준 점수 표시
        msg += f"{rank}. {r['name']} ({r['price']:,}원 | {r['chg_str']}) [{r['score']}점/100점 | {r['tag']} | {w5_mark}]\n"
        msg += f"   - 매수타점: {r['buy_trigger_str']}\n"
        msg += f"   - 패턴: {r['pattern_tag']}\n"
        msg += f"   - 수급: {r['investor']}\n"
        msg += f"   - 상대강도: {r['rs']}\n"
        msg += f"   - 30주선: {r['sma30']:,}원 (이격: {r['disp']}%) | 일봉30일선: {r['sma30_daily']:,}원 (이격: {r['disp_daily_30']}%)\n"
        msg += f"   - 50일거래비: {r['vol_ratio_sma50']}% [{bar}] | 일봉거래: {r['vol_today']:,}주 (전일비: {r['vol_ratio_prev']}%)\n\n"
else:
    msg += "오늘 조건을 충족하는 종목이 없습니다."

send_telegram(msg)
log("[*] 리포트 발송 완료")
