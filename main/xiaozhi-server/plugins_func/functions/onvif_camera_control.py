import os
import time
import requests
import asyncio
from datetime import datetime
from requests.auth import HTTPDigestAuth
from config.logger import setup_logging
from plugins_func.register import register_function, ToolType, ActionResponse, Action

# 尝试导入ONVIF依赖
try:
    from onvif import ONVIFCamera
    ONVIF_AVAILABLE = True
except ImportError:
    try:
        # 尝试其他可能的导入方式
        from onvif2 import ONVIFCamera
        ONVIF_AVAILABLE = True
    except ImportError:
        ONVIF_AVAILABLE = False

TAG = __name__
logger = setup_logging()

# ONVIF摄像头控制插件的函数描述
ONVIF_CAMERA_CONTROL_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "onvif_camera_control",
        "description": (
            "控制ONVIF摄像头进行云台操作和抓拍。用户可以说：'让卧室摄像头向上一点'、"
            "'帮我看看卧室的四周'、'抓拍一张客厅的照片'、'摄像头往左转'、'停止移动'等。"
            "支持云台移动、预设位置、抓拍等功能。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "camera_alias": {
                    "type": "string",
                    "description": "摄像头别名，如：卧室摄像头、客厅摄像头、书房摄像头等配置中的别名",
                },
                "action": {
                    "type": "string",
                    "description": "操作类型：move（移动）、capture（抓拍）、patrol（巡视）、stop（停止）、preset（预设位置）",
                },
                "direction": {
                    "type": "string",
                    "description": "移动方向：up（向上）、down（向下）、left（向左）、right（向右）、zoom_in（放大）、zoom_out（缩小）",
                },
                "preset_name": {
                    "type": "string",
                    "description": "预设位置名称，如：正前方、门口、窗户等",
                },
                "duration": {
                    "type": "number",
                    "description": "移动持续时间（秒），默认2秒",
                }
            },
            "required": ["camera_alias", "action"],
        },
    },
}

class ONVIFCameraManager:
    """ONVIF摄像头管理器"""
    
    def __init__(self):
        self.cameras = {}
        self.initialized = False
    
    def initialize_cameras(self, camera_configs):
        """初始化所有摄像头连接"""
        if not ONVIF_AVAILABLE:
            logger.bind(tag=TAG).error("ONVIF库未安装，请执行：pip install onvif-zeep")
            return False
        
        self.cameras = {}
        for camera_id, config in camera_configs.items():
            try:
                # 创建ONVIF摄像头连接
                camera = ONVIFCamera(
                    config['ip'], 
                    config['port'],
                    config['username'], 
                    config['password']
                )
                
                # 创建媒体服务
                media_service = camera.create_media_service()
                
                # 获取配置文件
                profiles = media_service.GetProfiles()
                profile_token = profiles[0].token if profiles else config.get('profile_token')
                
                # 尝试创建PTZ服务
                ptz_service = None
                try:
                    ptz_service = camera.create_ptz_service()
                    # 测试PTZ服务是否可用
                    try:
                        ptz_service.GetConfiguration(profile_token)
                        logger.bind(tag=TAG).info(f"摄像头 {config['alias']} PTZ功能可用")
                    except Exception as ptz_e:
                        logger.bind(tag=TAG).warning(f"摄像头 {config['alias']} PTZ功能不可用: {ptz_e}")
                        ptz_service = None
                except Exception as e:
                    logger.bind(tag=TAG).warning(f"摄像头 {config['alias']} 不支持PTZ服务: {e}")
                    ptz_service = None
                
                self.cameras[camera_id] = {
                    'device': camera,
                    'alias': config['alias'],
                    'ptz_service': ptz_service,
                    'media_service': media_service,
                    'profile_token': profile_token,
                    'config': config
                }
                
                logger.bind(tag=TAG).info(f"摄像头 {config['alias']} 初始化成功")
                
            except Exception as e:
                logger.bind(tag=TAG).error(f"摄像头 {config['alias']} 连接失败: {e}")
                continue
        
        self.initialized = len(self.cameras) > 0
        return self.initialized
    
    def find_camera_by_alias(self, alias):
        """根据别名查找摄像头"""
        for camera_id, camera_info in self.cameras.items():
            if camera_info['alias'] == alias:
                return camera_info
        return None
    
    def get_available_cameras(self):
        """获取可用摄像头列表"""
        return [info['alias'] for info in self.cameras.values()]

