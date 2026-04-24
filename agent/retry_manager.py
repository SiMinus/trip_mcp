"""
智能重试管理器
"""
import asyncio
import logging
import time
from enum import Enum
from dataclasses import dataclass
from typing import Dict, Any, Callable, Optional, List

try:
    from loguru import logger
except ModuleNotFoundError:  # pragma: no cover - fallback for lightweight envs
    logger = logging.getLogger(__name__)


class ErrorCategory(Enum):
    RATE_LIMIT = "rate_limit"
    NETWORK_ERROR = "network_error"
    AUTH_ERROR = "auth_error"
    DATA_ERROR = "data_error"
    SERVER_ERROR = "server_error"
    TIMEOUT_ERROR = "timeout_error"
    UNKNOWN_ERROR = "unknown_error"


@dataclass
class RetryPolicy:
    max_retries: int
    base_delay: float
    max_delay: float
    backoff_strategy: str
    category: ErrorCategory


class ErrorClassifier:
    """智能错误分类器"""
    
    def __init__(self):
        self.error_patterns = {
            ErrorCategory.RATE_LIMIT: [
                'rate limit', 'too many requests', '429', 'quota exceeded',
                'rate_limit_exceeded', 'throttled', 'request_limit_exceeded'
            ],
            ErrorCategory.NETWORK_ERROR: [
                'connection', 'network', 'dns', 'socket', 'timeout',
                'connection_error', 'network_unreachable'
            ],
            ErrorCategory.AUTH_ERROR: [
                'unauthorized', 'authentication', 'permission', '401', '403',
                'access_denied', 'invalid_token', 'expired_token'
            ],
            ErrorCategory.DATA_ERROR: [
                'invalid_request', 'bad_request', '400', 'validation',
                'invalid_format', 'parse_error'
            ],
            ErrorCategory.SERVER_ERROR: [
                'internal_server_error', '502', '503', '504', '500',
                'service_unavailable', 'server_error'
            ],
            ErrorCategory.TIMEOUT_ERROR: [
                'timeout', 'deadline', 'time_out', 'read_timeout',
                'connect_timeout', 'request_timeout'
            ]
        }
    
    def classify_error(self, error: Exception) -> ErrorCategory:
        """分类错误类型"""
        error_msg = str(error).lower()
        error_type = type(error).__name__.lower()
        
        if 'timeout' in error_type or 'timeout' in error_msg:
            return ErrorCategory.TIMEOUT_ERROR
        
        for category, patterns in self.error_patterns.items():
            if any(pattern in error_msg for pattern in patterns):
                return category
        
        if hasattr(error, 'status_code'):
            status_code = str(error.status_code)
            if status_code.startswith('4'):
                if status_code in ['401', '403']:
                    return ErrorCategory.AUTH_ERROR
                elif status_code == '429':
                    return ErrorCategory.RATE_LIMIT
                else:
                    return ErrorCategory.DATA_ERROR
            elif status_code.startswith('5'):
                return ErrorCategory.SERVER_ERROR
        
        return ErrorCategory.UNKNOWN_ERROR


class CircuitBreaker:
    """熔断器实现"""
    
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60, 
                 success_threshold: int = 3):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        
    def call_allowed(self) -> bool:
        """检查是否允许调用"""
        if self.state == "CLOSED":
            return True
        elif self.state == "OPEN":
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = "HALF_OPEN"
                self.success_count = 0
                return True
            return False
        else:  # HALF_OPEN
            return self.success_count < self.success_threshold
    
    def on_success(self):
        """调用成功时回调"""
        if self.state == "HALF_OPEN":
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                self.state = "CLOSED"
                self.failure_count = 0
        
    def on_failure(self):
        """调用失败时回调"""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.state == "HALF_OPEN":
            self.state = "OPEN"
        elif self.state == "CLOSED" and self.failure_count >= self.failure_threshold:
            self.state = "OPEN"
    
    def get_state(self) -> str:
        """获取当前状态"""
        return self.state


class BackoffStrategy:
    """退避策略实现"""
    
    @staticmethod
    def exponential(attempt: int, base_delay: float, max_delay: float) -> float:
        """指数退避"""
        delay = base_delay * (2 ** attempt)
        return min(delay, max_delay)
    
    @staticmethod
    def linear(attempt: int, base_delay: float, max_delay: float) -> float:
        """线性退避"""
        delay = base_delay * (attempt + 1)
        return min(delay, max_delay)
    
    @staticmethod
    def adaptive(attempt: int, base_delay: float, max_delay: float, 
                error_category: ErrorCategory) -> float:
        """自适应退避 - 根据错误类型调整"""
        if error_category == ErrorCategory.RATE_LIMIT:
            delay = base_delay * (3 ** attempt)
        elif error_category == ErrorCategory.NETWORK_ERROR:
            delay = base_delay * (2.5 ** attempt)
        elif error_category == ErrorCategory.SERVER_ERROR:
            delay = base_delay * (1.5 ** attempt)
        else:
            delay = base_delay * (2 ** attempt)
        
        return min(delay, max_delay)


