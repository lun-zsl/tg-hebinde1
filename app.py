import os
import asyncio
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from telethon import TelegramClient
from telethon.errors import UserPrivacyRestrictedError, UserAlreadyParticipantError, FloodWaitError

app = Flask(__name__, template_folder='.') # 这样设置后，index.html 放在 app.py 旁边即可，不用建新文件夹
CORS(app)

# 全局变量，用来存储 Telegram 客户端实例和日志
client = None
system_logs = "【系统通知】后端服务已就绪...\n"

def append_log(text):
    global system_logs
    system_logs += f"{text}\n"
    print(text)

@app.route('/')
def index():
    # 访问网页时，直接显示同级目录下的 index.html
    return render_template('index.html')

@app.route('/api/get_logs', methods=['GET'])
def get_logs():
    # 网页会定时来这里拿最新的日志
    return jsonify({"logs": system_logs})

@app.route('/api/send_code', methods=['POST'])
def send_code():
    global client
    data = request.json
    api_id = data.get('api_id')
    api_hash = data.get('api_hash')
    phone = data.get('phone')
    
    if not api_id or not api_hash or not phone:
        return jsonify({"status": "error", "message": "请填写完整的 API_ID, API_HASH 和手机号"})
    
    # 异步运行 Telegram 链接
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        client = TelegramClient(f'session_{phone}', int(api_id), api_hash)
        loop.run_until_complete(client.connect())
        
        # 发送验证码
        loop.run_until_complete(client.send_code_request(phone))
        append_log(f"【验证码提示】已向手机号 {phone} 发送验证码，请在网页输入。")
        return jsonify({"status": "success", "message": "验证码发送成功，请注意查收"})
    except Exception as e:
        append_log(f"【错误】发送验证码失败: {str(e)}")
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/login', methods=['POST'])
def login():
    global client
    data = request.json
    phone = data.get('phone')
    code = data.get('code')
    
    if not client:
        return jsonify({"status": "error", "message": "请先点击发送验证码"})
        
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(client.sign_in(phone, code))
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
    # 这里可以扩展你实际的拉人业务代码
    
    return jsonify({"status": "success", "message": "任务已启动"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

