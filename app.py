import os
import sqlite3
from dotenv import load_dotenv
from flask import Flask, request, abort

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageSendMessage

# 載入 .env 檔案
load_dotenv() 

# --- 設定 ---
app = Flask(__name__)
DB_NAME = 'quest_game.db' # 確保與 database.py 一致

# 取得 Line 憑證
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

if not CHANNEL_ACCESS_TOKEN or not CHANNEL_SECRET:
    raise ValueError("請在 .env 檔案中設定 LINE_CHANNEL_ACCESS_TOKEN 和 LINE_CHANNEL_SECRET")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# --- 資料庫操作函數 ---

def get_user_level(user_id):
    """取得玩家當前關卡ID，如果不存在則初始化為 L01。"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT current_level FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return result[0]
    else:
        # 新玩家，初始化進度
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users VALUES (?, ?, datetime('now'))", (user_id, 'L01'))
        conn.commit()
        conn.close()
        return 'L01'

def get_level_details(level_id):
    """根據關卡ID取得關卡內容。"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM levels WHERE level_id = ?", (level_id,))
    # 回傳結果：(level_id, question_text, correct_answer, next_clue_text, next_clue_image_url)
    details = cursor.fetchone()
    conn.close()
    return details

def update_user_level(user_id, next_level_id):
    """更新玩家進度到下一關。"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET current_level = ?, last_activity_time = datetime('now') WHERE user_id = ?", 
                   (next_level_id, user_id))
    conn.commit()
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
    
    return 'OK'

# --- 訊息處理函數 ---

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_input = event.message.text
    
    # 1. 取得玩家當前關卡資訊
    current_level_id = get_user_level(user_id)
    level_data = get_level_details(current_level_id)
    
    if not level_data:
        # 如果找不到關卡資料 (可能是最後一關或資料錯誤)
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="遊戲已結束或發生錯誤，請稍後再試。")
        )
        return

    # 解包關卡資訊 (從 level_data 中取得 correct_answer 和下一關資訊)
    _, question_text, correct_answer_raw, next_clue_text, next_clue_image_url = level_data

    # 2. 答案比對邏輯
    is_correct = clean_answer(user_input) == clean_answer(correct_answer_raw)

    if is_correct:
        # **答對處理**
        
        # 尋找下一關的 ID (例如 L01 -> L02)
        next_level_id = 'L' + str(int(current_level_id[1:]) + 1).zfill(2)
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
            # 這是最後一關
            update_user_level(user_id, 'COMPLETED') # 可將狀態設為完成
            reply_messages.append(TextSendMessage(text="🎉 恭喜您完成所有關卡，探險成功！"))

        line_bot_api.reply_message(event.reply_token, reply_messages)

    else:
        # **答錯處理**
        reply_message = f"❌ 答案不正確，請再仔細觀察現場，或輸入**『提示』**來獲取幫助。"
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_message)
        )


if __name__ == "__main__":
    from database import initialize_db
    initialize_db() # 啟動前先確保資料庫和初始資料已建立

    # 為了 Line Bot 測試，通常會需要 ngrok 來提供 HTTPS URL
    # 在本地端測試時，可以先運行 app.run()，再用 ngrok 轉發
    # ngrok http 5000 (預設 Flask port 是 5000)
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

def handle_message(event):
    # 確保收到的確實是文字訊息
    if not isinstance(event.message, TextMessage):
        return

    user_id = event.source.user_id
    user_message = event.message.text.strip().upper()  # 轉換為大寫以利比對

    # --- [新增] 檢查是否為重置指令 ---
    if user_message == 'RESET' or user_message == '重置':
        # 呼叫更新函數，將進度設回 L01
        update_user_level(user_id, 'L01')
        reply_text = "🕵️‍♂️ **進度已重設！** 您已回到第一關。請輸入 L01 的題目答案開始挑戰："
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )
        return # 處理完畢，直接返回

    # --- [原有邏輯] 判斷玩家當前關卡 ---
    current_level_id = get_user_level(user_id)
    # ... (後續的遊戲邏輯，例如查詢關卡詳情、比對答案等)