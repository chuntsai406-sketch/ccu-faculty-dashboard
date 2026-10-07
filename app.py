import os
import sqlite3
import subprocess
import pandas as pd
import streamlit as st
from datetime import datetime
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from urllib.parse import urljoin

st.set_page_config(
    page_title="中正大學企管系 - 師資瀏覽量數據儀表板",
    page_icon="📊",
    layout="wide"
)

DB_NAME = "ccu_faculty.db"

# 建立獨立連線（防鎖定 WAL 模式）
def get_db_connection():
    conn = sqlite3.connect(DB_NAME, timeout=60.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_db():
    conn = get_db_connection()
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

def fetch_and_update_db():
    init_db()
    today_str = datetime.now().strftime("%Y-%m-%d")
    base_url = "https://busadm.ccu.edu.tw/p/412-1248-3236.php?Lang=zh-tw"
    
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True)
    except Exception:
        pass

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_extra_http_headers({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
            page.goto(base_url, timeout=60000)
            page.wait_for_timeout(3000)
            
            soup = BeautifulSoup(page.content(), "html.parser")
            links = soup.select("a[href*='405-1248'], a[href*='404-1248']")
            
            teacher_links = []
            for a in links:
                href = a.get("href", "")
                name = a.get_text(strip=True)
                if name and name not in ["專任教師", "兼任教師", "首頁", "聯絡我們", "更多", "系主任"] and len(name) <= 6:
                    full_url = urljoin(base_url, href)
                    if (name, full_url) not in teacher_links:
                        teacher_links.append((name, full_url))

            for name, link in teacher_links:
                research_interests = "未提供"
                views_count = 0
                try:
                    page.goto(link, timeout=60000, wait_until="networkidle")
                    page.wait_for_selector(".PtStatistic i", timeout=10000)
                    page.wait_for_timeout(1500)
                    
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
                except Exception:
                    pass

                if views_count > 0:
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    try:
                        # 查之前的歷史紀錄
                        cursor.execute('''
                            SELECT total_views FROM daily_views 
                            WHERE teacher_name = ? AND record_date != ?
                            ORDER BY record_date DESC LIMIT 1
                        ''', (name, today_str))
                        prev_res = cursor.fetchone()
                        prev_views = prev_res[0] if prev_res else None
                        
                        daily_growth = (views_count - prev_views) if (prev_views is not None and views_count >= prev_views) else 0

                        # 強制將今日同名舊紀錄刪除，覆蓋寫入最新高數值
                        cursor.execute('''
                            DELETE FROM daily_views WHERE record_date = ? AND teacher_name = ?
                        ''', (today_str, name))
                        
                        cursor.execute('''
                            INSERT INTO daily_views 
                            (record_date, teacher_name, research_field, total_views, daily_growth, profile_url)
                            VALUES (?, ?, ?, ?, ?, ?)
                        ''', (today_str, name, research_interests, views_count, daily_growth, link))
                        conn.commit()
                    finally:
                        conn.close()

            browser.close()
    except Exception as e:
        st.error(f"更新過程發生異常: {e}")
    return True

@st.cache_data(ttl=86400, show_spinner="☁️ 雲端系統正在檢查數據更新，請稍候...")
def auto_run_crawler():
    return fetch_and_update_db()

# --- UI 介面 ---
st.title("📊 中正大學企管系 - 師資瀏覽數據視覺化看板")
st.markdown("本系統由雲端自動定時更新，提供即時師資瀏覽量與熱門研究領域排序分析。")

st.sidebar.header("⚙️ 系統操作與控制")

if st.sidebar.button("🔄 立即重新抓取最新數據"):
    with st.spinner("⏳ 正在即時連線中正企管系官網抓取，請稍候約 1~2 分鐘..."):
        st.cache_data.clear()
        fetch_and_update_db()
        st.success("✅ 最新數據已成功由官網即時抓取並更新完畢！")
        st.rerun()

auto_run_crawler()

def load_data():
    conn = get_db_connection()
    try:
        date_df = pd.read_sql_query("SELECT MAX(record_date) as max_date FROM daily_views", conn)
        latest_date = date_df['max_date'].iloc[0]
        
        if not latest_date:
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
        return df, latest_date
    finally:
        conn.close()

df, latest_date = load_data()

if df is None or df.empty:
    st.warning("⚠️ 資料庫初始化中，請點擊左側重新抓取數據...")
else:
    st.success(f"📅 最新數據更新日期：**{latest_date}** （共 {len(df)} 位師資）")

    st.sidebar.subheader("🔍 篩選與排序設定")
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

    # 列表預設排序
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
    
    # 圖表呈現：精確由高到低排列
    if chart_type == "總瀏覽數 Top 10":
        top10_df = df.sort_values(by="總瀏覽數", ascending=False).head(10)
        # Streamlit 柱狀圖由上至下顯示，Reverse 確保高的排在最上面/左邊
        chart_data = top10_df.sort_values(by="總瀏覽數", ascending=True)
        st.bar_chart(data=chart_data, x="教授姓名", y="總瀏覽數", color="#1f77b4")
    else:
        top10_df = df.sort_values(by="每日新增瀏覽數", ascending=False).head(10)
        chart_data = top10_df.sort_values(by="每日新增瀏覽數", ascending=True)
        st.bar_chart(data=chart_data, x="教授姓名", y="每日新增瀏覽數", color="#ff7f0e")

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
