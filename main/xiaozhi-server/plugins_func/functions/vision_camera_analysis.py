import os
import time
import base64
import asyncio
from datetime import datetime
from config.logger import setup_logging
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from core.utils.vllm import create_instance

TAG = __name__
logger = setup_logging()

# 视觉摄像头分析插件的函数描述
VISION_CAMERA_ANALYSIS_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "vision_camera_analysis",
        "description": (
            "【智能视觉分析】当用户询问摄像头画面中的具体内容时，使用此功能进行AI视觉分析。"
            "自动完成：1)摄像头抓拍 2)AI图像识别分析 3)返回分析结果。"
            "触发条件：用户询问'有几个人'、'有什么东西'、'是否异常'、'场景情况'等画面内容问题。"
            "注意：如果用户询问画面内容，必须使用此功能而不是单纯抓拍。"
            "示例：'卧室有几个人'→统计人数；'客厅有什么'→识别物体；'房间正常吗'→安全检查。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "camera_alias": {
                    "type": "string",
                    "description": "摄像头别名，如：卧室摄像头、客厅摄像头等配置中的别名",
                },
                "question": {
                    "type": "string", 
                    "description": "要分析的具体问题，如：有几个人、有什么物体、是否有异常、场景如何等",
                },
                "analysis_type": {
                    "type": "string",
                    "description": "分析类型：人数统计、物体识别、安全检查、场景分析，可选参数",
                }
            },
            "required": ["camera_alias", "question"],
        },
    },
}

async def capture_camera_image(conn, camera_alias: str):
    """调用摄像头抓拍功能"""
    try:
        # 导入ONVIF摄像头控制函数
        from plugins_func.functions.onvif_camera_control import onvif_camera_control
        
        logger.bind(tag=TAG).info(f"开始抓拍摄像头: {camera_alias}")
        
        # 调用摄像头抓拍功能
        capture_result = onvif_camera_control(
            conn, 
            camera_alias=camera_alias, 
            action="capture"
        )
        
        # 检查抓拍结果
        logger.bind(tag=TAG).info(f"抓拍结果 - action: {capture_result.action}, response: {repr(capture_result.response)}")
        
        if capture_result.action != Action.REQLLM:
            raise Exception(f"摄像头抓拍失败，action: {capture_result.action}")
        
        # 从返回消息中提取文件名（用于日志记录）
        result_message = capture_result.response or "抓拍成功"
        
        # 获取摄像头配置中的存储路径
        camera_config = conn.config["plugins"]["onvif_camera"]
        capture_storage = camera_config.get("capture_storage", {})
        if capture_storage is None:
            capture_storage = {}
        storage_path = capture_storage.get("local_path", "./captures")
        
        # 获取最新的抓拍图片文件
        if not os.path.exists(storage_path):
            raise Exception(f"抓拍存储目录不存在: {storage_path}")
        
        # 查找最新的图片文件
        image_files = []
        for file in os.listdir(storage_path):
            if file.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp')):
                file_path = os.path.join(storage_path, file)
                if os.path.isfile(file_path):
                    image_files.append((file_path, os.path.getmtime(file_path)))
        
        if not image_files:
            raise Exception("未找到抓拍的图片文件")
        
        # 获取最新的图片文件
        latest_image = max(image_files, key=lambda x: x[1])[0]
        logger.bind(tag=TAG).info(f"找到最新抓拍图片: {latest_image}")
        
        return latest_image, result_message
        
    except Exception as e:
        logger.bind(tag=TAG).error(f"摄像头抓拍失败: {e}")
        raise

async def analyze_image_with_vllm(conn, image_path: str, question: str):
    """使用豆包视觉模型分析图片"""
    try:
        # 读取图片并转换为base64
        with open(image_path, 'rb') as f:
            image_data = f.read()
            image_base64 = base64.b64encode(image_data).decode('utf-8')
        
        logger.bind(tag=TAG).info(f"图片已转换为base64，大小: {len(image_base64)} 字符")
        
        # 从插件自身配置中获取VLLM配置
        plugin_config = conn.config["plugins"]["vision_camera_analysis"]
        vllm_config = plugin_config.get("vllm_config")
        
        if not vllm_config:
            raise Exception("未在插件配置中找到vllm_config，请检查配置文件")
        
        # 创建VLLM实例
        vllm_type = vllm_config.get("type")
        if not vllm_type:
            raise Exception("VLLM配置中缺少type字段")
        
        vllm = create_instance(vllm_type, vllm_config)
        
        logger.bind(tag=TAG).info(f"开始视觉分析，使用模型: {vllm_config.get('model_name', 'unknown')}")
        
        # 调用视觉模型分析
        analysis_result = vllm.response(question, image_base64)
        
        logger.bind(tag=TAG).info(f"视觉分析完成，结果长度: {len(analysis_result)} 字符")
        
        return analysis_result
        
    except Exception as e:
        logger.bind(tag=TAG).error(f"视觉分析失败: {e}")
        raise

