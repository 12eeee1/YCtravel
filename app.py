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
    raise ValueError("請設定 LINE_CHANNEL_ACCESS_TOKEN 和 LINE_CHANNEL_SECRET")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)


# [新增] 歡迎訊息常量，供 FollowEvent 和 重置指令使用
WELCOME_MESSAGE = (
    "🌿 歡迎來到《圓山探險隊》。\n"
    "這是一場用「腳」閱讀的旅程，\n"
    "也是一場用「心」傾聽的課程。\n\n"
    "當你準備好，\n"
    "請輸入「START」或「開始」展開旅程，\n"
    "讓故事，從圓山的風裡開始說起。"
)


# --- 關卡數據 (Level Data) ---
# L04 和 L06 的圖片 URL 已使用您提供的 GitHub 連結。
LEVEL_DATA = {
    'L01': {
        'question': '圓山站的日文拼音是什麼？（出捷運站時，有聽到廣播嗎？），請輸入羅馬拼音。',
        'question_image': None,
        'answer': 'Maruyama',
        'next_clue': '✅ 答對了！從圓山啟程，接下來，我們要走進知識與禮樂的門。\n\n請前往https://maps.app.goo.gl/tTZJFnZTRwAq2f36A',
        'next_clue_image': None,
        'next_level_id': 'L02'
    },
    'L02': {
        'question': '🙏🎸🏹🐴🧮✈️ 這六個符號分別代表什麼？',
        'question_image': None,
        'answer': '禮樂射御書數',
        'next_clue': '✅ 很好！你已通過學問之門。下一站，前往信仰與教化交會之地。\n\n請前往https://maps.app.goo.gl/gD9w5eFzRzJ8fX9A7',
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
        'next_clue': '✅ 恭喜解鎖 L05！請前往樹人書院解開下一關謎題。',
        'next_clue_image': None,
        'next_level_id': 'L05'
    },
    'L05': {
        'question': '請到指定位置尋找實體寶藏、並從中獲取題目',
        'question_image': None,
        'answer': '鳳梨', 
        'next_clue': '看不太懂下面這張圖片想表達什麼嗎？ 前往下一個地點找看看線索吧！\n\n請前往https://maps.app.goo.gl/tTZJFnZTRwAq2f36A',
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
        # 當 DATABASE_URL 未設定時，提供一個友善的錯誤訊息
        raise ConnectionError("DATABASE_URL is not set. Please ensure the environment variable is configured.")
    
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
        # current_level 現在用於儲存狀態，例如 'L01_ANSWERING' 或 'L01_WAITING'
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id VARCHAR(255) PRIMARY KEY,
                current_level VARCHAR(30) NOT NULL, -- 加大長度以容納狀態字串
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
        # 即使初始化失敗，也讓應用程式繼續運行（但會影響功能）
        pass


def get_user_level(user_id):
    """取得玩家當前狀態ID，如果不存在則初始化為 WELCOME。"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT current_level FROM users WHERE user_id = %s", (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        # 回傳用戶的當前狀態 (e.g., 'L01_ANSWERING', 'L01_WAITING', 'COMPLETED')
        return result[0]
    else:
        # 新玩家，初始化進度到 'WELCOME' 狀態，等待 START 指令
        conn = get_db_connection()
        cursor = conn.cursor()
        # 確保在用戶不存在時，執行 INSERT 操作
        cursor.execute("INSERT INTO users (user_id, current_level, last_activity_time) VALUES (%s, 'WELCOME', NOW())", (user_id,))
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

def update_user_level(user_id, next_state):
    """更新玩家進度狀態。"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET current_level = %s, last_activity_time = NOW() WHERE user_id = %s", 
                   (next_state, user_id))
    conn.commit() 
    conn.close()

def clean_answer(text):
    """答案淨化處理：去除空格、轉小寫、去除標點符號（讓比對更彈性）。"""
    text = str(text).lower().strip()
    for char in '.,?!;:"\'，。？！；：「」':
        text = text.replace(char, '')
    # 針對中文習慣，將全形空格也移除
    text = text.replace('　', '').replace(' ', '')
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

# [更新] 處理新用戶追蹤事件 (Follow Event) - 使用全域常量
@handler.add(FollowEvent)
def handle_follow(event):
    user_id = event.source.user_id
    
    # 確保用戶狀態被初始化為 'WELCOME'
    get_user_level(user_id) 
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=WELCOME_MESSAGE) # 使用全域 WELCOME_MESSAGE
    )

