import os
import asyncio
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from telethon import TelegramClient
from telethon.errors import UserPrivacyRestrictedError, UserAlreadyParticipantError, FloodWaitError, SessionPasswordNeededError

app = Flask(__name__, template_folder='.') 
CORS(app)

# 全局变量，用来存储 Telegram 客户端实例和日志
client = None
phone_code_hashes = {}  # 存储发送验证码后返回的哈希值，登录时需要用到
system_logs = "【系统通知】后端服务已就绪...\n"

def append_log(text):
    global system_logs
    system_logs += f"{text}\n"
    print(text)

# 核心辅助函数：安全地在 Flask 线程中执行异步的 Telethon 任务
def run_async(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    if loop.is_running():
        # 如果当前事件循环在运行（Debug模式下常见），新建一个独立循环执行
        new_loop = asyncio.new_event_loop()
        return new_loop.run_until_complete(coro)
    else:
        return loop.run_until_complete(coro)

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
        # 每次创建新实例，避免旧实例残留的 Loop 状态导致冲突
        client = TelegramClient(f'session_{phone}', int(api_id), api_hash)
        await client.connect()
        # 发送验证码，并获取重要的 phone_code_hash
        result = await client.send_code_request(phone)
        return result.phone_code_hash

    try:
        # 使用安全的异步执行器
        code_hash = run_async(_send())
        phone_code_hashes[phone] = code_hash # 缓存这个 hash
        
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
    password = data.get('password') # 接收前端传来的两步验证密码
    
    if not client:
        return jsonify({"status": "error", "message": "请先点击发送验证码"})
    
    code_hash = phone_code_hashes.get(phone)
    if not code_hash:
        return jsonify({"status": "error", "message": "未找到对应的验证码发送记录，请重新发送"})

    async def _login():
        try:
            # 尝试使用 验证码 进行登录
            await client.sign_in(phone=phone, code=code, phone_code_hash=code_hash)
        except SessionPasswordNeededError:
            # 如果账号开启了“两步验证”，Telethon 会抛出这个异常
            if not password:
                raise Exception("该账号开启了二步验证，请输入两步验证密码！")
            # 传入密码进行二次验证登录
            await client.sign_in(password=password)

    try:
        run_async(_login())
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
    # 提醒：在 Codespaces 下调试建议关闭 use_reloader=False 避免多进程引发冲突
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)
