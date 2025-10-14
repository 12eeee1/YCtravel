import sqlite3
import json

DB_NAME = 'quest_game.db'

def initialize_db():
    """初始化資料庫並建立所需的表格。"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # 1. 關卡內容表 (儲存題目、答案、提示等)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS levels (
            level_id TEXT PRIMARY KEY,
            question_text TEXT,
            correct_answer TEXT,
            next_clue_text TEXT,
            next_clue_image_url TEXT
        );
    """)

    # 2. 玩家進度表 (追蹤每個玩家當前在哪一關)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            current_level TEXT,
            last_activity_time TEXT
        );
    """)

    # 插入您的關卡初始數據 (這部分需要根據您的方案填寫)
    initial_levels = [
        # level_id, question_text, correct_answer, next_clue_text, next_clue_image_url
        ('L01', 
         '歡迎來到實地探險！請前往**臺南孔廟**。找到大成殿前石柱上的**詞牌名**，並將它輸入 Line Bot。', 
         '醉花陰', 
         '答對了！恭喜進入下一關。請前往**赤崁樓**，尋找其正門牌匾上的**橫批**。', 
         ''),
        ('L02', 
         '請前往**赤崁樓**，尋找其正門牌匾上的**橫批**。', 
         '光耀萬代', 
         '完美！您已完成所有關卡，成為探險大師！', 
         ''),
                # --- 新增關卡 L03 ---
        ('L03', 
         '請前往**安平古堡**，找到寫有「熱蘭遮城」的碑文，輸入最下方一行**中文字的數量**。', 
         '8', # 假設答案是 8
         '厲害！線索就在眼前。下一站：**億載金城**。請在城門入口處找到當年建城**啟用典禮的日期**（僅輸入數字，例如：18761210）。', 
         ''),
         
        # --- 新增關卡 L04 ---
        ('L04', 
         '請前往**億載金城**。請在城門入口處找到當年建城**啟用典禮的日期**（僅輸入數字，例如：18761210）。', 
         '18761210', 
         '非常精準！下一站：**神農街**。請尋找街上某間店門口**懸掛的燈籠總數**（僅輸入數字）。', 
         ''),
         
        # --- 新增關卡 L05 (設定為最後一關) ---
        ('L05', 
         '請前往**神農街**。請尋找街上某間店門口**懸掛的燈籠總數**（僅輸入數字）。', 
         '15', # 假設答案是 15
         '完美！您已完成所有關卡，成為探險大師！', 
         '')
        # 您可以在此處增加更多關卡 L06, L07...
    ]
    
    # 僅在資料庫為空時插入初始數據
    for level in initial_levels:
        try:
            cursor.execute(f"INSERT INTO levels VALUES (?, ?, ?, ?, ?)", level)
        except sqlite3.IntegrityError:
            # 如果 level_id 已經存在，則跳過 (避免重複插入)
            pass

    conn.commit()
    conn.close()

# 記得在程式啟動時呼叫這個函數
if __name__ == '__main__':
    initialize_db()
    print("資料庫初始化完成，請檢查 quest_game.db 檔案。")