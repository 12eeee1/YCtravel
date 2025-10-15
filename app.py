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
# 🚨 圖片 URL 提示：Line Bot 的 ImageSendMessage 必須使用直接連到圖片檔案的 HTTPS URL (例如 .jpg, .png)。
# ⚠️ 像 ppt.cc 這種縮網址/轉址服務，Line 伺服器通常會拒絕解析，導致圖片發送失敗。
# L04 和 L06 已替換為 Placehold.co 的測試圖片，請務必將來替換成您自己的 **直接圖片連結**！
LEVEL_DATA = {
    'L01': {
        'question': '圓山站站名的日文為何？（羅馬拼音）\n\n（請直接回覆答案）',
        'question_image': None, # L01 題目圖片 URL (這個是直接連結，故可運作)
        'answer': 'Maruyama',
        'next_clue': '✅ 恭喜解鎖 第二關！下一個謎題在台北孔廟。\n\n請前往https://maps.app.goo.gl/tTZJFnZTRwAq2f36A',
        'next_clue_image': None,
        'next_level_id': 'L02'
    },
    'L02': {
        'question': '🙏🎸🏹🐴🧮✈️',
        'question_image': None,
        'answer': '禮樂射御書數',
        'next_clue': '✅ 恭喜解鎖 L03！請前往保安宮解開下一關',
        'next_clue_image': None,
        'next_level_id': 'L03'
    },
    'L03': {
        'question': '側城牆邊的碑文，碑文上刻著甚麼字？',
        'question_image': None,
        'answer': '保安', 
        'next_clue': '✅ 恭喜解鎖 L04！請回到保安宮正門對面，找到圖片中的石碑，石碑後方草叢藏著下一關的線索!',
        'next_clue_image': None,
        'next_level_id': 'L04'
    },
    'L04': {
        'question': '請依照取得的線索，解開謎底',
        'question_image': 'https://raw.githubusercontent.com/12eeee1/YCtravel/refs/heads/master/images/04.jpg',
        'answer': '頂', 
        'next_clue': '✅ 恭喜解鎖 L05！請到樹人書院',
        'next_clue_image': None,
        'next_level_id': 'L05'
    },
    'L05': {
        'question': '請到指定位置尋找實體寶藏、並從中獲取題目',
        'question_image': None,
        'answer': '鳳梨', 
        'next_clue': '看不太懂下面這張圖片想表達什麼嗎？ 前往下一個地點找看看線索吧！',
        'next_clue_image': None,
        'next_level_id': 'L06'
    },
    'L06': {
        'question': '解開題目後，可以跟我確認答案(不須輸入空格、標點符號)',
        'question_image': "https://raw.githubusercontent.com/12eeee1/YCtravel/refs/heads/master/images/05.jpg",
        'answer': '53878337515', 
        'next_clue': '🎉 恭喜您完成所有關卡，探險成功！',
        'next_clue_image': None,
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
        
        # 1. 建立 levels 表格 (如果不存在) [更新：新增 question_image_url 欄位]
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS levels (
                level_id VARCHAR(10) PRIMARY KEY,
                question_text TEXT NOT NULL,
                question_image_url TEXT, -- 新增題目圖片欄位
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
        # 🚨 [重要] 這裡的邏輯是只有在 level_id 'L01' 不存在時才匯入。
        # 因為您剛剛已經匯入過舊數據，若要使用新數據，我們需要手動清空表格。
        cursor.execute("SELECT COUNT(*) FROM levels WHERE level_id = 'L01';")
        if cursor.fetchone()[0] == 0:
            # 確保匯入新數據
            for level_id, data in LEVEL_DATA.items():
                cursor.execute(
                    """
                    INSERT INTO levels (level_id, question_text, question_image_url, correct_answer, next_clue_text, next_clue_image_url)
                    VALUES (%s, %s, %s, %s, %s, %s);
                    """,
                    (
                        level_id, 
                        data['question'], 
                        data['question_image'], # 匯入題目圖片
                        data['answer'], 
                        data['next_clue'], 
                        data['next_clue_image'] 
                    )
                )
        else:
            # 已經有數據了，需要更新它們，確保 L04 和 L06 的圖片 URL 是新的
            for level_id, data in LEVEL_DATA.items():
                 cursor.execute(
                    """
                    UPDATE levels
                    SET question_text = %s, question_image_url = %s, correct_answer = %s, next_clue_text = %s, next_clue_image_url = %s
                    WHERE level_id = %s;
                    """,
                    (
                        data['question'], 
                        data['question_image'],
                        data['answer'], 
                        data['next_clue'], 
                        data['next_clue_image'],
                        level_id
                    )
                )

        
        conn.commit()
        conn.close()
        print("PostgreSQL 初始化及關卡數據匯入完成。")

    except Exception as e:
        print(f"PostgreSQL 資料庫初始化失敗: {e}") 
        pass


def get_user_level(user_id):
    """取得玩家當前關卡ID，如果不存在則初始化為 L01。"""
    conn = get_db_connection()
    cursor = conn.cursor()
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
    # [更新] 增加 question_image_url 欄位
    cursor.execute("SELECT level_id, question_text, question_image_url, correct_answer, next_clue_text, next_clue_image_url FROM levels WHERE level_id = %s", (level_id,))
    # 回傳結果：(level_id, question_text, question_image_url, correct_answer, next_clue_text, next_clue_image_url)
    details = cursor.fetchone()
    conn.close()
    return details

def update_user_level(user_id, next_level_id):
    """更新玩家進度到下一關或重設關卡。"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET current_level = %s, last_activity_time = NOW() WHERE user_id = %s", 
                   (next_level_id, user_id))
    conn.commit() 
    conn.close()

def clean_answer(text):
    """答案淨化處理：去除空格、轉小寫、去除標點符號（讓比對更彈性）。"""
    text = str(text).lower().strip()
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
        print(f"處理 Line 訊息時發生錯誤: {e}")
        abort(500)
    
    return 'OK'

# --- 訊息處理函數 ---

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_input = event.message.text
    user_message_upper = user_input.strip().upper()
    
    # 1. 處理重置指令
    if user_message_upper == 'RESET' or user_message_upper == '重置':
        try:
            update_user_level(user_id, 'L01')
            
            # 取得 L01 的圖片和題目
            level_data = get_level_details('L01')
            if level_data:
                _, question_text, question_image_url, _, _, _ = level_data
                
                reply_messages = [
                    TextSendMessage(text="🕵️‍♂️ **進度已重設！** 您已回到第一關。"),
                    TextSendMessage(text=f"【L01 挑戰】\n{question_text}")
                ]
                
                # 發送 L01 題目圖片
                if question_image_url:
                    # Line ImageSendMessage 需要 original_content_url 和 preview_image_url
                    reply_messages.append(
                        ImageSendMessage(
                            original_content_url=question_image_url,
                            # 預覽圖可以使用相同的 URL，但 Line 建議使用較小的圖檔
                            preview_image_url=question_image_url 
                        )
                    )
                
                line_bot_api.reply_message(event.reply_token, reply_messages)
            
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 重置進度失敗：找不到關卡數據。"))

        except Exception as e:
            print(f"重置進度時發生錯誤: {e}")
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 重置進度失敗，請檢查資料庫連線或稍後再試。"))
        return

    # 2. 取得玩家當前關卡資訊
    current_level_id = get_user_level(user_id)
    level_data = get_level_details(current_level_id)
    
    if not level_data:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="🚨 遊戲系統錯誤：找不到關卡數據。請聯繫管理員檢查資料庫初始化。")
        )
        return

    # 解包關卡資訊：(level_id, question_text, question_image_url, correct_answer_raw, next_clue_text, next_clue_image_url)
    level_id_db, question_text, question_image_url, correct_answer_raw, next_clue_text, next_clue_image_url = level_data

    # 3. 答案比對邏輯
    is_correct = clean_answer(user_input) == clean_answer(correct_answer_raw)

    if is_correct:
        # **答對處理**
        
        # 尋找下一關的 ID (例如 L01 -> L02)
        try:
            current_level_num = int(current_level_id[1:])
            next_level_id = 'L' + str(current_level_num + 1).zfill(2)
        except ValueError:
            next_level_id = 'COMPLETED' 
        
        next_level_data = get_level_details(next_level_id)

        # 1. 初始訊息列表
        reply_messages = []
        
        if next_level_data:
            # 還有下一關
            update_user_level(user_id, next_level_id)
            
            # 解包下一關的題目資訊
            # (level_id, next_question_text, next_question_image_url, correct_answer, next_clue_text, next_clue_image_url)
            _, next_question_text, next_question_image_url, _, _, _ = next_level_data

            # 1. 發送當前關卡的【線索/轉場訊息】與下一關的【題目文字】，合併為一則訊息
            full_text_message = f"{next_clue_text}\n\n【{next_level_id} 挑戰】\n{next_question_text}"
            reply_messages.append(TextSendMessage(text=full_text_message))
            
            # 2. 發送下一關的題目圖片
            if next_question_image_url:
                reply_messages.append(
                    ImageSendMessage(
                        original_content_url=next_question_image_url,
                        preview_image_url=next_question_image_url 
                    )
                )

        else:
            # 這是最後一關，直接發送 current_level 的 next_clue_text (例如 '🎉 恭喜您完成所有關卡，探險成功！')
            update_user_level(user_id, 'COMPLETED') 
            reply_messages.append(TextSendMessage(text=next_clue_text))

        line_bot_api.reply_message(event.reply_token, reply_messages)

    else:
        # **答錯處理** - 現在會顯示當前關卡資訊，包含圖片
        reply_messages = [
            TextSendMessage(text="❌ 答案不正確，請再仔細觀察現場。"),
            TextSendMessage(text=f"【當前挑戰：{current_level_id}】\n{question_text}")
        ]

        if question_image_url:
             reply_messages.append(
                ImageSendMessage(
                    original_content_url=question_image_url,
                    preview_image_url=question_image_url
                )
            )

        line_bot_api.reply_message(
            event.reply_token,
            reply_messages
        )


# --- 確保在應用程式啟動時運行資料庫初始化 ---
# Gunicorn/Render 啟動時會運行這個區塊，確保資料庫表格和數據存在
with app.app_context():
    setup_db()

if __name__ == "__main__":
    # 本地啟動時使用
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
