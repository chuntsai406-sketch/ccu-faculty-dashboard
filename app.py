import os
import sqlite3
import pandas as pd
import streamlit as st
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import plotly.express as px
from playwright.sync_api import sync_playwright

st.set_page_config(
    page_title="中正大學企管系 - 師資瀏覽量數據儀表板",
    page_icon="📊",
    layout="wide"
)

DB_NAME = "ccu_faculty.db"

# 自動檢查並安裝 Playwright Chromium 瀏覽器
def ensure_playwright_browser():
    try:
        os.system("playwright install chromium")
    except Exception as e:
        st.write(f"Playwright 瀏覽器初始化提醒: {e}")

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

def parse_teacher_with_playwright(p, link):
    """使用真實 Chromium 瀏覽器開啟網頁，等待 JS 載入後抓取最新瀏覽數"""
    views = 0
    research_field = "未提供"
    
    browser = None
    try:
        # 啟動 Headless Chrome
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        page = browser.new_page()
        
        # 進入頁面，等待網路與DOM加載
        page.goto(link, timeout=20000, wait_until="domcontentloaded")
        
        # 等待數字標籤出現（最多等 5 秒）
        try:
            page.wait_for_selector(".PtStatistic", timeout=5000)
        except Exception:
            pass
            
        # 取得渲染完成後的完整 HTML
        content = page.content()
        soup = BeautifulSoup(content, "html.parser")
        
        # 1. 抓取瀏覽數
        pt_stat = soup.select_one(".PtStatistic i, .PtStatistic span, .PtStatistic")
        if pt_stat:
            v_text = pt_stat.get_text(strip=True).replace(",", "")
            import re
            nums = re.findall(r'\d+', v_text)
            if nums:
                views = int(nums[0])

        # 2. 抓取研究領域
        for h3 in soup.find_all(["h3", "h4", "strong"]):
            text = h3.get_text(strip=True)
            if "研究領域" in text or "專長" in text:
                next_div = h3.find_next_sibling(["div", "p", "span"])
                if next_div and next_div.get_text(strip=True):
                    research_field = next_div.get_text(" ", strip=True)
                break

    except Exception as e:
        pass
    finally:
        if browser:
            browser.close()

    return views, research_field

def fetch_and_update_all_playwright():
    init_db()
    ensure_playwright_browser()
    
    today_str = datetime.now().strftime("%Y-%m-%d")
    base_url = "https://busadm.ccu.edu.tw/p/412-1248-3236.php?Lang=zh-tw"
    logs = []
    
    import requests
    # 建立基礎連結圖
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        })
        res = session.get(base_url, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")
        links = soup.select("a[href*='405-1248'], a[href*='404-1248']")
        
        teacher_map = {}
        for a in links:
            href = a.get("href", "")
            name = a.get_text(strip=True)
            if name and name not in ["專任教師", "兼任教師", "首頁", "聯絡我們", "更多", "系主任"] and len(name) <= 5:
                full_url = urljoin(base_url, href)
                if name not in teacher_map:
                    teacher_map[name] = []
                if full_url not in teacher_map[name]:
                    teacher_map[name].append(full_url)

        logs.append(f"🔍 [Playwright 模式] 成功找到 {len(teacher_map)} 位教師，開始動態渲染抓取...")

        with sync_playwright() as p:
            success_count = 0
            for name, url_list in teacher_map.items():
                max_views = 0
                best_field = "未提供"
                best_link = url_list[0]

                # 遍歷該老師頁面，使用真實瀏覽器抓取
                for link in url_list:
                    views, field = parse_teacher_with_playwright(p, link)
                    if views > max_views:
                        max_views = views
                        best_link = link
                    if field != "未提供":
                        best_field = field

                if max_views > 0:
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
                        
                        daily_growth = (max_views - prev_views) if (prev_views is not None and max_views >= prev_views) else 0

                        cursor.execute('DELETE FROM daily_views WHERE record_date = ? AND teacher_name = ?', (today_str, name))
                        cursor.execute('''
                            INSERT INTO daily_views 
                            (record_date, teacher_name, research_field, total_views, daily_growth, profile_url)
                            VALUES (?, ?, ?, ?, ?, ?)
                        ''', (today_str, name, best_field, max_views, daily_growth, best_link))
                        conn.commit()
                        success_count += 1
                        logs.append(f"✅ {name}: 即時動態數字 {max_views}")
                    finally:
                        conn.close()
                else:
                    logs.append(f"❌ {name}: 未能渲染出數字")

            logs.append(f"🎉 渲染完畢！成功更新 {success_count} 位教師數據。")

    except Exception as e:
        logs.append(f"⚠️ 爬蟲執行發生例外: {str(e)}")

    return logs

# --- UI 介面 ---
st.title("📊 中正大學企管系 - 師資瀏覽數據視覺化看板")
st.markdown("本系統採用 Playwright 動態瀏覽器技術，即時模擬真實使用者獲取官網最新渲染數據。")

st.sidebar.header("⚙️ 系統操作與控制")

if st.sidebar.button("🔄 啟動動態瀏覽器 (Playwright) 抓取最新數據"):
    st.cache_data.clear()
    with st.spinner("⏳ 正在背景啟動 Chrome 瀏覽器渲染中正企管系官網，請稍候約 30-60 秒..."):
        logs = fetch_and_update_all_playwright()
        st.success("✅ 即時動態數據抓取完成！")
        with st.expander("🔍 點此查看即時抓取日誌 (Playwright Debug Logs)", expanded=True):
            for log in logs:
                st.write(log)
        st.rerun()

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
    st.warning("⚠️ 資料庫尚無數據，請點擊左側「啟動動態瀏覽器 (Playwright) 抓取最新數據」...")
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
