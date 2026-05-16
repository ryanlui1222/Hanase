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
client = genai.Client(api_key=GEMINI_KEY)

# ==========================================
# 2. Hanase AI 語言專家邏輯 (強化防護語言漂移)
# ==========================================
def hanase_ai_process(word):
    prompt = f"""
    你是 Hanase 應用的語言導師。使用者提供的生字是 '{word}'。
    ⚠️ 核心前提：使用者「絕對不會」輸入中文生字來學習。如果這個字看起來像中文，它 100% 是「日文漢字」（例如：膨大、もたらす）。

    請執行以下任務並回傳 JSON 格式：
    1. "lang": 識別語言類別 (EN, JA, ES)。若為漢字請強制判定為 JA。
    2. "prototype": 提供該字的原型。
    3. "translations": 提供四語精確翻譯，格式為 {{"zh": "中文", "en": "英文", "ja": "日文", "es": "西文"}}。
       - ⚠️ 關鍵要求：若為日文，請在漢字後方的括號內標註「平假名」讀音（振假名），例如：膨大(ぼうだい)する。勿用羅馬拼音。
    4. "cloze_sentence": 根據使用者的學術背景，造一個具深度的例句。
       - 🚨 嚴格限制：例句「必須」使用該生字的所屬語言撰寫！（例如：判定為 JA 就必須寫日文例句）。絕對不可以造中文例句。將該單字或其變體以 "____" 替代。
    5. "sentence_zh": 該例句的繁體中文翻譯。
    """
    
    try:
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

# --- 分頁 1：錄入 ---
with tabs[0]:
    st.subheader("手動新增單詞")
    input_word = st.text_input("在閱讀中遇到了什麼生詞？")
    if st.button("暫存至名單"):
        if input_word:
            existing = supabase.table("vocabulary").select("id").ilike("original_word", input_word).execute().data
            if existing:
                st.warning(f"⚠️ 你的字庫裡已經有 '{input_word}' 囉，不需要重複加入！")
            else:
                supabase.table("vocabulary").insert({"original_word": input_word, "status": "pending"}).execute()
                st.success(f"'{input_word}' 已加入待處理名單。")

# --- 分頁 2：處理 ---
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
                        try:
                            zh_meaning = result['translations'].get('zh', '')
                            dupes = supabase.table("vocabulary").select("original_word").eq("trans_zh", zh_meaning).neq("id", item['id']).execute().data
                            
                            if dupes:
                                supabase.table("vocabulary").delete().eq("id", item['id']).execute()
                                status.update(label=f"⚠️ '{item['original_word']}' 與已有的 '{dupes[0]['original_word']}' 意義相同，已剔除。", state="error")
                            else:
                                cloze = result.get('cloze_sentence', '')
                                zh_trans = result.get('sentence_zh', '')
                                combined_sentence = f"{cloze}\n\n{zh_trans}"

                                update_response = supabase.table("vocabulary").update({
                                    "prototype": result.get('prototype', ''),
                                    "trans_zh": zh_meaning,
                                    "trans_en": result['translations'].get('en', ''),
                                    "trans_ja": result['translations'].get('ja', ''),
                                    "trans_es": result['translations'].get('es', ''),
                                    "example_sentence": combined_sentence, 
                                    "status": "learning"
                                }).eq("id", item['id']).execute()
                                
                                if len(update_response.data) == 0:
                                    status.update(label=f"⚠️ {item['original_word']} 被資料庫拒絕更新", state="error")
                                else:
                                    status.update(label=f"✅ {item['original_word']} 解析與存檔完成！", state="complete")
                                    
                        except Exception as db_err:
                            status.update(label=f"❌ 資料庫寫入異常", state="error")
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
        st.markdown(f"**情境挑戰：**\n\n{word_data['example_sentence']}")
        
        ans = st.text_input("請填入單詞 (原型或變化型皆可)：", key="review_input")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("檢查答案", type="primary"):
                if ans.lower().strip() in [word_data['original_word'].lower(), word_data['prototype'].lower()]:
                    st.balloons()
                    st.success("正確！這就是 Hanase 的精神。")
                    supabase.table("vocabulary").update({"status": "mastered"}).eq("id", word_data['id']).execute()
                    st.rerun()
                else:
                    st.error(f"再試一次！提示：原型是 {word_data['prototype']} / 原始記錄是 {word_data['original_word']}")
        with col2:
            if st.button("⏸️ 這字我太熟了，暫停複習"):
                supabase.table("vocabulary").update({"status": "paused"}).eq("id", word_data['id']).execute()
                st.toast("已將單字移入暫停區！")
                st.rerun()
    else:
        st.info("太棒了！目前的單字都已經複習完畢。去閱讀更多文章吧！")

