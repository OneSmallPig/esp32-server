#!/usr/bin/env python3
"""
ONVIF摄像头连接测试脚本
用于验证摄像头是否能正常连接和控制
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from onvif import ONVIFCamera
except ImportError:
    # 尝试其他导入方式
    try:
        from onvif2 import ONVIFCamera
    except ImportError:
        print("❌ ONVIF库导入失败，请安装: pip install onvif-zeep")
        sys.exit(1)
import traceback

def test_camera_connection():
    """测试摄像头连接"""
    # 从配置文件读取摄像头信息
    camera_ip = "192.168.1.14"
    camera_port = 80
    username = "admin"
    password = "L21ED671"
    
    print(f"正在测试连接摄像头: {camera_ip}:{camera_port}")
    print(f"用户名: {username}")
    print(f"密码: {'*' * len(password)}")
    
    try:
        # 创建ONVIF摄像头对象
        print("\n1. 创建ONVIF摄像头连接...")
        camera = ONVIFCamera(camera_ip, camera_port, username, password)
        
        # 获取设备信息
        print("2. 获取设备信息...")
        device_service = camera.create_devicemgmt_service()
        device_info = device_service.GetDeviceInformation()
        print(f"   设备制造商: {device_info.Manufacturer}")
        print(f"   设备型号: {device_info.Model}")
        print(f"   固件版本: {device_info.FirmwareVersion}")
        print(f"   序列号: {device_info.SerialNumber}")
        
        # 获取媒体服务
        print("3. 创建媒体服务...")
        media_service = camera.create_media_service()
        
        # 获取配置文件
        print("4. 获取媒体配置文件...")
        profiles = media_service.GetProfiles()
        print(f"   找到 {len(profiles)} 个配置文件:")
        
        for i, profile in enumerate(profiles):
            print(f"   配置文件 {i+1}: {profile.Name} (Token: {profile.token})")
        
        if profiles:
            profile_token = profiles[0].token
            print(f"   将使用配置文件: {profile_token}")
            
            # 测试快照功能
            print("5. 测试快照功能...")
            try:
                snapshot_uri = media_service.GetSnapshotUri({'ProfileToken': profile_token})
                print(f"   快照URI: {snapshot_uri.Uri}")
                print("   ✅ 快照功能可用")
            except Exception as e:
                print(f"   ❌ 快照功能测试失败: {e}")
            
            # 测试PTZ功能
            print("6. 测试PTZ云台功能...")
            try:
                ptz_service = camera.create_ptz_service()
                ptz_config = ptz_service.GetConfiguration(profile_token)
                print(f"   PTZ配置: {ptz_config}")
                print("   ✅ PTZ功能可用")
                
                # 测试PTZ状态
                try:
                    ptz_status = ptz_service.GetStatus({'ProfileToken': profile_token})
                    print(f"   当前PTZ位置: Pan={ptz_status.Position.PanTilt.x}, Tilt={ptz_status.Position.PanTilt.y}")
                    print("   ✅ PTZ状态获取成功")
                except Exception as e:
                    print(f"   ⚠️  PTZ状态获取失败: {e}")
                    
            except Exception as e:
                print(f"   ❌ PTZ功能测试失败: {e}")
        
        print("\n🎉 摄像头连接测试完成！")
        return True
        
    except Exception as e:
        print(f"\n❌ 摄像头连接失败: {e}")
        print(f"详细错误信息:")
        traceback.print_exc()
        return False

def test_network_connectivity():
    """测试网络连通性"""
    import subprocess
    import requests
    
    camera_ip = "192.168.1.14"
    
    print(f"测试网络连通性...")
    
    # 测试ping
    try:
        result = subprocess.run(['ping', '-c', '3', camera_ip], 
                              capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            print(f"✅ Ping测试成功")
        else:
            print(f"❌ Ping测试失败")
            print(result.stderr)
    except Exception as e:
        print(f"❌ Ping测试出错: {e}")
    
    # 测试HTTP连接
    try:
        response = requests.get(f"http://{camera_ip}", timeout=5)
        print(f"✅ HTTP连接成功，状态码: {response.status_code}")
    except Exception as e:
        print(f"❌ HTTP连接失败: {e}")

if __name__ == "__main__":
    print("=" * 50)
    print("ONVIF摄像头连接测试")
    print("=" * 50)
    
    # 测试网络连通性
    test_network_connectivity()
    print()
    
    # 测试摄像头连接
    success = test_camera_connection()
    
    print("\n" + "=" * 50)
    if success:
        print("✅ 测试通过！摄像头可以正常使用")
    else:
        print("❌ 测试失败！请检查网络连接和摄像头配置")
    print("=" * 50) 