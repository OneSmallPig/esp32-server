from config.logger import setup_logging
import json
from plugins_func.register import (
    FunctionRegistry,
    ActionResponse,
    Action,
    ToolType,
    DeviceTypeRegistry,
)
from plugins_func.functions.hass_init import append_devices_to_prompt

TAG = __name__


class FunctionHandler:
    def __init__(self, conn):
        self.conn = conn
        self.config = conn.config
        self.device_type_registry = DeviceTypeRegistry()
        self.function_registry = FunctionRegistry()
        self.register_nessary_functions()
        self.register_config_functions()
        self.functions_desc = self.function_registry.get_all_function_desc()
        func_names = self.current_support_functions()
        self.modify_plugin_loader_des(func_names)
        self.finish_init = True

    def modify_plugin_loader_des(self, func_names):
        if "plugin_loader" not in func_names:
            return
        # 可编辑的列表中去掉plugin_loader
        surport_plugins = [func for func in func_names if func != "plugin_loader"]
        func_names = ",".join(surport_plugins)
        for function_desc in self.functions_desc:
            if function_desc["function"]["name"] == "plugin_loader":
                function_desc["function"]["description"] = function_desc["function"][
                    "description"
                ].replace("[plugins]", func_names)
                break

    def upload_functions_desc(self):
        self.functions_desc = self.function_registry.get_all_function_desc()

    def current_support_functions(self):
        func_names = []
        for func in self.functions_desc:
            func_names.append(func["function"]["name"])
        # 打印当前支持的函数列表
        self.conn.logger.bind(tag=TAG, session_id=self.conn.session_id).info(
            f"当前支持的函数列表: {func_names}"
        )
        return func_names

    def get_functions(self):
        """获取功能调用配置"""
        return self.functions_desc

    def register_nessary_functions(self):
        """注册必要的函数"""
        self.function_registry.register_function("handle_exit_intent")
        self.function_registry.register_function("plugin_loader")
        self.function_registry.register_function("get_time")
        self.function_registry.register_function("get_lunar")
        # self.function_registry.register_function("handle_speaker_volume_or_screen_brightness")

    def register_config_functions(self):
        """注册配置中的函数,可以不同客户端使用不同的配置"""
        config_functions = self.config["Intent"][self.config["selected_module"]["Intent"]].get("functions", [])
        
        # 强制添加send_email到函数列表（如果不存在的话）
        if "send_email" not in config_functions:
            config_functions = config_functions + ["send_email"]
            self.conn.logger.bind(tag=TAG, session_id=self.conn.session_id).info(
                "已自动添加send_email插件到函数列表"
            )
        
        # 强制添加save_weather_to_db到函数列表（如果不存在的话）
        if "save_weather_to_db" not in config_functions:
            config_functions = config_functions + ["save_weather_to_db"]
            self.conn.logger.bind(tag=TAG, session_id=self.conn.session_id).info(
                "已自动添加save_weather_to_db插件到函数列表"
            )
        
        # 强制添加onvif_camera_control到函数列表（如果不存在的话）
        if "onvif_camera_control" not in config_functions:
            config_functions = config_functions + ["onvif_camera_control"]
            self.conn.logger.bind(tag=TAG, session_id=self.conn.session_id).info(
                "已自动添加onvif_camera_control插件到函数列表"
            )
        
        # 强制添加vision_camera_analysis到函数列表（如果不存在的话）
        if "vision_camera_analysis" not in config_functions:
            config_functions = config_functions + ["vision_camera_analysis"]
            self.conn.logger.bind(tag=TAG, session_id=self.conn.session_id).info(
                "已自动添加vision_camera_analysis插件到函数列表"
            )
        
        for func in config_functions:
            self.function_registry.register_function(func)

        """home assistant需要初始化提示词"""
        append_devices_to_prompt(self.conn)

    def get_function(self, name):
        return self.function_registry.get_function(name)

    def handle_llm_function_call(self, conn, function_call_data):
        try:
            # 添加调试日志，查看完整的function_call_data
            self.conn.logger.bind(tag=TAG).info(f"收到function_call_data: {function_call_data}")
            
            function_name = function_call_data["name"]
            funcItem = self.get_function(function_name)
            if not funcItem:
                return ActionResponse(
                    action=Action.NOTFOUND, result="没有找到对应的函数", response=""
                )
            func = funcItem.func
            arguments = function_call_data["arguments"]
            
            # 添加调试日志，查看原始arguments
            self.conn.logger.bind(tag=TAG).info(f"原始arguments: {repr(arguments)}")
            
            # 修复可能的JSON格式错误：处理连续的JSON对象
            if arguments:
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError as e:
                    # 尝试修复连续JSON对象的问题
                    if '}{"' in arguments:
                        self.conn.logger.bind(tag=TAG).warning(f"检测到连续JSON对象，尝试修复: {e}")
                        # 分割连续的JSON对象
                        json_parts = []
                        current_part = ""
                        brace_count = 0
                        
                        for char in arguments:
                            if char == '{':
                                if brace_count == 0 and current_part:
                                    # 开始新的JSON对象
                                    json_parts.append(current_part)
                                    current_part = ""
                                brace_count += 1
                            elif char == '}':
                                brace_count -= 1
                            current_part += char
                        
                        if current_part:
                            json_parts.append(current_part)
                        
                        # 解析并合并所有JSON对象
                        merged_args = {}
                        for part in json_parts:
                            try:
                                part_args = json.loads(part)
                                merged_args.update(part_args)
                            except json.JSONDecodeError:
                                continue
                        
                        arguments = merged_args
                        self.conn.logger.bind(tag=TAG).info(f"修复后的arguments: {arguments}")
                    else:
                        raise e
            else:
                arguments = {}
            self.conn.logger.bind(tag=TAG).debug(
                f"调用函数: {function_name}, 参数: {arguments}"
            )
            if (
                funcItem.type == ToolType.SYSTEM_CTL
                or funcItem.type == ToolType.IOT_CTL
            ):
                return func(conn, **arguments)
            elif funcItem.type == ToolType.WAIT:
                return func(**arguments)
            elif funcItem.type == ToolType.CHANGE_SYS_PROMPT:
                return func(conn, **arguments)
            else:
                return ActionResponse(
                    action=Action.NOTFOUND, result="没有找到对应的函数", response=""
                )
        except Exception as e:
            self.conn.logger.bind(tag=TAG).error(f"处理function call错误: {e}")

        return None