class SmartRetryManager:
    """智能重试管理器"""
    
    def __init__(self):
        self.classifier = ErrorClassifier()
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}
        self.retry_policies = self._init_retry_policies()
    
    def _init_retry_policies(self) -> Dict[ErrorCategory, RetryPolicy]:
        """初始化重试策略"""
        return {
            ErrorCategory.RATE_LIMIT: RetryPolicy(
                max_retries=5, base_delay=5.0, max_delay=120.0,
                backoff_strategy='exponential', category=ErrorCategory.RATE_LIMIT
            ),
            ErrorCategory.NETWORK_ERROR: RetryPolicy(
                max_retries=4, base_delay=2.0, max_delay=60.0,
                backoff_strategy='adaptive', category=ErrorCategory.NETWORK_ERROR
            ),
            ErrorCategory.AUTH_ERROR: RetryPolicy(
                max_retries=0, base_delay=0, max_delay=0,
                backoff_strategy='none', category=ErrorCategory.AUTH_ERROR
            ),
            ErrorCategory.DATA_ERROR: RetryPolicy(
                max_retries=2, base_delay=1.0, max_delay=30.0,
                backoff_strategy='linear', category=ErrorCategory.DATA_ERROR
            ),
            ErrorCategory.SERVER_ERROR: RetryPolicy(
                max_retries=3, base_delay=3.0, max_delay=90.0,
                backoff_strategy='exponential', category=ErrorCategory.SERVER_ERROR
            ),
            ErrorCategory.TIMEOUT_ERROR: RetryPolicy(
                max_retries=3, base_delay=2.0, max_delay=45.0,
                backoff_strategy='adaptive', category=ErrorCategory.TIMEOUT_ERROR
            ),
            ErrorCategory.UNKNOWN_ERROR: RetryPolicy(
                max_retries=2, base_delay=1.0, max_delay=30.0,
                backoff_strategy='exponential', category=ErrorCategory.UNKNOWN_ERROR
            )
        }
    
    def get_circuit_breaker(self, module_name: str) -> CircuitBreaker:
        """获取模块的熔断器"""
        if module_name not in self.circuit_breakers:
            self.circuit_breakers[module_name] = CircuitBreaker()
        return self.circuit_breakers[module_name]
    
    async def execute_with_retry(self, 
                                module_name: str,
                                coro_fn: Callable,
                                *args, **kwargs) -> Dict[str, Any]:
        """执行带智能重试的调用"""
        circuit_breaker = self.get_circuit_breaker(module_name)
        
        if not circuit_breaker.call_allowed():
            logger.warning(f"模块 {module_name} 熔断器开启，跳过调用")
            return {"success": False, "data": [], "error": "Circuit breaker open"}
        
        last_exception = None
        attempt = 0
        
        while attempt < 10:  # 最大尝试次数，防止无限循环
            try:
                result = await coro_fn(*args, **kwargs)
                circuit_breaker.on_success()
                return {"success": True, "data": result or []}
                
            except Exception as e:
                last_exception = e
                error_category = self.classifier.classify_error(e)
                policy = self.retry_policies[error_category]
                
                logger.warning(f"模块 {module_name} 第{attempt+1}次调用失败: {e} "
                             f"[错误类别: {error_category.value}]")
                
                # 如果不可重试，直接返回
                if policy.max_retries == 0:
                    circuit_breaker.on_failure()
                    break
                
                # 如果超过最大重试次数，跳出循环
                if attempt >= policy.max_retries:
                    circuit_breaker.on_failure()
                    break
                
                # 计算退避延迟
                delay = BackoffStrategy.adaptive(
                    attempt, policy.base_delay, policy.max_delay, error_category
                )
                
                logger.info(f"模块 {module_name} {delay:.1f}秒后重试 "
                          f"[重试策略: {policy.backoff_strategy}]")
                await asyncio.sleep(delay)
                attempt += 1
        
        logger.error(f"模块 {module_name} 重试耗尽，将返回空结果")
        return {"success": False, "data": [], "error": last_exception}
