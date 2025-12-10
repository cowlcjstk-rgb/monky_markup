import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import datetime
import re
from urllib.parse import quote
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json

# --- 설정 ---
LOGIN_URL = "https://www.monkeytravel.com/th/totosys/index.php" 
BASE_PRODUCT_URL = "https://www.monkeytravel.com/th/totosys/product/spaProductRate.php?product_id={}"
GOOGLE_SHEET_NAME = "travel_data" 

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# --- 구글 시트 연결 ---
@st.cache_resource
def init_google_sheet():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    try:
        # 1순위: Streamlit Cloud Secrets
        if "gcp_service_account" in st.secrets:
            creds_dict = dict(st.secrets["gcp_service_account"])
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        # 2순위: 로컬 파일
        else:
            creds = ServiceAccountCredentials.from_json_keyfile_name("secrets.json", scope)
            
        client = gspread.authorize(creds)
        sheet = client.open(GOOGLE_SHEET_NAME).sheet1
        if not sheet.row_values(1):
            sheet.append_row(["product_id", "supplier", "product_name", "data_json", "updated_at"])
        return sheet
    except Exception as e:
        st.error(f"구글 시트 연결 실패: {e}")
        return None

def save_product_to_sheet(sheet, pid, supplier, p_name, data_json):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        records = sheet.get_all_records()
        df = pd.DataFrame(records)
        if not df.empty:
            df['product_id'] = df['product_id'].astype(str)
            
        if not df.empty and str(pid) in df['product_id'].values:
            row_idx = df.index[df['product_id'] == str(pid)].tolist()[0] + 2
            sheet.update_cell(row_idx, 2, supplier)
            sheet.update_cell(row_idx, 3, p_name)
            sheet.update_cell(row_idx, 4, data_json)
            sheet.update_cell(row_idx, 5, now)
        else:
            sheet.append_row([str(pid), supplier, p_name, data_json, now])
    except Exception as e:
        st.error(f"저장 중 오류 발생: {e}")

def load_products_from_sheet(sheet):
    try:
        records = sheet.get_all_records()
        df = pd.DataFrame(records)
        if not df.empty:
            df['product_id'] = df['product_id'].astype(str)
        return df
    except:
        return pd.DataFrame()

