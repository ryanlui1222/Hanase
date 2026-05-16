import streamlit as st
import google.generativeai as genai
from supabase import create_client
import json

# ==========================================
# 1. 基礎配置
# ==========================================
try:
    # 當部署到雲端或在本地有 secrets.toml 時，Streamlit 會自動讀取
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
    GEMINI_KEY = st.secrets["GEMINI_KEY"]
except KeyError:
    st.error("找不到必要的金鑰設定！請檢查本地的 secrets.toml 或雲端的 Secrets 設定。")
    st.stop()

# 初始化客戶端
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
genai.configure(api_key=GEMINI_KEY)

# ==========================================
# 2. Hanase AI 語言專家邏輯
# ==========================================
def hanase_ai_process(word):
    # 加上 generation_config 強制 AI 只輸出純 JSON
    model = genai.GenerativeModel(
        'gemini-1.5-flash',
        generation_config={"response_mime_type": "application/json"}
    )
    
    # 針對 Hanase 的專業定位優化 Prompt
    prompt = f"""
    你是 Hanase 應用的語言導師。使用者提供了生字 '{word}'。
    請執行以下任務並回傳 JSON 格式：
    1. 識別語言類別 (EN, JA, ES)。
    2. 提供該字的原型 (prototype)。
    3. 提供四語精確翻譯 (zh, en, ja, es)。
    4. 根據使用者的學術職業背景，造一個在日常生活或學術會議用得上的例句。
    5. 在例句中，將該單字或其變體以 "____" 替代，以便進行測驗。
    
    JSON 格式範例：
    {{
      "lang": "ES",
      "prototype": "hablar",
      "translations": {{"zh": "說話", "en": "to speak", "ja": "話す", "es": "hablar"}},
      "cloze_sentence": "Es necesario ____ sobre la política.",
      "sentence_zh": "討論政治是必要的。"
    }}
    請嚴格只回傳 JSON 內容。
    """
    try:
        response = model.generate_content(prompt)
        # 因為已經強制是 JSON，所以不需要再做任何 strip() 清理
        return json.loads(response.text)
    except Exception as e:
        st.error(f"解析發生錯誤: {e}")
        return None

# ==========================================
# 3. Hanase 介面設計
# ==========================================
st.title("🗣️ Hanase")
st.caption("從「記住」到「說出」")

tabs = st.tabs(["📥 錄入 (Capture)", "🔍 處理 (Process)", "🧠 複習 (Recall)"])

# --- 分頁 1：錄入 ---
with tabs[0]:
    st.subheader("新增單詞到 Hanase")
    input_word = st.text_input("在閱讀中遇到了什麼生詞？")
    if st.button("暫存至名單"):
        if input_word:
            supabase.table("vocabulary").insert({"original_word": input_word, "status": "pending"}).execute()
            st.success(f"'{input_word}' 已加入待處理名單。")

# --- 分頁 2：處理 (AI 接駁) ---
with tabs[1]:
    st.subheader("AI 語言自動化解析")
    # 抓取尚未處理的字
    pending_items = supabase.table("vocabulary").select("*").eq("status", "pending").execute().data
    
    if not pending_items:
        st.write("目前沒有待處理的單詞。")
    else:
        st.write(f"共有 {len(pending_items)} 個單詞等待 AI 解析。")
        if st.button("啟動 Hanase AI 批次處理"):
            for item in pending_items:
                with st.status(f"正在解析 {item['original_word']}..."):
                    result = hanase_ai_process(item['original_word'])
                    if result:
                        supabase.table("vocabulary").update({
                            "prototype": result['prototype'],
                            "trans_zh": result['translations']['zh'],
                            "trans_en": result['translations']['en'],
                            "trans_ja": result['translations']['ja'],
                            "trans_es": result['translations']['es'],
                            "example_sentence": result['cloze_sentence'],
                            "status": "learning"
                        }).eq("id", item['id']).execute()
            st.rerun()

# --- 分頁 3：複習 ---
with tabs[2]:
    st.subheader("主動回憶挑戰")
    review_data = supabase.table("vocabulary").select("*").eq("status", "learning").limit(1).execute().data
    
    if review_data:
        word_data = review_data[0]
        st.info(f"**中文意：** {word_data['trans_zh']}")
        st.write(f"**情境挑戰：** {word_data['example_sentence']}")
        
        ans = st.text_input("請填入單詞：", key="review_input")
        if st.button("檢查答案"):
            if ans.lower() in [word_data['original_word'].lower(), word_data['prototype'].lower()]:
                st.balloons()
                st.success("正確！這就是 Hanase 的精神。")
                # 簡單邏輯：對了就完成
                supabase.table("vocabulary").update({"status": "mastered"}).eq("id", word_data['id']).execute()
            else:
                st.error(f"再試一次！提示：原型是 {word_data['prototype']}")
