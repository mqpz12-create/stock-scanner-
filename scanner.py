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

log(f"[*] {today_str} 순수 기술적 VCP & 30주선 정밀 스캐너 구동...")

try:
    df_kospi = fdr.DataReader('KS11', start_str)
    kospi_close = df_kospi['Close']
except Exception:
    kospi_close = None

# 키움 조건식 D: 시가총액 1,000억 원 이상
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
            score = 10
        elif today_frgn < 0 and frgn_5d > 0:
            tag = f"{date_prefix}⚠️ 외인 당일매도({today_frgn:,}) | 5일누적(+{frgn_5d:,})"
            score = 3
        elif today_frgn > 0 and today_inst <= 0:
            tag = f"{date_prefix}💎 외인 당일매집(+{today_frgn:,}) | 5일누적({frgn_5d:,})"
            score = 8
        elif today_inst > 0 and today_frgn <= 0:
            tag = f"{date_prefix}⭐️ 기관 당일매집(+{today_inst:,}) | 5일누적({inst_5d:,})"
            score = 8
        elif today_inst < 0 and today_frgn < 0:
            tag = f"{date_prefix}⛔️ 외인·기관 동반매도 (외인{today_frgn:,} / 기관{today_inst:,})"
            score = 0
        else:
            tag = f"{date_prefix}⚪️ 수급 공방 (외인{today_frgn:,} / 기관{today_inst:,})"
            score = 5

        return tag, score
    except Exception:
        return "⚪️ 수급 공방 (중립)", 5

