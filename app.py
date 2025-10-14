import os
import psycopg2
from urllib.parse import urlparse
from dotenv import load_dotenv
from flask import Flask, request, abort

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageSendMessage

# 載入 .env 檔案（Render 環境會直接使用環境變數，本地測試則使用 .env）
load_dotenv() 

# --- 設定 ---
app = Flask(__name__)

# 取得 Line 憑證
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
# 取得 PostgreSQL 連接字串 (Render 自動提供)
DATABASE_URL = os.getenv("DATABASE_URL") 

if not CHANNEL_ACCESS_TOKEN or not CHANNEL_SECRET:
    raise ValueError("請設定 LINE_CHANNEL_ACCESS_TOKEN 和 LINE_CHANNEL_SECRET")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# --- 關卡數據 (Level Data) ---
# 將 data_config.py 的內容整合到此處
LEVEL_DATA = {
    'L01': {
        'question': '找出這張照片上，被詩人稱為「臺陽」的植物是什麼？',
        'answer': '醉花陰',
        'next_clue': '✅ 恭喜解鎖 L02！下一個謎題在城內最古老的廟宇裡。請找出位於「赤崁樓」正前方的贔屭碑，它們上方橫批的內容是什麼？',
        'image': None,
        'next_level_id': 'L02'
    },
    'L02': {
        'question': '赤崁樓正前方的贔屭碑，上方橫批的內容是什麼？',
        'answer': '光耀萬代',
        'next_clue': '✅ 恭喜解鎖 L03！請前往安平古堡（熱蘭遮城）西側的城牆邊。這裡有一塊重要的碑文，碑文上刻著「安平古堡」這四個字旁的詩句，是哪四個字？',
        'image': None,
        'next_level_id': 'L03'
    },
    'L03': {
        'question': '安平古堡西側城牆邊的碑文，碑文上刻著「安平古堡」這四個字旁的詩句，是哪四個字？',
        'answer': '億載金城', # 假設 L03 答案是 億載金城
        'next_clue': '✅ 恭喜解鎖 L04！來到億載金城，請觀察城牆上方的「砲臺」。數一數城牆上總共有幾座這種方形的砲座？ (只計算主城牆上的)',
        'image': None,
        'next_level_id': 'L04'
    },
    'L04': {
        'question': '億載金城城牆上總共有幾座方形的砲座？',
        'answer': '7', # 假設 L04 答案是 7
        'next_clue': '✅ 恭喜解鎖 L05！請到臺南大天后宮，在正殿的龍邊（右側）有一座供奉月老的偏殿。請數一數月老殿中的籤筒，共有多少支籤？',
        'image': None,
        'next_level_id': 'L05'
    },
    'L05': {
        'question': '臺南大天后宮月老殿中的籤筒，共有多少支籤？',
        'answer': '15', # 假設 L05 答案是 15
        'next_clue': '🎉 恭喜您完成所有關卡，探險成功！',
        'image': None,
        'next_level_id': 'COMPLETED'
    }
}

# --- PostgreSQL 資料庫操作函數 ---

