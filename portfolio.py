import streamlit as st
import pandas as pd
import plotly.express as px
import io
from google import genai
import re
from tenacity import retry, stop_after_attempt, wait_exponential

# Gemini API呼び出し用のリトライ処理（Exponential Backoff）
# 失敗しても、2秒, 4秒, 8秒...と待機しながら最大4回まで自動で再試行します
@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=2, max=10))
def generate_report_with_retry(client, prompt):
    return client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt
    )

@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=2, max=10))
def send_chat_with_retry(chat_session, user_input):
    return chat_session.send_message(user_input)

# ページレイアウトを「wide」に設定
st.set_page_config(page_title="AIポートフォリオ診断ダッシュボード", layout="wide")

# 1. タブの追加と上部固定（カスタムCSS）
st.markdown("""
<style>
[data-testid="stTabs"] [data-baseweb="tab-list"] {
    position: sticky;
    top: 3rem;
    z-index: 99;
    background-color: var(--primary-background-color);
}
</style>
""", unsafe_allow_html=True)

# サイドバー設定
st.sidebar.title("設定")
gemini_key = st.sidebar.text_input("Gemini APIキー", type="password")

# メイン画面タイトル
st.title("📊 AIポートフォリオ診断ダッシュボード")

with st.expander("📖 アプリの使い方（初めての方へ）", expanded=True):
    st.markdown("""
    1. **APIキーの設定**: 左側のサイドバーにGemini APIキーを入力してください。
    2. **表のコピー**: マネーフォワードや証券会社（楽天証券など）の画面を開き、「保有銘柄と評価額」が載っている表全体を文字選択してコピーします。
    3. **データの貼り付け**: 「データ入力・設定」タブのテキストエリアにそのまま貼り付けます。
    4. **列の選択（重要！）**: データがプレビューされたら、ドロップダウンから「銘柄名」「評価額」「評価損益」の列を正しく選んでください。
       - 💡 **データの自動整形について**: 評価額や評価損益の列に「¥」「円」「,」などの文字が含まれていても、**自動で数値データにクレンジング**される設計になっています。
    """)

# 4. タブによるレイアウト分割（3つ目のタブを追加）
tab1, tab2, tab3 = st.tabs(["📋 データ入力・設定", "📊 診断レポート", "💬 AI投資相談チャット"])

# タブ切り替え時に状態を維持するためのセッションステート
if 'run_analysis' not in st.session_state:
    st.session_state['run_analysis'] = False

