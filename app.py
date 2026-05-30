import streamlit as st
from google import genai
from google.genai import types
from supabase import create_client
import json
import time
import pandas as pd
from gtts import gTTS
import io

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
# 2. Hanase AI 語言專家邏輯
# ==========================================
def hanase_ai_process(word):
    prompt = f"""
    你是 Hanase 應用的語言導師。使用者提供了生字 '{word}'。
    請執行以下任務並回傳 JSON 格式：
    1. "lang": 識別語言類別 (EN, JA, ES)。
    2. "prototype": 提供該字的原型。
    3. "translations": 提供四語精確翻譯，格式為 {{"zh": "中文", "en": "英文", "ja": "日文", "es": "西文"}}。
       ⚠️ 關鍵要求：日文若包含漢字，務必在括號內標註平假名（振假名），如：膨大(ぼうだい)する。勿用羅馬拼音。
    4. "cloze_sentence": 根據使用者的學術背景，造一個具深度的例句。
       ⚠️ 極重要限制：例句「必須」使用該生字所屬的原始語言撰寫！（例如：該生字為日文，就必須造日文例句；生字為西文，就造西文例句）。並將該單字或其變體以 "____" 替代。
    5. "sentence_zh": 該例句的中文翻譯。
    """
    
    try:
        response = client.models.generate_content(
            model='gemini-3.1-flash-lite', # 🚀 已切換為高配額、高反應速度的 Lite 模型
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        return json.loads(response.text)
    except Exception as e:
        # 如果發生錯誤 (如 429 速限保護)，這裡會捕捉並回傳 None，觸發後續的斷路器
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
        clean_word = input_word.strip()
        if clean_word:
            existing = supabase.table("vocabulary").select("id").ilike("original_word", clean_word).execute().data
            if existing:
                st.warning(f"⚠️ 你的字庫裡已經有 '{clean_word}' 囉，不需要重複加入！")
            else:
                supabase.table("vocabulary").insert({"original_word": clean_word, "status": "pending"}).execute()
                st.success(f"'{clean_word}' 已加入待處理名單。")

# --- 分頁 2：處理 (AI 接駁與預覽) ---
with tabs[1]:
    st.subheader("AI 語言自動化解析")
    
    # 狀態記憶：是否正在全自動處理中
    if "auto_process" not in st.session_state:
        st.session_state.auto_process = False

    # ＝＝＝＝＝ 狀態 A：自動處理進行中 ＝＝＝＝＝
    if st.session_state.auto_process:
        st.warning("⏳ 系統正在全自動批次處理中，請放置此畫面不要關閉網頁...")
        if st.button("🛑 暫停處理 (緊急停止)"):
            st.session_state.auto_process = False
            st.rerun()

        # 每次只抓 3 個字來處理，避免 Streamlit 30 秒超時
        batch_items = supabase.table("vocabulary").select("*").eq("status", "pending").limit(3).execute().data
        
        if not batch_items:
            st.session_state.auto_process = False
            st.success("✨ 太棒了！所有單字皆已處理完畢！")
            time.sleep(2)
            st.rerun()
        else:
            progress_bar = st.progress(0)
            for idx, item in enumerate(batch_items):
                word = item['original_word']
                word_id = item['id']
                
                with st.status(f"正在解析: {word}...", expanded=False) as status:
                    result = hanase_ai_process(word)
                    
                    if result:
                        try:
                            # 語意去重檢查
                            zh_meaning = result['translations'].get('zh', '')
                            dupes = supabase.table("vocabulary").select("original_word").eq("trans_zh", zh_meaning).neq("id", word_id).execute().data
                            
                            if dupes:
                                supabase.table("vocabulary").delete().eq("id", word_id).execute()
                                status.update(label=f"⚠️ '{word}' 與庫中的 '{dupes[0]['original_word']}' 意義重複，已自動剔除。", state="error")
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
                                }).eq("id", word_id).execute()
                                
                                if len(update_response.data) == 0:
                                    status.update(label=f"⚠️ {word} 被資料庫拒絕更新", state="error")
                                else:
                                    status.update(label=f"✅ {word} 解析完成！", state="complete")
                        except Exception as db_err:
                            status.update(label=f"❌ 資料庫寫入異常", state="error")
                            st.error(f"詳細錯誤: {db_err}")
                    else:
                        # 【斷路器防護】如果 AI 回傳 None (例如觸發 429 速限)
                        status.update(label=f"❌ API 暫時阻擋了對 '{word}' 的解析", state="error")
                        st.error("⚠️ 已觸發 Gemini API 的速限保護。全自動處理已緊急暫停！")
                        st.info("💡 解決方案：請關閉自動模式，稍待片刻後再重新啟動批次處理。")
                        
                        st.session_state.auto_process = False
                        break # 中斷目前的 for 迴圈，不再送出這批次剩下的字
                            
                # 🏎️ 極速調校：配合 15 RPM 的限制，設定為 4 秒休眠，達成最快處理速度
                progress_bar.progress((idx + 1) / len(batch_items))
                time.sleep(4) 
                
            # 判斷是否要接力刷新
            if st.session_state.auto_process:
                st.rerun()