# 全局摄像头管理器
CAMERA_MANAGER = ONVIFCameraManager()

def initialize_camera_manager(conn):
    """初始化摄像头管理器"""
    global CAMERA_MANAGER
    
    if not CAMERA_MANAGER.initialized:
        if "plugins" not in conn.config or "onvif_camera" not in conn.config["plugins"]:
            logger.bind(tag=TAG).warning("未找到ONVIF摄像头配置")
            return False
        
        camera_config = conn.config["plugins"]["onvif_camera"]
        cameras = camera_config.get("cameras", {})
        
        if not cameras:
            logger.bind(tag=TAG).warning("摄像头配置为空")
            return False
        
        success = CAMERA_MANAGER.initialize_cameras(cameras)
        logger.bind(tag=TAG).info(f"摄像头管理器初始化{'成功' if success else '失败'}")
        return success
    
    return True

async def ptz_move(camera_info, direction, duration=2.0, speed=0.5):
    """云台移动控制"""
    try:
        # 检查是否有PTZ服务
        if 'ptz_service' not in camera_info or camera_info['ptz_service'] is None:
            return f"摄像头不支持PTZ云台控制功能"
            
        ptz_service = camera_info['ptz_service']
        profile_token = camera_info['profile_token']
        
        # 检查PTZ配置是否可用
        try:
            ptz_config = ptz_service.GetConfiguration(profile_token)
            logger.bind(tag=TAG).debug(f"PTZ配置: {ptz_config}")
        except Exception as e:
            logger.bind(tag=TAG).warning(f"无法获取PTZ配置: {e}")
            return f"摄像头PTZ配置不可用: {str(e)}"
        
        # 创建移动请求
        try:
            request = ptz_service.create_type('ContinuousMove')
            request.ProfileToken = profile_token
            
            # 初始化速度向量 - 使用更安全的方式
            request.Velocity = ptz_service.create_type('PTZSpeed')
            request.Velocity.PanTilt = ptz_service.create_type('Vector2D')
            request.Velocity.Zoom = ptz_service.create_type('Vector1D')
            
            # 初始化速度向量
            request.Velocity.PanTilt.x = 0.0
            request.Velocity.PanTilt.y = 0.0
            request.Velocity.Zoom.x = 0.0
        except Exception as e:
            logger.bind(tag=TAG).error(f"创建PTZ请求失败: {e}")
            return f"创建PTZ控制请求失败: {str(e)}"
        
        # 设置移动方向和速度
        if direction == 'left':
            request.Velocity.PanTilt.x = -speed
            direction_text = "向左"
        elif direction == 'right':
            request.Velocity.PanTilt.x = speed
            direction_text = "向右"
        elif direction == 'up':
            request.Velocity.PanTilt.y = speed
            direction_text = "向上"
        elif direction == 'down':
            request.Velocity.PanTilt.y = -speed
            direction_text = "向下"
        elif direction == 'zoom_in':
            request.Velocity.Zoom.x = speed
            direction_text = "放大"
        elif direction == 'zoom_out':
            request.Velocity.Zoom.x = -speed
            direction_text = "缩小"
        else:
            return f"不支持的移动方向: {direction}"
        
        # 开始移动
        ptz_service.ContinuousMove(request)
        logger.bind(tag=TAG).info(f"摄像头开始{direction_text}移动，持续{duration}秒")
        
        # 等待指定时间后停止
        await asyncio.sleep(duration)
        
        # 停止移动
        stop_request = ptz_service.create_type('Stop')
        stop_request.ProfileToken = profile_token
        stop_request.PanTilt = True
        stop_request.Zoom = True
        ptz_service.Stop(stop_request)
        
        return f"摄像头已完成{direction_text}移动"
        
    except Exception as e:
        logger.bind(tag=TAG).error(f"云台移动失败: {e}")
        return f"云台移动失败: {str(e)}"

