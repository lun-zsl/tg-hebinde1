import os
import asyncio
import threading
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from telethon import TelegramClient
from telethon.errors import UserPrivacyRestrictedError, UserAlreadyParticipantError, FloodWaitError, SessionPasswordNeededError
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest, InviteToChannelRequest

app = Flask(__name__, template_folder='.') 
CORS(app)

# ==================== 核心：独立后台线程配置 ====================
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
        client = TelegramClient(f'session_{phone}', int(api_id), api_hash, loop=telethon_loop)
        await client.connect()
        result = await client.send_code_request(phone)
        return result.phone_code_hash

    try:
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
    target_group = data.get('target_group').strip()
    pull_count = int(data.get('pull_count', 100))
    source_groups = data.get('source_groups', '').split('\n')
    
    if not client:
        return jsonify({"status": "error", "message": "错误：Telegram 账号未登录，请先登录！"})

    append_log(f"【任务启动】目标群: {target_group} | 计划拉取数量: {pull_count}")
    
    async def _do_pull_job():
        # 记录本次任务中所有加入过的群，以便最后统一无痕退出 [4]
        joined_groups = []
        try:
            # 1. 自动加入目的地群组 [2]
            append_log(f" 正在尝试自动加入目的地群: {target_group}...")
            try:
                target_entity = await client.get_input_entity(target_group)
                await client(JoinChannelRequest(target_entity))
                joined_groups.append(target_entity) # 标记需要退出 [4]
                append_log(" 成功加入目的地群。")
            except Exception as e:
                append_log(f"⚠️ 进目的地群提示（可能已在群内）: {str(e)}")
                target_entity = await client.get_input_entity(target_group)

            pulled_today = 0
            
            # 2. 循环处理采集群 [2]
            for src_group in source_groups:
                src_group = src_group.strip()
                if not src_group: 
                    continue
                
                append_log(f" 正在尝试自动加入采集群【{src_group}】...")
                try:
                    src_entity = await client.get_input_entity(src_group)
                    await client(JoinChannelRequest(src_entity))
                    joined_groups.append(src_entity) # 标记需要退出 [4]
                    append_log(f" 成功进入采集群【{src_group}】")
                except Exception as e:
                    append_log(f"💡 提示：该采集群无需重复加入或已在群内。")
                    src_entity = await client.get_input_entity(src_group)

                append_log(f" 开始从【{src_group}】中提取成员...")
                
                # 3. 提取并拉人
                async for user in client.iter_participants(src_entity, limit=200):
                    if pulled_today >= pull_count:
                        append_log("【目标达成】已达到您设定的拉取数量，准备清理战场...")
                        return
                    
                    if user.bot or not user.username:
                        continue
                        
                    try:
                        append_log(f" 正在尝试拉入用户: @{user.username} ...")
                        await client(InviteToChannelRequest(target_entity, [user]))
                        pulled_today += 1
                        append_log(f" 成功拉入 [ @{user.username} ] ！当前总计成功: {pulled_today} 人")
                        await asyncio.sleep(25) # 防封号延迟
                        
                    except UserPrivacyRestrictedError:
                        append_log(f"❌ 失败：用户 @{user.username} 开启了隐私保护。")
                    except UserAlreadyParticipantError:
                        append_log(f"💡 提示：用户 @{user.username} 已经在群里了。")
                    except FloodWaitError as e:
                        append_log(f"⚠️ 触发频率限制：官方要求必须等待 {e.seconds} 秒...")
                        await asyncio.sleep(e.seconds)
                    except Exception as e_user:
                        # 如果出现 Too many requests，说明整个号额度干光了，直接断开换退群清理 [1]
                        if "Too many requests" in str(e_user):
                            append_log("❌ 严重警告：本账号今日拉人额度已达官方死限！立即强行终止任务并自动退群！")
                            return
                        append_log(f"❌ 常规错误: {str(e_user)}")
                        await asyncio.sleep(5) 
                        
            append_log(f"【扫描完毕】所有采集群处理结束。")
            
        except Exception as e:
            append_log(f"【致命错误】后台任务中断: {str(e)}")
            
        finally:
            # 4. ⚡ 无论成功还是中途报错、触发限制，都会无痕自动退出加入过的群 [4]
            append_log("🧹 【清理战场】正在执行自动退群逻辑...")
            for group_entity in joined_groups:
                try:
                    await client(LeaveChannelRequest(group_entity))
                    append_log(" 已自动退出一个相关群组。")
                    await asyncio.sleep(2) # 优雅退群延迟
                except Exception as e_leave:
                    print(f"退群失败: {e_leave}")
            append_log("✨ 【无痕清理完成】所有临时加入的群组已全部自动退出！")

    # 提交到专用后台线程
    asyncio.run_coroutine_threadsafe(_do_pull_job(), telethon_loop)
    return jsonify({"status": "success", "message": "进群-拉人-退群全自动流水线任务已在后台启动！"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