# ＝＝＝＝＝ 狀態 B：閒置中 (顯示表格) ＝＝＝＝＝
    else:
        pending_items = supabase.table("vocabulary").select("*").eq("status", "pending").execute().data
        
        if not pending_items:
            st.info("🎉 目前沒有待處理的單詞。")
        else:
            st.write(f"共有 **{len(pending_items)}** 個單詞等待處理：")
            
            df_pending = pd.DataFrame([
                {"db_id": item['id'], "🗑️ 勾選刪除": False, "生字": item['original_word'], "加入時間": item['created_at'][:10]} 
                for item in pending_items
            ])
            
            # 【修改 1】將 "生字" 從 disabled 中移除，讓它可以被編輯
            edited_pending = st.data_editor(
                df_pending,
                column_config={"db_id": None},
                disabled=["加入時間"], 
                use_container_width=True,
                hide_index=True
            )
            
            # 將按鈕排成三列
            col1, col2, col3 = st.columns(3)
            
            with col1:
                if st.button("🚀 自動處理全部生字", type="primary"):
                    st.session_state.auto_process = True
                    st.rerun()

            with col2:
                # 【新增】儲存修改按鈕
                if st.button("💾 儲存生字修改"):
                    with st.spinner("儲存中..."):
                        for idx, row in edited_pending.iterrows():
                            orig_word = df_pending.loc[idx, "生字"]
                            new_word = row["生字"]
                            if orig_word != new_word:
                                supabase.table("vocabulary").update({"original_word": new_word}).eq("id", row["db_id"]).execute()
                        st.success("修改已儲存！")
                        st.rerun()

            with col3:
                if st.button("🗑️ 刪除勾選單字"):
                    items_to_delete = edited_pending[edited_pending["🗑️ 勾選刪除"] == True]
                    for idx, row in items_to_delete.iterrows():
                        supabase.table("vocabulary").delete().eq("id", row['db_id']).execute()
                    if len(items_to_delete) > 0:
                        st.success(f"已成功刪除 {len(items_to_delete)} 個單字！")
                        st.rerun()
                    else:
                        st.warning("請先在表格中勾選要刪除的單字。")

# --- 分頁 3：複習 ---
with tabs[2]:
    st.subheader("主提回憶挑戰")
    review_data = supabase.table("vocabulary").select("*").eq("status", "learning").limit(1).execute().data
    
    if review_data:
        word_data = review_data[0]
        st.info(f"**中文意：** {word_data['trans_zh']}")
        
        st.markdown(f"**情境挑戰：**\n{word_data['example_sentence']}")
        
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