async def ptz_patrol(camera_info, duration=10):
    """摄像头巡视功能（360度旋转）"""
    try:
        logger.bind(tag=TAG).info(f"开始摄像头巡视，持续{duration}秒")
        
        # 分段进行四个方向的移动
        directions = ['right', 'down', 'left', 'up']
        segment_duration = duration / 4
        
        for direction in directions:
            result = await ptz_move(camera_info, direction, segment_duration, 0.3)
            logger.bind(tag=TAG).debug(f"巡视移动: {result}")
            await asyncio.sleep(0.5)  # 短暂停顿
        
        return f"摄像头巡视完成，已查看四周"
        
    except Exception as e:
        logger.bind(tag=TAG).error(f"摄像头巡视失败: {e}")
        return f"摄像头巡视失败: {str(e)}"

async def ptz_stop(camera_info):
    """停止云台移动"""
    try:
        ptz_service = camera_info['ptz_service']
        profile_token = camera_info['profile_token']
        
        # 创建停止请求
        stop_request = ptz_service.create_type('Stop')
        stop_request.ProfileToken = profile_token
        stop_request.PanTilt = True
        stop_request.Zoom = True
        
        ptz_service.Stop(stop_request)
        return "摄像头已停止移动"
        
    except Exception as e:
        logger.bind(tag=TAG).error(f"停止云台失败: {e}")
        return f"停止云台失败: {str(e)}"

async def capture_snapshot(camera_info, storage_config):
    """抓拍功能"""
    try:
        media_service = camera_info['media_service']
        profile_token = camera_info['profile_token']
        camera_config = camera_info['config']
        alias = camera_info['alias']
        
        # 获取快照URI
        snapshot_uri_response = media_service.GetSnapshotUri({'ProfileToken': profile_token})
        snapshot_uri = snapshot_uri_response.Uri
        
        logger.bind(tag=TAG).info(f"获取快照URI: {snapshot_uri}")
        
        # 下载图像
        response = requests.get(
            snapshot_uri,
            auth=HTTPDigestAuth(camera_config['username'], camera_config['password']),
            timeout=10
        )
        
        if response.status_code == 200:
            # 确保存储目录存在
            storage_path = storage_config.get('local_path', './captures')
            os.makedirs(storage_path, exist_ok=True)
            
            # 生成文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{alias.replace('摄像头', '')}_{timestamp}.jpg"
            filepath = os.path.join(storage_path, filename)
            
            # 保存图像
            with open(filepath, 'wb') as f:
                f.write(response.content)
            
            # 检查文件大小
            file_size = os.path.getsize(filepath)
            logger.bind(tag=TAG).info(f"抓拍成功，文件大小: {file_size} bytes")
            
            return f"已成功抓拍{alias}的画面，保存为 {filename}，文件大小 {file_size//1024}KB"
        else:
            return f"抓拍失败，HTTP状态码: {response.status_code}"
            
    except Exception as e:
        logger.bind(tag=TAG).error(f"抓拍失败: {e}")
        return f"抓拍失败: {str(e)}"

async def goto_preset(camera_info, preset_name, preset_configs):
    """移动到预设位置"""
    try:
        ptz_service = camera_info['ptz_service']
        profile_token = camera_info['profile_token']
        
        # 查找预设位置配置
        preset_config = None
        for preset in preset_configs:
            if preset['name'] == preset_name:
                preset_config = preset
                break
        
        if not preset_config:
            return f"未找到预设位置: {preset_name}"
        
        # 创建绝对移动请求
        request = ptz_service.create_type('AbsoluteMove')
        request.ProfileToken = profile_token
        request.Position.PanTilt.x = preset_config['pan']
        request.Position.PanTilt.y = preset_config['tilt']
        request.Position.Zoom.x = preset_config['zoom']
        
        ptz_service.AbsoluteMove(request)
        
        return f"摄像头已移动到预设位置: {preset_name}"
        
    except Exception as e:
        logger.bind(tag=TAG).error(f"移动到预设位置失败: {e}")
        return f"移动到预设位置失败: {str(e)}"