def analyze_stock(code):
    try:
        df_d = fdr.DataReader(code, start_str)
        if len(df_d) < 180 or kospi_close is None:
            return None

        # 키움 조건식 I: 당일 거래량 20만 주 이상
        today_vol = float(df_d['Volume'].iloc[-1])
        if today_vol < 200_000:
            return None

        # 주봉 변환 (키움 W-FRI 일치)
        df_w = df_d.resample('W-FRI').agg({
            'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
        }).dropna()

        if len(df_w) < 35:
            return None

        df_w['SMA30'] = df_w['Close'].rolling(30).mean()
        current_price = int(df_d['Close'].iloc[-1])
        prev_close = int(df_d['Close'].iloc[-2])
        latest_date = df_d.index[-1]

        # 키움 조건식 B: 30주선 상승 추세유지 5회 이상
        sma30_series = df_w['SMA30'].dropna()
        if len(sma30_series) < 6:
            return None
        
        sma30_diffs = [sma30_series.iloc[-i] - sma30_series.iloc[-i-1] for i in range(1, 6)]
        if not all(d >= 0 for d in sma30_diffs):
            if sma30_series.iloc[-1] < sma30_series.iloc[-5]:
                return None

        sma30 = sma30_series.iloc[-1]

        # 키움 조건식 E: 30주선 대비 종가 등락률 -2% ~ +10% (이격도 98.0% ~ 110.0%)
        disp = (current_price / sma30) * 100.0
        if not (98.0 <= disp <= 110.0):
            return None

        # 1. 50일 거래량 이동평균선(SMA 50) 하회 계산
        vol_sma50 = df_d['Volume'].rolling(50).mean().iloc[-1]
        vol_ratio_sma50 = (today_vol / vol_sma50 * 100.0) if vol_sma50 > 0 else 100.0
        vol_50_under = (today_vol < vol_sma50)

        vol_1 = float(df_d['Volume'].iloc[-2])
        vol_ratio_prev = (today_vol / vol_1 * 100.0) if vol_1 > 0 else 100.0

        # 2. 마크 미너비니 VCP(상하 변동폭 수렴) 3단계 검증
        recent_20 = df_d.iloc[-20:]
        recent_10 = df_d.iloc[-10:]
        recent_5 = df_d.iloc[-5:]

        range_20 = (recent_20['High'].max() - recent_20['Low'].min()) / current_price * 100.0
        range_10 = (recent_10['High'].max() - recent_10['Low'].min()) / current_price * 100.0
        range_5 = (recent_5['High'].max() - recent_5['Low'].min()) / current_price * 100.0

        vcp_tightening = (range_20 >= range_10) and (range_10 >= range_5)
        
        if vcp_tightening and range_5 <= 6.5 and vol_50_under:
            vcp_tag = f"💎 완벽 VCP 수렴 (20일 {range_20:.1f}%→5일 {range_5:.1f}% | 50일이평 하회)"
            vcp_score = 25
        elif vcp_tightening and range_5 <= 8.5:
            vcp_tag = f"⚡️ VCP 압축 진행 (20일 {range_20:.1f}%→5일 {range_5:.1f}%)"
            vcp_score = 18
        elif range_5 <= 6.0:
            vcp_tag = f"🌀 단기 초미세 수렴 (5일 진폭 {range_5:.1f}%)"
            vcp_score = 12
        else:
            vcp_tag = f"지지선 수렴 중 (5일 진폭 {range_5:.1f}%)"
            vcp_score = 5

        # 등락률 및 모멘텀 시그널
        chg_pct = ((current_price - prev_close) / prev_close) * 100.0
        streak_tag = get_streak_info(df_d)
        
        chg_parts = [f"🔺+{chg_pct:.2f}%" if chg_pct > 0 else (f"🔻{chg_pct:.2f}%" if chg_pct < 0 else "➖ 0.00%")]
        if streak_tag: chg_parts.append(streak_tag)
        if vol_50_under: chg_parts.append("📉50일거래하회")
        chg_str = " ".join(chg_parts)

        # Mansfield Relative Strength
        df_rs = pd.DataFrame({'stock': df_d['Close'], 'kospi': kospi_close}).dropna()
        if len(df_rs) >= 120:
            rs_line = df_rs['stock'] / df_rs['kospi']
            window = min(len(df_rs), 250)
            rs_sma = rs_line.rolling(window, min_periods=60).mean()
            m_rs = ((rs_line.iloc[-1] / rs_sma.iloc[-1]) - 1.0) * 100.0
        else:
            m_rs = 0.0

        if m_rs >= 40.0:
            rs_tag = f"👑 최상위 슈퍼스톡 (Mansfield +{m_rs:.1f})"
        elif m_rs >= 20.0:
            rs_tag = f"🔥 시장 압도적 주도주 (Mansfield +{m_rs:.1f})"
        elif m_rs >= 5.0:
            rs_tag = f"🚀 지수 대비 강세 돌파 (Mansfield +{m_rs:.1f})"
        elif m_rs >= 0.0:
            rs_tag = f"🟢 지수 대비 우위 (Mansfield +{m_rs:.1f})"
        else:
            rs_tag = f"⚪️ 지수 하회 (Mansfield {m_rs:.1f})"

        investor_tag, investor_score = get_investor_trend(code, latest_date)

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
            "disp": round(disp, 1),
            "vol_today": int(today_vol),
            "vol_ratio_prev": round(vol_ratio_prev, 1),
            "vol_ratio_sma50": round(vol_ratio_sma50, 1),
            "vol_50_under": vol_50_under,
            "tag": tag,
            "vcp_tag": vcp_tag,
            "vcp_score": vcp_score,
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

msg = f"📊 [{today_str} 스탠 와인스타인 & VCP 정밀 리포트]\n"
msg += f"• 조건 충족 종목수: 총 {len(results)}개\n"

