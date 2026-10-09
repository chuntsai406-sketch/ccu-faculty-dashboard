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
    junk_keywords = [
        "瀏覽數", "友善列印", "JavaScript", "列印", "分享", "版權所有", 
        "系統維護", "English", "聯絡我們", "::: ", "網站導覽", "首頁"
    ]
    main_area = soup.select_one(".grid-container, .main-content, #Dyn_2_2, div[class*='content']")
    target_soup = main_area if main_area else soup

    for tr in target_soup.find_all("tr"):
        tr_text = tr.get_text(strip=True)
        if any(k in tr_text for k in ["專長", "研究領域", "授課領域", "研究方向"]):
            tds = tr.find_all(["td", "th"])
            if len(tds) >= 2:
                val = tds[-1].get_text(" ", strip=True)
                if val and not any(j in val for j in junk_keywords):
                    return val

    for elem in target_soup.find_all(["h3", "h4", "strong", "b", "div", "p", "td"]):
        text = elem.get_text(strip=True)
        if any(k in text for k in ["專長", "研究領域", "授課領域", "研究方向"]) and len(text) < 15:
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
        print("🚀 啟動 Chrome 瀏覽器...")
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        print("🌐 取得專任教師清單...")
        page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2000)
        
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

        print(f"🔍 開始解析 {len(teacher_map)} 位教師數據...")
        
        for name, url in teacher_map.items():
            views = 0
            research_field = "未提供"
            
            # 嘗試最多 2 次（避免網絡過慢）
            for attempt in range(2):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(3000)
                    
                    inner_soup = BeautifulSoup(page.content(), "html.parser")
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

                    research_field = extract_clean_research_field(inner_soup)
                    
                    if views > 0:
                        break # 成功抓到數字即跳出重試迴圈
                except Exception as e:
                    print(f"⚠️ 第 {attempt+1} 次讀取 {name} 失敗: {e}")

            # 資料庫處理：比對昨日數據
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute('SELECT total_views, research_field FROM daily_views WHERE teacher_name = ? AND record_date != ? ORDER BY record_date DESC LIMIT 1', (name, today_str))
            prev_res = cursor.fetchone()
            
            prev_views = prev_res[0] if prev_res else None
            prev_field = prev_res[1] if prev_res else "未提供"

            # 避險機制：若今日不幸抓到 0 次，則沿用昨日總數字，避免影響網頁呈現
            if views == 0 and prev_views is not None:
                views = prev_views
                if research_field == "未提供":
                    research_field = prev_field

            daily_growth = (views - prev_views) if (prev_views is not None and views >= prev_views) else 0

            cursor.execute('DELETE FROM daily_views WHERE record_date = ? AND teacher_name = ?', (today_str, name))
            cursor.execute('''
                INSERT INTO daily_views 
                (record_date, teacher_name, research_field, total_views, daily_growth, profile_url)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (today_str, name, research_field, views, daily_growth, url))
            conn.commit()
            conn.close()

            print(f"✅ {name}: {views:,} | 領域: {research_field[:15]}")

        browser.close()
        print("\n🎉 爬取完成！已更新所有教師數據。")

if __name__ == "__main__":
    run_crawler()
