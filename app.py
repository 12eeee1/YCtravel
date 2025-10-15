import os
import psycopg2
from urllib.parse import urlparse
from dotenv import load_dotenv
from flask import Flask, request, abort

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
# [更新] 導入 FollowEvent 處理新用戶追蹤事件
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageSendMessage, FollowEvent 

# 載入 .env 檔案（Render 環境會直接使用環境變數，本地測試則使用 .env）
load_dotenv() 

# --- 設定 ---
app = Flask(__name__)

# 取得 Line 憑證
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
# 取得 PostgreSQL 連接字串 (Render 自動提供)
DATABASE_URL = os.getenv("DATABASE_URL") 

# 修正：使用大寫變數名稱，並修正縮排字元
if not CHANNEL_ACCESS_TOKEN or not CHANNEL_SECRET:
    # 這是原本有問題的第 25 行，請確保這裡的縮排是標準空格或 Tab
    raise ValueError("請設定 LINE_CHANNEL_ACCESS_TOKEN 和 LINE_CHANNEL_SECRET")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# --- 關卡數據 (Level Data) ---
# L04 和 L06 的圖片 URL 已使用您提供的 GitHub 連結。
LEVEL_DATA = {
    'L01': {
        'question': '圓山站站名的日文為何？（羅馬拼音）\n\n（請直接回覆答案）',
        'question_image': None,
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
        
        # 1. 建立 levels 表格 (如果不存在) 
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS levels (
                level_id VARCHAR(10) PRIMARY KEY,
                question_text TEXT NOT NULL,
                question_image_url TEXT, -- 題目圖片欄位
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
        
        # 3. 匯入或更新關卡數據：這裡使用 UPDATE 確保每次部署時數據都是最新的
        for level_id, data in LEVEL_DATA.items():
            # 檢查資料是否存在，如果不存在則 INSERT，存在則 UPDATE
            cursor.execute("SELECT level_id FROM levels WHERE level_id = %s", (level_id,))
            
            if cursor.fetchone():
                # 存在則 UPDATE
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
            else:
                # 不存在則 INSERT
                cursor.execute(
                    """
                    INSERT INTO levels (level_id, question_text, question_image_url, correct_answer, next_clue_text, next_clue_image_url)
                    VALUES (%s, %s, %s, %s, %s, %s);
                    """,
                    (
                        level_id, 
                        data['question'], 
                        data['question_image'], 
                        data['answer'], 
                        data['next_clue'], 
                        data['next_clue_image'] 
                    )
                )
        
        conn.commit()
        conn.close()
        print("PostgreSQL 初始化及關卡數據匯入/更新完成。")

    except Exception as e:
        print(f"PostgreSQL 資料庫初始化失敗: {e}") 
        pass


def get_user_level(user_id):
    """取得玩家當前關卡ID，如果不存在則初始化為 WELCOME。"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT current_level FROM users WHERE user_id = %s", (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return result[0]
    else:
        # 新玩家，初始化進度到 'WELCOME' 狀態，等待 START 指令
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users VALUES (%s, 'WELCOME', NOW())", (user_id,))
        conn.commit()
        conn.close()
        return 'WELCOME'

def get_level_details(level_id):
    """根據關卡ID取得關卡內容。"""
    conn = get_db_connection()
    cursor = conn.cursor()
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

# [新增] 處理新用戶追蹤事件 (Follow Event)
@handler.add(FollowEvent)
def handle_follow(event):
    welcome_message = (
        "🌿 歡迎來到《圓山探險隊》。\n"
        "這是一場用「腳」閱讀的旅程，\n"
        "也是一場用「心」傾聽的課程。\n\n"
        "當你準備好，\n"
        "請輸入「START」或「開始」展開旅程，\n"
        "讓故事，從圓山的風裡開始說起。"
    )
    user_id = event.source.user_id
    
    # 確保用戶狀態被初始化為 'WELCOME'
    get_user_level(user_id) 
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=welcome_message)
    )


# --- 訊息處理函數 ---

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_input = event.message.text
    user_message_upper = user_input.strip().upper()
    
    # 1. 處理重置指令 (RESET/重置)
    if user_message_upper == 'RESET' or user_message_upper == '重置':
        try:
            # 無論當前在哪一關，都強制設為 L01
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
                    reply_messages.append(
                        ImageSendMessage(
                            original_content_url=question_image_url,
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
    
    # [新增] 處理 COMPLETED 狀態
    if current_level_id == 'COMPLETED':
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="🎉 恭喜您已完成所有挑戰！如果您想重新開始，請輸入「RESET」或「重置」。")
        )
        return

    # [新增] 處理 WELCOME 狀態和 START 啟動指令
    if current_level_id == 'WELCOME':
        if user_message_upper == 'START' or user_message_upper == '開始':
            # 進入 L01
            try:
                update_user_level(user_id, 'L01')
                
                level_data = get_level_details('L01')
                if level_data:
                    _, question_text, question_image_url, _, _, _ = level_data
                    
                    reply_messages = [
                        TextSendMessage(text="🚀 旅程開始！祝您探險愉快。"),
                        TextSendMessage(text=f"【L01 挑戰】\n{question_text}")
                    ]
                    
                    if question_image_url:
                        reply_messages.append(
                            ImageSendMessage(
                                original_content_url=question_image_url,
                                preview_image_url=question_image_url 
                            )
                        )
                    
                    line_bot_api.reply_message(event.reply_token, reply_messages)
                
                else:
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 遊戲啟動失敗：找不到 L01 關卡數據。"))

            except Exception as e:
                print(f"啟動遊戲時發生錯誤: {e}")
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 遊戲啟動失敗，請檢查資料庫連線或稍後再試。"))
            return
        else:
            # 玩家在 WELCOME 狀態但輸入了其他指令
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="請輸入「START」或「開始」以展開您的圓山探險旅程。")
            )
            return

    # 從這裡開始 current_level_id 必然是有效的 Lxx 關卡 ID
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
            # 這是最後一關
            update_user_level(user_id, 'COMPLETED') 
            reply_messages.append(TextSendMessage(text=next_clue_text))

        line_bot_api.reply_message(event.reply_token, reply_messages)

    else:
        # **答錯處理** - 顯示當前關卡資訊，包含圖片
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
