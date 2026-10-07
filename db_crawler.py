import sqlite3
import time
from datetime import date
import pandas as pd
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager
from urllib.parse import urljoin

DB_NAME = "ccu_faculty.db"

def init_db():
    """初始化 SQLite 資料庫表單"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # 建立歷史紀錄表（以日期 + 教授姓名為聯合主鍵，避免同天重複寫入）
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
    """查詢該教授最近一次記錄的總瀏覽數"""
    cursor.execute('''
        SELECT total_views FROM daily_views 
        WHERE teacher_name = ? 
        ORDER BY record_date DESC LIMIT 1
    ''', (teacher_name,))
    result = cursor.fetchone()
    return result[0] if result else None

def run_crawler_and_save():
    init_db()
    today_str = date.today().strftime("%Y-%m-%d")
    
    # 1. 爬蟲設定
    chrome_options = Options()
    chrome_options.add_argument("--headless") # 靜默模式（不顯示瀏覽器視窗）
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36")

    print(f"[{today_str}] 啟動爬蟲作業...")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    try:
        base_url = "https://busadm.ccu.edu.tw/p/412-1248-3236.php?Lang=zh-tw"
        driver.get(base_url)
        time.sleep(2)
        
        soup = BeautifulSoup(driver.page_source, "html.parser")
        links = soup.select("a[href*='405-1248']")
        
        teacher_links = []
        for a in links:
            href = a.get("href", "")
            name = a.get_text(strip=True)
            if name and name not in ["專任教師", "兼任教師", "首頁", "聯絡我們", "更多"] and len(name) <= 6:
                full_url = urljoin(base_url, href)
                if (name, full_url) not in teacher_links:
                    teacher_links.append((name, full_url))

        print(f"找到 {len(teacher_links)} 位教師，開始更新資料庫...\n")

        for index, (name, link) in enumerate(teacher_links, start=1):
            research_interests = "未提供"
            views_count = 0
            
            try:
                driver.get(link)
                # 等待動態瀏覽數載入
                try:
                    WebDriverWait(driver, 5).until(
                        lambda d: d.find_element(By.CSS_SELECTOR, ".PtStatistic i").text.strip() != ""
                    )
                except:
                    pass
                
                detail_soup = BeautifulSoup(driver.page_source, "html.parser")
                
                # 抓取瀏覽數
                pt_i = detail_soup.select_one(".PtStatistic i")
                if pt_i and pt_i.get_text(strip=True).isdigit():
                    views_count = int(pt_i.get_text(strip=True))

                # 抓取研究領域
                for h3 in detail_soup.find_all("h3"):
                    if "研究領域" in h3.get_text() or "專長" in h3.get_text():
                        next_div = h3.find_next_sibling("div")
                        if next_div and next_div.get_text(strip=True):
                            research_interests = next_div.get_text(" ", strip=True)
                        break

            except Exception as e:
                print(f"  └ 抓取 {name} 失敗: {e}")

            # 計算「每日新增」
            prev_views = get_yesterday_views(cursor, name)
            if prev_views is not None:
                daily_growth = views_count - prev_views
                # 防呆：若歷史數據異常導致負數則計為 0
                if daily_growth < 0:
                    daily_growth = 0
            else:
                daily_growth = 0 # 第一次抓取無前日數據，設為 0

            # 寫入/更新 SQLite 資料庫
            cursor.execute('''
                INSERT OR REPLACE INTO daily_views 
                (record_date, teacher_name, research_field, total_views, daily_growth, profile_url)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (today_str, name, research_interests, views_count, daily_growth, link))
            
            print(f"[{index}/{len(teacher_links)}] {name} | 總瀏覽: {views_count} | 今日新增: +{daily_growth}")
            time.sleep(0.5)

        conn.commit()
        print(f"\n✅ 今日數據成功儲存至 SQLite 資料庫 ({DB_NAME})！")

    finally:
        conn.close()
        driver.quit()

if __name__ == "__main__":
    run_crawler_and_save()