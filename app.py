import os
import asyncio
import threading
import random
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from telethon import TelegramClient
from telethon.errors import UserPrivacyRestrictedError, UserAlreadyParticipantError, FloodWaitError, SessionPasswordNeededError
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest, InviteToChannelRequest
from telethon.tl.types import UserStatusOnline, UserStatusOffline, UserStatusRecently

app = Flask(__name__, template_folder='.') 
CORS(app)

# ==================== 独立后台线程配置 ====================
telethon_loop = asyncio.new_event_loop()

def start_telethon_loop():
    asyncio.set_event_loop(telethon_loop)
    telethon_loop.run_forever()

threading.Thread(target=start_telethon_loop, daemon=True).start()

def run_async_safe(coro):
    future = asyncio.run_coroutine_threadsafe(coro, telethon_loop)
    return future.result()
# ===============================================================

# 全局变量与任务状态机控制
client = None
phone_code_hashes = {}  
system_logs = "【系统通知】全自动活人采集系统就绪...\n"

# 任务控制状态：'running' (运行中), 'paused' (暂停中), 'stopped' (停止)
job_status = "stopped" 

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
        return jsonify({"status": "error", "message": "请填写完整的凭证配置"})
    
    async def _send():
        global client
        client = TelegramClient(f'session_{phone}', int(api_id), api_hash, loop=telethon_loop)
        await client.connect()
        result = await client.send_code_request(phone)
        return result.phone_code_hash

    try:
        code_hash = run_async_safe(_send())
        phone_code_hashes[phone] = code_hash 
        append_log(f"【验证码提示】已向手机号 {phone} 发送验证码，请在网页输入。")
        return jsonify({"status": "success", "message": "验证码发送成功，请查收"})
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
        return jsonify({"status": "error", "message": "未找到验证码记录"})

    async def _login():
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=code_hash)
        except SessionPasswordNeededError:
            if not password:
                raise Exception("该账号开启了二步验证，请输入两步验证密码！")
            await client.sign_in(password=password)

    try:
        run_async_safe(_login())
        append_log("【登录成功】账号已成功登录 Telegram！")
        return jsonify({"status": "success", "message": "登录成功"})
    except Exception as e:
        append_log(f"【错误】登录失败: {str(e)}")
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/control_job', methods=['POST'])
def control_job():
    """接收前端发来的 暂停/继续/停止 指令"""
    global job_status
    action = request.json.get('action')
    
    if action == "pause":
        job_status = "paused"
        append_log("⏸️ 【任务暂停】拉人已暂停，保持当前进度，随时可以恢复...")
        return jsonify({"status": "success", "message": "任务已暂停"})
    elif action == "resume":
        job_status = "running"
        append_log("▶️ 【任务恢复】正在重新对接数据，继续拉人任务...")
        return jsonify({"status": "success", "message": "任务已继续"})
    elif action == "stop":
        job_status = "stopped"
        append_log("🛑 【强制停止】正在强制终止后台脚本并执行退群清理...")
        return jsonify({"status": "success", "message": "正在强行终止..."})
    
    return jsonify({"status": "error", "message": "未知指令"})

