import os
import sqlite3
import pandas as pd
import streamlit as st
from datetime import datetime
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from urllib.parse import urljoin

# 設定頁面
st.set_page_config(
    page_title="中正大學企管系 - 師資瀏覽量數據儀表板",
    page_icon="📊",
    layout="wide"
)

DB_NAME = "ccu_faculty.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_views (
            record_date TEXT,
            teacher_name TEXT,
            research_field TEXT,
            total_views INTEGER,
            daily_growth INTEGER,
            profile_url TEXT,
            PRIMARY KEY (record_date, teacher_name)
        )
    ''')
    conn.commit()
    conn.close()

def get_yesterday_views(cursor, teacher_name):
    cursor.execute('''
        SELECT total_views FROM daily_views 
        WHERE teacher_name = ? 
        ORDER BY record_date DESC LIMIT 1
    ''', (teacher_name,))
    result = cursor.fetchone()
    return result[0] if result else None

# 自動爬蟲邏輯 (設定 ttl=86400 秒，代表 24 小時自動執行一次)
@st.cache_data(ttl=86400, show_spinner="☁️ 雲端系統正在抓取最新數據，請稍候約 1~2 分鐘...")
def auto_run_crawler():
    init_db()
    today_str = datetime.now().strftime("%Y-%m-%d")
    base_url = "https://busadm.ccu.edu.tw/p/412-1248-3236.php?Lang=zh-tw"
    
    # 安裝 Playwright 瀏覽器元件（若未安裝）
    os.system("playwright install chromium")
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(base_url)
            page.wait_for_timeout(2000)
            
            soup = BeautifulSoup(page.content(), "html.parser")
            links = soup.select("a[href*='405-1248']")
            
            teacher_links = []
            for a in links:
                href = a.get("href", "")
                name = a.get_text(strip=True)
                if name and name not in ["專任教師", "兼任教師", "首頁", "聯絡我們", "更多"] and len(name) <= 6:
                    full_url = urljoin(base_url, href)
                    if (name, full_url) not in teacher_links:
                        teacher_links.append((name, full_url))

            for name, link in teacher_links:
                research_interests = "未提供"
                views_count = 0
                try:
                    page.goto(link)
                    page.wait_for_selector(".PtStatistic i", timeout=5000)
                    detail_soup = BeautifulSoup(page.content(), "html.parser")
                    
                    pt_i = detail_soup.select_one(".PtStatistic i")
                    if pt_i and pt_i.get_text(strip=True).isdigit():
                        views_count = int(pt_i.get_text(strip=True))

                    for h3 in detail_soup.find_all("h3"):
                        if "研究領域" in h3.get_text() or "專長" in h3.get_text():
                            next_div = h3.find_next_sibling("div")
                            if next_div and next_div.get_text(strip=True):
                                research_interests = next_div.get_text(" ", strip=True)
                            break
                except:
                    pass

                prev_views = get_yesterday_views(cursor, name)
                daily_growth = (views_count - prev_views) if (prev_views is not None and views_count >= prev_views) else 0

                cursor.execute('''
                    INSERT OR REPLACE INTO daily_views 
                    (record_date, teacher_name, research_field, total_views, daily_growth, profile_url)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (today_str, name, research_interests, views_count, daily_growth, link))

            conn.commit()
            browser.close()
    except Exception as e:
        st.error(f"自動更新過程發生異常: {e}")
    finally:
        conn.close()
    
    return True

# --- 網頁主要 UI ---
st.title("📊 中正大學企管系 - 師資瀏覽數據視覺化看板")
st.markdown("本系統由雲端自動定時更新，提供即時師資瀏覽量與熱門研究領域排序分析。")

# 觸發雲端自動更新
auto_run_crawler()

def load_data():
    conn = sqlite3.connect(DB_NAME)
    date_df = pd.read_sql_query("SELECT MAX(record_date) as max_date FROM daily_views", conn)
    latest_date = date_df['max_date'].iloc[0]
    
    if not latest_date:
        conn.close()
        return None, None
        
    query = f"""
        SELECT 
            teacher_name AS "教授姓名",
            research_field AS "研究領域",
            total_views AS "總瀏覽數",
            daily_growth AS "每日新增瀏覽數",
            profile_url AS "個人頁面連結",
            record_date AS "更新日期"
        FROM daily_views
        WHERE record_date = '{latest_date}'
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df, latest_date

df, latest_date = load_data()

if df is None or df.empty:
    st.warning("⚠️ 資料庫初始化中，請重新整理頁面...")
else:
    st.success(f"📅 最新數據更新日期：**{latest_date}** （共 {len(df)} 位師資，全自動雲端更新）")

    st.sidebar.header("🔍 篩選與排序設定")
    sort_option = st.sidebar.radio(
        "選擇排序方式：",
        ("依總瀏覽數排序 (高 → 低)", "依每日新增瀏覽數排序 (高 → 低)", "依教授姓名排序")
    )

    search_keyword = st.sidebar.text_input("搜尋教授姓名或研究領域：", "")

    if search_keyword:
        df = df[
            df["教授姓名"].str.contains(search_keyword, case=False, na=False) |
            df["研究領域"].str.contains(search_keyword, case=False, na=False)
        ]

    if sort_option == "依總瀏覽數排序 (高 → 低)":
        df = df.sort_values(by="總瀏覽數", ascending=False)
    elif sort_option == "依每日新增瀏覽數排序 (高 → 低)":
        df = df.sort_values(by="每日新增瀏覽數", ascending=False)
    else:
        df = df.sort_values(by="教授姓名", ascending=True)

    col1, col2, col3 = st.columns(3)
    with col1:
        top_view = df.sort_values(by="總瀏覽數", ascending=False).iloc[0]
        st.metric("最高總瀏覽數教授", top_view["教授姓名"], f"{top_view['總瀏覽數']:,} 次")
    with col2:
        top_growth = df.sort_values(by="每日新增瀏覽數", ascending=False).iloc[0]
        st.metric("今日新增最多教授", top_growth["教授姓名"], f"+{top_growth['每日新增瀏覽數']} 次")
    with col3:
        avg_views = int(df["總瀏覽數"].mean())
        st.metric("平均教授總瀏覽數", f"{avg_views:,} 次")

    st.divider()

    st.subheader("📈 Top 10 熱門教授瀏覽量圖表")
    chart_type = st.radio("選擇圖表指標：", ("總瀏覽數 Top 10", "每日新增瀏覽數 Top 10"), horizontal=True)
    
    if chart_type == "總瀏覽數 Top 10":
        top10_df = df.sort_values(by="總瀏覽數", ascending=False).head(10)
        st.bar_chart(data=top10_df, x="教授姓名", y="總瀏覽數", color="#1f77b4")
    else:
        top10_df = df.sort_values(by="每日新增瀏覽數", ascending=False).head(10)
        st.bar_chart(data=top10_df, x="教授姓名", y="每日新增瀏覽數", color="#ff7f0e")

    st.divider()

    st.subheader("📋 師資資料列表")
    st.dataframe(
        df,
        column_config={
            "個人頁面連結": st.column_config.LinkColumn("個人頁面連結", display_text="前往頁面"),
            "總瀏覽數": st.column_config.NumberColumn("總瀏覽數", format="%d"),
            "每日新增瀏覽數": st.column_config.NumberColumn("每日新增瀏覽數", format="+%d"),
        },
        use_container_width=True,
        hide_index=True
    )