if results:
    df_res = pd.DataFrame(results)

    final_results = []
    for _, r in df_res.iterrows():
        score = 0
        # 1. Mansfield RS 점수 (25점)
        if r['m_rs'] >= 40.0: score += 25
        elif r['m_rs'] >= 20.0: score += 20
        elif r['m_rs'] >= 5.0: score += 15
        elif r['m_rs'] >= 0.0: score += 10
        else: score += 0

        # 2. 30주선 지지 완성도 (25점)
        if 99.0 <= r['disp'] <= 103.0: score += 25
        elif 98.0 <= r['disp'] <= 106.0: score += 18
        else: score += 10

        # 3. 거래량 50일 이평 하회 & 마름 (15점)
        if r['vol_ratio_sma50'] <= 50.0: score += 15
        elif r['vol_50_under']: score += 12
        elif r['vol_ratio_sma50'] <= 100.0: score += 8
        else: score += 3

        # 4. VCP 변동성 축소 점수 (25점)
        score += r['vcp_score']

        # 5. 수급 가산점 (10점)
        score += r['investor_score']

        r_dict = dict(r)
        r_dict['score'] = score
        final_results.append(r_dict)

    df_res = pd.DataFrame(final_results)

    # 1. 종합 1위 (최우선 관심주)
    top_leader = df_res.sort_values(by=['score', 'm_rs'], ascending=[False, False]).iloc[0]

    # 2. Mansfield RS 1위 (초강세주)
    df_indie = df_res[df_res['code'] != top_leader['code']]
    rs_alpha = None
    if not df_indie.empty:
        rs_alpha = df_indie.sort_values(by=['m_rs', 'score'], ascending=[False, False]).iloc[0]

    msg += "\n🔥 [TODAY'S HIGHLIGHT : 최우선 관심주]\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    bar_t = make_vol_bar(top_leader['vol_ratio_sma50'])
    msg += f"🏆 종합 1위 최우선 압축주\n"
    msg += f"▶ {top_leader['name']} ({top_leader['price']:,}원 | {top_leader['chg_str']}) [와인스타인+VCP {top_leader['score']}점]\n"
    msg += f"   - 수급주체: {top_leader['investor']}\n"
    msg += f"   - 변동성패턴: {top_leader['vcp_tag']}\n"
    msg += f"   - 상대강도: {top_leader['rs']}\n"
    msg += f"   - 30주선: {top_leader['sma30']:,}원 (이격: {top_leader['disp']}%)\n"
    msg += f"   - 거래량: 50일이평비 {top_leader['vol_ratio_sma50']}% [{bar_t}] (전일비: {top_leader['vol_ratio_prev']}%)\n\n"

    if rs_alpha is not None:
        bar_i = make_vol_bar(rs_alpha['vol_ratio_sma50'])
        msg += f"⚡️ 시장 최고 상대강도주 (Mansfield RS 1위)\n"
        msg += f"▶ {rs_alpha['name']} ({rs_alpha['price']:,}원 | {rs_alpha['chg_str']}) [와인스타인+VCP {rs_alpha['score']}점]\n"
        msg += f"   - 수급주체: {rs_alpha['investor']}\n"
        msg += f"   - 변동성패턴: {rs_alpha['vcp_tag']}\n"
        msg += f"   - 상대강도: {rs_alpha['rs']}\n"
        msg += f"   - 30주선: {rs_alpha['sma30']:,}원 (이격: {rs_alpha['disp']}%)\n"
        msg += f"   - 거래량: 50일이평비 {rs_alpha['vol_ratio_sma50']}% [{bar_i}]\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"

    # 전체 포착 종목 리스트 (점수 순 정렬)
    df_sorted = df_res.sort_values(by=["score", "m_rs"], ascending=[False, False])
    msg += f"\n📋 [포착 종목 전체 랭킹] (총 {len(df_sorted)}개)\n"
    for _, r in df_sorted.iterrows():
        bar = make_vol_bar(r['vol_ratio_sma50'])
        msg += f"• {r['name']} ({r['price']:,}원 | {r['chg_str']}) [{r['score']}점 | {r['tag']}]\n"
        msg += f"   - 패턴: {r['vcp_tag']}\n"
        msg += f"   - 수급: {r['investor']}\n"
        msg += f"   - 상대강도: {r['rs']}\n"
        msg += f"   - 30주선: {r['sma30']:,}원 (이격: {r['disp']}%) | 50일거래비: {r['vol_ratio_sma50']}% [{bar}]\n"
        msg += f"   - 일봉거래: {r['vol_today']:,}주 (전일비: {r['vol_ratio_prev']}%)\n"
else:
    msg += "오늘 VCP 조건을 충족하는 종목이 없습니다."

send_telegram(msg)
log("[*] 리포트 발송 완료")