# --- 分頁 4：總覽 (解決排版擁擠與換行問題) ---
with tabs[3]:
    st.subheader("📚 我的多語字庫")
    
    filter_option = st.radio(
        "快速篩選：", 
        ["全部單字", "🧠 僅顯示學習中", "⏸️ 僅顯示暫停", "✅ 僅顯示已掌握"], 
        horizontal=True
    )
    
    if filter_option == "🧠 僅顯示學習中":
        status_filter = ["learning"]
    elif filter_option == "⏸️ 僅顯示暫停":
        status_filter = ["paused"]
    elif filter_option == "✅ 僅顯示已掌握":
        status_filter = ["mastered"]
    else:
        status_filter = ["learning", "mastered", "paused"]

    all_words = supabase.table("vocabulary").select("*").in_("status", status_filter).execute().data
    
    if all_words:
        import pandas as pd
        data_list = []
        for w in all_words:
            # 狀態圖示轉換
            if w['status'] == "mastered":
                status_icon = "✅ 掌握"
            elif w['status'] == "paused":
                status_icon = "⏸️ 暫停"
            else:
                status_icon = "🧠 學習中"
            
            # 【關鍵修改】將縫合的例句拆分為雙欄位，解決表格換行痛點
            ex_sentence = w.get('example_sentence', '')
            if '\n\n' in ex_sentence:
                cloze_part, zh_part = ex_sentence.split('\n\n', 1)
            else:
                cloze_part = ex_sentence
                zh_part = ""
                
            data_list.append({
                "db_id": w['id'], 
                "狀態": status_icon,
                "生字": w.get('original_word', ''),
                "原型": w.get('prototype', ''),
                "中文": w.get('trans_zh', ''),
                "英文": w.get('trans_en', ''),
                "日文": w.get('trans_ja', ''),
                "西文": w.get('trans_es', ''),
                "外文例句": cloze_part, 
                "中文例句": zh_part   
            })
        
        df = pd.DataFrame(data_list)
        st.write("💡 **操作提示：** 點擊「狀態」欄位可直接切換單字狀態。更改後請點擊下方按鈕儲存。")
        
        # 欄位總清單
        fixed_columns = ["狀態", "生字", "原型", "中文", "英文", "日文", "西文", "外文例句", "中文例句"]
        
        # 【關鍵修改】預設開啟的核心欄位 (隱藏其他語種以釋放版面空間)
        default_on = ["狀態", "生字", "中文", "外文例句", "中文例句"]
        
        cols = st.columns(len(fixed_columns))
        selected_cols = []
        
        for i, col_name in enumerate(fixed_columns):
            with cols[i]:
                # 若欄位在 default_on 中，預設打勾；否則預設取消
                if st.checkbox(col_name, value=(col_name in default_on)):
                    selected_cols.append(col_name)

        if selected_cols:
            cols_to_show = ["db_id"] + selected_cols if "狀態" in selected_cols else ["db_id", "狀態"] + selected_cols
            
            edited_df = st.data_editor(
                df[cols_to_show],
                column_config={
                    "db_id": None, 
                    "狀態": st.column_config.SelectboxColumn(
                        "狀態 (點擊修改)",
                        help="選擇單字的學習狀態",
                        options=["🧠 學習中", "✅ 掌握", "⏸️ 暫停"],
                        required=True
                    ),
                    "外文例句": st.column_config.TextColumn(width="large"),
                    "中文例句": st.column_config.TextColumn(width="large")
                },
                disabled=["生字", "原型", "中文", "英文", "日文", "西文", "外文例句", "中文例句"],
                use_container_width=True,
                hide_index=True
            )
            
            if st.button("💾 儲存所有狀態變更", type="primary"):
                with st.spinner("更新資料庫中..."):
                    for index, row in edited_df.iterrows():
                        orig_status = df.loc[index, "狀態"]
                        new_status = row["狀態"]
                        
                        if orig_status != new_status:
                            status_map = {"🧠 學習中": "learning", "✅ 掌握": "mastered", "⏸️ 暫停": "paused"}
                            db_status = status_map.get(new_status, "learning")
                            supabase.table("vocabulary").update({"status": db_status}).eq("id", row["db_id"]).execute()
                            
                    st.success("狀態已成功更新！")
                    st.rerun() 
        else:
            st.warning("請至少保留一個欄位以顯示表格。")
    else:
        st.info("這個分類下目前沒有單字喔！")
