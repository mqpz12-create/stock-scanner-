import os
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import FinanceDataReader as fdr
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

log(f"[*] {today_str} 와인스타인 원전 기준 VCP & 매수 타점 스캐너 구동...")

# 1. 네이버 당일 주도 테마 TOP 10 수집
top_themes = []
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Referer': 'https://finance.naver.com/'
}

try:
    url = "https://finance.naver.com/sise/theme.naver?&page=1"
    res = requests.get(url, headers=headers, timeout=6)
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
                        t_rate = float(t_rate_str)
                        top_themes.append((t_name, t_rate))
                    except ValueError:
                        pass
        top_themes.sort(key=lambda x: x[1], reverse=True)
        top_themes = top_themes[:10]
except Exception as e:
    log(f"[!] 테마 수집 실패: {e}")

try:
    df_kospi = fdr.DataReader('KS11', start_str)
    kospi_close = df_kospi['Close']
except Exception:
    kospi_close = None

# 키움 조건 D: 시가총액 1,000억 원 이상
df_krx = fdr.StockListing('KRX')
if 'Marcap' in df_krx.columns:
    df_krx = df_krx[df_krx['Marcap'] >= 1000_0000_0000]

target_tickers = list(df_krx['Code'])
must_have = ["005090", "065060", "094480", "327260", "010170", "028050", "319660", "080220", "005930", "000660", "402340"]
target_tickers = list(set(target_tickers + must_have))

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
        h = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://m.stock.naver.com/'}
        res = requests.get(url, headers=h, timeout=2.5)
        if res.status_code != 200:
            return "⚪️ 수급 공방"
            
        data = res.json()
        trends = data.get('message', []) if isinstance(data, dict) and 'message' in data else data
        if not trends:
            return "⚪️ 수급 공방"

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
        elif today_frgn < 0 and frgn_5d > 0:
            tag = f"{date_prefix}⚠️ 외인 당일매도({today_frgn:,}) | 5일누적(+{frgn_5d:,})"
        elif today_frgn > 0 and today_inst <= 0:
            tag = f"{date_prefix}💎 외인 당일매집(+{today_frgn:,}) | 5일누적({frgn_5d:,})"
        elif today_inst > 0 and today_frgn <= 0:
            tag = f"{date_prefix}⭐️ 기관 당일매집(+{today_inst:,}) | 5일누적({inst_5d:,})"
        elif today_inst < 0 and today_frgn < 0:
            tag = f"{date_prefix}⛔️ 외인·기관 동반매도 (외인{today_frgn:,} / 기관{today_inst:,})"
        else:
            tag = f"{date_prefix}⚪️ 수급 공방 (외인{today_frgn:,} / 기관{today_inst:,})"

        return tag
    except Exception:
        return "⚪️ 수급 공방"

