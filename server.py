import asyncio
import websockets
import json
import os
import base64
import time
from datetime import datetime
from collections import defaultdict

# ==================== الإعدادات ====================
PORT = int(os.environ.get("PORT", 10000))
connected = {}  # الأجهزة المتصلة {device_id: websocket}
device_info = {}  # معلومات الأجهزة {device_id: {name, capabilities, last_seen}}
audio_buffers = defaultdict(list)  # مخازن الصوت المؤقتة
video_buffers = defaultdict(list)  # مخازن الفيديو المؤقتة

# إحصائيات
stats = {
    "total_connections": 0,
    "total_frames": 0,
    "total_photos": 0,
    "total_audio": 0,
    "start_time": time.time()
}

print("=" * 70)
print("🎯 سيرفر التحكم المتكامل - الإصدار 2.0")
print("=" * 70)
print(f"📡 المنفذ: {PORT}")
print(f"🚀 بدء التشغيل: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 70)


# ==================== دوال مساعدة ====================
def log(message, type="INFO"):
    """تسجيل الرسائل مع الوقت"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {type}: {message}")


def save_file(data, folder, filename):
    """حفظ ملف"""
    os.makedirs(folder, exist_ok=True)
    filepath = os.path.join(folder, filename)
    with open(filepath, "wb") as f:
        f.write(data)
    return filepath


def format_size(size_bytes):
    """تنسيق حجم الملف"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


async def broadcast_to_all(message, exclude=None):
    """بث رسالة لجميع الأجهزة ما عدا المستبعد"""
    sent = 0
    disconnected = []
    
    for dev_id, ws in connected.items():
        if exclude and dev_id == exclude:
            continue
        
        try:
            await ws.send(json.dumps(message))
            sent += 1
        except:
            disconnected.append(dev_id)
    
    # تنظيف الأجهزة المنفصلة
    for dev_id in disconnected:
        if dev_id in connected:
            del connected[dev_id]
            log(f"🧹 تنظيف جهاز غير متصل: {dev_id}", "CLEAN")
    
    return sent


async def send_device_list(target_ws=None):
    """إرسال قائمة الأجهزة"""
    devices_list = []
    for dev_id, ws in connected.items():
        info = device_info.get(dev_id, {})
        devices_list.append({
            "id": dev_id,
            "name": info.get("name", f"جهاز {dev_id[:4]}"),
            "capabilities": info.get("capabilities", []),
            "last_seen": info.get("last_seen", time.time()),
            "status": "online"
        })
    
    message = {
        "type": "DEVICE_LIST",
        "devices": devices_list,
        "count": len(devices_list),
        "timestamp": time.time()
    }
    
    if target_ws:
        await target_ws.send(json.dumps(message))
    else:
        await broadcast_to_all(message)


# ==================== معالج الاتصالات الرئيسي ====================
async def handler(websocket):
    """معالجة جميع أنواع الاتصالات"""
    device_id = None
    
    try:
        # ===== 1️⃣ استقبال الرسائل =====
        async for message in websocket:
            try:
                data = json.loads(message)
                msg_type = data.get('type', 'unknown')
                device_id = data.get('deviceId', device_id or 'unknown')
                
                # تحديث آخر ظهور للجهاز
                if device_id and device_id != 'unknown':
                    if device_id in device_info:
                        device_info[device_id]['last_seen'] = time.time()
                
                # ===== 2️⃣ تسجيل جهاز جديد =====
                if msg_type == 'REGISTER':
                    device_id = data['deviceId']
                    device_name = data.get('deviceName', 'جهاز غير معروف')
                    capabilities = data.get('capabilities', [])
                    
                    connected[device_id] = websocket
                    device_info[device_id] = {
                        'name': device_name,
                        'capabilities': capabilities,
                        'last_seen': time.time(),
                        'connected_at': time.time()
                    }
                    stats['total_connections'] += 1
                    
                    log(f"✅ جهاز جديد: {device_name} ({device_id})", "REGISTER")
                    log(f"📋 الإمكانيات: {capabilities}", "INFO")
                    log(f"📊 إجمالي الأجهزة: {len(connected)}", "STATS")
                    
                    # رد تأكيد التسجيل
                    await websocket.send(json.dumps({
                        "type": "REGISTERED",
                        "deviceId": device_id,
                        "message": "تم التسجيل بنجاح",
                        "connected_devices": len(connected),
                        "timestamp": time.time()
                    }))
                    
                    # بث قائمة الأجهزة للجميع
                    await send_device_list()
                
                # ===== 3️⃣ طلب قائمة الأجهزة =====
                elif msg_type == 'GET_DEVICES':
                    await send_device_list(websocket)
                    log(f"📋 إرسال قائمة الأجهزة إلى {device_id}", "DEVICES")
                
                # ===== 4️⃣ إرسال أمر لجهاز محدد =====
                elif msg_type == 'COMMAND':
                    target_id = data.get('targetId')
                    command = data.get('command')
                    from_id = data.get('fromId', device_id)
                    
                    if target_id in connected:
                        await connected[target_id].send(json.dumps({
                            "type": "COMMAND",
                            "command": command,
                            "fromId": from_id,
                            "timestamp": time.time()
                        }))
                        
                        await websocket.send(json.dumps({
                            "type": "COMMAND_SENT",
                            "targetId": target_id,
                            "command": command,
                            "message": "تم إرسال الأمر",
                            "timestamp": time.time()
                        }))
                        
                        log(f"📤 أمر من {from_id} إلى {target_id}: {command}", "COMMAND")
                    else:
                        await websocket.send(json.dumps({
                            "type": "ERROR",
                            "message": f"الجهاز {target_id} غير متصل",
                            "timestamp": time.time()
                        }))
                        log(f"⚠️ جهاز غير متصل: {target_id}", "ERROR")
                
                # ===== 5️⃣ بث أمر للجميع =====
                elif msg_type == 'BROADCAST':
                    command = data.get('command')
                    from_id = data.get('fromId', device_id)
                    
                    sent = await broadcast_to_all({
                        "type": "COMMAND",
                        "command": command,
                        "fromId": from_id,
                        "broadcast": True,
                        "timestamp": time.time()
                    }, exclude=from_id)
                    
                    await websocket.send(json.dumps({
                        "type": "BROADCAST_SENT",
                        "count": sent,
                        "message": f"تم إرسال الأمر إلى {sent} جهاز",
                        "timestamp": time.time()
                    }))
                    
                    log(f"📢 بث من {from_id} إلى {sent} جهاز: {command}", "BROADCAST")
                
                # ===== 6️⃣ استقبال فيديو =====
                elif msg_type == 'VIDEO_FRAME':
                    frame_data = data.get('frame', '')
                    timestamp = data.get('timestamp', time.time())
                    sequence = data.get('sequence', 0)
                    is_last = data.get('isLast', False)
                    
                    stats['total_frames'] += 1
                    
                    # فك تشفير
                    try:
                        frame_bytes = base64.b64decode(frame_data)
                        size_str = format_size(len(frame_bytes))
                        log(f"📹 فيديو من {device_id} - الإطار #{sequence} - {size_str}", "VIDEO")
                        
                        # حفظ الفيديو إذا كان آخر إطار (اختياري)
                        if is_last:
                            filename = f"video_{device_id}_{int(time.time())}.mp4"
                            save_file(frame_bytes, "received_videos", filename)
                            log(f"💾 تم حفظ الفيديو: {filename}", "SAVE")
                        
                    except Exception as e:
                        log(f"❌ خطأ في فك تشفير الفيديو: {e}", "ERROR")
                    
                    # إعادة التوجيه للأجهزة الأخرى
                    forwarded = await broadcast_to_all({
                        "type": "VIDEO_FRAME",
                        "deviceId": device_id,
                        "frame": frame_data,
                        "sequence": sequence,
                        "isLast": is_last,
                        "timestamp": timestamp
                    }, exclude=device_id)
                    
                    # رد تأكيد
                    await websocket.send(json.dumps({
                        "type": "FRAME_RECEIVED",
                        "deviceId": device_id,
                        "sequence": sequence,
                        "forwarded": forwarded,
                        "timestamp": time.time()
                    }))
                
                # ===== 7️⃣ استقبال صورة =====
                elif msg_type == 'PHOTO':
                    image_data = data.get('image', '')
                    filename = data.get('filename', f"photo_{int(time.time())}.jpg")
                    
                    stats['total_photos'] += 1
                    
                    try:
                        image_bytes = base64.b64decode(image_data)
                        size_str = format_size(len(image_bytes))
                        
                        log(f"📸 صورة من {device_id} - {filename} - {size_str}", "PHOTO")
                        
                        # حفظ الصورة
                        saved_path = save_file(image_bytes, "received_photos", 
                                              f"{device_id}_{int(time.time())}.jpg")
                        
                        # إعادة التوجيه للأجهزة الأخرى
                        forwarded = await broadcast_to_all({
                            "type": "PHOTO",
                            "deviceId": device_id,
                            "image": image_data,
                            "filename": filename,
                            "timestamp": time.time()
                        }, exclude=device_id)
                        
                        # رد تأكيد
                        await websocket.send(json.dumps({
                            "type": "PHOTO_RECEIVED",
                            "deviceId": device_id,
                            "filename": filename,
                            "saved_as": os.path.basename(saved_path),
                            "size": len(image_bytes),
                            "size_str": size_str,
                            "forwarded": forwarded,
                            "timestamp": time.time()
                        }))
                        
                    except Exception as e:
                        log(f"❌ خطأ في حفظ الصورة: {e}", "ERROR")
                
                # ===== 8️⃣ استقبال صوت (تسجيل) =====
                elif msg_type == 'AUDIO':
                    audio_data = data.get('audio', '')
                    sample_rate = data.get('sampleRate', 16000)
                    channels = data.get('channels', 1)
                    duration = data.get('duration', 0)
                    
                    stats['total_audio'] += 1
                    
                    try:
                        audio_bytes = base64.b64decode(audio_data)
                        size_str = format_size(len(audio_bytes))
                        
                        log(f"🎤 صوت من {device_id} - {size_str} - {sample_rate}Hz", "AUDIO")
                        
                        # حفظ الصوت
                        filename = f"audio_{device_id}_{int(time.time())}.raw"
                        saved_path = save_file(audio_bytes, "received_audio", filename)
                        
                        # إعادة التوجيه
                        forwarded = await broadcast_to_all({
                            "type": "AUDIO",
                            "deviceId": device_id,
                            "audio": audio_data,
                            "sampleRate": sample_rate,
                            "channels": channels,
                            "timestamp": time.time()
                        }, exclude=device_id)
                        
                        # رد تأكيد
                        await websocket.send(json.dumps({
                            "type": "AUDIO_RECEIVED",
                            "deviceId": device_id,
                            "filename": filename,
                            "size_str": size_str,
                            "forwarded": forwarded,
                            "timestamp": time.time()
                        }))
                        
                    except Exception as e:
                        log(f"❌ خطأ في حفظ الصوت: {e}", "ERROR")
                
                # ===== 9️⃣ استقبال بث صوتي مباشر =====
                elif msg_type == 'AUDIO_STREAM':
                    audio_data = data.get('audio', '')
                    sequence = data.get('sequence', 0)
                    is_last = data.get('isLast', False)
                    sample_rate = data.get('sampleRate', 16000)
                    
                    # تخزين في المخزن المؤقت
                    audio_buffers[device_id].append({
                        'seq': sequence,
                        'data': audio_data,
                        'time': time.time()
                    })
                    
                    log(f"🔊 بث صوتي من {device_id} - الجزء {sequence}", "AUDIO_STREAM")
                    
                    # إعادة توجيه فورية
                    await broadcast_to_all({
                        "type": "AUDIO_STREAM",
                        "deviceId": device_id,
                        "audio": audio_data,
                        "sequence": sequence,
                        "isLast": is_last,
                        "sampleRate": sample_rate,
                        "timestamp": time.time()
                    }, exclude=device_id)
                    
                    # إذا كان الجزء الأخير، قم بدمج وحفظ
                    if is_last and device_id in audio_buffers:
                        all_parts = sorted(audio_buffers[device_id], key=lambda x: x['seq'])
                        
                        # دمج الأجزاء
                        combined = bytearray()
                        for part in all_parts:
                            combined.extend(base64.b64decode(part['data']))
                        
                        # حفظ الملف الكامل
                        filename = f"stream_{device_id}_{int(time.time())}.raw"
                        saved_path = save_file(combined, "received_audio_streams", filename)
                        
                        log(f"💾 تم حفظ البث الكامل: {filename} - {format_size(len(combined))}", "SAVE")
                        
                        # تنظيف المخزن
                        del audio_buffers[device_id]
                        
                        await websocket.send(json.dumps({
                            "type": "AUDIO_STREAM_COMPLETE",
                            "deviceId": device_id,
                            "filename": filename,
                            "size_str": format_size(len(combined)),
                            "parts": len(all_parts),
                            "timestamp": time.time()
                        }))
                
                # ===== 🔟 أمر صوتي =====
                elif msg_type == 'VOICE_COMMAND':
                    command_text = data.get('text', '')
                    confidence = data.get('confidence', 0)
                    audio_data = data.get('audio', '')
                    
                    log(f"🗣️ أمر صوتي من {device_id}: '{command_text}' (الثقة: {confidence}%)", "VOICE")
                    
                    # تحويل الأمر الصوتي إلى أمر عادي وتنفيذه
                    if 'شغل' in command_text or 'ابدأ' in command_text:
                        # إرسال أمر بدء البث للجهاز نفسه
                        pass
                    
                    await websocket.send(json.dumps({
                        "type": "VOICE_COMMAND_RECEIVED",
                        "deviceId": device_id,
                        "command": command_text,
                        "confidence": confidence,
                        "timestamp": time.time()
                    }))
                
                # ===== 1️⃣1️⃣ طلب إحصائيات =====
                elif msg_type == 'GET_STATS':
                    uptime = time.time() - stats['start_time']
                    hours = int(uptime // 3600)
                    minutes = int((uptime % 3600) // 60)
                    
                    await websocket.send(json.dumps({
                        "type": "STATS",
                        "connected_devices": len(connected),
                        "total_frames": stats['total_frames'],
                        "total_photos": stats['total_photos'],
                        "total_audio": stats['total_audio'],
                        "uptime": f"{hours}h {minutes}m",
                        "timestamp": time.time()
                    }))
                
                # ===== 1️⃣2️⃣ أمر غير معروف =====
                else:
                    log(f"⚠️ أمر غير معروف: {msg_type} من {device_id}", "WARNING")
                    await websocket.send(json.dumps({
                        "type": "ERROR",
                        "message": f"أمر غير معروف: {msg_type}",
                        "timestamp": time.time()
                    }))
            
            except json.JSONDecodeError:
                log(f"❌ رسالة غير صالحة: {message[:100]}...", "ERROR")
            except Exception as e:
                log(f"❌ خطأ في معالجة الرسالة: {e}", "ERROR")
    
    except websockets.exceptions.ConnectionClosed:
        log(f"🔴 قطع الاتصال: {device_id}", "DISCONNECT")
    except Exception as e:
        log(f"❌ خطأ عام: {e}", "ERROR")
    finally:
        # تنظيف عند قطع الاتصال
        if device_id and device_id in connected:
            del connected[device_id]
            if device_id in device_info:
                device_info[device_id]['last_seen'] = time.time()
            if device_id in audio_buffers:
                del audio_buffers[device_id]
            if device_id in video_buffers:
                del video_buffers[device_id]
            
            log(f"📊 الأجهزة المتبقية: {len(connected)}", "CLEAN")
            
            # بث القائمة المحدثة
            await send_device_list()


# ==================== فحص الصحة ====================
async def health_check(path, request_headers):
    """فحص صحي للسيرفر"""
    if path == "/":
        uptime = time.time() - stats['start_time']
        return websockets.http.Headers(), 200, json.dumps({
            "status": "running",
            "connected_devices": len(connected),
            "uptime_seconds": int(uptime),
            "version": "2.0"
        }).encode()
    return None


# ==================== تشغيل السيرفر ====================
async def main():
    """تشغيل السيرفر"""
    log("=" * 70, "START")
    log("🎯 سيرفر التحكم المتكامل جاهز للعمل", "START")
    log("=" * 70, "START")
    log(f"📡 المنفذ: {PORT}", "START")
    log(f"🌐 wss://your-server.onrender.com", "START")
    log("=" * 70, "START")
    log("📋 الأوامر المدعومة:", "START")
    log("   ✅ REGISTER - تسجيل جهاز", "START")
    log("   ✅ GET_DEVICES - قائمة الأجهزة", "START")
    log("   ✅ COMMAND - أوامر التحكم", "START")
    log("   ✅ BROADCAST - بث للجميع", "START")
    log("   ✅ VIDEO_FRAME - بث فيديو مباشر", "START")
    log("   ✅ PHOTO - إرسال صور", "START")
    log("   ✅ AUDIO - إرسال تسجيلات صوتية", "START")
    log("   ✅ AUDIO_STREAM - بث صوتي مباشر", "START")
    log("   ✅ VOICE_COMMAND - أوامر صوتية", "START")
    log("   ✅ GET_STATS - إحصائيات", "START")
    log("=" * 70, "START")
    
    async with websockets.serve(
        handler,
        "0.0.0.0",
        PORT,
        process_request=health_check,
        ping_interval=20,
        ping_timeout=60
    ):
        await asyncio.Future()


# ==================== نقطة الدخول ====================
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("\n👋 تم إيقاف السيرفر", "STOP")
    except Exception as e:
        log(f"❌ خطأ فادح: {e}", "FATAL")
