import streamlit as st
import yfinance as yf
from datetime import datetime, timedelta
import pytz
import plotly.graph_objects as go
import pandas as pd
import gspread
from gspread.exceptions import WorksheetNotFound
from google.oauth2.service_account import Credentials
import FinanceDataReader as fdr
import sys

# 트레이딩뷰 데이터피드 선택적 import
# Windows에서 패키지 이름이 tvDatafeed(대소문자 구분)일 수 있으므로 두 가지 모두 시도
try:
    try:
        from tvdatafeed import TvDatafeed, Interval
    except ImportError:
        # tvDatafeed (대소문자 구분)로 재시도
        from tvDatafeed import TvDatafeed, Interval
    TV_AVAILABLE = True
    print(f"[Success] tvdatafeed import 성공! Python: {sys.executable}")
except ImportError as e:
    TV_AVAILABLE = False
    print(f"[Warning] tvdatafeed 모듈을 찾을 수 없습니다: {type(e).__name__}: {e}")
    print(f"[Debug] Python 실행 경로: {sys.executable}")
    print(f"[Debug] Python 경로 목록: {sys.path[:3]}...")  # 처음 3개만 표시
    print("[Info] 설치 방법: pip install git+https://github.com/rongardF/tvdatafeed.git")
    # 더미 클래스 정의 (에러 방지)
    class Interval:
        in_daily = None
except Exception as e:
    TV_AVAILABLE = False
    print(f"[Error] tvdatafeed import 중 예상치 못한 오류: {type(e).__name__}: {e}")
    print(f"[Debug] Python 실행 경로: {sys.executable}")
    # 더미 클래스 정의 (에러 방지)
    class Interval:
        in_daily = None

# 트레이딩뷰 데이터피드 초기화 (한 번만 실행)
tv = None
if TV_AVAILABLE:
    try:
        tv = TvDatafeed()
    except Exception as e:
        print(f"[TradingView Init Error] {str(e)}")
        tv = None

# 구글 시트 연결 설정
# 방법 1: 서비스 계정 사용 (권장)
# .streamlit/secrets.toml 파일에 다음 내용을 추가하세요:
# [gsheets]
# type = "service_account"
# project_id = "your-project-id"
# private_key_id = "your-private-key-id"
# private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
# client_email = "your-service-account@your-project.iam.gserviceaccount.com"
# client_id = "your-client-id"
# auth_uri = "https://accounts.google.com/o/oauth2/auth"
# token_uri = "https://oauth2.googleapis.com/token"
# auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
# client_x509_cert_url = "https://www.googleapis.com/robot/v1/metadata/x509/..."
#
# 또는 방법 2: 스프레드시트를 "모두가 편집 가능"으로 설정하고 아래 코드 사용
# SPREADSHEET_ID = "1vlnPKjMiPaaYRLV18BS4D_pTPkAWXUP7_zdh14DZsiM"

SPREADSHEET_ID = "1vlnPKjMiPaaYRLV18BS4D_pTPkAWXUP7_zdh14DZsiM"
SHEET_NAME = "Sheet1"

def get_gsheets_client():
    """구글 시트 클라이언트 반환"""
    try:
        # secrets.toml에서 서비스 계정 정보 읽기
        if 'gsheets' in st.secrets:
            # 디버깅: 비밀 키 값은 제외하고 어떤 키들이 있는지 확인
            creds_info = dict(st.secrets['gsheets'])
            # st.write(f"Debug: Found keys in secrets: {list(creds_info.keys())}")
            
            # private_key 형식 보정 (줄바꿈 문자가 제대로 처리되지 않았을 경우 대비)
            if 'private_key' in creds_info:
                pk = creds_info['private_key']
                # 만약 문자열에 실제 줄바꿈이 없고 \n 문자만 있다면 치환 (일반적인 실수 방지)
                if "\\n" in pk and "\n" not in pk:
                    creds_info['private_key'] = pk.replace("\\n", "\n")

            scope = ['https://spreadsheets.google.com/feeds',
                    'https://www.googleapis.com/auth/drive']
            creds = Credentials.from_service_account_info(creds_info, scopes=scope)
            client = gspread.authorize(creds)
            return client
        else:
            st.error("secrets에 [gsheets] 섹션이 없습니다.")
            return None
    except Exception as e:
        import traceback
        st.error(f"구글 시트 연결 오류 상세: {str(e)}")
        st.code(traceback.format_exc())
        return None

gsheets_client = get_gsheets_client()