# --- 訊息處理函數 ---

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_input = event.message.text
    user_message_upper = user_input.strip().upper()
    user_input_normalized = clean_answer(user_input)
    
    # 1. 處理重置指令 (RESET/重置)
    if user_message_upper == 'RESET' or user_message_upper == '重置':
        try:
            # 關鍵變動：將用戶狀態強制設為 'WELCOME'，回到起始畫面
            update_user_level(user_id, 'WELCOME')
            
            # 回覆訊息：告知重設成功，並發送歡迎訊息
            reply_messages = [
                TextSendMessage(text="🕵️‍♂️ **進度已重設！** 您已回到起始畫面，請輸入「START」展開旅程。"),
                TextSendMessage(text=WELCOME_MESSAGE) 
            ]
            
            line_bot_api.reply_message(event.reply_token, reply_messages)

        except Exception as e:
            print(f"重置進度時發生錯誤: {e}")
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 重置進度失敗，請檢查資料庫連線或稍後再試。"))
        return

    # 2. 取得玩家當前關卡/狀態資訊
    current_state = get_user_level(user_id)
    
    # [新增] 處理 COMPLETED 狀態
    if current_state == 'COMPLETED':
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="🎉 恭喜您已完成所有挑戰！如果您想重新開始，請輸入「RESET」或「重置」。")
        )
        return

    # [新增] 處理 WELCOME 狀態和 START 啟動指令
    if current_state == 'WELCOME':
        if user_message_upper == 'START' or user_message_upper == '開始':
            # 進入 L01
            try:
                # 設置狀態為 L01_ANSWERING
                update_user_level(user_id, 'L01_ANSWERING')
                
                level_data = get_level_details('L01')
                if level_data:
                    # 解包關卡資訊：(level_id, question_text, question_image_url, correct_answer_raw, next_clue_text, next_clue_image_url)
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
    
    # --- 從這裡開始 current_state 必然是 Lxx_ANSWERING 或 Lxx_WAITING ---

    # 取得當前的基礎關卡 ID (例如從 'L03_ANSWERING' 取得 'L03')
    base_level_id = current_state.split('_')[0] 
    
    # 取得當前關卡的詳細資訊
    current_level_data = get_level_details(base_level_id)
    
    if not current_level_data:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="🚨 遊戲系統錯誤：找不到關卡數據。請聯繫管理員檢查資料庫初始化。")
        )
        return

    # 解包關卡資訊：(level_id, question_text, question_image_url, correct_answer_raw, next_clue_text, next_clue_image_url)
    _, question_text, question_image_url, correct_answer_raw, next_clue_text, next_clue_image_url = current_level_data
    
    
    # 3. 處理等待 (WAITING) 狀態 - 玩家應該要輸入「我到了」/「到」
    if current_state.endswith('_WAITING'):
        
        # 檢查是否為到達確認指令
        if user_input_normalized == '我到了' or user_input_normalized == '到':
            
            # 1. 成功確認到達，準備進入下一關
            try:
                # 找出下一關的 ID
                current_level_num = int(base_level_id[1:])
                next_level_id = 'L' + str(current_level_num + 1).zfill(2)
            except ValueError:
                # 理論上不應該發生，但作為防呆
                next_level_id = 'COMPLETED' 
                
            # 取得下一關的題目資訊
            next_level_data = get_level_details(next_level_id)
            reply_messages = []

            if next_level_data:
                # 還有下一關，發送題目
                
                # 更新狀態到下一關的 ANSWERING 模式
                update_user_level(user_id, f'{next_level_id}_ANSWERING')
                
                _, next_question_text, next_question_image_url, _, _, _ = next_level_data

                reply_messages.append(TextSendMessage(text=f"📍 **確認到達！**\n\n【{next_level_id} 挑戰】\n{next_question_text}"))
                
                # 發送下一關的題目圖片
                if next_question_image_url:
                    reply_messages.append(
                        ImageSendMessage(
                            original_content_url=next_question_image_url,
                            preview_image_url=next_question_image_url 
                        )
                    )
            else:
                # 這是最後一關的到達確認，遊戲結束 (L06之後)
                update_user_level(user_id, 'COMPLETED') 
                reply_messages.append(TextSendMessage(text=LEVEL_DATA['L06']['next_clue']))


            line_bot_api.reply_message(event.reply_token, reply_messages)
            
        else:
            # 提示玩家當前正在等待到達確認
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="請前往下一關地點，請到達後輸入「我到了」或「到」以開始下一關的謎題!")
            )
        return
        
    # 4. 處理答題 (ANSWERING) 狀態
    elif current_state.endswith('_ANSWERING'):
        
        # 答案比對邏輯
        is_correct = user_input_normalized == clean_answer(correct_answer_raw)

        if is_correct:
            # **答對處理：先給線索，然後進入 WAITING 狀態**
            
            # 1. 初始訊息列表
            reply_messages = []
            
            # 這是最後一關的答案
            if base_level_id == 'L06':
                update_user_level(user_id, 'COMPLETED') 
                reply_messages.append(TextSendMessage(text=next_clue_text))
            
            else:
                # 非最後一關，發送下一地點的線索
                
                # 判斷下一關的 ID (L01 -> L02)
                current_level_num = int(base_level_id[1:])
                next_level_id = 'L' + str(current_level_num + 1).zfill(2)
                
                # 更新狀態到 WAITING 模式
                update_user_level(user_id, f'{base_level_id}_WAITING')
                
                # 發送線索/轉場訊息
                reply_messages.append(TextSendMessage(text=next_clue_text))
                
                # 發送線索圖片 (如果有的話)
                if next_clue_image_url:
                    reply_messages.append(
                        ImageSendMessage(
                            original_content_url=next_clue_image_url,
                            preview_image_url=next_clue_image_url 
                        )
                    )
                
                reply_messages.append(TextSendMessage(text="請抵達地點後，輸入「我到了」或「到」來領取下一關的謎題！"))

            line_bot_api.reply_message(event.reply_token, reply_messages)

        else:
            # **答錯處理** - 顯示當前關卡資訊，包含圖片
            reply_messages = [
                TextSendMessage(text="❌ 答案不正確，請再仔細觀察現場或提示。"),
                TextSendMessage(text=f"【當前挑戰：{base_level_id}】\n{question_text}")
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

    else:
        # 處理未知狀態
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="🤔 狀態錯誤，請輸入「RESET」或「重置」來重新開始遊戲。")
        )

# --- 確保在應用程式啟動時運行資料庫初始化 ---
# Gunicorn/Render 啟動時會運行這個區塊，確保資料庫表格和數據存在
with app.app_context():
    setup_db()

if __name__ == "__main__":
    # 本地啟動時使用
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
