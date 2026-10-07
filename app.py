import sqlite3
import requests
import pandas as pd
import streamlit as st
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import plotly.express as px

st.set_page_config(
    page_title="中正大學企管系 - 師資瀏覽量數據儀表板",
    page_icon="📊",
    layout="wide"
)

DB_NAME = "ccu_faculty.db"

def get_db_connection():
    conn = sqlite3.connect(DB_NAME, timeout=30.0)
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

def fast_fetch_and_update():
    init_db()
    today_str = datetime.now().strftime("%Y-%m-%d")
    base_url = "https://busadm.ccu.edu.tw/p/412-1248-3236.php?Lang=zh-tw"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    session = requests.Session()
    session.headers.update(headers)

    try:
        res = session.get(base_url, timeout=15)
        soup = BeautifulSoup(res.text, "html.parser")
        
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
                # 抓取個人頁面
                page_res = session.get(link, timeout=15)
                page_soup = BeautifulSoup(page_res.text, "html.parser")

                for h3 in page_soup.find_all("h3"):
                    if "研究領域" in h3.get_text() or "專長" in h3.get_text():
                        next_div = h3.find_next_sibling("div")
                        if next_div and next_div.get_text(strip=True):
                            research_interests = next_div.get_text(" ", strip=True)
                        break

                # 透過系統 API 查詢最新瀏覽數字
                filename = link.split("/")[-1].split("?")[0]
                parts = filename.replace(".php", "").split("-")
                if len(parts) >= 3:
                    pt_id = parts[2].split(",")[0]
                    api_url = f"https://busadm.ccu.edu.tw/app/index.php?Action=mobileptstatistic&Op=getptsimplecount&pt_id={pt_id}"
                    api_res = session.get(api_url, timeout=10)
                    raw_views = api_res.text.strip().replace('"', '').replace(',', '')
                    if raw_views.isdigit():
                        views_count = int(raw_views)

                # 回退方案
                if views_count == 0:
                    pt_i = page_soup.select_one(".PtStatistic i")
                    if pt_i:
                        v_str = pt_i.get_text(strip=True).replace(",", "")
                        if v_str.isdigit():
                            views_count = int(v_str)

            except Exception:
                pass

            if views_count > 0:
                conn = get_db_connection()
                cursor = conn.cursor()
                try:
                    cursor.execute('''
                        SELECT total_views FROM daily_views 
                        WHERE teacher_name = ? AND record_date != ?
                        ORDER BY record_date DESC LIMIT 1
                    ''', (name, today_str))
                    prev_res = cursor.fetchone()
                    prev_views = prev_res[0] if prev_res else None
                    
                    daily_growth = (views_count - prev_views) if (prev_views is not None and views_count >= prev_views) else 0

                    # 刪除舊資料並寫入最新實時資料
                    cursor.execute('DELETE FROM daily_views WHERE record_date = ? AND teacher_name = ?', (today_str, name))
                    cursor.execute('''
                        INSERT INTO daily_views 
                        (record_date, teacher_name, research_field, total_views, daily_growth, profile_url)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (today_str, name, research_interests, views_count, daily_growth, link))
                    conn.commit()
                finally:
                    conn.close()

    except Exception as e:
        st.error(f"更新異常: {e}")
    return True

@st.cache_data(ttl=86400)
def auto_run_crawler():
    return fast_fetch_and_update()

# --- UI 介面 ---
st.title("📊 中正大學企管系 - 師資瀏覽數據視覺化看板")
st.markdown("本系統由雲端自動定時更新，提供即時師資瀏覽量與熱門研究領域排序分析。")

st.sidebar.header("⚙️ 系統操作與控制")

if st.sidebar.button("🔄 立即重新抓取最新數據"):
    with st.spinner("⏳ 正在即時抓取中正企管系官網數據..."):
        st.cache_data.clear()
        fast_fetch_and_update()
        st.success("✅ 最新數據已成功更新完畢！")
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
    st.warning("⚠️ 資料庫初始化中，請點擊左側按鈕重新抓取數據...")
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

    # 資料表排序
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
    
    # 解決【問題 1】：使用 Plotly 顯式指定 categoryorder="total descending"，確保絕對由高到低排列
    if chart_type == "總瀏覽數 Top 10":
        chart_df = df.sort_values(by="總瀏覽數", ascending=False).head(10)
        fig = px.bar(
            chart_df, 
            x="教授姓名", 
            y="總瀏覽數",
            text="總瀏覽數",
            color_discrete_sequence=["#1f77b4"]
        )
        fig.update_layout(xaxis={'categoryorder': 'total descending'})
        fig.update_traces(texttemplate='%{text}', textposition='outside')
        st.plotly_chart(fig, use_container_width=True)
    else:
        chart_df = df.sort_values(by="每日新增瀏覽數", ascending=False).head(10)
        fig = px.bar(
            chart_df, 
            x="教授姓名", 
            y="每日新增瀏覽數",
            text="每日新增瀏覽數",
            color_discrete_sequence=["#ff7f0e"]
        )
        fig.update_layout(xaxis={'categoryorder': 'total descending'})
        fig.update_traces(texttemplate='%{text}', textposition='outside')
        st.plotly_chart(fig, use_container_width=True)

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
