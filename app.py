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
# **【更新】新增 special_hint 欄位**
LEVEL_DATA = {
    'L01': {
        'intro_text': '【起點：啟程與覺醒】\n\n旅程從圓山開始。百年前的火車聲與風聲交織，你是否聽見「大龍峒」的呼喚？\n\n這裡，是圓山的起點，也是時間的入口。',
        'question': '圓山站的日文拼音是什麼？（出捷運站時，有聽到廣播嗎？），請輸入羅馬拼音。',
        'question_image': None,
        'answer': 'Maruyama',
        'special_hint': '🤔 提示：請仔細聆聽或查看捷運站內的指示牌。記得要輸入羅馬拼音，且首字母大寫喔！', # NEW
        'next_clue': '✅ 答對了！從圓山啟程，接下來，我們要走進知識與禮樂的門。\n\n請前往https://maps.app.goo.gl/tTZJFnZTRwAq2f36A',
        'next_clue_image': None,
        'next_level_id': 'L02'
    },
    'L02': {
        'intro_text': '【知識之門】現在您站在知識的殿堂前，這些古老的符號將引導您進入文化的深處。',
        'question': '🙏🎸🏹🐴🧮✈️ 這六個符號分別代表什麼？',
        'question_image': None,
        'answer': '禮樂射御書數',
        'special_hint': '🤔 提示：這些符號代表著古代君子必須學習的「六藝」。請試著輸入中文全稱，不要輸入空格。', # NEW
        'next_clue': '✅ 很好！你已通過學問之門。下一站，前往信仰與教化交會之地。\n\n請前往https://maps.app.goo.gl/gD9w5eFzRzJ8fX9A7',
        'next_clue_image': None,
        'next_level_id': 'L03'
    },
    'L03': {
        'intro_text': '【信仰的起點】書聲與香煙共存的地方。在祈願平安的同時，也是人心安定、學問延續的起點。',
        'question': '尋找前殿中的兩組刻字，尋獲後將兩位置連線，沿射線方向走進宮內，在宮內最高樓，石雕龍柱正下方的光源附近，有通往下一關的線索，請找到並將線索輸入line bot。',
        'question_image': "https://github.com/12eeee1/YCtravel/blob/master/images/06.jpg",
        'answer': '保安', 
        'special_hint': '🤔 提示：1.刻字在頭頂，而且靠近(面向宮內)的左側  2.找到刻字後，需要連成射線，而且終點也會在射線上方。', # NEW
        'next_clue': '✅ 恭喜解鎖 L04！請回到保安宮正門對面，找到圖片中的石碑，石碑後方草叢藏著下一關的線索!',
        'next_clue_image': None,
        'next_level_id': 'L04'
    },
    'L04': {
        'intro_text': '【社區的記憶】街市早已消逝，但聲音仍在巷弄中回蕩。每一「坎」都有一段故事，每一扇門後都藏著時間的皺摺。拼湊過去的碎片，你會找到通往下一扇門的字。',
        'question': '請回到保安宮正門口，找到圖片中的指定位置，尋找實體寶藏、並從中獲取題目。',
        'question_image': 'https://raw.githubusercontent.com/12eeee1/YCtravel/refs/heads/master/images/04.jpg',
        'answer': '頂', 
        'special_hint': '🤔 提示：完成填字遊戲，看看它最後形成什麼字？「這個字」有「上下」的對應。', # NEW
        'next_clue': '✅ 恭喜答對！頂街與下街，是大龍峒的兩端。接下來，前往那條藏著書香的巷弄。座標位置：https://maps.app.goo.gl/Wzpf5jd2DcXxvUoZ7',
        'next_clue_image': None,
        'next_level_id': 'L05'
    },
    'L05': {
        'intro_text': '【祈願之所】書聲藏於巷內。每年考季，供桌上會出現象徵靈感與好運的供品。百年樹人，書香未歇。',
        'question': '請到尋找祈願卡(上面標有"圓山探險隊"，在比較低的位置)，並從中獲取題目',
        'question_image': '紙上寫著甚麼呢?',
        'answer': '鳳梨', 
        'special_hint': '🤔 提示：請在書院內尋找一個「有頭有臉」的吉祥物，它代表著「旺來」的好運。', # NEW
        'next_clue': '鳳梨罐頭象徵「旺來」，而礦泉水，則寓意文思泉湧。昔日書聲朗朗今藏於巷，百年樹人，書香仍在。接下來，請用心「聽見」下一個線索。座標位置：\n\n請前往https://maps.app.goo.gl/6Wa1PMt1Ta3dPvTaA',
        'next_clue_image': None,
        'next_level_id': 'L06'
    },
    'L06': {
        'intro_text': '【理解之課】不是所有聲音都用耳朵聽見，有些知識，必須用心感受。手語，是另一種文字，書本，是另一種傾聽。找到那本藏著老師府舊影的書吧——它會指引你前往最終的歸宿。',
        'question': '解開題目後，可以跟我確認答案(不須輸入空格、標點符號)',
        'question_image': "https://raw.githubusercontent.com/12eeee1/YCtravel/refs/heads/master/images/05.jpg",
        'answer': '53878337515', 
        'special_hint': '🤔 提示：手語有許多系統，請以美國系統解答。', # NEW
        'next_clue': '恭喜你找到了那本書的索書碼，請前往圖書館找出此書',
        'next_clue_image': None,
        'next_level_id': 'L07'
    },
        'L07': {
        'intro_text': '【歸返與傳承】你來到老師府，書中的照片，重現在你眼前。老師已不在，但他的精神仍在街巷間流動。教育、信仰、知識、理解——這些線索在你腳下交織成故事的圓。',
        'question': '依據書中照片的線索，找到一個實體寶藏',
        'question_image': None,
        'answer': '53878337515', 
        'special_hint': '現代的老師里，都有守望相助隊，我們認為這是延續昔日鄰里互助的精神。', # NEW
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
    """初始化 PostgreSQL 資料庫表格並載入關卡數據，並執行必要的遷移。"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # === 【修復】資料庫遷移：確保 intro_text 欄位存在 ===
        try:
            cursor.execute("""
                ALTER TABLE levels
                ADD COLUMN intro_text TEXT;
            """)
            conn.commit()
            print("資料庫遷移成功：已為 levels 表格新增 intro_text 欄位。")
        except psycopg2.ProgrammingError as e:
            if 'already exists' in str(e):
                conn.rollback() 
                print("intro_text 欄位已存在，略過遷移。")
            elif 'does not exist' in str(e):
                conn.rollback()
            else:
                raise e 
        # === 遷移結束 ===
        
        # === 【新增】資料庫遷移：確保 special_hint 欄位存在 ===
        try:
            cursor.execute("""
                ALTER TABLE levels
                ADD COLUMN special_hint TEXT;
            """)
            conn.commit()
            print("資料庫遷移成功：已為 levels 表格新增 special_hint 欄位。")
        except psycopg2.ProgrammingError as e:
            if 'already exists' in str(e):
                conn.rollback() 
                print("special_hint 欄位已存在，略過遷移。")
            elif 'does not exist' in str(e):
                conn.rollback()
            else:
                raise e
        # === 遷移結束 ===


        # 1. 建立 levels 表格 (如果不存在) - 包含最新的 intro_text 和 special_hint
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS levels (
                level_id VARCHAR(10) PRIMARY KEY,
                intro_text TEXT, 
                special_hint TEXT,           -- 新增：特殊提示
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
                current_level VARCHAR(50) NOT NULL, -- 使用 VARCHAR(50) 以容納狀態字串
                last_activity_time TIMESTAMP WITHOUT TIME ZONE
            );
        """)
        
        # 3. 匯入或更新關卡數據：這裡使用 UPDATE 確保每次部署時數據都是最新的
        for level_id, data in LEVEL_DATA.items():
            # 檢查資料是否存在，如果不存在則 INSERT，存在則 UPDATE
            cursor.execute("SELECT level_id FROM levels WHERE level_id = %s", (level_id,))
            
            if cursor.fetchone():
                # 存在則 UPDATE (【更新】包含 intro_text 和 special_hint)
                cursor.execute(
                    """
                    UPDATE levels
                    SET intro_text = %s, special_hint = %s, question_text = %s, question_image_url = %s, correct_answer = %s, next_clue_text = %s, next_clue_image_url = %s
                    WHERE level_id = %s;
                    """,
                    (
                        data['intro_text'], 
                        data['special_hint'],    # 新增欄位
                        data['question'], 
                        data['question_image'],
                        data['answer'], 
                        data['next_clue'], 
                        data['next_clue_image'],
                        level_id
                    )
                )
            else:
                # 不存在則 INSERT (【更新】包含 intro_text 和 special_hint)
                cursor.execute(
                    """
                    INSERT INTO levels (level_id, intro_text, special_hint, question_text, question_image_url, correct_answer, next_clue_text, next_clue_image_url)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (
                        level_id, 
                        data['intro_text'], 
                        data['special_hint'],    # 新增欄位
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
    # 【更新】新增 special_hint 欄位，現在總共選擇 8 個欄位
    cursor.execute("SELECT level_id, intro_text, special_hint, question_text, question_image_url, correct_answer, next_clue_text, next_clue_image_url FROM levels WHERE level_id = %s", (level_id,))
    # 回傳結果：(level_id, intro_text, special_hint, question_text, question_image_url, correct_answer, next_clue_text, next_clue_image_url)
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
                    # 解包關卡資訊 (現在有 8 個元素，跳過 special_hint 和答案相關)
                    # (level_id, intro_text, special_hint, question_text, question_image_url, correct_answer_raw, next_clue_text, next_clue_image_url)
                    _, intro_text, _, question_text, question_image_url, _, _, _ = level_data
                    
                    reply_messages = [
                        TextSendMessage(text="🚀 旅程開始！祝您探險愉快。"),
                        TextSendMessage(text=intro_text), # 發送介紹文案
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

    # 解包關卡資訊：(level_id, intro_text, special_hint, question_text, question_image_url, correct_answer_raw, next_clue_text, next_clue_image_url)
    _, intro_text, special_hint, question_text, question_image_url, correct_answer_raw, next_clue_text, next_clue_image_url = current_level_data
    
    
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
                next_level_id = 'COMPLETED' 
                
            # 取得下一關的題目資訊
            next_level_data = get_level_details(next_level_id)
            reply_messages = []

            if next_level_data:
                # 還有下一關，發送題目
                
                # 更新狀態到下一關的 ANSWERING 模式
                update_user_level(user_id, f'{next_level_id}_ANSWERING')
                
                # 解包下一關資訊 (現在有8個元素)
                # (level_id, intro_text, special_hint, question_text, question_image_url, correct_answer_raw, next_clue_text, next_clue_image_url)
                _, next_intro_text, _, next_question_text, next_question_image_url, _, _, _ = next_level_data

                # 發送確認到達訊息
                reply_messages.append(TextSendMessage(text=f"📍 **確認到達！**"))
                
                # 發送下一關的介紹文案
                if next_intro_text:
                    reply_messages.append(TextSendMessage(text=next_intro_text)) 
                    
                # 發送下一關的題目
                reply_messages.append(TextSendMessage(text=f"【{next_level_id} 挑戰】\n{next_question_text}"))
                
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
                # 檢查 LEVEL_DATA 中是否有下一關，如果沒有則視為 COMPLETED
                next_level_id = 'L' + str(current_level_num + 1).zfill(2)
                
                # 更新狀態到 WAITING 模式 (使用當前關卡 ID，但狀態改為 WAITING)
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
            # **答錯處理** - 僅發送特殊提示 (special_hint)
            reply_messages = [
                TextSendMessage(text="❌ 答案不正確！這裡有個小小的提示幫助你："),
                # 只發送特殊提示，不重複發送 intro_text 或 question_text
                TextSendMessage(text=special_hint) 
            ]

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
# git add app.py
#git commit -m "Feature: Added question_image_url field and display L01 image on RESET."
#git push origin master