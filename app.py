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
    global client
    data = request.json
    target_group = data.get('target_group')
    pull_count = int(data.get('pull_count', 100))
    source_groups = data.get('source_groups', '').split('\n') # 按行切分多个采集群
    
    if not client:
        return jsonify({"status": "error", "message": "错误：Telegram 账号未登录，请先登录！"})

    append_log(f"【任务启动】目标群: {target_group} | 计划拉取数量: {pull_count}")
    
    # 核心异步执行函数：在固定的后台线程里安全跑
    async def _do_pull_job():
        try:
            from telethon.tl.functions.channels import InviteToChannelRequest
            
            # 1. 获取目的地群组权限实体
            target_entity = await client.get_input_entity(target_group)
            pulled_today = 0
            
            for src_group in source_groups:
                src_group = src_group.strip()
                if not src_group: 
                    continue
                
                append_log(f" 正在从采集群【{src_group}】中获取成员列表...")
                
                # 2. 遍历采集群里的成员（限制每次最多扫 200 人）
                async for user in client.iter_participants(src_group, limit=200):
                    if pulled_today >= pull_count:
                        append_log("【任务完成】已达到您设定的拉取数量目标，任务正常结束！")
                        return
                    
                    # 自动过滤：不要机器人，不要没有设置用户名(username)的人
                    if user.bot or not user.username:
                        continue
                        
                    try:
                        append_log(f" 正在尝试拉入用户: @{user.username} ...")
                        
                        # 3. 核心：发起拉人命令
                        await client(InviteToChannelRequest(target_entity, [user]))
                        
                        pulled_today += 1
                        append_log(f" 成功拉入 [ @{user.username} ] ！当前总计成功: {pulled_today} 人")
                        
                        # 4. 🚨 防封关键：拉完一个人强制休息 25 秒，防止被官方检测封号
                        await asyncio.sleep(25) 
                        
                    except UserPrivacyRestrictedError:
                        append_log(f"❌ 失败：用户 @{user.username} 开启了隐私保护，拒绝被陌生人拉群。")
                    except UserAlreadyParticipantError:
                        append_log(f"💡 提示：用户 @{user.username} 已经存在于目的地群了。")
                    except FloodWaitError as e:
                        append_log(f"⚠️ 触发官方频繁限制：操作速度过快！官方要求必须强制等待 {e.seconds} 秒...")
                        await asyncio.sleep(e.seconds)
                    except Exception as e_user:
                        append_log(f"❌ 针对该用户拉取错误: {str(e_user)}")
                        await asyncio.sleep(5) 
                        
            append_log(f"【任务结束】所有输入的采集群已全部扫描解析完毕，本次共成功拉入 {pulled_today} 人。")
            
        except Exception as e:
            append_log(f"【致命错误】后台拉人任务意外中断: {str(e)}")

    # 关键：不阻塞 Flask，直接把这个拉人循环任务抛进独立的 telethon_loop 线程去偷偷执行
    asyncio.run_coroutine_threadsafe(_do_pull_job(), telethon_loop)
    
    return jsonify({"status": "success", "message": "拉人任务已在云端后台安全启动，请紧密观察监控面板！"})

if __name__ == '__main__':
    # 彻底关闭 debug 模式，并在 Codespaces 下安全运行
    app.run(host='0.0.0.0', port=5000, debug=False)
