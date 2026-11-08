from flask import Flask, render_template, request, redirect, url_for, session, flash
import json
import os
import time
import requests
from multiprocessing import Process

app = Flask(__name__)
app.secret_key = "super_secret_key_change_me_12345"

DB_FILE = "bots_db.json"
API_BASE_TEMPLATE = "https://botapi.rubika.ir/v3/{token}/{method}"

bot_offsets = {}
processed_messages = {}
running_tokens = set()

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content: return {}
                return json.loads(content)
        except json.JSONDecodeError:
            return {}
    return {}

def save_db(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def api_call(token, method, payload=None):
    try:
        url = API_BASE_TEMPLATE.format(token=token, method=method)
        r = requests.post(url, json=payload or {}, timeout=30)
        data = r.json()
        if method == "getMe" and data.get("status") != "OK":
            return None
        return data.get("data", {})
    except Exception as e:
        return {}

def bot_loop(token, bot_name):
    print(f"✅ [{bot_name}] حلقه ربات روشن شد.")
    me = api_call(token, "getMe")
    if not me: return
    bot_id = me.get("bot", {}).get("bot_id", "")

    if token not in bot_offsets: bot_offsets[token] = None
    if token not in processed_messages: processed_messages[token] = set()

    while True:
        try:
            payload = {"limit": 10}
            if bot_offsets[token]:
                payload["offset_id"] = bot_offsets[token]
            
            res = api_call(token, "getUpdates", payload)
            updates = res.get("updates", [])
            
            for upd in updates:
                if upd.get("next_offset_id"):
                    bot_offsets[token] = upd.get("next_offset_id")
                    
                if upd.get("type") == "NewMessage":
                    msg = upd.get("new_message", {})
                    text = msg.get("text", "")
                    chat_id = upd.get("chat_id")
                    sender_id = msg.get("sender_id", "")
                    
                    if sender_id == bot_id: continue
                    
                    key = f"{chat_id}_{msg.get('message_id')}"
                    if key in processed_messages[token]: continue
                    processed_messages[token].add(key)
                    
                    print(f"📩 [{bot_name}] پیام از {chat_id}: {text[:30]}")
                    
                    db = load_db()
                    bot_data = db.get(token, {})
                    keyboard_btns = bot_data.get("keyboard_buttons", [])
                    inline_btns = bot_data.get("inline_buttons", [])

                    chat_keypad = None
                    if keyboard_btns:
                        rows = []
                        for btn in keyboard_btns:
                            rows.append([{"text": btn["text"], "button_type": "Simple"}])
                        chat_keypad = {"rows": rows}

                    inline_keypad = None
                    if inline_btns:
                        inline_rows = []
                        for i, btn in enumerate(inline_btns):
                            inline_rows.append([{
                                "text": btn["text"],
                                "button_type": "Simple",
                                "callback_data": f"inline_{i}"
                            }])
                        inline_keypad = {"rows": inline_rows}

                    if text.startswith("/"):
                        cmd = text.split()[0][1:].split("@")[0].lower()
                        if cmd == "start":
                            start_text = bot_data.get("start_text", "سلام!")
                            send_payload = {"chat_id": chat_id, "text": start_text}
                            
                            if chat_keypad:
                                send_payload["chat_keypad"] = chat_keypad
                            if inline_keypad:
                                send_payload["inline_keypad"] = inline_keypad
                            
                            res = api_call(token, "sendMessage", send_payload)
                            print(f"📤 پاسخ استارت: {res}")
                            
                        else:
                            api_call(token, "sendMessage", {"chat_id": chat_id, "text": f"دستور {cmd} دریافت شد."})
                    else:
                        api_call(token, "sendMessage", {"chat_id": chat_id, "text": f"شما گفتید: {text}"})
                
                elif upd.get("type") == "NewCallback":
                    cb = upd.get("new_callback", {})
                    chat_id = upd.get("chat_id")
                    callback_data = cb.get("callback_data", "")
                    
                    if callback_data.startswith("inline_"):
                        idx = int(callback_data.split("_")[1])
                        db = load_db()
                        bot_data = db.get(token, {})
                        inline_btns = bot_data.get("inline_buttons", [])
                        if idx < len(inline_btns):
                            answer = inline_btns[idx].get("answer", "پاسخی تعریف نشده.")
                            api_call(token, "sendMessage", {"chat_id": chat_id, "text": answer})
            
            if len(processed_messages[token]) > 2000:
                processed_messages[token].clear()
            time.sleep(1)
        except Exception as e:
            print(f"خطا در ربات {bot_name}: {e}")
            time.sleep(3)

def run_bots():
    time.sleep(3) # صبر برای بالا اومدن اولیه
    db = load_db()
    for token, data in db.items():
        if token not in running_tokens:
            running_tokens.add(token)
            bot_name = data.get("bot_name", token[:5])
            p = Process(target=bot_loop, args=(token, bot_name))
            p.daemon = True
            p.start()
            time.sleep(1)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        token = request.form.get('token', '').strip()
        if not token:
            return redirect(url_for('login'))

        bot_info = api_call(token, "getMe")
        if not bot_info:
            flash("❌ توکن نامعتبر است!", "error")
            return redirect(url_for('login'))

        session['token'] = token
        db = load_db()
        
        if token not in db:
            db[token] = {
                "bot_name": bot_info.get("bot", {}).get("bot_title", "ربات من"),
                "start_text": "سلام! به ربات من خوش آمدید.",
                "buttons": [],
                "auto_replies": [],
                "keyboard_buttons": [],
                "inline_buttons": []
            }
            save_db(db)
            print(f"✅ ربات {db[token]['bot_name']} اضافه شد!")
            
            # روشن کردن فوری پروسه ربات جدید
            p = Process(target=bot_loop, args=(token, db[token]['bot_name']))
            p.daemon = True
            p.start()

        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'token' not in session: return redirect(url_for('login'))
    token = session['token']
    db = load_db()
    bot_data = db.get(token, {})
    me = api_call(token, "getMe")
    bot_username = me.get("bot", {}).get("username", "") if me else ""
    return render_template('dashboard.html', bot=bot_data, token=token, bot_username=bot_username)

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if 'token' not in session: return redirect(url_for('login'))
    token = session['token']
    db = load_db()
    if request.method == 'POST':
        db[token]['start_text'] = request.form.get('start_text')
        save_db(db)
        print("✅ تنظیمات ذخیره شد!")
        return redirect(url_for('settings'))
    return render_template('settings.html', bot=db.get(token, {}))

@app.route('/manage_buttons', methods=['GET', 'POST'])
def manage_buttons():
    if 'token' not in session: return redirect(url_for('login'))
    token = session['token']
    db = load_db()
    bot_data = db.get(token, {})
    
    if request.method == 'POST':
        btn_type = request.form.get('btn_type')
        btn_text = request.form.get('btn_text')
        btn_answer = request.form.get('btn_answer', '')
        
        if btn_type == 'keyboard':
            bot_data.setdefault('keyboard_buttons', []).append({"text": btn_text})
        elif btn_type == 'inline':
            bot_data.setdefault('inline_buttons', []).append({"text": btn_text, "answer": btn_answer})
        
        db[token] = bot_data
        save_db(db)
        return redirect(url_for('manage_buttons'))
        
    return render_template('manage_buttons.html', bot=bot_data, token=token)

@app.route('/logout')
def logout():
    session.pop('token', None)
    return redirect(url_for('index'))

# === راه‌اندازی اولیه ===
# این بخش فقط زمانی اجرا میشه که فایل مستقیماً اجرا بشه (نه توسط Gunicorn)
if __name__ == '__main__':
    # یک پروسه برای ربات‌ها بساز
    p = Process(target=run_bots)
    p.daemon = True
    p.start()
    
    port = int(os.environ.get('PORT', 5000))
    print(f"🌐 وب‌سرور ربات ساز پارس روی پورت {port} روشن شد.")
    app.run(debug=False, port=port, host='0.0.0.0')

# === برای Gunicorn (روی Render) ===
# Gunicorn این فایل رو ایمپورت می‌کنه، پس run_bots رو صدا می‌زنیم
else:
    p = Process(target=run_bots)
    p.daemon = True
    p.start()