def analyze_stock(code):
    try:
        df_d = fdr.DataReader(code, start_str)
        if len(df_d) < 180 or kospi_close is None:
            return None

        today_vol = float(df_d['Volume'].iloc[-1])
        if today_vol < 200_000:
            return None

        # 키움 주봉 변환 (W-FRI)
        df_w = df_d.resample('W-FRI').agg({
            'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
        }).dropna()

        if len(df_w) < 35:
            return None

        # 주봉 5주선 및 30주선
        df_w['SMA5'] = df_w['Close'].rolling(5).mean()
        df_w['SMA30'] = df_w['Close'].rolling(30).mean()

        current_price = int(df_d['Close'].iloc[-1])
        prev_close = int(df_d['Close'].iloc[-2])
        latest_date = df_d.index[-1]

        # 1. 30주선 5주 연속 우상향 검증
        sma30_series = df_w['SMA30'].dropna()
        if len(sma30_series) < 6:
            return None
        
        sma30_diffs = [sma30_series.iloc[-i] - sma30_series.iloc[-i-1] for i in range(1, 6)]
        if not all(d > 0 for d in sma30_diffs):
            return None

        sma30 = sma30_series.iloc[-1]

        # 2. 30주선 이격도 (-2% ~ +10%)
        disp = (current_price / sma30) * 100.0
        if not (98.0 <= disp <= 110.0):
            return None

        # 3. 주봉 5주선 매물벽 검증 (머리 위 저항선 배제)
        sma5_val = df_w['SMA5'].iloc[-1]
        is_above_w5 = (current_price >= sma5_val)

        # 4. 거래량 50일 이평 하회
        vol_sma50 = df_d['Volume'].rolling(50).mean().iloc[-1]
        vol_ratio_sma50 = (today_vol / vol_sma50 * 100.0) if vol_sma50 > 0 else 100.0
        vol_50_under = (today_vol < vol_sma50)

        vol_1 = float(df_d['Volume'].iloc[-2])
        vol_ratio_prev = (today_vol / vol_1 * 100.0) if vol_1 > 0 else 100.0

        # 5. VCP 진폭 수축 분석
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
        if is_above_w5 and (is_flag_shape or is_contracting) and range_5 <= 7.0 and vol_50_under:
            pattern_tag = f"🚩 주봉5주선 위 완벽VCP (진폭 {range_5:.1f}% | 핸들수축)"
            pattern_score = 15
        elif (is_flag_shape or is_contracting) and range_5 <= 8.5:
            if is_above_w5:
                pattern_tag = f"⚡️ 주봉5주선 지지 깃발형 (5일 진폭 {range_5:.1f}%)"
                pattern_score = 12
            else:
                pattern_tag = f"🛡 30주선 지지/첫반등 (5일 진폭 {range_5:.1f}% | 5주선 매물저항)"
                pattern_score = 6
        elif range_5 <= 6.0:
            pattern_tag = f"🌀 단기 초미세 수렴 (5일 진폭 {range_5:.1f}%)"
            pattern_score = 8
        else:
            pattern_tag = f"30주선 지지 채널 (5일 진폭 {range_5:.1f}%)"
            pattern_score = 4

        # ============================================================
        # 6. 피벗 돌파 매수 타점 계산 (5주선은 '상방 개방' 여부로만 검증)
        # ============================================================
        pivot_high = int(recent_10['High'].max()) # 최근 10일 피벗 고점
        dist_to_pivot = ((pivot_high - current_price) / current_price) * 100.0

        if is_above_w5 and dist_to_pivot <= 3.0 and vol_50_under:
            buy_trigger_str = f"🎯 돌파매수 대기 (10일 피벗 {pivot_high:,}원 돌파 시)"
            trigger_score = 10
        elif is_above_w5:
            buy_trigger_str = f"⏳ 베이스 수축 진행 (피벗 {pivot_high:,}원 | 이격 +{dist_to_pivot:.1f}%)"
            trigger_score = 5
        else:
            buy_trigger_str = f"⛔️ 5주선 매물저항 구간 (머리 위 5주선: {int(round(sma5_val)):,}원)"
            trigger_score = 0

        # 장기(250일) + 단기(60일) 듀얼 Mansfield RS
        df_rs = pd.DataFrame({'stock': df_d['Close'], 'kospi': kospi_close}).dropna()
        if len(df_rs) >= 120:
            rs_line = df_rs['stock'] / df_rs['kospi']
            
            w_long = min(len(df_rs), 250)
            rs_sma_long = rs_line.rolling(w_long, min_periods=60).mean()
            m_rs_long = ((rs_line.iloc[-1] / rs_sma_long.iloc[-1]) - 1.0) * 100.0

            rs_sma_short = rs_line.rolling(60, min_periods=30).mean()
            m_rs_short = ((rs_line.iloc[-1] / rs_sma_short.iloc[-1]) - 1.0) * 100.0
        else:
            m_rs_long, m_rs_short = 0.0, 0.0

        if m_rs_long >= 20.0 and m_rs_short > 0:
            rs_tag = f"👑 장단기 듀얼 슈퍼스톡 (RS 장기+{m_rs_long:.1f} / 단기+{m_rs_short:.1f})"
        elif m_rs_long > 0 and m_rs_short > 0:
            rs_tag = f"🔥 장단기 동반 우상향 (RS 장기+{m_rs_long:.1f} / 단기+{m_rs_short:.1f})"
        elif m_rs_long > 0:
            rs_tag = f"🟢 장기 추세 우위 (RS 장기+{m_rs_long:.1f} / 단기{m_rs_short:+.1f})"
        else:
            rs_tag = f"⚪️ 지수 하회 (RS 장기{m_rs_long:.1f} / 단기{m_rs_short:+.1f})"

        investor_tag = get_investor_trend(code, latest_date)

        chg_pct = ((current_price - prev_close) / prev_close) * 100.0
        streak_tag = get_streak_info(df_d)
        chg_parts = [f"🔺+{chg_pct:.2f}%" if chg_pct > 0 else (f"🔻{chg_pct:.2f}%" if chg_pct < 0 else "➖ 0.00%")]
        if streak_tag: chg_parts.append(streak_tag)
        if vol_50_under: chg_parts.append("📉50일거래하회")
        chg_str = " ".join(chg_parts)

        name_match = df_krx[df_krx['Code'] == code]
        name = name_match['Name'].iloc[0] if not name_match.empty else code
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
            "sma5_w": int(round(sma5_val)),
            "is_above_w5": is_above_w5,
            "disp": round(disp, 1),
            "vol_today": int(today_vol),
            "vol_ratio_prev": round(vol_ratio_prev, 1),
            "vol_ratio_sma50": round(vol_ratio_sma50, 1),
            "vol_50_under": vol_50_under,
            "tag": tag,
            "pattern_tag": pattern_tag,
            "pattern_score": pattern_score,
            "buy_trigger_str": buy_trigger_str,
            "trigger_score": trigger_score,
            "rs": rs_tag,
            "m_rs_long": m_rs_long,
            "m_rs_short": m_rs_short,
            "investor": investor_tag
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