@app.route('/api/start_job', methods=['POST'])
def start_job():
    global client, job_status
    data = request.json
    target_group = data.get('target_group').strip()
    pull_count = int(data.get('pull_count', 100))
    source_groups = data.get('source_groups', '').split('\n')
    
    if not client:
        return jsonify({"status": "error", "message": "Telegram 账号未登录！"})

    job_status = "running"
    append_log(f"【任务启动】目标群: {target_group} | 限制最高拉取: {pull_count} 人")
    
    async def _do_pull_job():
        global job_status
        joined_groups = []
        try:
            # 1. 自动进入目的地群组
            try:
                target_entity = await client.get_input_entity(target_group)
                await client(JoinChannelRequest(target_entity))
                joined_groups.append(target_entity)
                append_log(" 成功进入目的地群组。")
            except Exception:
                target_entity = await client.get_input_entity(target_group)

            pulled_today = 0
            three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)

            for src_group in source_groups:
                src_group = src_group.strip()
                if not src_group or job_status == "stopped": 
                    continue
                
                append_log(f" 正在尝试进入采集群【{src_group}】...")
                try:
                    src_entity = await client.get_input_entity(src_group)
                    await client(JoinChannelRequest(src_entity))
                    joined_groups.append(src_entity)
                except Exception:
                    src_entity = await client.get_input_entity(src_group)

                append_log(f" 🔍 开始扫描【{src_group}】中的【活跃真人】...")
                
                # 2. 遍历采集群成员
                async for user in client.iter_participants(src_entity, limit=500):
                    # 【随时暂停/停止的核心拦截器】
                    while job_status == "paused":
                        await asyncio.sleep(2) # 如果状态是暂停，后台就在这里空转等待
                    if job_status == "stopped" or pulled_today >= pull_count:
                        break
                    
                    if user.bot or not user.username:
                        continue
                    
                    # 💡 【高阶真人筛选机制】：判断在线状态
                    is_active_user = False
                    if isinstance(user.status, UserStatusOnline):
                        is_active_user = True  # 当前在线，绝对是真人
                    elif isinstance(user.status, UserStatusRecently):
                        is_active_user = True  # 最近刚上线，活人
                    elif isinstance(user.status, UserStatusOffline):
                        # 如果离线，判断最后上线时间是否在 3 天内
                        if user.status.was_online and user.status.was_online > three_days_ago:
                            is_active_user = True
                    
                    if not is_active_user:
                        continue # 僵尸号、长达一周/一个月不上线的人直接过滤跳过

                    # 3. 执行强行拉人
                    try:
                        append_log(f" [活跃真人] 发现! 正在把 @{user.username} 强行拉入群组...")
                        await client(InviteToChannelRequest(target_entity, [user]))
                        pulled_today += 1
                        append_log(f" 🟢 成功拉入 [ @{user.username} ] ！当前成功累计: {pulled_today} / {pull_count} 人")
                        
                        if pulled_today >= pull_count:
                            append_log(" 目标拉取数量已达成，完美完成任务！")
                            job_status = "stopped"
                            break

                        # ⏳ 【满足您的要求】：每拉一个人休息 30 到 40 秒之间的随机时间
                        sleep_time = random.randint(30, 40)
                        append_log(f" 💤 安全防封防检测：原地休眠 {sleep_time} 秒...")
                        
                        # 在休眠期间，也要允许响应暂停或停止
                        for _ in range(sleep_time):
                            if job_status == "stopped": break
                            while job_status == "paused": await asyncio.sleep(1)
                            await asyncio.sleep(1)

                    except UserPrivacyRestrictedError:
                        append_log(f"❌ 拒绝：@{user.username} 开启了防陌生人拉群限制。")
                    except UserAlreadyParticipantError:
                        append_log(f"💡 提示：@{user.username} 本来就在群里。")
                    except FloodWaitError as e:
                        append_log(f"⚠️ 触发风控：操作过快，官方要求强行休眠 {e.seconds} 秒...")
                        await asyncio.sleep(e.seconds)
                    except Exception as e_user:
                        if "Too many requests" in str(e_user):
                            append_log("❌ 严重错误：当前账号今日额度已死，立即终止！")
                            job_status = "stopped"
                            break
                        append_log(f"❌ 拉人失败: {str(e_user)}")
                        await asyncio.sleep(5)

            append_log(" 所有群组扫描采集结束。")
        except Exception as e:
            append_log(f"【后台异常】{str(e)}")
        finally:
            # 4. 全自动无痕退群清理机制
            append_log("🧹 任务状态结束，正在为您自动清理退群...")
            for group_entity in joined_groups:
                try:
                    await client(LeaveChannelRequest(group_entity))
                    await asyncio.sleep(1.5)
                except Exception:
                    pass
            append_log("✨ 所有临时加入的采集群和目的群已全部无痕安全退出。")
            job_status = "stopped"

    asyncio.run_coroutine_threadsafe(_do_pull_job(), telethon_loop)
    return jsonify({"status": "success", "message": "全自动活人采集任务已提交至后台！"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