def get_question_template(conn, analysis_type: str, original_question: str):
    """根据分析类型获取问题模板"""
    try:
        plugin_config = conn.config["plugins"]["vision_camera_analysis"]
        templates = plugin_config.get("question_templates", {})
        
        if analysis_type and analysis_type in templates:
            return templates[analysis_type]
        
        # 如果没有指定类型或找不到模板，使用原始问题
        return original_question
        
    except Exception:
        # 如果获取模板失败，使用原始问题
        return original_question

@register_function("vision_camera_analysis", VISION_CAMERA_ANALYSIS_FUNCTION_DESC, ToolType.SYSTEM_CTL)
def vision_camera_analysis(conn, camera_alias: str, question: str, analysis_type: str = None):
    """视觉摄像头分析主函数"""
    
    async def _async_vision_analysis():
        try:
            logger.bind(tag=TAG).info(f"开始视觉摄像头分析，摄像头: {camera_alias}, 问题: {question}")
            
            # 检查摄像头是否支持
            plugin_config = conn.config.get("plugins", {}).get("vision_camera_analysis", {})
            supported_cameras = plugin_config.get("supported_cameras", [])
            
            if camera_alias not in supported_cameras:
                return ActionResponse(
                    Action.REQLLM,
                    f"摄像头'{camera_alias}'不在支持的摄像头列表中。支持的摄像头：{', '.join(supported_cameras)}",
                    None
                )
            
            # 获取分析配置
            analysis_settings = plugin_config.get("analysis_settings", {})
            capture_delay = analysis_settings.get("capture_delay", 2)
            
            # 步骤1：抓拍摄像头图像
            try:
                image_path, capture_message = await capture_camera_image(conn, camera_alias)
                logger.bind(tag=TAG).info(f"抓拍成功: {capture_message}")
                
                # 等待图像稳定
                if capture_delay > 0:
                    await asyncio.sleep(capture_delay)
                
            except Exception as e:
                return ActionResponse(
                    Action.REQLLM,
                    f"摄像头抓拍失败: {str(e)}",
                    None
                )
            
            # 步骤2：准备分析问题
            final_question = get_question_template(conn, analysis_type, question)
            logger.bind(tag=TAG).info(f"最终分析问题: {final_question}")
            
            # 步骤3：使用豆包视觉模型分析图像
            try:
                analysis_result = await analyze_image_with_vllm(conn, image_path, final_question)
                
                # 组合最终结果
                result_message = f"📷 摄像头抓拍完成：{capture_message}\n\n🔍 视觉分析结果：\n{analysis_result}"
                
                # 检查是否保留分析后的图片
                keep_images = analysis_settings.get("keep_analyzed_images", True)
                if not keep_images:
                    try:
                        os.remove(image_path)
                        logger.bind(tag=TAG).info(f"已删除分析后的图片: {image_path}")
                    except Exception as e:
                        logger.bind(tag=TAG).warning(f"删除图片失败: {e}")
                
                return ActionResponse(Action.REQLLM, result_message, None)
                
            except Exception as e:
                return ActionResponse(
                    Action.REQLLM,
                    f"视觉分析失败: {str(e)}。摄像头抓拍正常：{capture_message}",
                    None
                )
            
        except Exception as e:
            import traceback
            error_detail = traceback.format_exc()
            logger.bind(tag=TAG).error(f"视觉摄像头分析出错: {str(e)}")
            logger.bind(tag=TAG).error(f"详细错误信息: {error_detail}")
            return ActionResponse(
                Action.REQLLM,
                f"视觉摄像头分析出现问题: {str(e)}",
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
                return asyncio.run(_async_vision_analysis())
            
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(run_in_thread)
                return future.result(timeout=60)  # 设置60秒超时
                
        except RuntimeError:
            # 没有运行中的事件循环，直接运行
            return asyncio.run(_async_vision_analysis())
            
    except Exception as e:
        logger.bind(tag=TAG).error(f"异步执行失败: {e}")
        return ActionResponse(
            Action.REQLLM,
            f"视觉摄像头分析执行失败: {str(e)}",
            None
        ) 