with tab1:
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
        
        with tab1:
            st.write("### 📋 読み込んだデータ（プレビュー）")
            # 3. データプレビュー表の行番号非表示と横幅いっぱいへの拡大
            st.dataframe(df, hide_index=True, use_container_width=True)
            
            if not df.empty and len(df.columns) >= 3:
                # カラム選択のドロップダウンを3列で配置
                col1, col2, col3 = st.columns(3)
                name_col = col1.selectbox("銘柄名・ティッカーの列を選択", df.columns, index=0 if len(df.columns) > 0 else 0)
                val_col = col2.selectbox("評価額（現在価値）の列を選択", df.columns, index=1 if len(df.columns) > 1 else 0)
                profit_col = col3.selectbox("評価損益（含み益・含み損）の列を選択", df.columns, index=2 if len(df.columns) > 2 else 0)
                
                # 分析実行ボタン
                if st.button("🚀 分析を実行"):
                    st.session_state['run_analysis'] = True
            else:
                st.warning("表データとして正しく読み込めませんでした。3つ以上の列が含まれているか確認してください。")
        
        # 分析実行後の処理（タブ2とタブ3への影響）
        if st.session_state['run_analysis'] and not df.empty and len(df.columns) >= 3:
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
            
            # 評価額列と評価損益列のクレンジング
            df_clean[val_col] = df_clean[val_col].apply(clean_currency)
            df_clean[profit_col] = df_clean[profit_col].apply(clean_currency)
            
            # 評価額の欠損値を除外
            df_clean = df_clean.dropna(subset=[val_col])
            
            # 評価損益の欠損値を0で埋める
            df_clean[profit_col] = df_clean[profit_col].fillna(0)
            
            # 評価額が0以下のものを除外（円グラフ描画エラー防止）
            df_clean = df_clean[df_clean[val_col] > 0]
            
            if df_clean.empty:
                with tab1:
                    st.error("有効な数値データが抽出できませんでした。列の選択が正しいか確認してください。")
            else:
                with tab2:
                    st.write("### 🥧 ポートフォリオ資産割合")
                    
                    # 銘柄数が多すぎる場合に見づらくなるのを防ぐため、全体の2%未満の銘柄を「その他」にまとめる
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
                    
                    # 1. 円グラフのラベルと凡例の最適化
                    # テキストを内側に配置しパーセンテージのみ表示。凡例は右側に表示
                    fig.update_traces(textposition='inside', textinfo='percent')
                    fig.update_layout(
                        uniformtext_minsize=10, 
                        uniformtext_mode='hide', 
                        showlegend=True,
                        legend=dict(orientation="v", yanchor="auto", y=0.5, xanchor="left", x=1.05)
                    )
                    
                    st.plotly_chart(fig, use_container_width=True)
                    
                    st.write("---")
                    st.write("### 🤖 Gemini AI診断レポート")
                    
                    if not gemini_key:
                        st.warning("サイドバーにGemini APIキーを入力してください。")
                    else:
                        with st.spinner("AIがポートフォリオを分析中です...（混雑時は自動で再試行します）"):
                            try:
                                # 最新のgenai SDKクライアント初期化（セッションに保持して切断エラーを回避）
                                client_changed = False
                                if 'gemini_client' not in st.session_state or st.session_state.get('gemini_api_key') != gemini_key:
                                    st.session_state.gemini_client = genai.Client(api_key=gemini_key)
                                    st.session_state.gemini_api_key = gemini_key
                                    client_changed = True
                                
                                client = st.session_state.gemini_client
                                
                                # ポートフォリオデータを文字列にフォーマット
                                portfolio_str = ""
                                for _, row in df_clean.iterrows():
                                    portfolio_str += f"- {row[name_col]}: 評価額 {row[val_col]:,.0f}円 / 評価損益 {row[profit_col]:,.0f}円\n"
                                
                                # 2. プロンプト文の改善（メリハリのある出力）
                                prompt = f"""あなたはプロの証券アナリストです。以下の保有資産ポートフォリオを分析してください。
1. 各銘柄をセクター（IT、ヘルスケア、金融、消費財など）に大まかに分類し、ポートフォリオ全体のセクター比率を推測してください。
2. 現在の資産配分における『弱点やリスク（特定の業界への過剰な偏りなど）』を指摘してください。
3. リスク分散のために、次に購入を検討すべきおすすめのセクターや投資戦略を提案してください。
・金額に関する表記は、ドル（$）を使わず、必ず『円（¥）』を使用してください。
・各銘柄の『評価損益』のデータも考慮し、現在の含み益・含み損の状況を踏まえた『利益確定の目安』や『損切りの検討』など、実践的なアドバイスも含めてください。

【出力形式の厳守事項】
・セクター分類や比率、金額などの『データ・数値』を示す部分は、必ずMarkdownの表（テーブル）形式を用いて構造化してください。
・リスクの指摘や戦略の提案などの『長文の解説』は、表の中に押し込まず、見出しと箇条書きを活用して読みやすい文章として出力してください。

【ポートフォリオデータ】
{portfolio_str}"""
                                
                                # 分析レポートのキャッシュ処理（チャット入力ごとの再生成を防ぐ）
                                if 'analysis_report' not in st.session_state or st.session_state.get('last_portfolio') != portfolio_str:
                                    response = generate_report_with_retry(client, prompt)
                                    st.session_state['analysis_report'] = response.text
                                    st.session_state['last_portfolio'] = portfolio_str
                                
                                # 結果を美しく表示
                                st.markdown(st.session_state['analysis_report'])
                                
                                # 3. チャットセッションと前提知識の初期化
                                if 'chat_session' not in st.session_state or st.session_state.get('chat_portfolio') != portfolio_str or client_changed:
                                    # 初期化時の無駄な1リクエスト消費を防ぐため、system_instructionを利用する
                                    init_prompt = f"以下のポートフォリオデータを前提として、ユーザーからの投資相談に乗ってください。\n{portfolio_str}"
                                    
                                    # チャットセッション作成
                                    st.session_state.chat_session = client.chats.create(
                                        model="gemini-2.5-flash",
                                        config={"system_instruction": init_prompt}
                                    )
                                    # 会話履歴の初期化
                                    st.session_state.messages = []
                                    st.session_state['chat_portfolio'] = portfolio_str
                                
                            except Exception as e:
                                st.error(f"Gemini API呼び出し中にエラーが発生しました: {e}")

        # 5. チャットUIの実装（タブ3）
        with tab3:
            st.write("### 💬 AI投資相談チャット")
            if 'chat_session' not in st.session_state:
                st.info("タブ1で表データを読み込み、「分析を実行」してください。AIがポートフォリオを把握した後にチャットが開始できます。")
            else:
                # チャット領域用コンテナを設定
                chat_container = st.container(height=500, border=True)

                # ユーザーの会話履歴を表示
                with chat_container:
                    for msg in st.session_state.messages:
                        with st.chat_message(msg["role"]):
                            st.markdown(msg["content"])
                        
                # チャット入力
                if user_input := st.chat_input("投資に関する質問や相談を入力してください（例：○○銘柄は利確すべき？）："):
                    # ユーザーの入力を表示して履歴へ追加
                    with chat_container:
                        with st.chat_message("user"):
                            st.markdown(user_input)
                    st.session_state.messages.append({"role": "user", "content": user_input})
                    
                    # AIの応答を取得して表示し、履歴へ追加
                    with chat_container:
                        with st.chat_message("assistant"):
                            with st.spinner("AIが考え中...（混雑時は自動で再試行します）"):
                                try:
                                    chat_response = send_chat_with_retry(st.session_state.chat_session, user_input)
                                    st.markdown(chat_response.text)
                                    st.session_state.messages.append({"role": "assistant", "content": chat_response.text})
                                except Exception as e:
                                    st.error(f"チャットAPI呼び出し中にエラーが発生しました: {e}")
            
    except Exception as e:
        with tab1:
            st.error(f"データのパース中にエラーが発生しました: {e}")
