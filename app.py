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
from telethon.tl.types import UserStatusOnline, UserStatusRecently, UserStatusOffline

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

client = None
phone_code_hashes = {}  
system_logs = "【金字塔真人版】多群联合精准采集强拉系统已就绪...\n"
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
    append_log(f"【金字塔任务启动】目标群: {target_group} | 目标活人数量: {pull_count} 人")
    
    async def _do_pull_job():
        global job_status
        joined_groups = []
        try:
            try:
                target_entity = await client.get_input_entity(target_group)
                await client(JoinChannelRequest(target_entity))
                joined_groups.append(target_entity)
                append_log(" 成功进入目的地群组。")
            except Exception:
                target_entity = await client.get_input_entity(target_group)

            pulled_today = 0

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

                append_log(f" 🔍 正在高速榨取【{src_group}】并过滤留存【离线1-3天内及在线】的高活跃成员...")
                
                three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)
                active_users_pool = []
                
                async for user in client.iter_participants(src_entity, limit=1000):
                    if user.bot or not user.username:
                        continue
                    
                    is_qualified = False
                    if isinstance(user.status, UserStatusOnline):
                        is_qualified = True
                    elif isinstance(user.status, UserStatusRecently):
                        is_qualified = True
                    elif isinstance(user.status, UserStatusOffline) and user.status.was_online:
                        if user.status.was_online >= three_days_ago:
                            is_qualified = True
                    
                    if is_qualified:
                        active_users_pool.append(user)

                def sort_rule(u):
                    if isinstance(u.status, UserStatusOnline): return (0, 0)
                    if isinstance(u.status, UserStatusRecently): return (1, 0)
                    return (2, -u.status.was_online.timestamp())

                active_users_pool.sort(key=sort_rule)
                append_log(f" ✨ 清洗完毕！已精准剥离 3 天以外死粉。当前队列留存真活人: {len(active_users_pool)} 个，启动强拉...")

                for user in active_users_pool:
                    while job_status == "paused":
                        await asyncio.sleep(1)
                    if job_status == "stopped" or pulled_today >= pull_count:
                        break
                    
                    status_lbl = ""
                    if isinstance(user.status, UserStatusOnline): 
                        status_lbl = "⚡ 当前在线"
                    elif isinstance(user.status, UserStatusRecently): 
                        status_lbl = "🥈 近期上线"
                    else: 
                        status_lbl = f"🥉 1-3天内活跃({user.status.was_online.strftime('%m-%d %H:%M')})"

                    try:
                        append_log(f" 正在强拉【{status_lbl}】真人: @{user.username} ...")
                        await client(InviteToChannelRequest(target_entity, [user]))
                        pulled_today += 1
                        append_log(f" 🟢 成功拉入: [ @{user.username} ] ！当前总进度: {pulled_today} / {pull_count}")
                        
                        if pulled_today >= pull_count:
                            append_log(" 目标强拉额度已全额达成！")
                            job_status = "stopped"
                            break

                        sleep_time = random.randint(30, 40)
                        append_log(f" 💤 正在进行安全休眠 {sleep_time} 秒...")
                        
                        for _ in range(sleep_time):
                            if job_status == "stopped": 
                                break
                            while job_status == "paused": 
                                await asyncio.sleep(1)
                            await asyncio.sleep(1)

                    except UserPrivacyRestrictedError:
                        append_log(f"❌ 失败：@{user.username} 开启了隐私保护。")
                    except UserAlreadyParticipantError:
                        append_log(f"💡 提示：@{user.username} 已经在目的地群了。")
                    except FloodWaitError as e:
                        append_log(f"⚠️ 触发风控：官方拒绝强拉，要求强制等待 {e.seconds} 秒。正在硬抗等待...")
                        await asyncio.sleep(e.seconds)
                    except Exception as e_user:
                        if "Too many requests" in str(e_user):
                            append_log("🚨 [额度死限] 触发官方死限！硬抗休眠 120 秒后继续冲锋...")
                            for _ in range(120):
                                if job_status == "stopped": 
                                    break
                                while job_status == "paused": 
                                    await asyncio.sleep(1)
                                await asyncio.sleep(1)
                        else:
                            append_log(f"❌ 强拉中途受阻: {str(e_user)}")
                            await asyncio.sleep(5)

            append_log(" 所有指定的采集源群组已全量清洗剥离完毕。")
        except Exception as e:
            append_log(f"【严重后台异常】{str(e)}")
        finally:
            append_log("🧹 任务状态结束，正在为您自动清理退群...")
            for group_entity in joined_groups:
                try:
                    await client(LeaveChannelRequest(group_entity))
                    await asyncio.sleep(1.5)
                except Exception:
                    pass
            append_log("✨ 所有临时加的群已自动无痕退出。")
            job_status = "stopped"

    asyncio.run_coroutine_threadsafe(_do_pull_job(), telethon_loop)
    return jsonify({"status": "success", "message": "金字塔高活跃精准强拉任务已提交后台！"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