def get_db_connection():
    """建立並回傳 PostgreSQL 連接，針對 Render 環境配置 SSL。"""
    if not DATABASE_URL:
        raise ConnectionError("DATABASE_URL is not set.")
    
    # 使用 sslmode='require' 來滿足 Render 的安全要求
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def setup_db():
    """初始化 PostgreSQL 資料庫表格並載入關卡數據。"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 1. 建立 levels 表格 (如果不存在)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS levels (
                level_id VARCHAR(10) PRIMARY KEY,
                question_text TEXT NOT NULL,
                correct_answer VARCHAR(255) NOT NULL,
                next_clue_text TEXT,
                next_clue_image_url TEXT
            );
        """)

        # 2. 建立 users 表格 (追蹤玩家進度)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id VARCHAR(255) PRIMARY KEY,
                current_level VARCHAR(10) NOT NULL,
                last_activity_time TIMESTAMP WITHOUT TIME ZONE
            );
        """)
        
        # 3. 匯入關卡數據 (如果 levels 表格是空的)
        # 檢查是否有 L01 數據，避免重複寫入
        cursor.execute("SELECT COUNT(*) FROM levels WHERE level_id = 'L01';")
        if cursor.fetchone()[0] == 0:
            for level_id, data in LEVEL_DATA.items():
                cursor.execute(
                    """
                    INSERT INTO levels (level_id, question_text, correct_answer, next_clue_text, next_clue_image_url)
                    VALUES (%s, %s, %s, %s, %s);
                    """,
                    (
                        level_id, 
                        data['question'], 
                        data['answer'], 
                        data['next_clue'], 
                        data['image']
                    )
                )
        
        conn.commit()
        conn.close()
        print("PostgreSQL 初始化及關卡數據匯入完成。")

    except Exception as e:
        print(f"PostgreSQL 資料庫初始化失敗: {e}") # 現在會將錯誤印出來
        # 即使失敗，也讓應用程式繼續運行，但 Bot 可能無法工作
        pass


def get_user_level(user_id):
    """取得玩家當前關卡ID，如果不存在則初始化為 L01。"""
    conn = get_db_connection()
    cursor = conn.cursor()
    # 佔位符使用 %s
    cursor.execute("SELECT current_level FROM users WHERE user_id = %s", (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return result[0]
    else:
        # 新玩家，初始化進度
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users VALUES (%s, 'L01', NOW())", (user_id,))
        conn.commit()
        conn.close()
        return 'L01'

def get_level_details(level_id):
    """根據關卡ID取得關卡內容。"""
    conn = get_db_connection()
    cursor = conn.cursor()
    # 佔位符使用 %s
    cursor.execute("SELECT * FROM levels WHERE level_id = %s", (level_id,))
    # 回傳結果：(level_id, question_text, correct_answer, next_clue_text, next_clue_image_url)
    details = cursor.fetchone()
    conn.close()
    return details

def update_user_level(user_id, next_level_id):
    """更新玩家進度到下一關或重設關卡。"""
    conn = get_db_connection()
    cursor = conn.cursor()
    # 佔位符使用 %s
    cursor.execute("UPDATE users SET current_level = %s, last_activity_time = NOW() WHERE user_id = %s", 
                   (next_level_id, user_id))
    conn.commit() # 關鍵：確保變更寫入資料庫
    conn.close()

def clean_answer(text):
    """答案淨化處理：去除空格、轉小寫、去除標點符號（讓比對更彈性）。"""
    text = str(text).lower().strip()
    # 簡單去除常見標點符號
    for char in '.,?!;:"\'，。？！；：「」':
        text = text.replace(char, '')
    return text

# --- Line Bot Webhook 路由 ---

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        print("Invalid signature. Please check your channel access token/secret.")
        abort(400)
    except Exception as e:
        # 捕獲所有例外，並在日誌中顯示，這對 Render 診斷很重要
        print(f"處理 Line 訊息時發生錯誤: {e}")
        abort(500)
    
    return 'OK'

# --- 訊息處理函數 ---

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_input = event.message.text
    user_message_upper = user_input.strip().upper()
    
    # 1. 新增：處理重置指令 (必須在取得進度前執行，才能立刻重設)
    if user_message_upper == 'RESET' or user_message_upper == '重置':
        try:
            update_user_level(user_id, 'L01')
            reply_text = "🕵️‍♂️ **進度已重設！** 您已回到第一關。請輸入 L01 的題目答案開始挑戰：\n\n醉花陰"
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply_text)
            )
        except Exception as e:
            # 確保即使資料庫寫入失敗也能回覆
            print(f"重置進度時發生錯誤: {e}")
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="❌ 重置進度失敗，請檢查資料庫連線或稍後再試。")
            )
        return

    # 2. 取得玩家當前關卡資訊
    current_level_id = get_user_level(user_id)
    level_data = get_level_details(current_level_id)
    
    if not level_data:
        # 如果找不到關卡資料，表示資料庫沒有正確初始化
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="🚨 遊戲系統錯誤：找不到關卡數據。請聯繫管理員檢查資料庫初始化。")
        )
        return

    # 解包關卡資訊
    # 由於 PostgreSQL 回傳的是 tuple，這裡使用索引
    # level_data: (level_id, question_text, correct_answer, next_clue_text, next_clue_image_url)
    level_id_db, question_text, correct_answer_raw, next_clue_text, next_clue_image_url = level_data

    # 3. 答案比對邏輯
    is_correct = clean_answer(user_input) == clean_answer(correct_answer_raw)

    if is_correct:
        # **答對處理**
        
        # 尋找下一關的 ID (例如 L01 -> L02)
        try:
            next_level_id = 'L' + str(int(current_level_id[1:]) + 1).zfill(2)
        except ValueError:
            # 處理 COMPLETED 狀態，避免錯誤
            next_level_id = 'COMPLETED' 
        
        next_level_data = get_level_details(next_level_id)

        reply_messages = [
            TextSendMessage(text=f"✅ 恭喜！您找到了正確答案：{correct_answer_raw}！")
        ]
        
        if next_level_data:
            # 還有下一關
            update_user_level(user_id, next_level_id)
            
            # 發送下一關的文字提示
            reply_messages.append(TextSendMessage(text=next_clue_text))

            # 如果有下一關提示圖片，也發送 (圖片必須是公開 URL)
            if next_clue_image_url:
                reply_messages.append(
                    ImageSendMessage(
                        original_content_url=next_clue_image_url,
                        preview_image_url=next_clue_image_url # Line 規定預覽圖也要有
                    )
                )
        else:
            # 這是最後一關，或下一關 ID 已超出範圍
            update_user_level(user_id, 'COMPLETED') # 將狀態設為完成
            reply_messages.append(TextSendMessage(text="🎉 恭喜您完成所有關卡，探險成功！"))

        line_bot_api.reply_message(event.reply_token, reply_messages)

    else:
        # **答錯處理**
        reply_message = f"❌ 答案不正確，請再仔細觀察現場，或輸入**『提示』**來獲取幫助。"
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_message)
        )


# --- 確保在應用程式啟動時運行資料庫初始化 ---
# Gunicorn/Render 啟動時會運行這個區塊，確保資料庫表格和數據存在
with app.app_context():
    setup_db()

if __name__ == "__main__":
    # 本地啟動時使用
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
