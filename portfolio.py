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

with st.expander("📖 アプリの使い方（初めての方へ）", expanded=True):
    st.markdown("""
    1. **APIキーの設定**: 左側のサイドバーにGemini APIキーを入力してください。
    2. **表のコピー**: マネーフォワードや証券会社（楽天証券など）の画面を開き、「保有銘柄と評価額」が載っている表全体を文字選択してコピーします。
    3. **データの貼り付け**: 下のテキストエリアにそのまま貼り付けます。
    4. **列の選択（重要！）**: データがプレビューされたら、ドロップダウンから「銘柄名」と「評価額」の列を正しく選んでください。
       - 💡 **データの自動整形について**: 評価額の列に「¥」「円」「,」などの文字が含まれていても、**自動で数値データにクレンジング**される設計になっているため、そのまま分析を実行して問題ありません！
    """)

# テキストエリア配置
raw_text = st.text_area("マネーフォワードや証券会社の『保有銘柄一覧』の表をコピーして、ここに貼り付けてください", height=200)

if raw_text.strip():
    # 入力テキストにタブが含まれていればタブ区切り、それ以外はカンマ区切りと判定
    sep = '\t' if '\t' in raw_text else ','
    
    # コピー元の表（楽天証券など）の仕様で、ヘッダー行が「改行＋タブ」で崩れる問題を補正
    cleaned_text = raw_text.replace('\n\t', '\t')
    
    try:
        # 文字列をデータフレームとして読み込み。念のためフォーマットの異なる行はスキップ
        df = pd.read_csv(io.StringIO(cleaned_text), sep=sep, on_bad_lines='skip', engine='python')
        
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
                    
                    # 銘柄数が多すぎる場合に見づらくなるのを防ぐため、全体の1%未満の銘柄を「その他」にまとめる
                    total_val = df_clean[val_col].sum()
                    threshold = total_val * 0.02 # 2%未満をまとめる
                    
                    df_large = df_clean[df_clean[val_col] >= threshold]
                    df_small = df_clean[df_clean[val_col] < threshold]
                    
                    if not df_small.empty:
                        other_row = pd.DataFrame([{name_col: 'その他', val_col: df_small[val_col].sum()}])
                        df_plot = pd.concat([df_large, other_row], ignore_index=True)
                    else:
                        df_plot = df_clean.copy()

                    # Plotlyによる円グラフ描画 (ダークテーマ・ドーナツ型)
                    fig = px.pie(df_plot, names=name_col, values=val_col, template='plotly_dark', hole=0.4)
                    
                    # テキストを内側に配置し、はみ出すほど小さい項目はラベルを非表示にする
                    fig.update_traces(textposition='inside', textinfo='percent+label')
                    fig.update_layout(uniformtext_minsize=10, uniformtext_mode='hide', showlegend=False)
                    
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

【出力形式の厳守事項】
結果は必ずMarkdownの表（テーブル）形式を用いて、見やすく構造化して出力してください。箇条書きのみの単調な出力は避けてください。

【ポートフォリオデータ】
{portfolio_str}"""
                                
                                # Gemini-2.5-flashを呼び出し
                                response = client.models.generate_content(
                                    model='gemini-2.5-flash',
                                    contents=prompt
                                )
                                
                                # 結果を美しく表示
                                st.markdown(response.text)
                                
                            except Exception as e:
                                st.error(f"Gemini API呼び出し中にエラーが発生しました: {e}")
        else:
            st.warning("表データとして正しく読み込めませんでした。複数の列が含まれているか確認してください。")
            
    except Exception as e:
        st.error(f"データのパース中にエラーが発生しました: {e}")
