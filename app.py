import os
import asyncio
import threading
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from telethon import TelegramClient
from telethon.errors import UserPrivacyRestrictedError, UserAlreadyParticipantError, FloodWaitError, SessionPasswordNeededError

app = Flask(__name__, template_folder='.') 
CORS(app)

# ==================== 核心：独立后台线程配置 ====================
# 创建一个专门给 Telethon 用的专用异步事件循环
telethon_loop = asyncio.new_event_loop()

def start_telethon_loop():
    """在独立的线程中永远运行这个事件循环"""
    asyncio.set_event_loop(telethon_loop)
    telethon_loop.run_forever()

# 启动后台线程
threading.Thread(target=start_telethon_loop, daemon=True).start()

def run_async_safe(coro):
    """安全地将异步任务提交到专门的后台线程中去执行，并等待结果"""
    future = asyncio.run_coroutine_threadsafe(coro, telethon_loop)
    return future.result() # 阻塞等待异步执行完毕并拿到返回值
# ===============================================================

# 全局变量
client = None
phone_code_hashes = {}  
system_logs = "【系统通知】后端服务已就绪...\n"

def append_log(text):
    global system_logs
    system_logs += f"{text}\n"
    print(text)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/get_logs', methods=['GET'])
def get_logs():
    return jsonify({"logs": system_logs})

@app.route('/api/send_code', methods=['POST'])
def send_code():
    global client, phone_code_hashes
    data = request.json
    api_id = data.get('api_id')
    api_hash = data.get('api_hash')
    phone = data.get('phone')
    
    if not api_id or not api_hash or not phone:
        return jsonify({"status": "error", "message": "请填写完整的 API_ID, API_HASH 和手机号"})
    
    async def _send():
        global client
        # 注意：在独立循环中初始化 client 时，必须传入 loop 参数显式绑定
        client = TelegramClient(f'session_{phone}', int(api_id), api_hash, loop=telethon_loop)
        await client.connect()
        result = await client.send_code_request(phone)
        return result.phone_code_hash

    try:
        # 通过安全的线程传输器运行
        code_hash = run_async_safe(_send())
        phone_code_hashes[phone] = code_hash 
        
        append_log(f"【验证码提示】已向手机号 {phone} 发送验证码，请在网页输入。")
        return jsonify({"status": "success", "message": "验证码发送成功，请注意查收"})
    except Exception as e:
        append_log(f"【错误】发送验证码失败: {str(e)}")
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/login', methods=['POST'])
def login():
    global client, phone_code_hashes
    data = request.json
    phone = data.get('phone')
    code = data.get('code')
    password = data.get('password') 
    
    if not client:
        return jsonify({"status": "error", "message": "请先点击发送验证码"})
    
    code_hash = phone_code_hashes.get(phone)
    if not code_hash:
        return jsonify({"status": "error", "message": "未找到对应的验证码发送记录，请重新发送"})

    async def _login():
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=code_hash)
        except SessionPasswordNeededError:
            if not password:
                raise Exception("该账号开启了二步验证，请输入两步验证密码！")
            await client.sign_in(password=password)

    try:
        # 通过安全的线程传输器运行
        run_async_safe(_login())
        append_log("【登录成功】账号已成功登录 Telegram！")
        return jsonify({"status": "success", "message": "登录成功"})
    except Exception as e:
        append_log(f"【错误】登录失败: {str(e)}")
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/start_job', methods=['POST'])
def start_job():
    data = request.json
    target_group = data.get('target_group')
    pull_count = data.get('pull_count')
    source_groups = data.get('source_groups')
    
    append_log(f"【任务启动】目标群: {target_group} | 数量: {pull_count}")
    append_log("【提示】拉人核心逻辑正在后台准备执行...")
    
    return jsonify({"status": "success", "message": "任务已启动"})

if __name__ == '__main__':
    # 彻底关闭 debug 模式，并在 Codespaces 下安全运行
    app.run(host='0.0.0.0', port=5000, debug=False)
