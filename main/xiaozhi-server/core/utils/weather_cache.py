"""
天气数据缓存池 - 幻影池实现
用于缓存天气数据，减少API调用，提升响应速度
"""

import time
import json
import threading
from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from config.logger import setup_logging

logger = setup_logging()
TAG = __name__

class WeatherCachePool:
    """天气数据缓存池 - 幻影池实现"""
    
    def __init__(self, config: Dict[str, Any]):
        """
        初始化缓存池
        
        Args:
            config: 缓存配置
                - weather_cache_ttl: 天气缓存时间(秒)，默认3600(1小时)
                - city_cache_ttl: 城市缓存时间(秒)，默认86400(24小时)
                - max_cache_size: 最大缓存条目数，默认50
                - enable_async_refresh: 是否启用异步刷新，默认True
        """
        self.config = config
        self.weather_cache_ttl = config.get("weather_cache_ttl", 3600)  # 1小时
        self.city_cache_ttl = config.get("city_cache_ttl", 86400)  # 24小时
        self.max_cache_size = config.get("max_cache_size", 50)
        self.enable_async_refresh = config.get("enable_async_refresh", True)
        
        # 缓存存储
        self._weather_cache: Dict[str, Dict[str, Any]] = {}  # 天气数据缓存
        self._city_cache: Dict[str, Dict[str, Any]] = {}     # 城市信息缓存
        self._access_order: Dict[str, float] = {}             # LRU访问顺序
        
        # 线程锁
        self._cache_lock = threading.RLock()
        
        # 统计信息
        self._stats = {
            "weather_hits": 0,
            "weather_misses": 0,
            "city_hits": 0,
            "city_misses": 0,
            "evictions": 0
        }
        
        logger.bind(tag=TAG).info(f"天气缓存池初始化完成 - 天气缓存:{self.weather_cache_ttl}s, 城市缓存:{self.city_cache_ttl}s")
    
    def _generate_weather_key(self, location: str, hour_precision: bool = True) -> str:
        """生成天气缓存键"""
        now = datetime.now()
        if hour_precision:
            # 按小时精度缓存
            time_key = now.strftime("%Y%m%d_%H")
        else:
            # 按天精度缓存
            time_key = now.strftime("%Y%m%d")
        return f"weather_{location}_{time_key}"
    
    def _generate_city_key(self, location: str) -> str:
        """生成城市缓存键"""
        return f"city_{location}"
    
    def _is_expired(self, cache_entry: Dict[str, Any]) -> bool:
        """检查缓存是否过期"""
        return time.time() > cache_entry.get("expires_at", 0)
    
    def _need_refresh(self, cache_entry: Dict[str, Any], refresh_threshold: float = 0.8) -> bool:
        """检查是否需要异步刷新(缓存剩余时间小于阈值)"""
        if not self.enable_async_refresh:
            return False
        
        expires_at = cache_entry.get("expires_at", 0)
        created_at = cache_entry.get("created_at", 0)
        total_ttl = expires_at - created_at
        remaining_ttl = expires_at - time.time()
        
        return remaining_ttl < (total_ttl * (1 - refresh_threshold))
    
    def _evict_lru(self):
        """LRU淘汰策略"""
        with self._cache_lock:
            total_entries = len(self._weather_cache) + len(self._city_cache)
            if total_entries < self.max_cache_size:
                return
            
            # 按访问时间排序，淘汰最久未使用的
            sorted_access = sorted(self._access_order.items(), key=lambda x: x[1])
            
            for key, _ in sorted_access[:total_entries - self.max_cache_size + 1]:
                if key.startswith("weather_"):
                    self._weather_cache.pop(key, None)
                elif key.startswith("city_"):
                    self._city_cache.pop(key, None)
                
                self._access_order.pop(key, None)
                self._stats["evictions"] += 1
                
                logger.bind(tag=TAG).debug(f"LRU淘汰缓存: {key}")
    
    def get_weather_data(self, location: str) -> Optional[Dict[str, Any]]:
        """
        获取缓存的天气数据
        
        Args:
            location: 城市名称
            
        Returns:
            缓存的天气数据，如果没有或过期则返回None
        """
        cache_key = self._generate_weather_key(location)
        
        with self._cache_lock:
            if cache_key in self._weather_cache:
                cache_entry = self._weather_cache[cache_key]
                
                if not self._is_expired(cache_entry):
                    # 更新访问时间
                    self._access_order[cache_key] = time.time()
                    self._stats["weather_hits"] += 1
                    
                    logger.bind(tag=TAG).debug(f"天气缓存命中: {location}")
                    return cache_entry["data"]
                else:
                    # 过期清理
                    del self._weather_cache[cache_key]
                    self._access_order.pop(cache_key, None)
            
            self._stats["weather_misses"] += 1
            logger.bind(tag=TAG).debug(f"天气缓存未命中: {location}")
            return None
    
    def set_weather_data(self, location: str, weather_data: Dict[str, Any]):
        """
        设置天气数据缓存
        
        Args:
            location: 城市名称
            weather_data: 天气数据
        """
        cache_key = self._generate_weather_key(location)
        current_time = time.time()
        
        cache_entry = {
            "data": weather_data,
            "created_at": current_time,
            "expires_at": current_time + self.weather_cache_ttl,
            "location": location
        }
        
        with self._cache_lock:
            self._weather_cache[cache_key] = cache_entry
            self._access_order[cache_key] = current_time
            
            # 执行LRU淘汰
            self._evict_lru()
            
            logger.bind(tag=TAG).debug(f"天气数据已缓存: {location}, 过期时间: {datetime.fromtimestamp(cache_entry['expires_at'])}")
    
    def get_city_info(self, location: str) -> Optional[Dict[str, Any]]:
        """
        获取缓存的城市信息
        
        Args:
            location: 城市名称
            
        Returns:
            缓存的城市信息，如果没有或过期则返回None
        """
        cache_key = self._generate_city_key(location)
        
        with self._cache_lock:
            if cache_key in self._city_cache:
                cache_entry = self._city_cache[cache_key]
                
                if not self._is_expired(cache_entry):
                    # 更新访问时间
                    self._access_order[cache_key] = time.time()
                    self._stats["city_hits"] += 1
                    
                    logger.bind(tag=TAG).debug(f"城市缓存命中: {location}")
                    return cache_entry["data"]
                else:
                    # 过期清理
                    del self._city_cache[cache_key]
                    self._access_order.pop(cache_key, None)
            
            self._stats["city_misses"] += 1
            logger.bind(tag=TAG).debug(f"城市缓存未命中: {location}")
            return None
    
    def set_city_info(self, location: str, city_info: Dict[str, Any]):
        """
        设置城市信息缓存
        
        Args:
            location: 城市名称
            city_info: 城市信息
        """
        cache_key = self._generate_city_key(location)
        current_time = time.time()
        
        cache_entry = {
            "data": city_info,
            "created_at": current_time,
            "expires_at": current_time + self.city_cache_ttl,
            "location": location
        }
        
        with self._cache_lock:
            self._city_cache[cache_key] = cache_entry
            self._access_order[cache_key] = current_time
            
            # 执行LRU淘汰
            self._evict_lru()
            
            logger.bind(tag=TAG).debug(f"城市信息已缓存: {location}, 过期时间: {datetime.fromtimestamp(cache_entry['expires_at'])}")
    
    def clear_cache(self):
        """清空所有缓存"""
        with self._cache_lock:
            self._weather_cache.clear()
            self._city_cache.clear()
            self._access_order.clear()
            
            logger.bind(tag=TAG).info("所有缓存已清空")
    
    def clean_expired(self):
        """清理过期缓存"""
        current_time = time.time()
        cleaned_count = 0
        
        with self._cache_lock:
            # 清理过期天气缓存
            expired_weather_keys = [
                key for key, entry in self._weather_cache.items()
                if current_time > entry.get("expires_at", 0)
            ]
            
            for key in expired_weather_keys:
                del self._weather_cache[key]
                self._access_order.pop(key, None)
                cleaned_count += 1
            
            # 清理过期城市缓存
            expired_city_keys = [
                key for key, entry in self._city_cache.items()
                if current_time > entry.get("expires_at", 0)
            ]
            
            for key in expired_city_keys:
                del self._city_cache[key]
                self._access_order.pop(key, None)
                cleaned_count += 1
        
        if cleaned_count > 0:
            logger.bind(tag=TAG).info(f"清理了 {cleaned_count} 条过期缓存")
    
    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        with self._cache_lock:
            total_weather_requests = self._stats["weather_hits"] + self._stats["weather_misses"]
            total_city_requests = self._stats["city_hits"] + self._stats["city_misses"]
            
            return {
                "weather_cache": {
                    "hits": self._stats["weather_hits"],
                    "misses": self._stats["weather_misses"],
                    "hit_rate": self._stats["weather_hits"] / max(total_weather_requests, 1),
                    "entries": len(self._weather_cache)
                },
                "city_cache": {
                    "hits": self._stats["city_hits"],
                    "misses": self._stats["city_misses"],
                    "hit_rate": self._stats["city_hits"] / max(total_city_requests, 1),
                    "entries": len(self._city_cache)
                },
                "general": {
                    "total_entries": len(self._weather_cache) + len(self._city_cache),
                    "max_entries": self.max_cache_size,
                    "evictions": self._stats["evictions"]
                }
            }
    
    def get_cache_info(self) -> str:
        """获取缓存信息的格式化字符串"""
        stats = self.get_stats()
        
        return f"""
=== 天气缓存池状态 ===
天气缓存: {stats['weather_cache']['entries']}条, 命中率: {stats['weather_cache']['hit_rate']:.2%}
城市缓存: {stats['city_cache']['entries']}条, 命中率: {stats['city_cache']['hit_rate']:.2%}
总缓存: {stats['general']['total_entries']}/{stats['general']['max_entries']}
LRU淘汰次数: {stats['general']['evictions']}
        """.strip()


# 全局缓存池实例
_weather_cache_pool: Optional[WeatherCachePool] = None

def get_weather_cache_pool(config: Dict[str, Any] = None) -> WeatherCachePool:
    """获取全局天气缓存池实例"""
    global _weather_cache_pool
    
    if _weather_cache_pool is None:
        if config is None:
            # 默认配置
            config = {
                "weather_cache_ttl": 3600,  # 1小时
                "city_cache_ttl": 86400,    # 24小时
                "max_cache_size": 50,
                "enable_async_refresh": True
            }
        _weather_cache_pool = WeatherCachePool(config)
    
    return _weather_cache_pool 