# 메시지 조립
msg = f"📊 [{today_str} 와인스타인 원전 100점 VCP 리포트]\n"
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
        
        # 1. 듀얼 Mansfield RS (30점 만점)
        if r['m_rs_long'] > 0 and r['m_rs_short'] > 0: score += 30
        elif r['m_rs_long'] > 0: score += 15

        # 2. 머리 위 저항 배제 / 주봉 5주선 위 안착 (20점 만점)
        if r['is_above_w5']: score += 20
        else: score += 5

        # 3. 50일 거래량 마름 (20점 만점)
        if r['vol_ratio_sma50'] <= 50.0: score += 20
        elif r['vol_50_under']: score += 12

        # 4. 차트 패턴 수축 (10점 만점)
        score += int(round(r['pattern_score'] * 0.67))

        # 5. 피벗 돌파 준비 상태 (10점 만점)
        score += r['trigger_score']

        # 6. 30주선 이격 밀착도 (10점 만점)
        if 99.0 <= r['disp'] <= 103.0: score += 10
        elif 103.0 < r['disp'] <= 107.0: score += 7
        else: score += 4

        r_dict = dict(r)
        r_dict['score'] = score
        final_results.append(r_dict)

    df_res = pd.DataFrame(final_results)
    df_sorted = df_res.sort_values(by=["score", "m_rs_long"], ascending=[False, False]).reset_index(drop=True)

    msg += f"📋 [포착 종목 셋업 랭킹] (총 {len(df_sorted)}개)\n"
    for idx, r in df_sorted.iterrows():
        rank = idx + 1
        bar = make_vol_bar(r['vol_ratio_sma50'])
        w5_mark = "🟢5주선위(상방열림)" if r['is_above_w5'] else "🟡5주선아래(매물저항)"
        
        msg += f"{rank}. {r['name']} ({r['price']:,}원 | {r['chg_str']}) [{r['score']}점 | {r['tag']} | {w5_mark}]\n"
        msg += f"   - 매수타점: {r['buy_trigger_str']}\n"
        msg += f"   - 패턴: {r['pattern_tag']}\n"
        msg += f"   - 수급: {r['investor']}\n"
        msg += f"   - 상대강도: {r['rs']}\n"
        msg += f"   - 30주선: {r['sma30']:,}원 (이격: {r['disp']}%) | 주봉5주선: {r['sma5_w']:,}원\n"
        msg += f"   - 50일거래비: {r['vol_ratio_sma50']}% [{bar}] | 일봉거래: {r['vol_today']:,}주 (전일비: {r['vol_ratio_prev']}%)\n"
else:
    msg += "오늘 조건을 충족하는 종목이 없습니다."

send_telegram(msg)
log("[*] 리포트 발송 완료")
