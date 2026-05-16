import streamlit as st
import google.generativeai as genai
from supabase import create_client
import json

# ==========================================
# 1. 基礎配置與金鑰安全讀取
# ==========================================
st.set_page_config(page_title="Hanase - Speak Your World", page_icon="🗣️", layout="wide")

try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
    GEMINI_KEY = st.secrets["GEMINI_KEY"]
except KeyError:
    st.error("找不到必要的金鑰設定！請檢查本地的 secrets.toml 或雲端的 Secrets 設定。")
    st.stop()

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
genai.configure(api_key=GEMINI_KEY)

# ==========================================
# 2. Hanase AI 語言專家邏輯 (啟用嚴格 JSON 模式)
# ==========================================
def hanase_ai_process(word):
    # 這裡強制指定 AI 只能輸出 application/json 格式
    model = genai.GenerativeModel(
        'gemini-1.5-flash-latest', # 加上 -latest
        generation_config={"response_mime_type": "application/json"}
    )
    
    prompt = f"""
    你是 Hanase 應用的語言導師。使用者提供了生字 '{word}'。
    請執行以下任務並回傳 JSON 格式：
    1. "lang": 識別語言類別 (EN, JA, ES)。
    2. "prototype": 提供該字的原型。
    3. "translations": 提供四語精確翻譯，格式為 {{"zh": "中文", "en": "英文", "ja": "日文", "es": "西文"}}。
    4. "cloze_sentence": 根據使用者的學術背景，造一個具深度的例句。並將該單字或其變體以 "____" 替代。
    5. "sentence_zh": 該例句的中文翻譯。
    """
    
    try:
        response = model.generate_content(prompt)
        return json.loads(response.text) # JSON 模式下不需要 strip
    except Exception as e:
        # 如果解析失敗，把錯誤印在畫面上讓我們好抓蟲
        st.error(f"❌ 解析 '{word}' 時發生錯誤: {str(e)}")
        if 'response' in locals():
            st.warning(f"AI 原始回傳內容: {response.text}")
        return None

# ==========================================
# 3. Hanase 介面設計
# ==========================================
st.title("🗣️ Hanase")
st.caption("從「記住」到「說出」——你的多語言輸出夥伴")

# 新增為四個分頁
tabs = st.tabs(["📥 錄入 (Capture)", "🔍 處理 (Process)", "🧠 複習 (Recall)", "📚 總覽 (Overview)"])

# --- 分頁 1：錄入 ---
with tabs[0]:
    st.subheader("手動新增單詞")
    input_word = st.text_input("在閱讀中遇到了什麼生詞？")
    if st.button("暫存至名單"):
        if input_word:
            supabase.table("vocabulary").insert({"original_word": input_word, "status": "pending"}).execute()
            st.success(f"'{input_word}' 已加入待處理名單。")

# --- 分頁 2：處理 (AI 接駁與預覽) ---
with tabs[1]:
    st.subheader("AI 語言自動化解析")
    
    # 抓取尚未處理的字
    pending_items = supabase.table("vocabulary").select("*").eq("status", "pending").execute().data
    
    if not pending_items:
        st.info("🎉 目前沒有待處理的單詞。")
    else:
        st.write(f"共有 **{len(pending_items)}** 個單詞等待處理：")
        
        # 顯示待處理清單
        st.dataframe(
            [{"生字": item['original_word'], "加入時間": item['created_at'][:10]} for item in pending_items],
            use_container_width=True
        )
        
        if st.button("啟動 Hanase AI 批次處理", type="primary"):
            progress_bar = st.progress(0)
            for idx, item in enumerate(pending_items):
                with st.status(f"正在解析: {item['original_word']}...", expanded=False) as status:
                    result = hanase_ai_process(item['original_word'])
                    
                    if result:
                        try:
                            # 執行更新並捕捉回傳結果
                            update_response = supabase.table("vocabulary").update({
                                "prototype": result.get('prototype', ''),
                                "trans_zh": result['translations'].get('zh', ''),
                                "trans_en": result['translations'].get('en', ''),
                                "trans_ja": result['translations'].get('ja', ''),
                                "trans_es": result['translations'].get('es', ''),
                                "example_sentence": result.get('cloze_sentence', ''),
                                "status": "learning"
                            }).eq("id", item['id']).execute()
                            
                            # 關鍵檢查：是否有真的更新到資料？
                            if len(update_response.data) == 0:
                                status.update(label=f"⚠️ {item['original_word']} 被資料庫拒絕更新 (可能是 RLS 權限問題)", state="error")
                            else:
                                status.update(label=f"✅ {item['original_word']} 解析與存檔完成！", state="complete")
                                
                        except Exception as db_err:
                            status.update(label=f"❌ 資料庫寫入發生異常", state="error")
                            st.error(f"詳細錯誤: {db_err}")
            
                # 更新進度條
                progress_bar.progress((idx + 1) / len(pending_items))
                
            st.success("批次處理執行完畢！請查看上方是否有錯誤訊息。")
            # 移除 st.rerun()，改為提供一個手動刷新按鈕
            if st.button("🔄 重新載入畫面以更新名單"):
                st.rerun()

# --- 分頁 3：複習 ---
with tabs[2]:
    st.subheader("主動回憶挑戰")
    review_data = supabase.table("vocabulary").select("*").eq("status", "learning").limit(1).execute().data
    
    if review_data:
        word_data = review_data[0]
        st.info(f"**中文意：** {word_data['trans_zh']}")
        st.write(f"**情境挑戰：** {word_data['example_sentence']}")
        
        ans = st.text_input("請填入單詞 (原型或變化型皆可)：", key="review_input")
        if st.button("檢查答案"):
            if ans.lower().strip() in [word_data['original_word'].lower(), word_data['prototype'].lower()]:
                st.balloons()
                st.success("正確！這就是 Hanase 的精神。")
                supabase.table("vocabulary").update({"status": "mastered"}).eq("id", word_data['id']).execute()
            else:
                st.error(f"再試一次！提示：原型是 {word_data['prototype']} / 原始記錄是 {word_data['original_word']}")
    else:
        st.info("太棒了！目前的單字都已經複習完畢。去閱讀更多文章吧！")

# --- 分頁 4：總覽 (全新功能) ---
with tabs[3]:
    st.subheader("我的多語字庫")
    # 抓取 learning 和 mastered 的單字
    all_words = supabase.table("vocabulary").select("*").in_("status", ["learning", "mastered"]).execute().data
    
    if all_words:
        # 使用 Streamlit 原生表格呈現
        formatted_data = []
        for w in all_words:
            formatted_data.append({
                "狀態": "✅ 掌握" if w['status'] == "mastered" else "🧠 學習中",
                "生字": w['original_word'],
                "原型": w['prototype'],
                "中文": w['trans_zh'],
                "英文": w['trans_en'],
                "日文": w['trans_ja'],
                "西文": w['trans_es']
            })
        st.dataframe(formatted_data, use_container_width=True)
    else:
        st.write("目前字庫空空如也，快去收集單字吧！")
