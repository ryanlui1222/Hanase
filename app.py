import streamlit as st
from google import genai
from google.genai import types
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

# 【新版接駁方式】初始化 Gemini 客戶端
client = genai.Client(api_key=GEMINI_KEY)

# ==========================================
# 2. Hanase AI 語言專家邏輯 (新版 SDK 寫法)
# ==========================================
def hanase_ai_process(word):
    prompt = f"""
    你是 Hanase 應用的語言導師。使用者提供了生字 '{word}'。
    請執行以下任務並回傳 JSON 格式：
    1. "lang": 識別語言類別 (EN, JA, ES)。
    2. "prototype": 提供該字的原型。
    3. "translations": 提供四語精確翻譯，格式為 {{"zh": "中文", "en": "英文", "ja": "日文", "es": "西文"}}。
       ⚠️ 關鍵要求：日文翻譯若包含漢字，請務必在漢字後方的括號內標註「平假名」讀音（振假名），例如：膨大(ぼうだい)する。絕對不要使用羅馬拼音。
    4. "cloze_sentence": 根據使用者的學術背景，造一個具深度的例句。並將該單字或其變體以 "____" 替代。
    5. "sentence_zh": 該例句的中文翻譯。
    """
    
    try:
        # 【新版接駁方式】使用 client.models.generate_content 並指定 gemini-2.5-flash
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        return json.loads(response.text)
    except Exception as e:
        st.error(f"❌ 解析 '{word}' 時發生錯誤: {str(e)}")
        return None

# ==========================================
# 3. Hanase 介面設計
# ==========================================
st.title("🗣️ Hanase")
st.caption("從「記住」到「說出」——你的多語言輸出夥伴")

tabs = st.tabs(["📥 錄入 (Capture)", "🔍 處理 (Process)", "🧠 複習 (Recall)", "📚 總覽 (Overview)"])

# --- 分頁 1：錄入 (加入防呆機制) ---
with tabs[0]:
    st.subheader("手動新增單詞")
    input_word = st.text_input("在閱讀中遇到了什麼生詞？")
    if st.button("暫存至名單"):
        if input_word:
            # 先去資料庫找找看有沒有一模一樣的字
            existing = supabase.table("vocabulary").select("id").ilike("original_word", input_word).execute().data
            
            if existing:
                st.warning(f"⚠️ 你的字庫裡已經有 '{input_word}' 囉，不需要重複加入！")
            else:
                supabase.table("vocabulary").insert({"original_word": input_word, "status": "pending"}).execute()
                st.success(f"'{input_word}' 已加入待處理名單。")

# --- 分頁 2：處理 (AI 接駁與預覽) ---
with tabs[1]:
    st.subheader("AI 語言自動化解析")
    
    pending_items = supabase.table("vocabulary").select("*").eq("status", "pending").execute().data
    
    if not pending_items:
        st.info("🎉 目前沒有待處理的單詞。")
    else:
        st.write(f"共有 **{len(pending_items)}** 個單詞等待處理：")
        
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
                    if result:
                        try:
                            # 【防禦 2：語意重複檢查】
                            # 使用 AI 翻譯出來的「中文意義」去比對庫中是否已有相同概念的單字
                            zh_meaning = result['translations'].get('zh', '')
                            # 尋找除了自己 (pending) 以外，是否有其他字擁有相同的中文翻譯
                            dupes = supabase.table("vocabulary").select("original_word").eq("trans_zh", zh_meaning).neq("id", item['id']).execute().data
                            
                            if dupes:
                                # 發現語意重複！直接刪除這個 pending 的待處理字
                                supabase.table("vocabulary").delete().eq("id", item['id']).execute()
                                status.update(label=f"⚠️ '{item['original_word']}' 與庫中已有的 '{dupes[0]['original_word']}' 意義相同，已自動剔除。", state="error")
                            else:
                                # 沒有重複，正常更新資料庫
                                update_response = supabase.table("vocabulary").update({
                                    "prototype": result.get('prototype', ''),
                                    "trans_zh": zh_meaning,
                                    "trans_en": result['translations'].get('en', ''),
                                    "trans_ja": result['translations'].get('ja', ''),
                                    "trans_es": result['translations'].get('es', ''),
                                    "example_sentence": result.get('cloze_sentence', ''),
                                    "status": "learning"
                                }).eq("id", item['id']).execute()
                                
                                if len(update_response.data) == 0:
                                    status.update(label=f"⚠️ {item['original_word']} 被資料庫拒絕更新", state="error")
                                else:
                                    status.update(label=f"✅ {item['original_word']} 解析與存檔完成！", state="complete")
                                    
                        except Exception as db_err:
                            status.update(label=f"❌ 資料庫寫入發生異常", state="error")
                            st.error(f"詳細錯誤: {db_err}")
            
                progress_bar.progress((idx + 1) / len(pending_items))
                
            st.success("批次處理執行完畢！")
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
                st.rerun()
            else:
                st.error(f"再試一次！提示：原型是 {word_data['prototype']} / 原始記錄是 {word_data['original_word']}")
    else:
        st.info("太棒了！目前的單字都已經複習完畢。去閱讀更多文章吧！")

# --- 分頁 4：總覽 (進化版：固定欄位開關) ---
with tabs[3]:
    st.subheader("📚 我的多語字庫")
    
    all_words = supabase.table("vocabulary").select("*").in_("status", ["learning", "mastered"]).execute().data
    
    if all_words:
        import pandas as pd
        
        data_list = []
        for w in all_words:
            data_list.append({
                "生字": w.get('original_word', ''),
                "原型": w.get('prototype', ''),
                "中文": w.get('trans_zh', ''),
                "英文": w.get('trans_en', ''),
                "日文": w.get('trans_ja', ''),
                "西文": w.get('trans_es', ''),
                "例句": w.get('example_sentence', '') 
            })
        
        df = pd.DataFrame(data_list)
        
        st.write("💡 **溫習小撇步：** 點擊下方核取方塊來隱藏/顯示特定欄位。")
        
        # 定義你希望的「固定欄位順序」
        fixed_columns = ["生字", "原型", "中文", "英文", "日文", "西文", "例句"]
        
        # 建立與欄位數量相同的橫向排版
        cols = st.columns(len(fixed_columns))
        selected_cols = []
        
        # 在每個橫向區塊中放入 Checkbox
        for i, col_name in enumerate(fixed_columns):
            with cols[i]:
                # 預設全部打勾，使用者可以手動取消勾選來隱藏
                if st.checkbox(col_name, value=True):
                    selected_cols.append(col_name)

        # 確保有選擇欄位才顯示表格
        if selected_cols:
            # df[selected_cols] 會嚴格按照 selected_cols 裡面的順序（即 fixed_columns 的順序）來渲染
            st.dataframe(df[selected_cols], use_container_width=True)
        else:
            st.warning("請至少保留一個欄位以顯示表格。")
            
    else:
        st.write("目前字庫空空如也，快去收集單字吧！")
