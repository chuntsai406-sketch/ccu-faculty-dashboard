import sqlite3
import re
from datetime import datetime
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

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

def extract_clean_research_field(soup):
    """精準提取專長領域，排除頂部導覽列與底部頁尾雜訊"""
    junk_keywords = [
        "瀏覽數", "友善列印", "JavaScript", "列印", "分享", "版權所有", 
        "系統維護", "English", "聯絡我們", "::: ", "網站導覽", "首頁"
    ]
    
    # 限制只在主要內容區域搜尋，避開頂部頁首與頁尾
    main_area = soup.select_one(".grid-container, .main-content, #Dyn_2_2, div[class*='content']")
    target_soup = main_area if main_area else soup

    # 策略 1：優先從表格 <tr> 中尋找
    for tr in target_soup.find_all("tr"):
        tr_text = tr.get_text(strip=True)
        if any(k in tr_text for k in ["專長", "研究領域", "授課領域", "研究方向"]):
            tds = tr.find_all(["td", "th"])
            if len(tds) >= 2:
                val = tds[-1].get_text(" ", strip=True)
                if val and not any(j in val for j in junk_keywords):
                    return val

    # 策略 2：尋找帶有專長關鍵字的標籤與下一個兄弟節點
    for elem in target_soup.find_all(["h3", "h4", "strong", "b", "div", "p", "td"]):
        text = elem.get_text(strip=True)
        if any(k in text for k in ["專長", "研究領域", "授課領域", "研究方向"]) and len(text) < 15:
            # 嘗試抓取下一個同級標籤
            nxt = elem.find_next_sibling(["div", "p", "span", "td", "ul"])
            if not nxt:
                nxt = elem.parent.find_next_sibling()
            if nxt:
                val = nxt.get_text(" ", strip=True)
                if val and len(val) >= 2 and not any(j in val for j in junk_keywords):
                    return val

    return "未提供"

def run_crawler():
    init_db()
    today_str = datetime.now().strftime("%Y-%m-%d")
    base_url = "https://busadm.ccu.edu.tw/p/412-1248-3236.php?Lang=zh-tw"
    
    with sync_playwright() as p:
        print("🚀 啟動 Chrome 瀏覽器解析中...")
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/123.0.0.0 Safari/537.36")
        page = context.new_page()

        print("🌐 連線中正企管系專任教師頁面...")
        page.goto(base_url, wait_until="networkidle")
        
        soup = BeautifulSoup(page.content(), "html.parser")
        main_content = soup.select_one(".grid-container, .main-content, #Dyn_2_2")
        search_area = main_content if main_content else soup
        links = search_area.select("a[href*='405-1248'], a[href*='404-1248']")
        
        teacher_map = {}
        for a in links:
            href = a.get("href", "")
            name = a.get_text(strip=True)
            if name and name not in ["專任教師", "兼任教師", "名譽教授", "首頁", "聯絡我們", "更多", "系主任"] and 2 <= len(name) <= 4:
                full_url = href if href.startswith("http") else f"https://busadm.ccu.edu.tw/p/{href}"
                teacher_map[name] = full_url

        print(f"🔍 開始精準解析 {len(teacher_map)} 位教師之數據與領域...")
        
        success_count = 0
        for name, url in teacher_map.items():
            views = 0
            research_field = "未提供"
            try:
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(1000)
                
                inner_soup = BeautifulSoup(page.content(), "html.parser")
                
                # 1. 抓取數字
                stat_elem = inner_soup.select_one(".PtStatistic, div[class*='Statistic'], .i-statistic")
                if stat_elem:
                    nums = re.findall(r'\d+', stat_elem.get_text().replace(',', ''))
                    if nums:
                        views = int(nums[0])
                
                if views == 0:
                    text_match = re.search(r'瀏覽次數[：:\s]*([0-9,]+)|點閱[：:\s]*([0-9,]+)', page.content())
                    if text_match:
                        num_str = text_match.group(1) or text_match.group(2)
                        views = int(num_str.replace(',', ''))

                # 2. 抓取研究領域
                research_field = extract_clean_research_field(inner_soup)

            except Exception as e:
                print(f"⚠️ 解析 {name} 失敗: {e}")

            # 寫入 SQLite
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute('SELECT total_views FROM daily_views WHERE teacher_name = ? AND record_date != ? ORDER BY record_date DESC LIMIT 1', (name, today_str))
            prev_res = cursor.fetchone()
            prev_views = prev_res[0] if prev_res else None
            daily_growth = (views - prev_views) if (prev_views is not None and views >= prev_views) else 0

            cursor.execute('DELETE FROM daily_views WHERE record_date = ? AND teacher_name = ?', (today_str, name))
            cursor.execute('''
                INSERT INTO daily_views 
                (record_date, teacher_name, research_field, total_views, daily_growth, profile_url)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (today_str, name, research_field, views, daily_growth, url))
            conn.commit()
            conn.close()

            if views > 0:
                success_count += 1
                print(f"✅ [{success_count}/21] {name}: {views:,} | 領域: {research_field[:25]}")

        browser.close()
        print(f"\n🎉 修正完畢！成功更新 {success_count} 位教師數據。")

if __name__ == "__main__":
    run_crawler()