import sqlite3
import pandas as pd
import streamlit as st

# 設定網頁頁面標題與寬度
st.set_page_config(
    page_title="中正大學企管系 - 師資瀏覽量數據儀表板",
    page_icon="📊",
    layout="wide"
)

DB_NAME = "ccu_faculty.db"

def load_data():
    """從 SQLite 讀取最新日期的爬蟲數據"""
    conn = sqlite3.connect(DB_NAME)
    
    # 取得最新紀錄日期
    date_df = pd.read_sql_query("SELECT MAX(record_date) as max_date FROM daily_views", conn)
    latest_date = date_df['max_date'].iloc[0]
    
    if not latest_date:
        conn.close()
        return None, None
        
    # 讀取該最新日期的所有教授資料
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

# --- 主畫面標題 ---
st.title("📊 中正大學企管系 - 師資瀏覽數據視覺化看板")
st.markdown("本系統每日自動更新數據，提供師資瀏覽量與熱門研究領域排序分析。")

df, latest_date = load_data()

if df is None or df.empty:
    st.warning("⚠️ 目前資料庫中尚無資料，請先執行 `db_crawler.py` 爬取數據！")
else:
    st.success(f"📅 最新數據更新日期：**{latest_date}** （共 {len(df)} 位師資）")

    # --- 控制區：控制卡片與篩選器 ---
    st.sidebar.header("🔍 篩選與排序設定")
    
    # 1. 排序選擇
    sort_option = st.sidebar.radio(
        "選擇排序方式：",
        ("依總瀏覽數排序 (高 → 低)", "依每日新增瀏覽數排序 (高 → 低)", "依教授姓名排序")
    )

    # 2. 搜尋關鍵字
    search_keyword = st.sidebar.text_input("搜尋教授姓名或研究領域：", "")

    # 依條件篩選
    if search_keyword:
        df = df[
            df["教授姓名"].str.contains(search_keyword, case=False, na=False) |
            df["研究領域"].str.contains(search_keyword, case=False, na=False)
        ]

    # 依條件排序
    if sort_option == "依總瀏覽數排序 (高 → 低)":
        df = df.sort_values(by="總瀏覽數", ascending=False)
    elif sort_option == "依每日新增瀏覽數排序 (高 → 低)":
        df = df.sort_values(by="每日新增瀏覽數", ascending=False)
    else:
        df = df.sort_values(by="教授姓名", ascending=True)

    # --- 關鍵指標展示卡片 ---
    col1, col2, col3 = st.columns(3)
    with col1:
        top_view_teacher = df.sort_values(by="總瀏覽數", ascending=False).iloc[0]
        st.metric("最高總瀏覽數教授", top_view_teacher["教授姓名"], f"{top_view_teacher['總瀏覽數']:,} 次")
    with col2:
        top_growth_teacher = df.sort_values(by="每日新增瀏覽數", ascending=False).iloc[0]
        st.metric("今日新增最多教授", top_growth_teacher["教授姓名"], f"+{top_growth_teacher['每日新增瀏覽數']} 次")
    with col3:
        avg_views = int(df["總瀏覽數"].mean())
        st.metric("平均教授總瀏覽數", f"{avg_views:,} 次")

    st.divider()

    # --- 視覺化圖表區 ---
    st.subheader("📈 Top 10 熱門教授瀏覽量圖表")
    
    chart_type = st.radio("選擇圖表指標：", ("總瀏覽數 Top 10", "每日新增瀏覽數 Top 10"), horizontal=True)
    
    if chart_type == "總瀏覽數 Top 10":
        top10_df = df.sort_values(by="總瀏覽數", ascending=False).head(10)
        st.bar_chart(data=top10_df, x="教授姓名", y="總瀏覽數", color="#1f77b4")
    else:
        top10_df = df.sort_values(by="每日新增瀏覽數", ascending=False).head(10)
        st.bar_chart(data=top10_df, x="教授姓名", y="每日新增瀏覽數", color="#ff7f0e")

    st.divider()

    # --- 詳細資料表格區 ---
    st.subheader("📋 師資資料列表（可直接點擊標頭重新排序）")
    
    # 呈現點擊可前往個人頁面的超連結
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