# --- 🧮 HTML 파싱 ---
def process_html_to_dataframe(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    data_rows = []

    title_tag = soup.find('a', href=re.compile(r'product_detail\.php'))
    product_name_extracted = title_tag.get_text(strip=True) if title_tag else "Unknown Product"

    accordion_buttons = soup.find_all('a', class_='accordion-button')
    
    for btn in accordion_buttons:
        date_text = ""
        b_tag = btn.find('b')
        if b_tag: date_text = b_tag.get_text(strip=True)
        
        start_date, end_date = "Unknown", "Unknown"
        if "~" in date_text:
            parts = date_text.split("~")
            start_date, end_date = parts[0].strip(), parts[1].strip()

        target_id = btn.get('data-bs-target')
        if target_id:
            target_id = target_id.replace('#', '')
            target_div = soup.find(id=target_id)
            
            if target_div:
                tables = target_div.find_all('table', attrs={'id': re.compile(r'priceTable_')})
                for table in tables:
                    tbodies = table.find_all('tbody')
                    for tbody in tbodies:
                        rows = tbody.find_all('tr')
                        last_program_name = "Unknown"
                        for row in rows:
                            name_td = row.find('td', class_='text-start')
                            if name_td:
                                b_tag = name_td.find('b')
                                if b_tag: last_program_name = b_tag.get_text(strip=True)
                            
                            duration_val = ""
                            duration_input = row.find('input', attrs={'name': re.compile(r'rate\.\d+\.duration')})
                            if duration_input:
                                duration_val = duration_input.get('value', '').strip()
                            else:
                                tds = row.find_all('td')
                                for td in tds:
                                    if td != name_td:
                                        dur_b = td.find('b')
                                        if dur_b and dur_b.get_text(strip=True).isdigit():
                                            duration_val = dur_b.get_text(strip=True)
                                            break
                            
                            if last_program_name.isdigit(): 
                                final_option_name = f"Option {last_program_name} {duration_val}"
                            else:
                                final_option_name = f"{last_program_name} {duration_val}".strip()

                            net_val, sale_val = 0, 0
                            currency = "THB"

                            net_input = row.find('input', attrs={'name': re.compile(r'adult\.nett')})
                            if net_input:
                                try: net_val = float(net_input.get('value', '0').replace(',', ''))
                                except: pass
                            
                            sale_input = row.find('input', attrs={'name': re.compile(r'adult\.sale\.monkey')})
                            if sale_input:
                                try: sale_val = float(sale_input.get('value', '0').replace(',', ''))
                                except: pass
                            
                            curr_div = row.find('div', attrs={'data-currency-nett': True})
                            if curr_div: currency = curr_div.get('data-currency-nett')

                            if net_val > 0 or sale_val > 0:
                                data_rows.append({
                                    '시작일': start_date,
                                    '종료일': end_date,
                                    '옵션명': final_option_name,
                                    '사이트': 'mk',
                                    '대상': '성인',
                                    '통화': currency,
                                    '네트가': int(net_val),
                                    '세일가': int(sale_val)
                                })

    if not data_rows: return pd.DataFrame(), product_name_extracted
    df = pd.DataFrame(data_rows)

    try:
        today = datetime.date.today()
        temp_dates = pd.to_datetime(df['종료일'], errors='coerce').dt.date
        df = df[ (temp_dates >= today) | (temp_dates.isna()) ]
    except: pass

    if df.empty: return pd.DataFrame(), product_name_extracted
    
    rates = [6.6, 10, 11]
    for r in rates:
        rate_key = str(r).replace('.0', '')
        col_comm = f'커미션_{rate_key}%'
        col_supply = f'공급가_{rate_key}%'
        col_markup = f'마크업_{rate_key}' 
        
        df[col_comm] = (df['세일가'] * (r / 100)).round().astype(int)
        df[col_supply] = (df['세일가'] - df[col_comm]).astype(int)
        
        def calc_deficit(row):
            supply = row[col_supply]
            net = row['네트가']
            if supply == 0: return "0%"
            if supply < net:
                diff = net - supply
                percent = (diff / supply) * 100
                return f"{percent:.0f}%"
            return "0%"

        df[col_markup] = df.apply(calc_deficit, axis=1)

    return df, product_name_extracted

# --- 메인 프로그램 ---
def main():
    st.set_page_config(page_title="스파 상품 마크업 (Web)", layout="wide")
    
    sheet = init_google_sheet()
    if sheet is None: st.stop()

    def highlight_deficit(val):
        color = 'black'
        if isinstance(val, str) and '%' in val:
            if val != "0%": color = 'red'
            return f'color: {color}; font-weight: bold;'
        return f'color: {color}'

    st.title("✈️ 스파 상품 마크업 계산기")

    # --- 사이드바 ---
    with st.sidebar:
        st.header("1. 연결 설정")
        manual_cookie_str = st.text_area("쿠키 전체 텍스트", height=100)
        
        if 'cookie_saved' not in st.session_state:
            st.session_state['cookie_saved'] = False

        if st.button("설정 저장"):
            st.session_state['manual_cookie_str'] = manual_cookie_str
            st.session_state['cookie_saved'] = True
            st.success("저장 완료!")
            st.rerun()

        st.markdown("---")
        
        # [추가됨] 분석 화면으로 바로가는 버튼 (HTML 링크 방식)
        if st.session_state.get('cookie_saved'):
            st.markdown("""
                <a href="#analysis_section" style="text-decoration:none;">
                    <button style="
                        width: 100%; 
                        padding: 0.5rem; 
                        border: 1px solid #FF4B4B; 
                        border-radius: 5px; 
                        background-color: transparent; 
                        color: #FF4B4B; 
                        font-weight: bold;
                        cursor: pointer;">
                        📊 상품 마크업 분석 바로가기
                    </button>
                </a>
                <br><br>
            """, unsafe_allow_html=True)

        st.header("2. 데이터 업데이트")
        product_ids_input = st.text_area("상품 ID 리스트", height=150)
        
        if st.button("데이터 가져오기"):
            if not st.session_state.get('cookie_saved') or not st.session_state.get('manual_cookie_str'):
                st.error("먼저 쿠키를 입력하고 [설정 저장]을 눌러주세요.")
                st.stop()

            active_session = requests.Session()
            active_session.headers.update(HEADERS)
            
            raw_cookie = st.session_state['manual_cookie_str']
            clean_cookie = raw_cookie.replace('\n', '').replace('\r', '')
            
            try:
                for item in clean_cookie.split(';'):
                    if '=' in item:
                        k, v = item.split('=', 1)
                        if v.strip():
                            try: v.encode('latin-1')
                            except: v = quote(v.strip())
                            active_session.cookies.set(k.strip(), v)
            except Exception as e: 
                st.warning(f"쿠키 파싱 경고: {e}")

            id_list = [x.strip() for x in product_ids_input.split('\n') if x.strip()]
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            for i, pid in enumerate(id_list):
                status_text.text(f"처리 중: {pid}")
                target_url = BASE_PRODUCT_URL.format(pid)
                try:
                    res = active_session.get(target_url)
                    res.encoding = 'utf-8'
                    if "login" in res.url: st.error("로그인 풀림"); break
                    
                    final_df, p_name = process_html_to_dataframe(res.text)
                    
                    if not final_df.empty:
                        json_str = final_df.to_json(orient='records', force_ascii=False, date_format='iso')
                        save_product_to_sheet(sheet, pid, "Unknown", p_name, json_str)
                    else:
                        save_product_to_sheet(sheet, pid, "Unknown", p_name, "[]")
                    
                except Exception as e: st.error(f"Error: {e}")
                progress_bar.progress((i + 1) / len(id_list))
            
            status_text.text("완료!")
            st.success("저장 완료!")
            st.rerun()

    # --- 메인 화면 로직 ---
    if not st.session_state.get('cookie_saved'):
        st.info("👈 왼쪽 사이드바에 **'쿠키(Cookie)'** 값을 입력해야 데이터를 가져올 수 있습니다.")
        
        with st.expander("ℹ️ 쿠키 값 가져오는 방법 (필독)", expanded=True):
            # [수정됨] 문법 오류 해결 (따옴표 닫기)
            st.markdown("""
            ### 1. 관리자 페이지 접속
            크롬 브라우저로 [MonkeyTravel 관리자 페이지]에 접속하여 로그인합니다.
            
            ### 2. 개발자 도구 열기
            키보드의 `F12` 키를 누릅니다.
            
            ### 3. 네트워크(Network) 탭 확인
            1. 개발자 도구 상단 메뉴에서 `Network` 탭을 클릭합니다.
            2. 키보드 `F5`를 눌러 페이지를 새로고침 합니다.
            3. 목록 맨 위에 있는 파일(보통 index.php)을 클릭합니다.
            
            ### 4. 쿠키 값 복사
            1. 오른쪽 창에서 `Headers` 탭을 클릭합니다.
            2. 스크롤을 내려 `Request Headers` 항목을 찾습니다.
            3. 그 안에 있는 `Cookie:` 옆의 긴 텍스트를 전부 복사합니다.
            4. 복사한 값을 왼쪽 사이드바 '쿠키 전체 텍스트' 칸에 붙여넣고 [설정 저장]을 누릅니다.
            """)
            st.warning("⚠️ 주의: 로그아웃 하면 쿠키 값이 바뀌므로, 다시 로그인했다면 쿠키도 새로 복사해야 합니다.")

    else:
        # [수정됨] 앵커 설정 (바로가기 버튼 도착지점)
        st.header("상품 마크업 분석", anchor="analysis_section")
        
        all_products = load_products_from_sheet(sheet)

        if not all_products.empty:
            all_products['display_label'] = all_products.apply(
                lambda x: f"[{x['product_id']}] {x['product_name']}", axis=1
            )
            
            product_options = all_products['display_label'].unique().tolist()
            selected_label = st.selectbox("분석할 상품을 선택하세요", product_options)
            
            if selected_label:
                selected_id = selected_label.split(']')[0].replace('[', '')
                
                # ID 찾을 때 문자열 비교로 안전하게 처리
                filtered_rows = all_products[all_products['product_id'] == str(selected_id)]
                
                if not filtered_rows.empty:
                    row = filtered_rows.iloc[0]
                    
                    st.markdown(f"### 📦 {row['product_name']}")
                    st.caption(f"ID: {selected_id} | 업데이트: {row['updated_at']}")
                    
                    raw_data = row.get('data_json', '[]')
                    try:
                        if isinstance(raw_data, str) and (raw_data.startswith('[') or raw_data.startswith('{')):
                            final_df = pd.read_json(raw_data)
                        else:
                            final_df = pd.DataFrame()
                    except: final_df = pd.DataFrame()

                    if not final_df.empty:
                        display_df = final_df.copy()
                        cols_num = ['네트가', '세일가'] + [c for c in display_df.columns if '커미션' in c or '공급가' in c]
                        for c in cols_num:
                            if c in display_df.columns:
                                display_df[c] = display_df[c].apply(lambda x: f"{x:,}")

                        st.dataframe(
                            display_df.style.map(highlight_deficit, subset=[c for c in display_df.columns if '마크업' in c]),
                            use_container_width=True,
                            hide_index=True,
                            height=600
                        )
                    else:
                        st.warning("유효한 가격 정보가 없습니다.")
                else:
                    st.error("데이터를 찾을 수 없습니다.")
        else:
            st.info("👈 왼쪽에서 데이터를 먼저 가져와주세요.")

if __name__ == "__main__":
    main()