# --- 分頁 4：總覽 ---
with tabs[3]:
    st.subheader("📚 我的多語字庫")
    
    # 【修改 3】發音欄位與口音直接掛鉤
    play_lang = st.radio(
        "🔊 點擊播放的欄位與口音：", 
        ["日文", "英文", "西文"], 
        horizontal=True,
        help="選擇後，點擊下方表格的播放鍵，將會朗讀該單字對應語言的欄位內容。"
    )
    
    filter_option = st.radio(
        "快速篩選：", 
        ["全部單字", "🧠 僅顯示學習中", "⏸️ 僅顯示暫停", "✅ 僅顯示已掌握"], 
        horizontal=True
    )
    
    # 【新增 2】編輯模式防誤觸開關
    edit_mode = st.toggle("✏️ 啟用編輯模式 (解鎖狀態與文字修改)")
    
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
        data_list = []
        for w in all_words:
            if w['status'] == "mastered":
                status_icon = "✅ 掌握"
            elif w['status'] == "paused":
                status_icon = "⏸️ 暫停"
            else:
                status_icon = "🧠 學習中"
                
            data_list.append({
                "db_id": w['id'], 
                "狀態": status_icon,
                "生字": w.get('original_word', ''),
                "原型": w.get('prototype', ''),
                "中文": w.get('trans_zh', ''),
                "英文": w.get('trans_en', ''),
                "日文": w.get('trans_ja', ''),
                "西文": w.get('trans_es', ''),
                "例句": w.get('example_sentence', '') 
            })
        
        df = pd.DataFrame(data_list)
        
        if "last_play_state" not in st.session_state:
            st.session_state.last_play_state = {}
            
        if edit_mode:
            st.info("💡 **編輯模式已開啟：** 您現在可以修改表格內的任何文字或狀態，修改完畢請點擊下方按鈕儲存。")
        else:
            st.write("💡 **閱讀模式：** 勾選「🔊 播放」即可發音，想聽下一個字不需取消打勾。若需修改資料請開啟上方編輯模式。")
        
        fixed_columns = ["狀態", "生字", "原型", "中文", "英文", "日文", "西文", "例句"]
        cols = st.columns(len(fixed_columns))
        selected_cols = []
        
        for i, col_name in enumerate(fixed_columns):
            with cols[i]:
                if st.checkbox(col_name, value=True):
                    selected_cols.append(col_name)

        if selected_cols:
            cols_to_show = ["db_id"] + selected_cols if "狀態" in selected_cols else ["db_id", "狀態"] + selected_cols
            
            display_df = df[cols_to_show].copy()
            display_df.insert(1, "🔊 播放", False) 
            
            # 根據編輯模式決定鎖定哪些欄位 (確保播放鍵永遠可點)
            if edit_mode:
                disabled_cols = [] # 全部解鎖
            else:
                disabled_cols = ["狀態", "生字", "原型", "中文", "英文", "日文", "西文", "例句"] # 鎖死除了播放鍵以外的所有欄位

            edited_df = st.data_editor(
                display_df,
                column_config={
                    "db_id": None, 
                    "🔊 播放": st.column_config.CheckboxColumn("🔊 播放", help="勾選即可聆聽"),
                    "狀態": st.column_config.SelectboxColumn(
                        "狀態",
                        options=["🧠 學習中", "✅ 掌握", "⏸️ 暫停", "🗑️ 徹底刪除"],
                        required=True
                    )
                },
                disabled=disabled_cols,
                use_container_width=True,
                hide_index=True
            )
            
            # 【發音邏輯】
            current_play_state = edited_df["🔊 播放"].to_dict()
            newly_checked_idx = None

            for idx, is_checked in current_play_state.items():
                was_checked = st.session_state.last_play_state.get(idx, False)
                if is_checked and not was_checked:
                    newly_checked_idx = idx
                    break

            st.session_state.last_play_state = current_play_state

            if newly_checked_idx is not None:
                # 核心改動：使用者選「日文」，就去讀「日文」那一欄的內容
                text_to_read = edited_df.loc[newly_checked_idx, play_lang]
                
                if pd.notna(text_to_read) and str(text_to_read).strip() != "":
                    lang_map = {"日文": "ja", "英文": "en", "西文": "es"}
                    try:
                        tts = gTTS(text=str(text_to_read), lang=lang_map[play_lang])
                        fp = io.BytesIO()
                        tts.write_to_fp(fp)
                        fp.seek(0)
                        st.audio(fp.read(), format="audio/mp3", autoplay=True)
                    except Exception as e:
                        st.toast(f"發音產生失敗：{e}")
                else:
                    st.toast(f"這個單字的「{play_lang}」欄位是空的喔！")

            
            # --- 全面儲存邏輯 (僅在編輯模式顯示) ---
            if edit_mode:
                if st.button("💾 儲存所有變更", type="primary"):
                    with st.spinner("更新資料庫中..."):
                        for index, row in edited_df.iterrows():
                            # 建立一個字典來收集有被修改的欄位
                            update_payload = {}
                            
                            # 1. 檢查狀態是否改變
                            orig_status = df.loc[index, "狀態"]
                            new_status = row.get("狀態", orig_status)
                            if orig_status != new_status:
                                if new_status == "🗑️ 徹底刪除":
                                    supabase.table("vocabulary").delete().eq("id", row["db_id"]).execute()
                                    continue # 刪除後不需執行後續更新
                                else:
                                    status_map = {"🧠 學習中": "learning", "✅ 掌握": "mastered", "⏸️ 暫停": "paused"}
                                    update_payload["status"] = status_map.get(new_status, "learning")
                            
                            # 2. 檢查各文字欄位是否改變
                            col_map = {
                                "生字": "original_word", "原型": "prototype", 
                                "中文": "trans_zh", "英文": "trans_en", 
                                "日文": "trans_ja", "西文": "trans_es", "例句": "example_sentence"
                            }
                            for zh_col, db_col in col_map.items():
                                if zh_col in row and row[zh_col] != df.loc[index, zh_col]:
                                    update_payload[db_col] = row[zh_col]
                            
                            # 3. 若有任何改變，則寫入 Supabase
                            if update_payload:
                                supabase.table("vocabulary").update(update_payload).eq("id", row["db_id"]).execute()
                                
                        st.success("所有變更已成功更新！")
                        st.rerun() 
        else:
            st.warning("請至少保留一個欄位以顯示表格。")
    else:
        st.info("這個分類下目前沒有單字喔！")
