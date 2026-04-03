import streamlit as st
import pandas as pd
import plotly.express as px
import io
from google import genai
import re

# ページレイアウトを「wide」に設定
st.set_page_config(page_title="AIポートフォリオ診断ダッシュボード", layout="wide")

# サイドバー設定
st.sidebar.title("設定")
gemini_key = st.sidebar.text_input("Gemini APIキー", type="password")

# メイン画面タイトル
st.title("📊 AIポートフォリオ診断ダッシュボード")

# テキストエリア配置
raw_text = st.text_area("マネーフォワードや証券会社の『保有銘柄一覧』の表をコピーして、ここに貼り付けてください", height=200)

if raw_text.strip():
    # 入力テキストにタブが含まれていればタブ区切り、それ以外はカンマ区切りと判定
    sep = '\t' if '\t' in raw_text else ','
    
    try:
        # 文字列をデータフレームとして読み込み
        df = pd.read_csv(io.StringIO(raw_text), sep=sep)
        
        st.write("### 📋 読み込んだデータ（プレビュー）")
        st.dataframe(df)
        
        if not df.empty and len(df.columns) >= 2:
            # カラム選択のドロップダウンを2列で配置
            col1, col2 = st.columns(2)
            name_col = col1.selectbox("銘柄名・ティッカーの列を選択", df.columns)
            val_col = col2.selectbox("評価額（現在価値）の列を選択", df.columns)
            
            # 分析実行ボタン
            if st.button("🚀 分析を実行"):
                # データクレンジング処理
                df_clean = df.copy()
                
                def clean_currency(x):
                    if pd.isna(x):
                        return None
                    # 文字列に変換してから「数字とピリオド・マイナス」以外（¥, 円, カンマ, スペース等）を削除
                    x_str = str(x)
                    x_str = re.sub(r'[^\d\.\-]', '', x_str)
                    try:
                        return float(x_str)
                    except ValueError:
                        return None
                
                # 評価額列のクレンジングと欠損値の除外（dropna）
                df_clean[val_col] = df_clean[val_col].apply(clean_currency)
                df_clean = df_clean.dropna(subset=[val_col])
                
                # 評価額が0以下のものを除外（円グラフ描画エラー防止）
                df_clean = df_clean[df_clean[val_col] > 0]
                
                if df_clean.empty:
                    st.error("有効な数値データが抽出できませんでした。列の選択が正しいか確認してください。")
                else:
                    st.write("---")
                    st.write("### 🥧 ポートフォリオ資産割合")
                    # Plotlyによる円グラフ描画 (ダークテーマ)
                    fig = px.pie(df_clean, names=name_col, values=val_col, template='plotly_dark')
                    st.plotly_chart(fig, use_container_width=True)
                    
                    st.write("---")
                    st.write("### 🤖 Gemini AI診断レポート")
                    
                    if not gemini_key:
                        st.warning("サイドバーにGemini APIキーを入力してください。")
                    else:
                        with st.spinner("AIがポートフォリオを分析中です..."):
                            try:
                                # 最新のgenai SDKクライアント初期化
                                client = genai.Client(api_key=gemini_key)
                                
                                # ポートフォリオデータを文字列にフォーマット
                                portfolio_str = ""
                                for _, row in df_clean.iterrows():
                                    portfolio_str += f"- {row[name_col]}: {row[val_col]:,.0f}\n"
                                
                                # プロンプト作成
                                prompt = f"""あなたはプロの証券アナリストです。以下の保有資産ポートフォリオを分析してください。
1. 各銘柄をセクター（IT、ヘルスケア、金融、消費財など）に大まかに分類し、ポートフォリオ全体のセクター比率を推測してください。
2. 現在の資産配分における『弱点やリスク（特定の業界への過剰な偏りなど）』を指摘してください。
3. リスク分散のために、次に購入を検討すべきおすすめのセクターや投資戦略を提案してください。

【ポートフォリオデータ】
{portfolio_str}"""
                                
                                # Gemini-2.5-flashを呼び出し
                                response = client.models.generate_content(
                                    model='gemini-2.5-flash',
                                    contents=prompt
                                )
                                
                                # 結果を美しく表示
                                st.info(response.text)
                                
                            except Exception as e:
                                st.error(f"Gemini API呼び出し中にエラーが発生しました: {e}")
        else:
            st.warning("表データとして正しく読み込めませんでした。複数の列が含まれているか確認してください。")
            
    except Exception as e:
        st.error(f"データのパース中にエラーが発生しました: {e}")