# 페이지 설정
st.set_page_config(
    page_title="실시간 시황 대시보드",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 다크 모드 스타일 적용
st.markdown("""
    <style>
    .main {
        background-color: #0e1117;
    }
    .stMetric {
        background-color: #1e1e1e;
        padding: 1rem;
        border-radius: 0.5rem;
        border: 1px solid #333;
    }
    h1 {
        color: #ffffff;
    }
    .update-time {
        color: #888;
        font-size: 0.9rem;
    }
    h2 {
        color: #ffffff;
        margin-top: 2rem;
        margin-bottom: 1rem;
    }
    h3 {
        color: #ffffff;
    }
    </style>
    """, unsafe_allow_html=True)

def load_data():
    """구글 시트에서 데이터를 읽어와서 session_state에 로드"""
    if gsheets_client is None:
        return False
    
    try:
        # 스프레드시트 열기
        spreadsheet = gsheets_client.open_by_key(SPREADSHEET_ID)
        
        # 시트 찾기 (시트 이름으로 찾거나 첫 번째 시트 사용)
        try:
            worksheet = spreadsheet.worksheet(SHEET_NAME)
        except WorksheetNotFound:
            # 시트가 없으면 첫 번째 시트 사용
            worksheet = spreadsheet.sheet1
        except Exception:
            # 다른 오류면 첫 번째 시트 사용
            worksheet = spreadsheet.sheet1
        
        # 모든 데이터 읽기
        all_values = worksheet.get_all_values()
        
        if len(all_values) < 2:  # 헤더만 있거나 비어있음
            return False
        
        # 첫 번째 행을 헤더로 사용
        headers = all_values[0]
        data_rows = all_values[1:]
        
        # DataFrame 생성
        df = pd.DataFrame(data_rows, columns=headers)
        
        # 필요한 컬럼 확인
        required_cols = ['Category', 'TickerName', 'Symbol', 'Order', 'CategoryOrder']
        if not all(col in df.columns for col in required_cols):
            # 컬럼이 없으면 첫 5개 컬럼을 사용 (CategoryOrder가 없으면 추가)
            if len(df.columns) >= 4:
                if len(df.columns) < 5:
                    # CategoryOrder 컬럼이 없으면 추가 (기본값 0)
                    df['CategoryOrder'] = 0
                # 컬럼명 설정
                col_names = required_cols[:len(df.columns)]
                if len(df.columns) == 4:
                    col_names = required_cols[:4] + ['CategoryOrder']
                df.columns = col_names[:len(df.columns)]
            else:
                return False
        
        # 빈 행 제거
        df = df.dropna(subset=['Category', 'TickerName', 'Symbol'])
        df = df[df['Category'].astype(str).str.strip() != '']
        df = df[df['TickerName'].astype(str).str.strip() != '']
        df = df[df['Symbol'].astype(str).str.strip() != '']
        
        if df.empty:
            return False
        
        # Order 컬럼을 숫자로 변환 (실패 시 인덱스 사용)
        try:
            df['Order'] = pd.to_numeric(df['Order'], errors='coerce')
            df = df.fillna({'Order': 0})
        except:
            df['Order'] = range(len(df))
        
        # CategoryOrder와 Order로 정렬 (카테고리 순서 우선, 그 다음 티커 순서)
        if 'CategoryOrder' in df.columns:
            try:
                df['CategoryOrder'] = pd.to_numeric(df['CategoryOrder'], errors='coerce')
                df = df.fillna({'CategoryOrder': 999})  # CategoryOrder가 없으면 맨 뒤로
            except:
                df['CategoryOrder'] = 999
        else:
            df['CategoryOrder'] = 999
        
        df = df.sort_values(by=['CategoryOrder', 'Order'])
        
        # session_state 재구성
        market_data = {}
        category_order = []
        ticker_order = {}
        category_order_map = {}  # 카테고리별 CategoryOrder 값 저장
        
        for _, row in df.iterrows():
            category = str(row['Category']).strip()
            ticker_name = str(row['TickerName']).strip()
            symbol = str(row['Symbol']).strip()
            category_order_val = row.get('CategoryOrder', 999)
            
            if not category or not ticker_name or not symbol:
                continue
            
            # 카테고리 순서 정보 저장
            if category not in category_order_map:
                category_order_map[category] = category_order_val
            
            if category not in market_data:
                market_data[category] = {}
                if category not in category_order:
                    category_order.append(category)
                ticker_order[category] = []
            
            market_data[category][ticker_name] = symbol
            ticker_order[category].append(ticker_name)
        
        # 카테고리 순서를 CategoryOrder 값에 따라 정렬
        category_order = sorted(category_order, key=lambda x: category_order_map.get(x, 999))
        
        st.session_state.market_data = market_data
        st.session_state.category_order = category_order
        st.session_state.ticker_order = ticker_order
        
        return True
    except Exception as e:
        error_msg = str(e)
        # 시트 관련 오류인 경우 더 자세한 정보 제공
        if "Sheet1" in error_msg or "worksheet" in error_msg.lower():
            try:
                spreadsheet = gsheets_client.open_by_key(SPREADSHEET_ID)
                available_sheets = [sheet.title for sheet in spreadsheet.worksheets()]
                st.warning(f"시트 '{SHEET_NAME}'를 찾을 수 없습니다. 사용 가능한 시트: {', '.join(available_sheets) if available_sheets else '없음'}")
            except:
                pass
        st.error(f"데이터 로드 오류: {error_msg}")
        return False

def save_data():
    """현재 session_state 데이터를 구글 시트에 저장"""
    if gsheets_client is None:
        st.error("구글 시트 연결이 없습니다.")
        return False
    
    try:
        # session_state 데이터를 리스트로 변환
        rows = []
        category_order = st.session_state.get('category_order', [])
        ticker_order = st.session_state.get('ticker_order', {})
        market_data = st.session_state.get('market_data', {})
        
        # 카테고리 순서에 따라 처리 (카테고리 순서도 함께 저장)
        for category_idx, category in enumerate(category_order):
            if category in market_data:
                tickers = market_data[category]
                ticker_list = ticker_order.get(category, list(tickers.keys()))
                
                # 순서에 없는 티커 추가
                for ticker_name in tickers.keys():
                    if ticker_name not in ticker_list:
                        ticker_list.append(ticker_name)
                
                # 순서대로 행 추가 (카테고리 순서 포함)
                for order, ticker_name in enumerate(ticker_list):
                    if ticker_name in tickers:
                        rows.append([
                            category,
                            ticker_name,
                            tickers[ticker_name],
                            order,  # 티커 순서
                            category_idx  # 카테고리 순서
                        ])
        
        # 순서에 없는 카테고리도 추가 (맨 뒤에 추가)
        max_category_idx = len(category_order)
        for category in market_data.keys():
            if category not in category_order:
                tickers = market_data[category]
                ticker_list = ticker_order.get(category, list(tickers.keys()))
                for order, ticker_name in enumerate(ticker_list):
                    if ticker_name in tickers:
                        rows.append([
                            category,
                            ticker_name,
                            tickers[ticker_name],
                            order,  # 티커 순서
                            max_category_idx  # 카테고리 순서 (맨 뒤)
                        ])
                max_category_idx += 1
        
        if not rows:
            return False
        
        # 스프레드시트 열기
        spreadsheet = gsheets_client.open_by_key(SPREADSHEET_ID)
        
        # 시트 찾기 또는 생성
        try:
            worksheet = spreadsheet.worksheet(SHEET_NAME)
        except WorksheetNotFound:
            # 시트가 없으면 생성
            worksheet = spreadsheet.add_worksheet(title=SHEET_NAME, rows=1000, cols=10)
        except Exception:
            # 다른 오류면 첫 번째 시트 사용
            worksheet = spreadsheet.sheet1
        
        # 헤더와 데이터 준비 (CategoryOrder 컬럼 추가)
        headers = [['Category', 'TickerName', 'Symbol', 'Order', 'CategoryOrder']]
        all_data = headers + rows
        
        # 시트 전체 지우기
        worksheet.clear()
        
        # 새 데이터 쓰기
        worksheet.update('A1', all_data, value_input_option='RAW')
        
        return True
    except Exception as e:
        st.error(f"데이터 저장 오류: {str(e)}")
        return False

def get_default_data():
    """기본 데이터 반환"""
    return {
        "주요 지수": {
            "KOSPI": "^KS11",
            "S&P500": "^GSPC",
            "중국 상해 종합": "000001.SS",
            "일본 니케이225": "^N225"
        },
        "외환": {
            "달러 인덱스": "DX-Y.NYB",
            "원/달러 환율": "KRW=X",
            "원/위안 환율": "CNYKRW=X",
            "미국 10년물 국채금리": "^TNX"
        },
        "원자재": {
            "원유": "CL=F",
            "금": "GC=F",
            "은": "SI=F",
            "구리": "HG=F"
        }
    }

# 초기 데이터 설정 함수
def init_market_data():
    """세션 상태 초기화 - 구글 시트에서 로드하거나 기본값 설정"""
    if 'market_data' not in st.session_state:
        # 구글 시트에서 데이터 로드 시도
        if gsheets_client is not None:
            if load_data():
                return  # 성공적으로 로드됨
        
        # 시트가 비어있거나 로드 실패 시 기본 데이터 사용
        default_data = get_default_data()
        st.session_state.market_data = default_data
        st.session_state.category_order = list(default_data.keys())
        st.session_state.ticker_order = {}
        for category, tickers in default_data.items():
            st.session_state.ticker_order[category] = list(tickers.keys())
        
        # 기본 데이터를 시트에 저장
        if gsheets_client is not None:
            save_data()
    
    # 카테고리 순서 초기화 (없는 경우)
    if 'category_order' not in st.session_state:
        st.session_state.category_order = list(st.session_state.market_data.keys())
    
    # 카테고리별 티커 순서 초기화 (없는 경우)
    if 'ticker_order' not in st.session_state:
        st.session_state.ticker_order = {}
        for category, tickers in st.session_state.market_data.items():
            st.session_state.ticker_order[category] = list(tickers.keys())

def _period_to_dates(period):
    """period 문자열을 시작일과 종료일로 변환"""
    end_date = datetime.now()
    
    if period == "1mo":
        start_date = end_date - timedelta(days=30)
    elif period == "6mo":
        start_date = end_date - timedelta(days=180)
    elif period == "1y":
        start_date = end_date - timedelta(days=365)
    elif period == "2y":
        start_date = end_date - timedelta(days=730)
    elif period == "5y":
        start_date = end_date - timedelta(days=1825)
    elif period == "10y":
        start_date = end_date - timedelta(days=3650)
    elif period == "15y":
        start_date = end_date - timedelta(days=5475)
    elif period == "20y":
        start_date = end_date - timedelta(days=7300)
    else:
        start_date = end_date - timedelta(days=365)  # 기본값: 1년
    
    return start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')

def _period_to_interval(period):
    """period 문자열을 TradingView Interval로 변환"""
    if not TV_AVAILABLE:
        return None
    
    if period == "1mo":
        return Interval.in_daily
    elif period == "6mo":
        return Interval.in_daily
    elif period == "1y":
        return Interval.in_daily
    elif period == "2y":
        return Interval.in_daily
    elif period == "5y":
        return Interval.in_daily
    elif period == "max":
        return Interval.in_daily
    else:
        return Interval.in_daily  # 기본값: 일봉

@st.cache_data(ttl=3600)  # 1시간 캐시 (티커 목록은 자주 변경되지 않음)
def search_tickers(query, source):
    """티커 검색 함수"""
    query_lower = query.lower()
    results = []
    
    if source == "yfinance":
        # 주요 yfinance 티커 목록 (키워드 기반)
        ticker_list = [
            # 주식 지수
            ("코스피", "^KS11", "KOSPI 종합주가지수"),
            ("코스닥", "^KQ11", "KOSDAQ 종합주가지수"),
            ("S&P500", "^GSPC", "S&P 500 지수"),
            ("나스닥", "^IXIC", "NASDAQ 종합지수"),
            ("다우", "^DJI", "다우 존스 산업평균지수"),
            ("니케이", "^N225", "닛케이 225 지수"),
            ("상해종합", "000001.SS", "상하이 종합 지수"),
            ("CSI300", "000300.SS", "CSI 300 지수"),
            ("항셍", "^HSI", "항셍 지수"),
            
            # 환율
            ("원달러", "KRW=X", "원/달러 환율"),
            ("원위안", "CNYKRW=X", "원/위안 환율"),
            ("원엔", "JPYKRW=X", "원/엔 환율"),
            ("달러인덱스", "DX-Y.NYB", "달러 인덱스"),
            ("유로달러", "EURUSD=X", "유로/달러 환율"),
            ("엔달러", "JPY=X", "엔/달러 환율"),
            
            # 원자재
            ("유가", "CL=F", "WTI 원유 선물"),
            ("원유", "CL=F", "WTI 원유 선물"),
            ("브렌트", "BZ=F", "브렌트 원유 선물"),
            ("금", "GC=F", "금 선물"),
            ("은", "SI=F", "은 선물"),
            ("구리", "HG=F", "구리 선물"),
            ("팔라듐", "PA=F", "팔라듐 선물"),
            ("백금", "PL=F", "백금 선물"),
            ("천연가스", "NG=F", "천연가스 선물"),
            ("가솔린", "RB=F", "가솔린 선물"),
            ("난방유", "HO=F", "난방유 선물"),
            ("밀", "ZW=F", "밀 선물"),
            ("옥수수", "ZC=F", "옥수수 선물"),
            ("대두", "ZS=F", "대두 선물"),
            ("원당", "SB=F", "원당 선물"),
            ("코코아", "CC=F", "코코아 선물"),
            ("커피", "KC=F", "커피 선물"),
            ("면화", "CT=F", "면화 선물"),
            ("원목", "LBS=F", "원목 선물"),
            
            # 채권
            ("미국10년물", "^TNX", "미국 10년 국채금리"),
            ("미국30년물", "^TYX", "미국 30년 국채금리"),
            ("미국2년물", "^IRX", "미국 2년 국채금리"),
            
            # 주요 주식 (삼성, 애플 등)
            ("삼성전자", "005930.KS", "삼성전자 (KOSPI)"),
            ("SK하이닉스", "000660.KS", "SK하이닉스 (KOSPI)"),
            ("NAVER", "035420.KS", "NAVER (KOSPI)"),
            ("카카오", "035720.KS", "카카오 (KOSPI)"),
            ("애플", "AAPL", "Apple Inc."),
            ("마이크로소프트", "MSFT", "Microsoft Corporation"),
            ("구글", "GOOGL", "Alphabet Inc."),
            ("아마존", "AMZN", "Amazon.com Inc."),
            ("테슬라", "TSLA", "Tesla Inc."),
            ("엔비디아", "NVDA", "NVIDIA Corporation"),
            ("메타", "META", "Meta Platforms Inc."),
        ]
        
        for name, symbol, desc in ticker_list:
            if query_lower in name.lower() or query_lower in desc.lower() or query_lower in symbol.lower():
                results.append({
                    'name': name,
                    'symbol': symbol,
                    'description': desc,
                    'source': 'yfinance'
                })
    
    elif source == "FinanceDataReader (한국)":
        try:
            # 한국 주식 목록 가져오기
            stock_list = fdr.StockListing('KRX')
            
            # 검색어로 필터링
            if not stock_list.empty:
                # 종목명 또는 코드로 검색
                mask = (
                    stock_list['Name'].str.contains(query, case=False, na=False) |
                    stock_list['Symbol'].str.contains(query, case=False, na=False) |
                    stock_list['Sector'].str.contains(query, case=False, na=False)
                )
                filtered = stock_list[mask].head(50)  # 최대 50개
                
                for _, row in filtered.iterrows():
                    sector = row.get('Sector', 'N/A')
                    market = row.get('Market', 'N/A')
                    results.append({
                        'name': f"{row['Name']} ({sector})",
                        'symbol': row['Symbol'],
                        'description': f"시장: {market}",
                        'source': 'FinanceDataReader'
                    })
        except Exception as e:
            print(f"[FDR Search Error] {str(e)}")
            # 에러는 조용히 처리 (UI에서 처리)
    
    elif source == "TradingView":
        # TradingView는 직접 검색 API가 없으므로 가이드 제공
        tradingview_guides = [
            ("한국 10년 국채", "TVC:KR10Y", "TradingView 한국 10년 국채"),
            ("한국 3년 국채", "TVC:KR3Y", "TradingView 한국 3년 국채"),
            ("한국 30년 국채", "TVC:KR30Y", "TradingView 한국 30년 국채"),
            ("WTI 원유", "TVC:USOIL", "TradingView WTI 원유"),
            ("브렌트 원유", "TVC:UKOIL", "TradingView 브렌트 원유"),
            ("금", "TVC:GOLD", "TradingView 금"),
            ("은", "TVC:SILVER", "TradingView 은"),
            ("구리", "TVC:COPPER", "TradingView 구리"),
            ("S&P500", "SPX:SPX", "TradingView S&P500"),
            ("나스닥", "NASDAQ:NDX", "TradingView 나스닥"),
            ("다우", "DJI:DJI", "TradingView 다우존스"),
        ]
        
        for name, symbol, desc in tradingview_guides:
            if query_lower in name.lower() or query_lower in desc.lower() or query_lower in symbol.lower():
                results.append({
                    'name': name,
                    'symbol': symbol,
                    'description': desc,
                    'source': 'TradingView'
                })
    
    return results

def generate_ticker_search_prompt(search_query, data_source):
    """티커 검색을 위한 AI 프롬프트 생성"""
    if data_source == "yfinance":
        prompt = f"""다음 검색어에 해당하는 yfinance 티커 심볼을 찾아주세요: "{search_query}"

요구사항:
1. 검색어와 관련된 모든 주요 티커 심볼을 찾아주세요
2. 각 티커에 대해 다음 형식으로 제공해주세요:
   - 티커 이름 (한글)
   - 티커 심볼 (yfinance 형식)
   - 설명 (간단히)

예시:
- 유가 또는 원유 검색 시:
  • WTI 원유 선물: CL=F
  • 브렌트 원유 선물: BZ=F
  
- 금 검색 시:
  • 금 선물: GC=F

중요: 티커 심볼만 정확하게 제공해주세요. yfinance에서 사용 가능한 형식이어야 합니다."""
    
    elif data_source == "FinanceDataReader (한국)":
        prompt = f"""다음 검색어에 해당하는 한국 주식/채권 티커 심볼을 찾아주세요: "{search_query}"

요구사항:
1. 검색어와 관련된 모든 한국 주식/채권 티커를 찾아주세요
2. FinanceDataReader에서 사용 가능한 형식이어야 합니다
3. 각 티커에 대해 다음 형식으로 제공해주세요:
   - 티커 이름 (종목명)
   - 티커 심볼 (6자리 종목코드 또는 KR10Y, KR3Y 같은 국채 코드)
   - 설명 (간단히)

예시:
- 삼성 검색 시:
  • 삼성전자: 005930
  • 삼성SDI: 006400
  
- 국채 검색 시:
  • 한국 10년 국채: KR10Y
  • 한국 3년 국채: KR3Y

중요: 티커 심볼만 정확하게 제공해주세요. FinanceDataReader에서 사용 가능한 형식이어야 합니다."""
    
    elif data_source == "TradingView":
        prompt = f"""다음 검색어에 해당하는 TradingView 티커 심볼을 찾아주세요: "{search_query}"

요구사항:
1. TradingView에서 사용 가능한 심볼을 찾아주세요
2. TradingView 심볼 형식: EXCHANGE:SYMBOL (예: TVC:KR10Y, SPX:SPX)
3. 각 티커에 대해 다음 형식으로 제공해주세요:
   - 티커 이름 (한글)
   - 티커 심볼 (EXCHANGE:SYMBOL 형식)
   - 설명 (간단히)

예시:
- 한국 국채 검색 시:
  • 한국 10년 국채: TVC:KR10Y
  • 한국 3년 국채: TVC:KR3Y
  
- 원유 검색 시:
  • WTI 원유: TVC:USOIL
  • 브렌트 원유: TVC:UKOIL

중요: 
- 티커 심볼은 반드시 EXCHANGE:SYMBOL 형식이어야 합니다
- TradingView에서 실제로 사용 가능한 심볼이어야 합니다
- 거래소 코드와 심볼을 정확하게 구분해서 제공해주세요"""
    
    return prompt

@st.cache_data(ttl=60)  # 60초마다 캐시 갱신
def get_ticker_data(ticker_symbol, period="1y", cache_key=None):
    """티커 데이터를 가져오는 함수 (기간별 히스토리 포함)
    
    우선순위:
    1. 콜론(:)이 있으면 트레이딩뷰 사용 (예: TVC:KR10Y)
    2. 한국 국채 티커(KR10Y, KR3Y, KR30Y 등)는 FinanceDataReader 사용
    3. 그 외는 yfinance 사용
    """
    # 트레이딩뷰 티커 확인 (콜론이 있는 경우)
    original_symbol = ticker_symbol  # 원본 심볼 보관
    if ':' in ticker_symbol:
        if tv is not None:
            try:
                # exchange와 symbol 분리
                parts = ticker_symbol.split(':', 1)
                if len(parts) != 2:
                    raise ValueError(f"TradingView: 잘못된 심볼 형식 - {ticker_symbol}")
                
                exchange = parts[0]
                symbol = parts[1]
                
                # 기간에 맞는 시작일 계산
                start_date, end_date = _period_to_dates(period)
                interval = _period_to_interval(period)
                
                if interval is None:
                    raise ValueError("TradingView: Interval을 사용할 수 없습니다")
                
                # 트레이딩뷰에서 데이터 가져오기
                df = tv.get_hist(
                    symbol=symbol,
                    exchange=exchange,
                    interval=interval,
                    n_bars=10000  # 충분히 많은 데이터 가져오기
                )
                
                if df is None or df.empty:
                    raise ValueError(f"TradingView: {ticker_symbol}에 대한 데이터가 없습니다")
                
                # 데이터 포맷 표준화
                # 트레이딩뷰는 보통 datetime 인덱스를 사용
                if not isinstance(df.index, pd.DatetimeIndex):
                    df.index = pd.to_datetime(df.index)
                
                # Close 컬럼 확인 및 변환
                if 'close' in df.columns:
                    df['Close'] = df['close']
                elif 'Close' not in df.columns:
                    # 숫자 컬럼 찾기
                    numeric_cols = df.select_dtypes(include=[float, int]).columns
                    if len(numeric_cols) > 0:
                        df['Close'] = df[numeric_cols[0]]
                    else:
                        raise ValueError(f"TradingView: {ticker_symbol}에 Close 컬럼이 없습니다")
                
                # 기간 필터링 (시작일 이후만)
                start_dt = pd.to_datetime(start_date)
                df = df[df.index >= start_dt]
                
                if df.empty:
                    raise ValueError(f"TradingView: {ticker_symbol}에 필터링 후 데이터가 없습니다")
                
                # Close 컬럼만 추출하고 정렬
                hist = df[['Close']].copy()
                hist = hist.sort_index()
                
                # 현재가와 전일가 계산
                if len(hist) >= 2:
                    current_price = hist['Close'].iloc[-1]
                    prev_price = hist['Close'].iloc[-2]
                elif len(hist) == 1:
                    current_price = hist['Close'].iloc[-1]
                    prev_price = current_price
                else:
                    current_price = 0
                    prev_price = 0
                
                change_pct = ((current_price - prev_price) / prev_price) * 100 if prev_price != 0 else 0
                
                return {
                    'current': current_price,
                    'change_pct': change_pct,
                    'history': hist['Close']
                }
            except Exception as e:
                # TradingView 실패 시 로그 출력
                print(f"[TradingView Error] {ticker_symbol}: {str(e)}")
                # fallback: 콜론 뒤 부분만 추출하여 재시도
                parts = ticker_symbol.split(':', 1)
                if len(parts) == 2 and parts[1].startswith('KR'):
                    ticker_symbol = parts[1]  # KR10Y로 변경하여 FDR 사용
                    print(f"[Fallback] TradingView 실패, FDR로 재시도: {ticker_symbol}")
        else:
            # tv가 None이면 콜론 뒤 부분만 추출하여 재시도
            parts = ticker_symbol.split(':', 1)
            if len(parts) == 2 and parts[1].startswith('KR'):
                ticker_symbol = parts[1]  # KR10Y로 변경하여 FDR 사용
                print(f"[Fallback] TradingView 미사용, FDR로 시도: {ticker_symbol}")
    
    # 한국 국채 티커 확인 (KR로 시작하고 숫자로 끝나는 패턴) 또는 한국 주요 지수
    is_korean_bond = ticker_symbol.startswith('KR') and len(ticker_symbol) >= 3
    is_korean_index = ticker_symbol in ['^KS11', '^KQ11']
    
    if is_korean_bond or is_korean_index:
        # FinanceDataReader 사용
        try:
            # 심볼 변환 (yfinance -> FDR)
            target_symbol = ticker_symbol
            if is_korean_index:
                # ^KS11 -> KS11, ^KQ11 -> KQ11
                target_symbol = ticker_symbol.replace('^', '')
            
            start_date, end_date = _period_to_dates(period)
            
            # FinanceDataReader로 데이터 가져오기
            df = fdr.DataReader(target_symbol, start_date, end_date)
            
            if df.empty:
                raise ValueError(f"FDR: {ticker_symbol}에 대한 데이터가 없습니다")
            
            # 데이터 포맷 표준화 (yfinance 형식과 동일하게)
            # FDR은 보통 Date를 인덱스로 사용하거나 별도 컬럼으로 가짐
            if 'Date' in df.columns:
                df.set_index('Date', inplace=True)
            
            # 인덱스가 DatetimeIndex가 아니면 변환
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            
            # Close 컬럼 확인 (없으면 첫 번째 숫자 컬럼 사용)
            if 'Close' not in df.columns:
                # 숫자 컬럼 찾기
                numeric_cols = df.select_dtypes(include=[float, int]).columns
                if len(numeric_cols) > 0:
                    df['Close'] = df[numeric_cols[0]]
                else:
                    raise ValueError(f"FDR: {ticker_symbol}에 Close 컬럼이 없습니다")
            
            # Close 컬럼만 추출하고 정렬
            hist = df[['Close']].copy()
            hist = hist.sort_index()
            
            # 현재가와 전일가 계산
            if len(hist) >= 2:
                current_price = hist['Close'].iloc[-1]
                prev_price = hist['Close'].iloc[-2]
            elif len(hist) == 1:
                current_price = hist['Close'].iloc[-1]
                prev_price = current_price
            else:
                current_price = 0
                prev_price = 0
            
            change_pct = ((current_price - prev_price) / prev_price) * 100 if prev_price != 0 else 0
            
            return {
                'current': current_price,
                'change_pct': change_pct,
                'history': hist['Close']
            }
        except Exception as e:
            # FDR 실패 시 로그 출력
            print(f"[FDR Error] {ticker_symbol}: {str(e)}")
            return {
                'current': 0,
                'change_pct': 0,
                'history': pd.Series()
            }
    else:
        # yfinance 사용 (기존 로직)
        try:
            ticker = yf.Ticker(ticker_symbol)
            
            # 기간에 맞는 히스토리 데이터 가져오기
            hist = ticker.history(period=period)
            
            if hist.empty:
                # 데이터가 없는 경우 info에서 가져오기 시도
                try:
                    info = ticker.info
                    current_price = info.get('regularMarketPrice', info.get('previousClose', 0))
                    prev_price = info.get('previousClose', current_price)
                    hist = pd.DataFrame({'Close': [prev_price, current_price]}, 
                                      index=pd.date_range(end=datetime.now(), periods=2, freq='D'))
                except:
                    raise ValueError(f"yfinance: {ticker_symbol}에 대한 데이터를 가져올 수 없습니다")
            
            # 현재가와 전일가 계산
            if len(hist) >= 2:
                current_price = hist['Close'].iloc[-1]
                prev_price = hist['Close'].iloc[-2]
            elif len(hist) == 1:
                current_price = hist['Close'].iloc[-1]
                prev_price = current_price
            else:
                current_price = 0
                prev_price = 0
            
            change_pct = ((current_price - prev_price) / prev_price) * 100 if prev_price != 0 else 0
            
            return {
                'current': current_price,
                'change_pct': change_pct,
                'history': hist['Close']
            }
        except Exception as e:
            # yfinance 실패 시 로그 출력
            print(f"[yfinance Error] {ticker_symbol}: {str(e)}")
            return {
                'current': 0,
                'change_pct': 0,
                'history': pd.Series()
            }

def create_sparkline_chart(history_data, change_pct, ticker_name):
    """Sparkline 스타일의 영역 차트 생성"""
    # x축 설정 초기화
    xaxis_config = dict(
        showgrid=False,
        showticklabels=False,
        zeroline=False
    )
    
    if history_data.empty or len(history_data) == 0:
        # 빈 차트 반환
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=[], y=[], mode='lines'))
    else:
        # 등락에 따른 색상 결정 (상승=빨강, 하락=파랑)
        line_color = '#ef4444' if change_pct >= 0 else '#3b82f6'
        fill_color = 'rgba(239, 68, 68, 0.2)' if change_pct >= 0 else 'rgba(59, 130, 246, 0.2)'
        
        # 인덱스를 날짜로 변환
        if isinstance(history_data.index, pd.DatetimeIndex):
            dates = history_data.index
        else:
            dates = pd.date_range(end=datetime.now(), periods=len(history_data), freq='D')
        
        # 이동평균선 계산 (20주 = 100일, 80주 = 400일)
        ma20 = history_data.rolling(window=100).mean()  # 20주 이평선
        ma80 = history_data.rolling(window=400).mean()  # 80주 이평선
        
        # Y축 범위 계산 (최솟값, 최댓값) - 이동평균선 포함
        all_values = pd.concat([history_data, ma20, ma80]).dropna()
        min_value = all_values.min()
        max_value = all_values.max()
        value_range = max_value - min_value
        
        # 전체 폭의 5% 여유 추가 (상단과 하단 각각 2.5%)
        padding = value_range * 0.025 if value_range > 0 else abs(min_value) * 0.025 if min_value != 0 else 1
        y_min = min_value - padding
        y_max = max_value + padding
        
        fig = go.Figure()
        
        # 영역 차트 추가
        fig.add_trace(go.Scatter(
            x=dates,
            y=history_data.values,
            fill='tozeroy',
            mode='lines',
            line=dict(color=line_color, width=2),
            fillcolor=fill_color,
            hovertemplate='%{y:.2f}<extra></extra>',
            showlegend=False,
            name='종가'
        ))
        
        # 20주 이평선 추가 (주황색)
        if not ma20.isna().all():
            fig.add_trace(go.Scatter(
                x=dates,
                y=ma20.values,
                mode='lines',
                line=dict(color='#ff8c00', width=1.5),
                hovertemplate='20주 이평: %{y:.2f}<extra></extra>',
                showlegend=False,
                name='20주 이평'
            ))
        
        # 80주 이평선 추가 (초록색)
        if not ma80.isna().all():
            fig.add_trace(go.Scatter(
                x=dates,
                y=ma80.values,
                mode='lines',
                line=dict(color='#22c55e', width=1.5),
                hovertemplate='80주 이평: %{y:.2f}<extra></extra>',
                showlegend=False,
                name='80주 이평'
            ))
        
        # Y축 범위 설정
        fig.update_yaxes(range=[y_min, y_max])
        
        # 연도 틱 위치 계산 (데이터 범위에서 연도별로)
        try:
            # 데이터의 첫 번째와 마지막 연도 추출
            min_year = dates.min().year
            max_year = dates.max().year
            
            # 연도별 틱 위치 생성 (각 연도의 1월 1일)
            tickvals = []
            ticktext = []
            for year in range(min_year, max_year + 1):
                try:
                    tick_date = pd.Timestamp(year, 1, 1)
                    if tick_date >= dates.min() and tick_date <= dates.max():
                        tickvals.append(tick_date)
                        ticktext.append(f"{year % 100}년")  # 25년, 24년 형식
                except:
                    pass
            
            # 최대 5개의 틱만 표시 (너무 많으면 제한)
            if len(tickvals) > 5:
                step = max(1, len(tickvals) // 5)
                tickvals = tickvals[::step]
                ticktext = ticktext[::step]
            
            # tickvals가 비어있지 않을 때만 x축 설정에 추가
            if len(tickvals) > 0:
                xaxis_config = dict(
                    showgrid=False,
                    showticklabels=True,
                    zeroline=False,
                    tickvals=tickvals,
                    ticktext=ticktext,
                    tickfont=dict(size=9, color='#888'),
                    tickangle=0
                )
            else:
                # tickvals가 비어있으면 자동으로 연도 표시
                xaxis_config = dict(
                    showgrid=False,
                    showticklabels=True,
                    zeroline=False,
                    tickformat='%y년',
                    tickfont=dict(size=9, color='#888'),
                    tickangle=0,
                    dtick='M12'  # 12개월마다 틱 (연도별)
                )
        except Exception as e:
            # 오류 발생 시 기본 설정 사용
            print(f"[Chart Year Display Error] {ticker_name}: {str(e)}")
            xaxis_config = dict(
                showgrid=False,
                showticklabels=True,
                zeroline=False,
                tickformat='%y년',
                tickfont=dict(size=9, color='#888'),
                tickangle=0
            )
    
    # Sparkline 스타일: 최소한의 축 정보 (x축에 연도만 표시)
    fig.update_layout(
        height=120,
        margin=dict(l=0, r=0, t=0, b=25, pad=0),  # 하단 마진 추가 (x축 레이블 공간)
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
        xaxis=xaxis_config,
        yaxis=dict(
            showgrid=False,
            showticklabels=False,
            zeroline=False
        ),
        hovermode='x unified'
    )
    
    return fig

def render_ticker_card(name, symbol, ticker_data):
    """개별 티커 카드 렌더링"""
    # 숫자 포맷팅
    current_value = ticker_data['current']
    change_value = ticker_data['change_pct']
    
    # 가격 포맷팅 (소수점 자리수 조정)
    if abs(current_value) < 1:
        current_str = f"{current_value:.4f}"
    elif abs(current_value) < 100:
        current_str = f"{current_value:.2f}"
    else:
        current_str = f"{current_value:,.2f}"
    
    # 등락율 포맷팅
    change_str = f"{change_value:+.2f}%"
    
    # 카드 스타일 컨테이너
    with st.container():
        # 지표 이름
        st.markdown(f"### {name}")
        
        # 현재가와 등락율 표시
        col_price, col_change = st.columns([2, 1])
        with col_price:
            st.markdown(f"**{current_str}**")
        with col_change:
            # 등락율 색상
            if change_value >= 0:
                st.markdown(f'<span style="color: #ef4444;">{change_str}</span>', unsafe_allow_html=True)
            else:
                st.markdown(f'<span style="color: #3b82f6;">{change_str}</span>', unsafe_allow_html=True)
        
        # Sparkline 차트
        if not ticker_data['history'].empty:
            fig = create_sparkline_chart(ticker_data['history'], change_value, name)
            st.plotly_chart(fig, width='stretch', config={'displayModeBar': False})
        else:
            st.info("데이터 없음")

# 사이드바 관리 기능
def render_sidebar():
    """사이드바에 카테고리/티커 관리 UI 렌더링"""
    with st.sidebar:
        st.header("⚙️ 설정")
        
        # 조회 기간 설정
        period_options = {
            "1개월": "1mo",
            "6개월": "6mo",
            "1년": "1y",
            "2년": "2y",
            "5년": "5y",
            "10년": "10y",
            "15년": "15y",
            "20년": "20y"
        }
        
        # 기본값 설정 (첫 실행 시 5년)
        if 'selected_period' not in st.session_state:
            st.session_state.selected_period = "5y"
        
        # 현재 선택된 기간에 맞는 인덱스 찾기
        current_period_value = st.session_state.selected_period
        default_index = list(period_options.values()).index(current_period_value) if current_period_value in period_options.values() else 4  # 5년 (index 4)
        
        selected_period_label = st.selectbox(
            "조회 기간 설정",
            options=list(period_options.keys()),
            index=default_index
        )
        st.session_state.selected_period = period_options[selected_period_label]
        
        st.markdown("---")
        
        # 카테고리 관리 섹션
        st.header("📁 카테고리 관리")
        
        # 새 카테고리 추가
        with st.expander("➕ 새 카테고리 추가"):
            new_category = st.text_input("카테고리 이름", key="new_category_input")
            if st.button("카테고리 추가", key="add_category_btn"):
                if new_category and new_category.strip():
                    if new_category not in st.session_state.market_data:
                        st.session_state.market_data[new_category] = {}
                        # 카테고리 순서에 추가
                        if new_category not in st.session_state.category_order:
                            st.session_state.category_order.append(new_category)
                        # 티커 순서 초기화
                        if new_category not in st.session_state.ticker_order:
                            st.session_state.ticker_order[new_category] = []
                        save_data()
                        st.rerun()
                    else:
                        st.warning("이미 존재하는 카테고리입니다.")
                else:
                    st.warning("카테고리 이름을 입력해주세요.")
        
        # 카테고리 삭제
        with st.expander("🗑️ 카테고리 삭제"):
            if st.session_state.market_data:
                category_to_delete = st.selectbox(
                    "삭제할 카테고리 선택",
                    options=list(st.session_state.market_data.keys()),
                    key="delete_category_select"
                )
                if st.button("카테고리 삭제", key="delete_category_btn"):
                    if category_to_delete in st.session_state.market_data:
                        del st.session_state.market_data[category_to_delete]
                        # 카테고리 순서에서 삭제
                        if category_to_delete in st.session_state.category_order:
                            st.session_state.category_order.remove(category_to_delete)
                        # 티커 순서에서 삭제
                        if category_to_delete in st.session_state.ticker_order:
                            del st.session_state.ticker_order[category_to_delete]
                        # 캐시 클리어
                        st.cache_data.clear()
                        save_data()
                        st.rerun()
            else:
                st.info("삭제할 카테고리가 없습니다.")
        
        st.markdown("---")
        
        # 티커 관리 섹션
        st.header("📊 티커 관리")
        
        # 새 티커 추가
        with st.expander("➕ 새 티커 추가"):
            ticker_name = st.text_input("티커 이름", key="new_ticker_name")
            ticker_symbol = st.text_input("티커 심볼 (예: ^KS11, 005930.KS)", key="new_ticker_symbol")
            
            if st.session_state.market_data:
                selected_category = st.selectbox(
                    "카테고리 선택",
                    options=list(st.session_state.market_data.keys()),
                    key="ticker_category_select"
                )
            else:
                st.warning("먼저 카테고리를 추가해주세요.")
                selected_category = None
            
            if st.button("티커 추가", key="add_ticker_btn"):
                if ticker_name and ticker_symbol and selected_category:
                    if ticker_name not in st.session_state.market_data[selected_category]:
                        st.session_state.market_data[selected_category][ticker_name] = ticker_symbol
                        # 순서에 추가
                        if selected_category not in st.session_state.ticker_order:
                            st.session_state.ticker_order[selected_category] = []
                        st.session_state.ticker_order[selected_category].append(ticker_name)
                        save_data()
                        st.rerun()
                    else:
                        st.warning("이미 존재하는 티커 이름입니다.")
                else:
                    st.warning("모든 필드를 입력해주세요.")
        
        # 티커 삭제
        with st.expander("🗑️ 티커 삭제"):
            if st.session_state.market_data:
                # 카테고리별로 티커 삭제
                for category, tickers in st.session_state.market_data.items():
                    if tickers:  # 티커가 있는 카테고리만 표시
                        st.subheader(f"📂 {category}")
                        # 순서에 따라 티커 표시
                        ticker_list = st.session_state.ticker_order.get(category, list(tickers.keys()))
                        for ticker_name in ticker_list:
                            if ticker_name in tickers:
                                ticker_symbol = tickers[ticker_name]
                                col1, col2 = st.columns([3, 1])
                                with col1:
                                    st.text(f"{ticker_name} ({ticker_symbol})")
                                with col2:
                                    if st.button("삭제", key=f"delete_{category}_{ticker_name}"):
                                        # 티커 삭제
                                        del st.session_state.market_data[category][ticker_name]
                                        # 순서에서도 삭제
                                        if category in st.session_state.ticker_order:
                                            if ticker_name in st.session_state.ticker_order[category]:
                                                st.session_state.ticker_order[category].remove(ticker_name)
                                        # 캐시 클리어 (선택적)
                                        st.cache_data.clear()
                                        save_data()
                                        st.rerun()
            else:
                st.info("삭제할 티커가 없습니다.")
        
        # 티커 검색기 (버튼만)
        st.markdown("---")
        st.header("🔍 티커 검색기")
        
        # 검색기 열림 상태 초기화
        if 'ticker_search_open' not in st.session_state:
            st.session_state.ticker_search_open = False
        
        # 검색기 열기 버튼
        if st.button("🔍 티커 검색기 열기", key="open_ticker_search_btn", use_container_width=True):
            st.session_state.ticker_search_open = True
            st.rerun()
        
        st.markdown("---")
        
        # 카테고리 순서 변경
        st.header("🔄 카테고리 순서 변경")
        with st.expander("📋 카테고리 순서 조정"):
            if st.session_state.market_data and st.session_state.category_order:
                # 현재 순서 가져오기
                current_category_order = st.session_state.category_order.copy()
                # 존재하지 않는 카테고리 제거
                current_category_order = [cat for cat in current_category_order if cat in st.session_state.market_data]
                # 순서에 없는 카테고리 추가
                for cat in st.session_state.market_data.keys():
                    if cat not in current_category_order:
                        current_category_order.append(cat)
                
                st.write("**현재 순서:**")
                for idx, category in enumerate(current_category_order):
                    ticker_count = len(st.session_state.market_data.get(category, {}))
                    st.write(f"{idx + 1}. {category} ({ticker_count}개 티커)")
                
                # 순서 변경 UI
                st.write("**순서 변경:**")
                col_up, col_down = st.columns(2)
                
                with col_up:
                    category_to_move_up = st.selectbox(
                        "위로 이동",
                        options=current_category_order[1:] if len(current_category_order) > 1 else [],
                        key="move_category_up_select"
                    )
                    if st.button("⬆️ 위로", key="move_category_up_btn") and category_to_move_up:
                        idx = current_category_order.index(category_to_move_up)
                        current_category_order[idx], current_category_order[idx - 1] = current_category_order[idx - 1], current_category_order[idx]
                        st.session_state.category_order = current_category_order
                        save_data()
                        st.rerun()
                
                with col_down:
                    category_to_move_down = st.selectbox(
                        "아래로 이동",
                        options=current_category_order[:-1] if len(current_category_order) > 1 else [],
                        key="move_category_down_select"
                    )
                    if st.button("⬇️ 아래로", key="move_category_down_btn") and category_to_move_down:
                        idx = current_category_order.index(category_to_move_down)
                        current_category_order[idx], current_category_order[idx + 1] = current_category_order[idx + 1], current_category_order[idx]
                        st.session_state.category_order = current_category_order
                        save_data()
                        st.rerun()
            else:
                st.info("순서를 변경할 카테고리가 없습니다.")
        
        st.markdown("---")
        
        # 티커 순서 변경
        st.header("🔄 티커 순서 변경")
        with st.expander("📋 티커 순서 조정"):
            if st.session_state.market_data:
                selected_category_for_order = st.selectbox(
                    "카테고리 선택",
                    options=list(st.session_state.market_data.keys()),
                    key="order_category_select"
                )
                
                if selected_category_for_order and selected_category_for_order in st.session_state.market_data:
                    tickers_in_category = st.session_state.market_data[selected_category_for_order]
                    if tickers_in_category:
                        # 현재 순서 가져오기
                        current_order = st.session_state.ticker_order.get(selected_category_for_order, list(tickers_in_category.keys()))
                        # 존재하지 않는 티커 제거
                        current_order = [t for t in current_order if t in tickers_in_category]
                        # 새로운 티커 추가
                        for ticker_name in tickers_in_category.keys():
                            if ticker_name not in current_order:
                                current_order.append(ticker_name)
                        
                        st.write("**현재 순서:**")
                        for idx, ticker_name in enumerate(current_order):
                            st.write(f"{idx + 1}. {ticker_name} ({tickers_in_category[ticker_name]})")
                        
                        # 순서 변경 UI
                        st.write("**순서 변경:**")
                        col_up, col_down = st.columns(2)
                        
                        with col_up:
                            ticker_to_move_up = st.selectbox(
                                "위로 이동",
                                options=current_order[1:] if len(current_order) > 1 else [],
                                key="move_up_select"
                            )
                            if st.button("⬆️ 위로", key="move_up_btn") and ticker_to_move_up:
                                idx = current_order.index(ticker_to_move_up)
                                current_order[idx], current_order[idx - 1] = current_order[idx - 1], current_order[idx]
                                st.session_state.ticker_order[selected_category_for_order] = current_order
                                save_data()
                                st.rerun()
                        
                        with col_down:
                            ticker_to_move_down = st.selectbox(
                                "아래로 이동",
                                options=current_order[:-1] if len(current_order) > 1 else [],
                                key="move_down_select"
                            )
                            if st.button("⬇️ 아래로", key="move_down_btn") and ticker_to_move_down:
                                idx = current_order.index(ticker_to_move_down)
                                current_order[idx], current_order[idx + 1] = current_order[idx + 1], current_order[idx]
                                st.session_state.ticker_order[selected_category_for_order] = current_order
                                save_data()
                                st.rerun()
                    else:
                        st.info("이 카테고리에 티커가 없습니다.")
            else:
                st.info("순서를 변경할 티커가 없습니다.")
        
        st.markdown("---")
        
        # 디버깅 정보 섹션
        st.header("🔍 디버깅 정보")
        with st.expander("📊 데이터 소스 상태"):
            st.write("**트레이딩뷰 상태:**")
            st.write(f"- TV_AVAILABLE: `{TV_AVAILABLE}`")
            st.write(f"- tv 객체: `{'초기화됨 ✅' if tv is not None else 'None ❌'}`")
            
            # 트레이딩뷰 테스트 버튼
            if st.button("🔬 트레이딩뷰 테스트", key="test_tradingview_btn"):
                if tv is not None and TV_AVAILABLE:
                    try:
                        interval_val = Interval.in_daily if hasattr(Interval, 'in_daily') and Interval.in_daily is not None else None
                        if interval_val is None:
                            st.warning("⚠️ Interval.in_daily를 사용할 수 없습니다")
                        else:
                            test_df = tv.get_hist(
                                symbol='KR10Y',
                                exchange='TVC',
                                interval=interval_val,
                                n_bars=10
                            )
                            if test_df is not None and not test_df.empty:
                                st.success(f"✅ 트레이딩뷰 작동 중! (데이터 {len(test_df)}행)")
                                st.dataframe(test_df.head())
                                st.write(f"**컬럼명:** {list(test_df.columns)}")
                                st.write(f"**인덱스 타입:** {type(test_df.index)}")
                            else:
                                st.warning("⚠️ 데이터가 비어있습니다")
                    except Exception as e:
                        st.error(f"❌ 오류: {str(e)}")
                        import traceback
                        st.code(traceback.format_exc())
                else:
                    st.error("❌ tv 객체가 초기화되지 않았습니다")
                    if not TV_AVAILABLE:
                        st.info("💡 tvdatafeed 모듈을 설치해야 합니다: `pip install git+https://github.com/rongardF/tvdatafeed.git`")
            
            st.write("---")
            st.write("**FinanceDataReader 상태:**")
            try:
                import FinanceDataReader as fdr
                st.write("✅ FDR 사용 가능")
            except ImportError:
                st.write("❌ FDR을 찾을 수 없습니다")
            
            st.write("**yfinance 상태:**")
            try:
                import yfinance as yf
                st.write("✅ yfinance 사용 가능")
            except ImportError:
                st.write("❌ yfinance를 찾을 수 없습니다")
            
            st.write("---")
            st.write("**구글 시트 연결:**")
            if gsheets_client is not None:
                st.write("✅ 연결됨")
            else:
                st.write("❌ 연결 안 됨 (서비스 계정 설정 필요)")

# 메인 대시보드
def render_ticker_search_modal():
    """티커 검색기 모달 UI 렌더링"""
    if not st.session_state.get('ticker_search_open', False):
        return
    
    # 모달 스타일
    st.markdown("""
    <style>
    .ticker-search-modal {
        position: fixed;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        background-color: #1e1e1e;
        border: 2px solid #333;
        border-radius: 10px;
        padding: 2rem;
        z-index: 1000;
        width: 80%;
        max-width: 800px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.5);
    }
    </style>
    """, unsafe_allow_html=True)
    
    # 모달 컨테이너
    with st.container():
        col1, col2 = st.columns([10, 1])
        with col1:
            st.header("🔍 티커 검색기")
        with col2:
            if st.button("✖️", key="close_search_modal_btn"):
                st.session_state.ticker_search_open = False
                st.rerun()
        
        st.markdown("---")
        
        # 데이터 소스 선택
        search_source = st.selectbox(
            "데이터 소스 선택",
            options=["yfinance", "FinanceDataReader (한국)", "TradingView"],
            key="ticker_search_source_modal"
        )
        
        # 검색어 입력
        search_query = st.text_input(
            "검색어 입력 (예: 유가, 원유, 금, 삼성, 일본국채 등)",
            key="ticker_search_query_modal",
            placeholder="검색어를 입력하세요..."
        )
        
        # 프롬프트 생성 버튼
        if st.button("📝 AI 프롬프트 생성", key="generate_prompt_btn", use_container_width=True):
            if search_query:
                prompt = generate_ticker_search_prompt(search_query, search_source)
                st.session_state['generated_prompt'] = prompt
                st.session_state['prompt_search_query'] = search_query
                st.session_state['prompt_data_source'] = search_source
            else:
                st.warning("검색어를 입력해주세요.")
        
        # 생성된 프롬프트 표시
        if 'generated_prompt' in st.session_state and st.session_state['generated_prompt']:
            st.markdown("---")
            st.markdown("### 🤖 AI 프롬프트 (Gemini/ChatGPT에 붙여넣기)")
            st.info(f"**검색어**: {st.session_state['prompt_search_query']} | **데이터 소스**: {st.session_state['prompt_data_source']}")
            
            # 프롬프트 코드 블록
            st.code(st.session_state['generated_prompt'], language=None)
            
            # 복사 안내
            st.success("💡 위 프롬프트를 복사하여 Gemini 또는 ChatGPT에 붙여넣으세요!")
            
            # 프롬프트 초기화 버튼
            if st.button("🔄 새 프롬프트 생성", key="reset_prompt_btn"):
                if 'generated_prompt' in st.session_state:
                    del st.session_state['generated_prompt']
                st.rerun()

def main():
    # 초기 데이터 설정
    init_market_data()
    
    # 사이드바 렌더링
    render_sidebar()
    
    # 티커 검색기 모달 렌더링 (열려있을 때만)
    render_ticker_search_modal()
    
    # 헤더
    st.title("📊 실시간 시황 대시보드")
    
    # 마지막 업데이트 시간
    kst = pytz.timezone('Asia/Seoul')
    update_time = datetime.now(kst).strftime("%Y-%m-%d %H:%M:%S KST")
    period_label = [k for k, v in {
        "1개월": "1mo", "6개월": "6mo", "1년": "1y", 
        "2년": "2y", "5년": "5y", "10년": "10y",
        "15년": "15y", "20년": "20y"
    }.items() if v == st.session_state.selected_period][0]
    st.markdown(f'<p class="update-time">마지막 업데이트: {update_time} | 조회 기간: {period_label}</p>', unsafe_allow_html=True)
    
    st.markdown("---")
    
    # 카테고리별로 데이터 로딩 및 표시
    if not st.session_state.market_data:
        st.info("📝 사이드바에서 카테고리와 티커를 추가해주세요.")
    else:
        # 모든 티커 데이터 수집
        all_ticker_data = {}
        for category, tickers in st.session_state.market_data.items():
            for ticker_name, ticker_symbol in tickers.items():
                all_ticker_data[(category, ticker_name)] = ticker_symbol
        
        # 데이터 로딩
        data = {}
        with st.spinner("데이터를 불러오는 중..."):
            for (category, ticker_name), ticker_symbol in all_ticker_data.items():
                data[(category, ticker_name)] = get_ticker_data(ticker_symbol, period=st.session_state.selected_period)
        
        # 카테고리별로 섹션 나누어 표시 (순서대로)
        num_columns = 3
        
        # 카테고리 순서에 따라 표시
        category_list = [cat for cat in st.session_state.category_order if cat in st.session_state.market_data]
        # 순서에 없는 카테고리 추가
        for cat in st.session_state.market_data.keys():
            if cat not in category_list:
                category_list.append(cat)
        
        for category in category_list:
            tickers = st.session_state.market_data[category]
            if tickers:  # 티커가 있는 카테고리만 표시
                # 카테고리 헤더
                st.markdown(f"## 📂 {category}")
                
                # 티커 순서에 따라 표시
                ticker_list = st.session_state.ticker_order.get(category, list(tickers.keys()))
                # 존재하지 않는 티커 제거
                ticker_list = [t for t in ticker_list if t in tickers]
                # 새로운 티커 추가
                for ticker_name in tickers.keys():
                    if ticker_name not in ticker_list:
                        ticker_list.append(ticker_name)
                
                # 3열 그리드 레이아웃
                for i in range(0, len(ticker_list), num_columns):
                    cols = st.columns(num_columns)
                    
                    for j, col in enumerate(cols):
                        idx = i + j
                        if idx < len(ticker_list):
                            ticker_name = ticker_list[idx]
                            if ticker_name in tickers:  # 안전성 체크
                                ticker_data = data.get((category, ticker_name))
                                if ticker_data:
                                    with col:
                                        render_ticker_card(ticker_name, tickers[ticker_name], ticker_data)
                
                st.markdown("---")

if __name__ == "__main__":
    main()