@register_function("onvif_camera_control", ONVIF_CAMERA_CONTROL_FUNCTION_DESC, ToolType.SYSTEM_CTL)
def onvif_camera_control(conn, camera_alias: str, action: str, direction: str = None, 
                        preset_name: str = None, duration: float = 2.0):
    """ONVIF摄像头控制主函数"""
    
    async def _async_camera_control():
        try:
            logger.bind(tag=TAG).info(f"开始摄像头控制，摄像头: {camera_alias}, 动作: {action}")
            
            # 初始化摄像头管理器
            if not initialize_camera_manager(conn):
                return ActionResponse(
                    Action.REQLLM,
                    "摄像头系统初始化失败，请检查配置或网络连接",
                    None
                )
            
            # 查找指定摄像头
            camera_info = CAMERA_MANAGER.find_camera_by_alias(camera_alias)
            if not camera_info:
                available_cameras = CAMERA_MANAGER.get_available_cameras()
                return ActionResponse(
                    Action.REQLLM,
                    f"未找到摄像头'{camera_alias}'，可用的摄像头有：{', '.join(available_cameras)}",
                    None
                )
            
            # 获取配置
            camera_config = conn.config["plugins"]["onvif_camera"]
            storage_config = camera_config.get("capture_storage", {"local_path": "./captures"})
            ptz_settings = camera_config.get("ptz_settings", {})
            
            result_message = ""
            
            # 根据动作类型执行相应操作
            if action == "move":
                if not direction:
                    result_message = "移动操作需要指定方向（up/down/left/right/zoom_in/zoom_out）"
                else:
                    speed = ptz_settings.get("move_speed", 0.5)
                    result_message = await ptz_move(camera_info, direction, duration, speed)
                    
            elif action == "patrol":
                patrol_duration = duration if duration > 2 else 10
                result_message = await ptz_patrol(camera_info, patrol_duration)
                
            elif action == "stop":
                result_message = await ptz_stop(camera_info)
                
            elif action == "capture":
                result_message = await capture_snapshot(camera_info, storage_config)
                
            elif action == "preset":
                if not preset_name:
                    result_message = "预设位置操作需要指定位置名称"
                else:
                    preset_positions = ptz_settings.get("preset_positions", [])
                    result_message = await goto_preset(camera_info, preset_name, preset_positions)
                    
            else:
                result_message = f"不支持的操作类型: {action}，支持的操作有：move、patrol、stop、capture、preset"
            
            return ActionResponse(Action.REQLLM, result_message, None)
            
        except Exception as e:
            import traceback
            error_detail = traceback.format_exc()
            logger.bind(tag=TAG).error(f"摄像头控制出错: {str(e)}")
            logger.bind(tag=TAG).error(f"详细错误信息: {error_detail}")
            return ActionResponse(
                Action.REQLLM,
                f"摄像头控制出现问题: {str(e)}",
                None
            )
    
    # 使用asyncio运行异步函数
    try:
        # 检查是否有运行中的事件循环
        try:
            loop = asyncio.get_running_loop()
            # 如果有运行中的循环，在新线程中运行
            import concurrent.futures
            import threading
            
            def run_in_thread():
                return asyncio.run(_async_camera_control())
            
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(run_in_thread)
                return future.result(timeout=30)
                
        except RuntimeError:
            # 没有运行中的事件循环，直接运行
            return asyncio.run(_async_camera_control())
            
    except Exception as e:
        logger.bind(tag=TAG).error(f"异步执行失败: {e}")
        return ActionResponse(
            Action.REQLLM,
            f"摄像头控制执行失败: {str(e)}",
            None
        ) 