import hashlib
import json
import logging
import os
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any


logger = logging.getLogger(__name__)


class ConfigStorageError(ValueError):
    """Raised when the local configuration cannot be safely persisted."""


@dataclass(frozen=True)
class ConfigSnapshot:
    """Immutable file-plus-environment configuration view for one runtime boundary."""

    config_file: Path
    config_dir_source: str
    file_values: Mapping[str, object]
    values: Mapping[str, object]
    environment_values: Mapping[str, str | None]


class Config:
    _instance = None
    _SETUP_COMMAND = (
        "Run `smart-search setup`, or configure XAI_API_KEY and/or "
        "OPENAI_COMPATIBLE_API_URL plus OPENAI_COMPATIBLE_API_KEY, then run "
        "`smart-search doctor --format json`."
    )
    _DEFAULT_MODEL = "grok-4-fast"
    _DEFAULT_XAI_TOOLS = "web_search,x_search"
    _DEFAULT_VALIDATION_LEVEL = "balanced"
    _DEFAULT_FALLBACK_MODE = "auto"
    _DEFAULT_MINIMUM_PROFILE = "standard"
    _DEFAULT_INTENT_ROUTER_MODE = "hybrid"
    _DEFAULT_INTENT_ROUTER_TIMEOUT_SECONDS = "8"
    _DEFAULT_INTENT_EMBEDDING_THRESHOLD = "0.74"
    _DEFAULT_INTENT_EMBEDDING_MARGIN = "0.05"
    _DEFAULT_CACHE_ENABLED = "false"
    _DEFAULT_SEARCH_CACHE_TTL_SECONDS = "30"
    _DEFAULT_FETCH_CACHE_TTL_SECONDS = "300"
    _DEFAULT_CACHE_MAX_SIZE = "256"
    _MODEL_ROUTES_KEY = "SMART_SEARCH_MODEL_ROUTES"
    _CACHE_TTL_BOUNDS = (1, 604800)
    _CACHE_MAX_SIZE_BOUNDS = (1, 10000)
    _CONFIG_DIR_MODE = 0o700
    _CONFIG_FILE_MODE = 0o600
    _ALLOWED_XAI_TOOLS = {"web_search", "x_search"}
    _ALLOWED_VALIDATION_LEVELS = {"fast", "balanced", "strict"}
    _ALLOWED_FALLBACK_MODES = {"auto", "off"}
    _ALLOWED_MINIMUM_PROFILES = {"lite", "standard", "full", "off"}
    _ALLOWED_INTENT_ROUTER_MODES = {"hybrid", "rules", "off"}
    _MODEL_ROUTE_PROVIDER_ALIASES = {
        "xai": "xai-responses",
        "xai-responses": "xai-responses",
        "grok": "xai-responses",
        "openai": "openai-compatible",
        "openai-compatible": "openai-compatible",
        "chat-completions": "openai-compatible",
    }
    _MODEL_ROUTE_XAI_TOOLS = {"web_search", "x_search"}
    _CONFIG_KEYS = {
        "XAI_API_URL",
        "XAI_API_KEY",
        "XAI_MODEL",
        "XAI_TOOLS",
        "OPENAI_COMPATIBLE_API_URL",
        "OPENAI_COMPATIBLE_API_KEY",
        "OPENAI_COMPATIBLE_MODEL",
        "OPENAI_COMPATIBLE_FALLBACK_MODELS",
        "OPENAI_COMPATIBLE_STREAM",
        "SMART_SEARCH_VALIDATION_LEVEL",
        "SMART_SEARCH_FALLBACK_MODE",
        "SMART_SEARCH_MINIMUM_PROFILE",
        "SMART_SEARCH_MODEL_ROUTES",
        "SMART_SEARCH_RESEARCH_PREFERRED_PROVIDERS",
        "SMART_SEARCH_RESEARCH_DISABLED_PROVIDERS",
        "SMART_SEARCH_INTENT_ROUTER",
        "SMART_SEARCH_PROMPT_DIR",
        "SMART_SEARCH_SEARCH_PROMPT_FILE",
        "SMART_SEARCH_FETCH_PROMPT_FILE",
        "SMART_SEARCH_RESEARCH_PROMPT_FILE",
        "INTENT_EMBEDDING_API_URL",
        "INTENT_EMBEDDING_API_KEY",
        "INTENT_EMBEDDING_MODEL",
        "INTENT_EMBEDDING_THRESHOLD",
        "INTENT_EMBEDDING_MARGIN",
        "INTENT_CLASSIFIER_API_URL",
        "INTENT_CLASSIFIER_API_KEY",
        "INTENT_CLASSIFIER_MODEL",
        "INTENT_ROUTER_TIMEOUT_SECONDS",
        "EXA_API_KEY",
        "EXA_BASE_URL",
        "EXA_TIMEOUT_SECONDS",
        "CONTEXT7_API_KEY",
        "CONTEXT7_BASE_URL",
        "CONTEXT7_TIMEOUT_SECONDS",
        "ZHIPU_API_KEY",
        "ZHIPU_API_URL",
        "ZHIPU_SEARCH_ENGINE",
        "ZHIPU_TIMEOUT_SECONDS",
        "ZHIPU_MCP_API_KEY",
        "ZHIPU_MCP_SEARCH_API_URL",
        "ZHIPU_MCP_READER_API_URL",
        "ZHIPU_MCP_ZREAD_API_URL",
        "ZHIPU_MCP_TIMEOUT_SECONDS",
        "JINA_API_KEY",
        "JINA_READER_API_URL",
        "JINA_RESPOND_WITH",
        "JINA_TIMEOUT_SECONDS",
        "TAVILY_API_KEY",
        "TAVILY_API_URL",
        "TAVILY_ENABLED",
        "TAVILY_TIMEOUT_SECONDS",
        "FIRECRAWL_API_KEY",
        "FIRECRAWL_API_URL",
        "ANYSEARCH_API_KEY",
        "ANYSEARCH_API_URL",
        "ANYSEARCH_TIMEOUT_SECONDS",
        "SMART_SEARCH_DEBUG",
        "SMART_SEARCH_LOG_LEVEL",
        "SMART_SEARCH_LOG_DIR",
        "SMART_SEARCH_RETRY_MAX_ATTEMPTS",
        "SMART_SEARCH_RETRY_MULTIPLIER",
        "SMART_SEARCH_RETRY_MAX_WAIT",
        "SMART_SEARCH_OUTPUT_CLEANUP",
        "SMART_SEARCH_LOG_TO_FILE",
        "SMART_SEARCH_CACHE_ENABLED",
        "SMART_SEARCH_SEARCH_CACHE_TTL_SECONDS",
        "SMART_SEARCH_FETCH_CACHE_TTL_SECONDS",
        "SMART_SEARCH_CACHE_MAX_SIZE",
        "SSL_VERIFY",
    }
    _CREDENTIAL_KEYS = {
        key
        for key in _CONFIG_KEYS
        if "KEY" in key or "TOKEN" in key or "SECRET" in key
    } | {"SMART_SEARCH_MODEL_ROUTES"}
    _LEGACY_CONFIG_KEYS: dict[str, str] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._config_file = None
            cls._instance._config_dir_source = None
            cls._instance._config_snapshot = None
            cls._instance._cached_model = None
            cls._instance._credential_state_digest = None
            cls._instance._credential_epoch = 0
        return cls._instance

    @staticmethod
    def _default_config_dir() -> Path:
        if sys.platform.startswith("win"):
            local_appdata = os.getenv("LOCALAPPDATA")
            if local_appdata:
                return Path(local_appdata).expanduser() / "smart-search"
        return Path.home() / ".config" / "smart-search"

    @staticmethod
    def _legacy_windows_config_dir() -> Path:
        return Path.home() / ".config" / "smart-search"

    @staticmethod
    def _config_dir_override_value() -> str:
        return os.getenv("SMART_SEARCH_CONFIG_DIR") or ""

    @staticmethod
    def _same_config_dir(left: Path, right: Path) -> bool:
        left_text = os.path.abspath(os.path.expanduser(str(left)))
        right_text = os.path.abspath(os.path.expanduser(str(right)))
        if sys.platform.startswith("win"):
            left_text = left_text.replace("/", "\\").rstrip("\\").lower()
            right_text = right_text.replace("/", "\\").rstrip("\\").lower()
        else:
            left_text = left_text.rstrip("/")
            right_text = right_text.rstrip("/")
        return left_text == right_text

    @classmethod
    def _config_dir_override_matches_default(cls) -> bool:
        env_dir = cls._config_dir_override_value()
        if not env_dir:
            return False
        return cls._same_config_dir(Path(env_dir).expanduser(), cls._default_config_dir())

    @staticmethod
    def _resolve_config_dir() -> tuple[Path, str]:
        env_dir = os.getenv("SMART_SEARCH_CONFIG_DIR")
        if env_dir:
            return Path(env_dir).expanduser(), "environment"
        default_dir = Config._default_config_dir()
        if sys.platform.startswith("win"):
            legacy_dir = Config._legacy_windows_config_dir()
            if legacy_dir != default_dir and not (default_dir / "config.json").exists() and (legacy_dir / "config.json").exists():
                return legacy_dir, "legacy_windows_home"
        return default_dir, "default"

    @staticmethod
    def _safe_mkdir(p: Path) -> bool:
        try:
            p.mkdir(parents=True, exist_ok=True, mode=Config._CONFIG_DIR_MODE)
            if not sys.platform.startswith("win"):
                p.chmod(Config._CONFIG_DIR_MODE)
            return p.is_dir()
        except (PermissionError, OSError):
            return False

    @staticmethod
    def _secure_file_mode(p: Path) -> None:
        if not sys.platform.startswith("win"):
            p.chmod(Config._CONFIG_FILE_MODE)

    @staticmethod
    def _config_storage_hint() -> str:
        return "请设置 SMART_SEARCH_CONFIG_DIR 指向可写且受保护的配置目录"

    def _config_storage_error(self, config_dir: Path | None = None) -> str:
        target = config_dir or self.config_file.parent
        return f"无法准备安全配置目录: {target}。{self._config_storage_hint()}。"

    @property
    def config_file(self) -> Path:
        if self._config_file is None:
            config_dir, config_dir_source = self._resolve_config_dir()
            self._safe_mkdir(config_dir)
            self._config_file = config_dir / "config.json"
            self._config_dir_source = config_dir_source
        return self._config_file

    @property
    def config_dir_source(self) -> str:
        if self._config_file is None:
            _ = self.config_file
        return self._config_dir_source or "override"

    def _environment_values(self) -> dict[str, str | None]:
        return {key: os.getenv(key) for key in self._CONFIG_KEYS}

    def _get_config_snapshot(self) -> ConfigSnapshot:
        """
        /*
         * ================================================================================
         * 步骤1：加载配置快照
         * ================================================================================
         * 目标：让一次命令内的配置属性共享同一份文件读取结果。
         * 数据源：本地 config.json 和 _CONFIG_KEYS 对应的环境变量。
         * 操作：
         * 1) 文件只在快照缺失、路径变化或环境覆盖变化时读取。
         * 2) 环境变量覆盖文件值，并把原始文件值保留给 config list/source。
         * ================================================================================
         */
        """
        config_file = self.config_file
        config_dir_source = self.config_dir_source
        environment_values = self._environment_values()
        snapshot = self._config_snapshot
        if (
            snapshot is not None
            and snapshot.config_file == config_file
            and snapshot.config_dir_source == config_dir_source
            and dict(snapshot.environment_values) == environment_values
        ):
            return snapshot

        logger.info("开始加载配置快照: %s", config_file)
        file_values = self._load_config_file()
        if not isinstance(file_values, dict):
            file_values = {}
        file_values = dict(file_values)
        merged_values = dict(file_values)
        for key, value in environment_values.items():
            if value is not None:
                merged_values[key] = value

        snapshot = ConfigSnapshot(
            config_file=config_file,
            config_dir_source=config_dir_source,
            file_values=MappingProxyType(file_values),
            values=MappingProxyType(merged_values),
            environment_values=MappingProxyType(environment_values),
        )
        self._config_snapshot = snapshot
        logger.info("配置快照加载完成: %s", config_file)
        return snapshot

    @property
    def snapshot(self) -> ConfigSnapshot:
        return self._get_config_snapshot()

    def invalidate_snapshot(self) -> None:
        self._config_snapshot = None

    def refresh(self) -> ConfigSnapshot:
        """
        /*
         * ================================================================================
         * 步骤2：显式刷新配置
         * ================================================================================
         * 目标：在外部文件变化或测试边界变化后建立新的不可变配置视图。
         * 数据源：当前 config_file、config.json 和环境变量。
         * 操作：
         * 1) 丢弃当前快照。
         * 2) 立即加载并返回新的快照，供调用方固定本次配置。
         * ================================================================================
         */
        """
        logger.info("开始刷新配置快照")
        self.invalidate_snapshot()
        snapshot = self._get_config_snapshot()
        logger.info("配置快照刷新完成: %s", snapshot.config_file)
        return snapshot

    def _load_config_file(self) -> dict:
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
            return {}

    @classmethod
    def _parse_model_routes_value(cls, raw: object) -> list[dict[str, Any]]:
        """
        /*
         * ================================================================================
         * 步骤1：解析并校验模型路由
         * ================================================================================
         * 目标：把配置文件或环境变量中的模型路由转换为统一的内部结构。
         * 数据源：SMART_SEARCH_MODEL_ROUTES JSON 数组。
         * 操作：
         * 1) 校验数组、路由 ID、provider、地址、密钥和模型字段。
         * 2) 规范 provider 别名、OpenRouter 模型后缀和可选 fallback 字段。
         * 3) 拒绝重复 ID、未知 provider 和不完整路由，避免运行时猜测配置意图。
         * ================================================================================
         */
        """
        logger.info("步骤1开始：解析模型路由配置")
        value = raw
        try:
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError as exc:
                    raise ValueError("Invalid SMART_SEARCH_MODEL_ROUTES: expected a JSON array.") from exc
            if not isinstance(value, list):
                raise ValueError("Invalid SMART_SEARCH_MODEL_ROUTES: expected a JSON array.")

            routes: list[dict[str, Any]] = []
            seen_ids: set[str] = set()
            for index, item in enumerate(value, start=1):
                if not isinstance(item, Mapping):
                    raise ValueError(f"Invalid SMART_SEARCH_MODEL_ROUTES route {index}: expected an object.")

                route_id = str(item.get("id") or "").strip()
                if not route_id:
                    raise ValueError(f"Invalid SMART_SEARCH_MODEL_ROUTES route {index}: missing id.")
                if route_id in seen_ids:
                    raise ValueError(f"Invalid SMART_SEARCH_MODEL_ROUTES: duplicate id: {route_id}.")

                provider_value = str(item.get("provider") or "").strip().lower()
                provider = cls._MODEL_ROUTE_PROVIDER_ALIASES.get(provider_value, "")
                if not provider:
                    allowed = ", ".join(sorted({"xai-responses", "openai-compatible"}))
                    raise ValueError(
                        f"Invalid SMART_SEARCH_MODEL_ROUTES route {route_id}: "
                        f"unsupported provider {provider_value or '<empty>'}; supported: {allowed}."
                    )

                api_url = str(item.get("api_url") or "").strip()
                api_key = str(item.get("api_key") or "").strip()
                model = str(item.get("model") or "").strip()
                if not api_url or not api_key or not model:
                    raise ValueError(
                        f"Invalid SMART_SEARCH_MODEL_ROUTES route {route_id}: "
                        "api_url, api_key, and model are required."
                    )

                normalized_model = cls.apply_model_suffix_for_url(model, api_url)
                route: dict[str, Any] = {
                    "id": route_id,
                    "provider": provider,
                    "api_url": api_url,
                    "api_key": api_key,
                    "model": normalized_model,
                }
                if provider == "xai-responses":
                    tools_value = item.get("tools")
                    if tools_value is None:
                        tools = ["web_search", "x_search"]
                    elif isinstance(tools_value, str):
                        tools = [part.strip().lower() for part in tools_value.split(",") if part.strip()]
                    elif isinstance(tools_value, list):
                        tools = [str(part).strip().lower() for part in tools_value if str(part).strip()]
                    else:
                        raise ValueError(f"Invalid SMART_SEARCH_MODEL_ROUTES route {route_id}: tools must be a list or string.")
                    invalid_tools = [tool for tool in tools if tool not in cls._MODEL_ROUTE_XAI_TOOLS]
                    if invalid_tools:
                        allowed = ", ".join(sorted(cls._MODEL_ROUTE_XAI_TOOLS))
                        raise ValueError(
                            f"Invalid SMART_SEARCH_MODEL_ROUTES route {route_id}: "
                            f"unsupported tools {', '.join(invalid_tools)}; supported: {allowed}."
                        )
                    route["tools"] = list(dict.fromkeys(tools))
                else:
                    stream_value = item.get("stream", False)
                    if isinstance(stream_value, bool):
                        stream = stream_value
                    elif isinstance(stream_value, str) and stream_value.strip().lower() in {"true", "1", "yes"}:
                        stream = True
                    elif isinstance(stream_value, str) and stream_value.strip().lower() in {"false", "0", "no", ""}:
                        stream = False
                    else:
                        raise ValueError(f"Invalid SMART_SEARCH_MODEL_ROUTES route {route_id}: stream must be boolean.")
                    fallback_value = item.get("fallback_models", [])
                    if isinstance(fallback_value, str):
                        fallback_models = [part.strip() for part in fallback_value.split(",") if part.strip()]
                    elif isinstance(fallback_value, list):
                        fallback_models = [str(part).strip() for part in fallback_value if str(part).strip()]
                    else:
                        raise ValueError(
                            f"Invalid SMART_SEARCH_MODEL_ROUTES route {route_id}: fallback_models must be a list or string."
                        )
                    normalized_fallbacks: list[str] = []
                    for fallback_model in fallback_models:
                        candidate = cls.apply_model_suffix_for_url(fallback_model, api_url)
                        if candidate != normalized_model and candidate not in normalized_fallbacks:
                            normalized_fallbacks.append(candidate)
                    route["stream"] = stream
                    route["fallback_models"] = normalized_fallbacks

                routes.append(route)
                seen_ids.add(route_id)
        except ValueError:
            logger.info("步骤1结束：模型路由配置校验失败")
            raise
        logger.info("步骤1结束：模型路由配置解析完成，条数=%s", len(routes))
        return routes

    @classmethod
    def _mask_nested_secrets(cls, value: object) -> object:
        """
        /*
         * ================================================================================
         * 步骤2：脱敏模型路由
         * ================================================================================
         * 目标：让 config、model 和 doctor 输出可以展示路由，但不泄露嵌套密钥。
         * 数据源：已规范化的模型路由对象。
         * 操作：递归复制字典和列表，仅对 key/token/secret 字段使用统一掩码。
         * ================================================================================
         */
        """
        logger.info("步骤2开始：脱敏模型路由")
        if isinstance(value, Mapping):
            masked = {
                str(key): cls._mask_api_key(str(item))
                if any(marker in str(key).upper() for marker in ("KEY", "TOKEN", "SECRET"))
                else cls._mask_nested_secrets(item)
                for key, item in value.items()
            }
            logger.info("步骤2结束：模型路由字典脱敏完成")
            return masked
        if isinstance(value, list):
            masked_list = [cls._mask_nested_secrets(item) for item in value]
            logger.info("步骤2结束：模型路由列表脱敏完成，条数=%s", len(masked_list))
            return masked_list
        logger.info("步骤2结束：模型路由标量无需脱敏")
        return value

    @property
    def model_routes_configured(self) -> bool:
        snapshot = self._get_config_snapshot()
        return self._MODEL_ROUTES_KEY in snapshot.values

    @property
    def model_routes(self) -> list[dict[str, Any]]:
        snapshot = self._get_config_snapshot()
        if self._MODEL_ROUTES_KEY not in snapshot.values:
            return []
        return self._parse_model_routes_value(snapshot.values.get(self._MODEL_ROUTES_KEY))

    def get_model_routes(self, *, masked: bool = True) -> list[dict[str, Any]]:
        routes = self.model_routes
        if not masked:
            return routes
        return self._mask_nested_secrets(routes)  # type: ignore[return-value]

    def set_model_routes(self, routes: object) -> list[dict[str, Any]]:
        """
        /*
         * ================================================================================
         * 步骤3：持久化模型路由
         * ================================================================================
         * 目标：用原子配置写入保存经过校验的有序路由列表。
         * 数据源：model add、model remove 或 config set 提交的路由对象。
         * 操作：校验路由、保留其他配置项、写入 SMART_SEARCH_MODEL_ROUTES 并刷新快照。
         * ================================================================================
         */
        """
        logger.info("步骤3开始：保存模型路由")
        snapshot = self._get_config_snapshot()
        if snapshot.environment_values.get(self._MODEL_ROUTES_KEY) is not None:
            raise ValueError("SMART_SEARCH_MODEL_ROUTES is controlled by the environment and cannot be edited locally.")
        normalized = self._parse_model_routes_value(routes)
        config_data = dict(snapshot.file_values)
        config_data[self._MODEL_ROUTES_KEY] = normalized
        self._save_config_file(config_data)
        self._cached_model = None
        logger.info("步骤3结束：模型路由保存完成，条数=%s", len(normalized))
        return normalized

    def _legacy_model_routes_for_migration(self, snapshot: ConfigSnapshot) -> list[dict[str, Any]]:
        """
        /*
         * ==============================================================================
         * 步骤4：构造旧主搜索模型路由
         * ==============================================================================
         * 目标：首次 model add 时保留本地 legacy 主搜索配置。
         * 数据源：同一配置快照中的 XAI_* 与 OPENAI_COMPATIBLE_* 文件值。
         * 操作：
         * 1) 拒绝活跃 provider 的环境覆盖，避免把环境凭据写入 config.json。
         * 2) 按 legacy fallback 顺序构造稳定 ID 的独立 route。
         * ==============================================================================
        */
        """
        logger.info("步骤4开始：构造旧主搜索模型路由")
        xai_api_key = self.xai_api_key
        openai_api_url = self.openai_compatible_api_url
        openai_api_key = self.openai_compatible_api_key
        active_providers = (
            (
                "xai-responses",
                bool(xai_api_key),
                ("XAI_API_URL", "XAI_API_KEY", "XAI_MODEL", "XAI_TOOLS"),
            ),
            (
                "openai-compatible",
                bool(openai_api_url and openai_api_key),
                (
                    "OPENAI_COMPATIBLE_API_URL",
                    "OPENAI_COMPATIBLE_API_KEY",
                    "OPENAI_COMPATIBLE_MODEL",
                    "OPENAI_COMPATIBLE_STREAM",
                    "OPENAI_COMPATIBLE_FALLBACK_MODELS",
                ),
            ),
        )
        environment_keys = [
            key
            for _, configured, keys in active_providers
            if configured
            for key in keys
            if snapshot.environment_values.get(key) is not None
        ]
        if environment_keys:
            logger.info("步骤4结束：旧主搜索迁移被环境配置阻止")
            raise ValueError(
                "Cannot migrate legacy main-search configuration controlled by the environment "
                f"({', '.join(environment_keys)}). Set SMART_SEARCH_MODEL_ROUTES in the environment instead."
            )

        routes: list[dict[str, Any]] = []
        if xai_api_key:
            routes.append(
                {
                    "id": "legacy-xai-responses",
                    "provider": "xai-responses",
                    "api_url": self.xai_api_url,
                    "api_key": xai_api_key,
                    "model": self.xai_model,
                    "tools": self.parse_xai_tools(self.xai_tools_raw),
                }
            )
        if openai_api_url and openai_api_key:
            routes.append(
                {
                    "id": "legacy-openai-compatible",
                    "provider": "openai-compatible",
                    "api_url": openai_api_url,
                    "api_key": openai_api_key,
                    "model": self.openai_compatible_model,
                    "stream": self.openai_compatible_stream,
                    "fallback_models": self.openai_compatible_fallback_models,
                }
            )
        logger.info("步骤4结束：旧主搜索模型路由构造完成，条数=%s", len(routes))
        return routes

    def add_model_route(self, route: Mapping[str, object]) -> list[dict[str, Any]]:
        """
        /*
         * ==============================================================================
         * 步骤5：追加模型路由并迁移旧配置
         * ==============================================================================
         * 目标：保留已保存的 legacy 主搜索配置，再追加用户提交的 route。
         * 数据源：当前快照、legacy provider 配置与 model add 参数。
         * 操作：
         * 1) route list 存在时保留其顺序；缺失时构造 legacy routes。
         * 2) 统一交给 set_model_routes 校验并原子持久化。
         * ==============================================================================
        */
        """
        logger.info("步骤5开始：添加模型路由")
        snapshot = self._get_config_snapshot()
        routes = (
            list(self.model_routes)
            if self.model_routes_configured
            else self._legacy_model_routes_for_migration(snapshot)
        )
        routes.append(dict(route))
        result = self.set_model_routes(routes)
        logger.info("步骤5结束：模型路由添加完成，条数=%s", len(result))
        return result

    def remove_model_route(self, route_id: str) -> list[dict[str, Any]]:
        logger.info("开始删除模型路由: id=%s", route_id)
        normalized_id = str(route_id or "").strip()
        routes = self.model_routes
        remaining = [route for route in routes if route.get("id") != normalized_id]
        if len(remaining) == len(routes):
            raise ValueError(f"Model route not found: {normalized_id}")
        result = self.set_model_routes(remaining)
        logger.info("模型路由删除完成，条数=%s", len(result))
        return result

    def _save_config_file(self, config_data: dict) -> None:
        target = self.config_file
        temp_path: Path | None = None
        temp_fd: int | None = None
        logger.info("开始安全写入配置: %s", target)
        try:
            if not self._safe_mkdir(target.parent):
                raise PermissionError(self._config_storage_error(target.parent))

            # Keep the temporary file beside the target so os.replace is atomic
            # on the same filesystem and an interrupted write preserves the old file.
            temp_fd, temp_name = tempfile.mkstemp(
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=str(target.parent),
            )
            temp_path = Path(temp_name)
            if not sys.platform.startswith("win"):
                os.fchmod(temp_fd, self._CONFIG_FILE_MODE)
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                temp_fd = None
                json.dump(config_data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            self._secure_file_mode(temp_path)
            os.replace(temp_path, target)
            temp_path = None
            self.invalidate_snapshot()
            logger.info("配置写入完成: %s", target)
        except (IOError, OSError, TypeError, ValueError) as e:
            if temp_fd is not None:
                try:
                    os.close(temp_fd)
                except OSError:
                    pass
            hint = f"。{self._config_storage_hint()}"
            raise ConfigStorageError(f"无法保存配置文件: {str(e)}{hint}") from e
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink()
                except FileNotFoundError:
                    pass

    def _get_config_value(self, key: str, default: str | None = None) -> str | None:
        snapshot = self._get_config_snapshot()
        value = snapshot.values.get(key)
        if value is None and key not in snapshot.environment_values:
            value = os.getenv(key)
        if value is None:
            legacy_key = next((old for old, new in self._LEGACY_CONFIG_KEYS.items() if new == key), None)
            if legacy_key:
                value = snapshot.file_values.get(legacy_key)
        if value is None:
            return default
        return str(value)

    def get_saved_config(self, masked: bool = True) -> dict:
        data = self._get_config_snapshot().file_values
        normalized: dict[str, Any] = {}
        for old_key, new_key in self._LEGACY_CONFIG_KEYS.items():
            if old_key in data and new_key not in data:
                normalized[new_key] = str(data[old_key])
        for key, value in data.items():
            if key in self._CONFIG_KEYS and value is not None:
                if key == self._MODEL_ROUTES_KEY:
                    try:
                        normalized[key] = self._parse_model_routes_value(value)
                    except ValueError:
                        normalized[key] = "<invalid SMART_SEARCH_MODEL_ROUTES>"
                else:
                    normalized[key] = str(value)
        if not masked:
            return normalized
        masked_config: dict[str, Any] = {}
        for key, value in normalized.items():
            if key == self._MODEL_ROUTES_KEY and isinstance(value, list):
                masked_config[key] = self._mask_nested_secrets(value)
            elif isinstance(value, str):
                masked_config[key] = self._mask_if_secret(key, value)
            else:
                masked_config[key] = value
        return masked_config

    def get_config_source(self, key: str) -> str:
        snapshot = self._get_config_snapshot()
        environment_value = snapshot.environment_values.get(key)
        if key not in snapshot.environment_values:
            environment_value = os.getenv(key)
        if environment_value is not None:
            return "environment"
        if key in snapshot.file_values:
            return "config_file"
        legacy_key = next((old for old, new in self._LEGACY_CONFIG_KEYS.items() if new == key), None)
        if legacy_key and legacy_key in snapshot.file_values:
            return "config_file"
        return "default"

    def get_config_sources(self) -> dict[str, str]:
        snapshot = self._get_config_snapshot()
        sources: dict[str, str] = {}
        for key in sorted(self._CONFIG_KEYS):
            if snapshot.environment_values.get(key) is not None:
                sources[key] = "environment"
            elif key in snapshot.file_values:
                sources[key] = "config_file"
            else:
                legacy_key = next((old for old, new in self._LEGACY_CONFIG_KEYS.items() if new == key), None)
                sources[key] = "config_file" if legacy_key and legacy_key in snapshot.file_values else "default"
        return sources

    def set_config_value(self, key: str, value: object) -> None:
        key = key.strip().upper()
        if key not in self._CONFIG_KEYS:
            raise ValueError(f"Unsupported config key: {key}")
        config_data = dict(self._get_config_snapshot().file_values)
        config_data[key] = self._parse_model_routes_value(value) if key == self._MODEL_ROUTES_KEY else value
        self._save_config_file(config_data)
        if key in {
            "XAI_API_URL",
            "XAI_API_KEY",
            "XAI_MODEL",
            "XAI_TOOLS",
            "OPENAI_COMPATIBLE_API_URL",
            "OPENAI_COMPATIBLE_API_KEY",
            "OPENAI_COMPATIBLE_MODEL",
            "OPENAI_COMPATIBLE_FALLBACK_MODELS",
            "OPENAI_COMPATIBLE_STREAM",
            "SMART_SEARCH_MODEL_ROUTES",
            "SMART_SEARCH_VALIDATION_LEVEL",
            "SMART_SEARCH_FALLBACK_MODE",
            "SMART_SEARCH_MINIMUM_PROFILE",
            "SMART_SEARCH_INTENT_ROUTER",
        }:
            self._cached_model = None

    def unset_config_value(self, key: str) -> None:
        key = key.strip().upper()
        if key not in self._CONFIG_KEYS:
            raise ValueError(f"Unsupported config key: {key}")
        config_data = dict(self._get_config_snapshot().file_values)
        config_data.pop(key, None)
        for old_key, new_key in self._LEGACY_CONFIG_KEYS.items():
            if new_key == key:
                config_data.pop(old_key, None)
        self._save_config_file(config_data)
        if key in {
            "XAI_API_URL",
            "XAI_API_KEY",
            "XAI_MODEL",
            "XAI_TOOLS",
            "OPENAI_COMPATIBLE_API_URL",
            "OPENAI_COMPATIBLE_API_KEY",
            "OPENAI_COMPATIBLE_MODEL",
            "OPENAI_COMPATIBLE_FALLBACK_MODELS",
            "OPENAI_COMPATIBLE_STREAM",
            "SMART_SEARCH_MODEL_ROUTES",
            "SMART_SEARCH_VALIDATION_LEVEL",
            "SMART_SEARCH_FALLBACK_MODE",
            "SMART_SEARCH_MINIMUM_PROFILE",
            "SMART_SEARCH_INTENT_ROUTER",
        }:
            self._cached_model = None

    def config_path_info(self) -> dict:
        config_file = self.config_file
        storage_ok = self._safe_mkdir(config_file.parent)
        storage_error = "" if storage_ok else self._config_storage_error(config_file.parent)
        return {
            "ok": storage_ok,
            "config_file": str(config_file),
            "config_dir": str(config_file.parent),
            "config_dir_source": self.config_dir_source,
            "default_config_file": str(self._default_config_dir() / "config.json"),
            "legacy_windows_config_file": str(self._legacy_windows_config_dir() / "config.json") if sys.platform.startswith("win") else "",
            "legacy_windows_config_exists": (self._legacy_windows_config_dir() / "config.json").exists() if sys.platform.startswith("win") else False,
            "config_dir_override_value": self._config_dir_override_value(),
            "config_dir_override_matches_default": self._config_dir_override_matches_default(),
            "exists": config_file.exists(),
            "config_storage_ok": storage_ok,
            "error_type": "" if storage_ok else "config_error",
            "error": storage_error,
        }

    @property
    def debug_enabled(self) -> bool:
        return (self._get_config_value("SMART_SEARCH_DEBUG", "false") or "false").lower() in ("true", "1", "yes")

    @property
    def retry_max_attempts(self) -> int:
        return int(self._get_config_value("SMART_SEARCH_RETRY_MAX_ATTEMPTS", "3") or "3")

    @property
    def retry_multiplier(self) -> float:
        return float(self._get_config_value("SMART_SEARCH_RETRY_MULTIPLIER", "1") or "1")

    @property
    def retry_max_wait(self) -> int:
        return int(self._get_config_value("SMART_SEARCH_RETRY_MAX_WAIT", "10") or "10")

    @property
    def xai_api_url(self) -> str:
        return self._get_config_value("XAI_API_URL", "https://api.x.ai/v1") or "https://api.x.ai/v1"

    @property
    def xai_api_key(self) -> str | None:
        return self._get_config_value("XAI_API_KEY")

    @property
    def xai_model(self) -> str:
        return self._get_config_value("XAI_MODEL") or self._base_model_value()

    @property
    def xai_tools_raw(self) -> str:
        return self._get_config_value("XAI_TOOLS", self._DEFAULT_XAI_TOOLS) or self._DEFAULT_XAI_TOOLS

    @property
    def openai_compatible_api_url(self) -> str | None:
        return self._get_config_value("OPENAI_COMPATIBLE_API_URL")

    @property
    def openai_compatible_api_key(self) -> str | None:
        return self._get_config_value("OPENAI_COMPATIBLE_API_KEY")

    @property
    def openai_compatible_model(self) -> str:
        model = self._get_config_value("OPENAI_COMPATIBLE_MODEL") or self._base_model_value()
        return self.apply_model_suffix_for_url(model, self.openai_compatible_api_url or "")

    @property
    def openai_compatible_fallback_models(self) -> list[str]:
        raw = self._get_config_value("OPENAI_COMPATIBLE_FALLBACK_MODELS", "") or ""
        models: list[str] = []
        seen: set[str] = set()
        api_url = self.openai_compatible_api_url or ""
        primary = self.openai_compatible_model
        for item in raw.split(","):
            model = item.strip()
            if not model:
                continue
            model = self.apply_model_suffix_for_url(model, api_url)
            if model == primary or model in seen:
                continue
            seen.add(model)
            models.append(model)
        return models

    @property
    def openai_compatible_stream(self) -> bool:
        return (self._get_config_value("OPENAI_COMPATIBLE_STREAM", "false") or "false").lower() in ("true", "1", "yes")

    def parse_xai_tools(self, raw: str | None = None) -> list[str]:
        raw = raw or self.xai_tools_raw
        tools: list[str] = []
        invalid: list[str] = []
        seen: set[str] = set()
        for item in raw.split(","):
            tool = item.strip().lower()
            if not tool:
                continue
            if tool not in self._ALLOWED_XAI_TOOLS:
                invalid.append(tool)
                continue
            if tool not in seen:
                seen.add(tool)
                tools.append(tool)
        if invalid:
            allowed = ", ".join(sorted(self._ALLOWED_XAI_TOOLS))
            invalid_text = ", ".join(invalid)
            raise ValueError(f"Invalid XAI_TOOLS: {invalid_text}. Supported values: {allowed}")
        return tools

    def _validated_enum(self, key: str, default: str, allowed: set[str]) -> str:
        value = (self._get_config_value(key, default) or default).strip().lower()
        if value not in allowed:
            allowed_text = ", ".join(sorted(allowed))
            raise ValueError(f"Invalid {key}: {value}. Supported values: {allowed_text}")
        return value

    def _enum_info(self, key: str, default: str, allowed: set[str]) -> tuple[str, str]:
        value = (self._get_config_value(key, default) or default).strip().lower()
        if value not in allowed:
            allowed_text = ", ".join(sorted(allowed))
            return value, f"Invalid {key}: {value}. Supported values: {allowed_text}"
        return value, ""

    def _float_value(self, key: str, default: str) -> float:
        value = self._get_config_value(key, default) or default
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid {key}: {value}. Expected a number.")

    def _float_info(self, key: str, default: str) -> tuple[float, str]:
        try:
            return self._float_value(key, default), ""
        except ValueError as e:
            return float(default), str(e)

    def _bounded_float_value(self, key: str, default: str, minimum: float, maximum: float) -> float:
        value = self._float_value(key, default)
        if value < minimum or value > maximum:
            raise ValueError(f"Invalid {key}: {value}. Expected a number between {minimum:g} and {maximum:g}.")
        return value

    def _bounded_float_info(self, key: str, default: str, minimum: float, maximum: float) -> tuple[float, str]:
        try:
            return self._bounded_float_value(key, default, minimum, maximum), ""
        except ValueError as e:
            return float(default), str(e)

    def _bool_value(self, key: str, default: str) -> bool:
        value = (self._get_config_value(key, default) or default).strip().lower()
        if value not in {"true", "false", "1", "0", "yes", "no"}:
            raise ValueError(f"Invalid {key}: {value}. Expected true or false.")
        return value in {"true", "1", "yes"}

    def _bool_info(self, key: str, default: str) -> tuple[bool, str]:
        try:
            return self._bool_value(key, default), ""
        except ValueError as e:
            return default.lower() in {"true", "1", "yes"}, str(e)

    def _bounded_int_value(self, key: str, default: str, minimum: int, maximum: int) -> int:
        value = self._get_config_value(key, default) or default
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid {key}: {value}. Expected an integer.")
        if parsed < minimum or parsed > maximum:
            raise ValueError(f"Invalid {key}: {parsed}. Expected an integer between {minimum} and {maximum}.")
        return parsed

    def _bounded_int_info(self, key: str, default: str, minimum: int, maximum: int) -> tuple[int, str]:
        try:
            return self._bounded_int_value(key, default, minimum, maximum), ""
        except ValueError as e:
            return int(default), str(e)

    @property
    def validation_level(self) -> str:
        return self._validated_enum(
            "SMART_SEARCH_VALIDATION_LEVEL",
            self._DEFAULT_VALIDATION_LEVEL,
            self._ALLOWED_VALIDATION_LEVELS,
        )

    @property
    def fallback_mode(self) -> str:
        return self._validated_enum(
            "SMART_SEARCH_FALLBACK_MODE",
            self._DEFAULT_FALLBACK_MODE,
            self._ALLOWED_FALLBACK_MODES,
        )

    @property
    def minimum_profile(self) -> str:
        return self._validated_enum(
            "SMART_SEARCH_MINIMUM_PROFILE",
            self._DEFAULT_MINIMUM_PROFILE,
            self._ALLOWED_MINIMUM_PROFILES,
        )

    @property
    def prompt_dir(self) -> str:
        return self._get_config_value("SMART_SEARCH_PROMPT_DIR", "") or ""

    @property
    def search_prompt_file(self) -> str:
        return self._get_config_value("SMART_SEARCH_SEARCH_PROMPT_FILE", "") or ""

    @property
    def fetch_prompt_file(self) -> str:
        return self._get_config_value("SMART_SEARCH_FETCH_PROMPT_FILE", "") or ""

    @property
    def research_prompt_file(self) -> str:
        return self._get_config_value("SMART_SEARCH_RESEARCH_PROMPT_FILE", "") or ""

    @property
    def intent_router_mode(self) -> str:
        return self._validated_enum(
            "SMART_SEARCH_INTENT_ROUTER",
            self._DEFAULT_INTENT_ROUTER_MODE,
            self._ALLOWED_INTENT_ROUTER_MODES,
        )

    @property
    def cache_enabled(self) -> bool:
        return self._bool_value("SMART_SEARCH_CACHE_ENABLED", self._DEFAULT_CACHE_ENABLED)

    @property
    def search_cache_ttl_seconds(self) -> int:
        return self._bounded_int_value(
            "SMART_SEARCH_SEARCH_CACHE_TTL_SECONDS",
            self._DEFAULT_SEARCH_CACHE_TTL_SECONDS,
            *self._CACHE_TTL_BOUNDS,
        )

    @property
    def fetch_cache_ttl_seconds(self) -> int:
        return self._bounded_int_value(
            "SMART_SEARCH_FETCH_CACHE_TTL_SECONDS",
            self._DEFAULT_FETCH_CACHE_TTL_SECONDS,
            *self._CACHE_TTL_BOUNDS,
        )

    @property
    def cache_max_size(self) -> int:
        return self._bounded_int_value(
            "SMART_SEARCH_CACHE_MAX_SIZE",
            self._DEFAULT_CACHE_MAX_SIZE,
            *self._CACHE_MAX_SIZE_BOUNDS,
        )

    @property
    def credential_epoch(self) -> int:
        """
        ================================================================================
        步骤1：刷新凭据 epoch
        ================================================================================
        目标：凭据轮换后让旧缓存失效，但不把 secret 放入 key 或日志。
        数据源：当前环境变量和本地配置文件中的 credential keys。
        操作：
        1) 只在 Config 私有内存中比较凭据摘要。
        2) 凭据摘要变化时递增 epoch，调用方只使用整数 epoch。
        """
        values = [self._get_config_value(key, "") or "" for key in sorted(self._CREDENTIAL_KEYS)]
        digest = hashlib.sha256("\0".join(values).encode("utf-8")).hexdigest()
        if self._credential_state_digest is None:
            self._credential_state_digest = digest
        elif digest != self._credential_state_digest:
            self._credential_state_digest = digest
            self._credential_epoch += 1
        return int(self._credential_epoch)

    def runtime_cache_fingerprint(
        self,
        capability: str,
        provider: str,
        options: dict[str, object] | None = None,
    ) -> str:
        """
        ================================================================================
        步骤2：计算非敏感行为配置指纹
        ================================================================================
        目标：配置快照刷新后不复用旧 provider 结果。
        数据源：capability/provider 对应的 endpoint、模型参数和调用选项。
        操作：
        1) 排除所有 credential key，只收集非敏感行为配置。
        2) 将调用参数和配置按稳定 JSON 编码后计算摘要。
        """
        provider_config_keys = {
            "web_search": {
                "zhipu": ("ZHIPU_API_URL", "ZHIPU_SEARCH_ENGINE", "ZHIPU_TIMEOUT_SECONDS"),
                "zhipu-mcp": ("ZHIPU_MCP_SEARCH_API_URL", "ZHIPU_MCP_TIMEOUT_SECONDS"),
                "tavily": ("TAVILY_API_URL", "TAVILY_ENABLED", "TAVILY_TIMEOUT_SECONDS"),
                "firecrawl": ("FIRECRAWL_API_URL",),
            },
            "docs_search": {
                "context7": ("CONTEXT7_BASE_URL", "CONTEXT7_TIMEOUT_SECONDS"),
                "exa": ("EXA_BASE_URL", "EXA_TIMEOUT_SECONDS"),
            },
            "web_fetch": {
                "tavily": ("TAVILY_API_URL", "TAVILY_ENABLED", "TAVILY_TIMEOUT_SECONDS"),
                "jina": ("JINA_READER_API_URL", "JINA_RESPOND_WITH", "JINA_TIMEOUT_SECONDS"),
                "zhipu-mcp-reader": ("ZHIPU_MCP_READER_API_URL", "ZHIPU_MCP_TIMEOUT_SECONDS"),
                "firecrawl": ("FIRECRAWL_API_URL",),
            },
            "vertical_search": {
                "anysearch": ("ANYSEARCH_API_URL", "ANYSEARCH_TIMEOUT_SECONDS"),
            },
        }
        keys = provider_config_keys.get(capability, {}).get(provider, ())
        values = {
            key: self._get_config_value(key, "") or ""
            for key in keys
            if key not in self._CREDENTIAL_KEYS
        }
        values["SMART_SEARCH_OUTPUT_CLEANUP"] = self._get_config_value("SMART_SEARCH_OUTPUT_CLEANUP", "true") or "true"
        payload = {
            "capability": capability,
            "provider": provider,
            "config": values,
            "options": options or {},
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:24]

    @property
    def intent_embedding_api_url(self) -> str:
        return self._get_config_value("INTENT_EMBEDDING_API_URL", "") or ""

    @property
    def intent_embedding_api_key(self) -> str | None:
        return self._get_config_value("INTENT_EMBEDDING_API_KEY")

    @property
    def intent_embedding_model(self) -> str:
        return self._get_config_value("INTENT_EMBEDDING_MODEL", "") or ""

    @property
    def intent_embedding_threshold(self) -> float:
        return self._bounded_float_value("INTENT_EMBEDDING_THRESHOLD", self._DEFAULT_INTENT_EMBEDDING_THRESHOLD, 0.0, 1.0)

    @property
    def intent_embedding_margin(self) -> float:
        return self._bounded_float_value("INTENT_EMBEDDING_MARGIN", self._DEFAULT_INTENT_EMBEDDING_MARGIN, 0.0, 1.0)

    @property
    def intent_classifier_api_url(self) -> str:
        return self._get_config_value("INTENT_CLASSIFIER_API_URL", "") or ""

    @property
    def intent_classifier_api_key(self) -> str | None:
        return self._get_config_value("INTENT_CLASSIFIER_API_KEY")

    @property
    def intent_classifier_model(self) -> str:
        return self._get_config_value("INTENT_CLASSIFIER_MODEL", "") or ""

    @property
    def intent_router_timeout(self) -> float:
        return self._float_value("INTENT_ROUTER_TIMEOUT_SECONDS", self._DEFAULT_INTENT_ROUTER_TIMEOUT_SECONDS)

    def _csv_values(self, key: str) -> list[str]:
        raw = self._get_config_value(key, "") or ""
        values: list[str] = []
        seen: set[str] = set()
        for item in raw.split(","):
            value = item.strip().lower()
            if value and value not in seen:
                seen.add(value)
                values.append(value)
        return values

    @property
    def research_preferred_providers(self) -> list[str]:
        return self._csv_values("SMART_SEARCH_RESEARCH_PREFERRED_PROVIDERS")

    @property
    def research_disabled_providers(self) -> list[str]:
        return self._csv_values("SMART_SEARCH_RESEARCH_DISABLED_PROVIDERS")

    @property
    def tavily_enabled(self) -> bool:
        return (self._get_config_value("TAVILY_ENABLED", "true") or "true").lower() in ("true", "1", "yes")

    @property
    def tavily_api_url(self) -> str:
        return self._get_config_value("TAVILY_API_URL", "https://api.tavily.com") or "https://api.tavily.com"

    @property
    def tavily_api_key(self) -> str | None:
        return self._get_config_value("TAVILY_API_KEY")

    @property
    def tavily_timeout(self) -> float:
        return float(self._get_config_value("TAVILY_TIMEOUT_SECONDS", "30") or "30")

    @property
    def firecrawl_api_url(self) -> str:
        return self._get_config_value("FIRECRAWL_API_URL", "https://api.firecrawl.dev/v2") or "https://api.firecrawl.dev/v2"

    @property
    def firecrawl_api_key(self) -> str | None:
        return self._get_config_value("FIRECRAWL_API_KEY")

    @property
    def anysearch_api_url(self) -> str:
        return self._get_config_value("ANYSEARCH_API_URL", "https://api.anysearch.com/mcp") or "https://api.anysearch.com/mcp"

    @property
    def anysearch_api_key(self) -> str | None:
        return self._get_config_value("ANYSEARCH_API_KEY")

    @property
    def anysearch_timeout(self) -> float:
        return float(self._get_config_value("ANYSEARCH_TIMEOUT_SECONDS", "30") or "30")

    @property
    def log_level(self) -> str:
        return (self._get_config_value("SMART_SEARCH_LOG_LEVEL", "INFO") or "INFO").upper()

    @property
    def log_dir(self) -> Path:
        log_dir_str = self.log_dir_config_value
        log_dir = Path(log_dir_str)
        if log_dir.is_absolute():
            return log_dir

        return self.config_file.parent / log_dir

    @property
    def log_dir_config_value(self) -> str:
        return self._get_config_value("SMART_SEARCH_LOG_DIR", "logs") or "logs"

    @staticmethod
    def apply_model_suffix_for_url(model: str, api_url: str) -> str:
        if "openrouter" in api_url and ":online" not in model:
            return f"{model}:online"
        return model

    def _base_model_value(self) -> str:
        return self._DEFAULT_MODEL

    @staticmethod
    def _mask_api_key(key: str) -> str:
        if not key or len(key) <= 8:
            return "***"
        return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"

    @classmethod
    def _mask_if_secret(cls, key: str, value: str) -> str:
        if "KEY" in key or "TOKEN" in key or "SECRET" in key:
            return cls._mask_api_key(value)
        return value

    @property
    def output_cleanup_enabled(self) -> bool:
        return (self._get_config_value("SMART_SEARCH_OUTPUT_CLEANUP", "true") or "true").lower() in ("true", "1", "yes")

    @property
    def log_to_file_enabled(self) -> bool:
        return (self._get_config_value("SMART_SEARCH_LOG_TO_FILE", "false") or "false").lower() in ("true", "1", "yes")

    @property
    def ssl_verify_enabled(self) -> bool:
        return (self._get_config_value("SSL_VERIFY", "true") or "true").lower() not in ("false", "0", "no")

    @property
    def exa_api_key(self) -> str | None:
        return self._get_config_value("EXA_API_KEY")

    @property
    def exa_base_url(self) -> str:
        return self._get_config_value("EXA_BASE_URL", "https://api.exa.ai") or "https://api.exa.ai"

    @property
    def exa_timeout(self) -> float:
        return float(self._get_config_value("EXA_TIMEOUT_SECONDS", "30") or "30")

    @property
    def context7_api_key(self) -> str | None:
        return self._get_config_value("CONTEXT7_API_KEY")

    @property
    def context7_base_url(self) -> str:
        return self._get_config_value("CONTEXT7_BASE_URL", "https://context7.com") or "https://context7.com"

    @property
    def context7_timeout(self) -> float:
        return float(self._get_config_value("CONTEXT7_TIMEOUT_SECONDS", "30") or "30")

    @property
    def zhipu_api_key(self) -> str | None:
        return self._get_config_value("ZHIPU_API_KEY")

    @property
    def zhipu_api_url(self) -> str:
        return self._get_config_value("ZHIPU_API_URL", "https://open.bigmodel.cn/api") or "https://open.bigmodel.cn/api"

    @property
    def zhipu_search_engine(self) -> str:
        return self._get_config_value("ZHIPU_SEARCH_ENGINE", "search_std") or "search_std"

    @property
    def zhipu_timeout(self) -> float:
        return float(self._get_config_value("ZHIPU_TIMEOUT_SECONDS", "30") or "30")

    @property
    def zhipu_mcp_api_key(self) -> str | None:
        return self._get_config_value("ZHIPU_MCP_API_KEY")

    @property
    def zhipu_mcp_search_api_url(self) -> str:
        return self._get_config_value(
            "ZHIPU_MCP_SEARCH_API_URL",
            "https://open.bigmodel.cn/api/mcp/web_search_prime/mcp",
        ) or "https://open.bigmodel.cn/api/mcp/web_search_prime/mcp"

    @property
    def zhipu_mcp_reader_api_url(self) -> str:
        return self._get_config_value(
            "ZHIPU_MCP_READER_API_URL",
            "https://open.bigmodel.cn/api/mcp/web_reader/mcp",
        ) or "https://open.bigmodel.cn/api/mcp/web_reader/mcp"

    @property
    def zhipu_mcp_zread_api_url(self) -> str:
        return self._get_config_value(
            "ZHIPU_MCP_ZREAD_API_URL",
            "https://open.bigmodel.cn/api/mcp/zread/mcp",
        ) or "https://open.bigmodel.cn/api/mcp/zread/mcp"

    @property
    def zhipu_mcp_timeout(self) -> float:
        return float(self._get_config_value("ZHIPU_MCP_TIMEOUT_SECONDS", "30") or "30")

    @property
    def jina_api_key(self) -> str | None:
        return self._get_config_value("JINA_API_KEY")

    @property
    def jina_reader_api_url(self) -> str:
        return self._get_config_value("JINA_READER_API_URL", "https://r.jina.ai") or "https://r.jina.ai"

    @property
    def jina_respond_with(self) -> str:
        return self._get_config_value("JINA_RESPOND_WITH", "") or ""

    @property
    def jina_timeout(self) -> float:
        return float(self._get_config_value("JINA_TIMEOUT_SECONDS", "30") or "30")

    def get_config_info(self) -> dict:
        config_parameter_errors: list[str] = []
        config_path = self.config_path_info()
        model_routes: list[dict[str, Any]] = []
        model_routes_error = ""
        try:
            if self.model_routes_configured:
                model_routes = self.model_routes
        except ValueError as exc:
            model_routes_error = str(exc)
        explicit_main_configured = bool(
            model_routes
            or self.xai_api_key
            or (self.openai_compatible_api_url and self.openai_compatible_api_key)
        )
        if explicit_main_configured:
            config_status = "ok: 配置完整"
        else:
            config_status = f"config_error: {self._SETUP_COMMAND}"

        validation_level, validation_error = self._enum_info(
            "SMART_SEARCH_VALIDATION_LEVEL",
            self._DEFAULT_VALIDATION_LEVEL,
            self._ALLOWED_VALIDATION_LEVELS,
        )
        fallback_mode, fallback_error = self._enum_info(
            "SMART_SEARCH_FALLBACK_MODE",
            self._DEFAULT_FALLBACK_MODE,
            self._ALLOWED_FALLBACK_MODES,
        )
        minimum_profile, minimum_error = self._enum_info(
            "SMART_SEARCH_MINIMUM_PROFILE",
            self._DEFAULT_MINIMUM_PROFILE,
            self._ALLOWED_MINIMUM_PROFILES,
        )
        intent_router_mode, intent_router_error = self._enum_info(
            "SMART_SEARCH_INTENT_ROUTER",
            self._DEFAULT_INTENT_ROUTER_MODE,
            self._ALLOWED_INTENT_ROUTER_MODES,
        )
        intent_router_timeout, intent_router_timeout_error = self._float_info(
            "INTENT_ROUTER_TIMEOUT_SECONDS",
            self._DEFAULT_INTENT_ROUTER_TIMEOUT_SECONDS,
        )
        intent_embedding_threshold, intent_embedding_threshold_error = self._bounded_float_info(
            "INTENT_EMBEDDING_THRESHOLD",
            self._DEFAULT_INTENT_EMBEDDING_THRESHOLD,
            0.0,
            1.0,
        )
        intent_embedding_margin, intent_embedding_margin_error = self._bounded_float_info(
            "INTENT_EMBEDDING_MARGIN",
            self._DEFAULT_INTENT_EMBEDDING_MARGIN,
            0.0,
            1.0,
        )
        cache_enabled, cache_enabled_error = self._bool_info(
            "SMART_SEARCH_CACHE_ENABLED",
            self._DEFAULT_CACHE_ENABLED,
        )
        search_cache_ttl_seconds, search_cache_ttl_error = self._bounded_int_info(
            "SMART_SEARCH_SEARCH_CACHE_TTL_SECONDS",
            self._DEFAULT_SEARCH_CACHE_TTL_SECONDS,
            *self._CACHE_TTL_BOUNDS,
        )
        fetch_cache_ttl_seconds, fetch_cache_ttl_error = self._bounded_int_info(
            "SMART_SEARCH_FETCH_CACHE_TTL_SECONDS",
            self._DEFAULT_FETCH_CACHE_TTL_SECONDS,
            *self._CACHE_TTL_BOUNDS,
        )
        cache_max_size, cache_max_size_error = self._bounded_int_info(
            "SMART_SEARCH_CACHE_MAX_SIZE",
            self._DEFAULT_CACHE_MAX_SIZE,
            *self._CACHE_MAX_SIZE_BOUNDS,
        )
        config_parameter_errors.extend(
            error
            for error in (
                validation_error,
                fallback_error,
                minimum_error,
                intent_router_error,
                intent_router_timeout_error,
                intent_embedding_threshold_error,
                intent_embedding_margin_error,
                cache_enabled_error,
                search_cache_ttl_error,
                fetch_cache_ttl_error,
                cache_max_size_error,
                model_routes_error,
            )
            if error
        )
        if config_parameter_errors and config_status.startswith("ok:"):
            config_status = f"config_error: {'; '.join(config_parameter_errors)}"
        if not config_path.get("ok", False):
            config_status = f"config_error: {config_path.get('error', self._config_storage_hint())}"

        return {
            "XAI_API_URL": self.xai_api_url,
            "XAI_API_KEY": self._mask_api_key(self.xai_api_key) if self.xai_api_key else "未配置",
            "XAI_MODEL": self.xai_model,
            "XAI_TOOLS": self.xai_tools_raw,
            "OPENAI_COMPATIBLE_API_URL": self.openai_compatible_api_url or "未配置",
            "OPENAI_COMPATIBLE_API_KEY": self._mask_api_key(self.openai_compatible_api_key) if self.openai_compatible_api_key else "未配置",
            "OPENAI_COMPATIBLE_MODEL": self.openai_compatible_model,
            "OPENAI_COMPATIBLE_FALLBACK_MODELS": ",".join(self.openai_compatible_fallback_models),
            "OPENAI_COMPATIBLE_STREAM": self.openai_compatible_stream,
            "SMART_SEARCH_MODEL_ROUTES": self.get_model_routes(masked=True) if not model_routes_error else "<invalid SMART_SEARCH_MODEL_ROUTES>",
            "SMART_SEARCH_VALIDATION_LEVEL": validation_level,
            "SMART_SEARCH_FALLBACK_MODE": fallback_mode,
            "SMART_SEARCH_MINIMUM_PROFILE": minimum_profile,
            "SMART_SEARCH_PROMPT_DIR": self.prompt_dir,
            "SMART_SEARCH_SEARCH_PROMPT_FILE": self.search_prompt_file,
            "SMART_SEARCH_FETCH_PROMPT_FILE": self.fetch_prompt_file,
            "SMART_SEARCH_RESEARCH_PROMPT_FILE": self.research_prompt_file,
            "SMART_SEARCH_RESEARCH_PREFERRED_PROVIDERS": ",".join(self.research_preferred_providers),
            "SMART_SEARCH_RESEARCH_DISABLED_PROVIDERS": ",".join(self.research_disabled_providers),
            "SMART_SEARCH_INTENT_ROUTER": intent_router_mode,
            "INTENT_EMBEDDING_API_URL": self.intent_embedding_api_url or "未配置",
            "INTENT_EMBEDDING_API_KEY": self._mask_api_key(self.intent_embedding_api_key) if self.intent_embedding_api_key else "未配置",
            "INTENT_EMBEDDING_MODEL": self.intent_embedding_model or "未配置",
            "INTENT_EMBEDDING_THRESHOLD": intent_embedding_threshold,
            "INTENT_EMBEDDING_MARGIN": intent_embedding_margin,
            "INTENT_CLASSIFIER_API_URL": self.intent_classifier_api_url or "未配置",
            "INTENT_CLASSIFIER_API_KEY": self._mask_api_key(self.intent_classifier_api_key) if self.intent_classifier_api_key else "未配置",
            "INTENT_CLASSIFIER_MODEL": self.intent_classifier_model or "未配置",
            "INTENT_ROUTER_TIMEOUT_SECONDS": intent_router_timeout,
            "SMART_SEARCH_CACHE_ENABLED": cache_enabled,
            "SMART_SEARCH_SEARCH_CACHE_TTL_SECONDS": search_cache_ttl_seconds,
            "SMART_SEARCH_FETCH_CACHE_TTL_SECONDS": fetch_cache_ttl_seconds,
            "SMART_SEARCH_CACHE_MAX_SIZE": cache_max_size,
            "SMART_SEARCH_DEBUG": self.debug_enabled,
            "SMART_SEARCH_LOG_LEVEL": self.log_level,
            "SMART_SEARCH_LOG_DIR": self.log_dir_config_value,
            "SMART_SEARCH_RETRY_MAX_ATTEMPTS": self.retry_max_attempts,
            "SMART_SEARCH_RETRY_MULTIPLIER": self.retry_multiplier,
            "SMART_SEARCH_RETRY_MAX_WAIT": self.retry_max_wait,
            "TAVILY_API_URL": self.tavily_api_url,
            "TAVILY_ENABLED": self.tavily_enabled,
            "TAVILY_API_KEY": self._mask_api_key(self.tavily_api_key) if self.tavily_api_key else "未配置",
            "TAVILY_TIMEOUT_SECONDS": self.tavily_timeout,
            "FIRECRAWL_API_URL": self.firecrawl_api_url,
            "FIRECRAWL_API_KEY": self._mask_api_key(self.firecrawl_api_key) if self.firecrawl_api_key else "未配置",
            "ANYSEARCH_API_URL": self.anysearch_api_url,
            "ANYSEARCH_API_KEY": self._mask_api_key(self.anysearch_api_key) if self.anysearch_api_key else "未配置",
            "ANYSEARCH_TIMEOUT_SECONDS": self.anysearch_timeout,
            "SMART_SEARCH_OUTPUT_CLEANUP": self.output_cleanup_enabled,
            "SMART_SEARCH_LOG_TO_FILE": self.log_to_file_enabled,
            "SSL_VERIFY": self.ssl_verify_enabled,
            "EXA_API_KEY": self._mask_api_key(self.exa_api_key) if self.exa_api_key else "未配置",
            "EXA_BASE_URL": self.exa_base_url,
            "EXA_TIMEOUT_SECONDS": self.exa_timeout,
            "CONTEXT7_API_KEY": self._mask_api_key(self.context7_api_key) if self.context7_api_key else "未配置",
            "CONTEXT7_BASE_URL": self.context7_base_url,
            "CONTEXT7_TIMEOUT_SECONDS": self.context7_timeout,
            "ZHIPU_API_KEY": self._mask_api_key(self.zhipu_api_key) if self.zhipu_api_key else "未配置",
            "ZHIPU_API_URL": self.zhipu_api_url,
            "ZHIPU_SEARCH_ENGINE": self.zhipu_search_engine,
            "ZHIPU_TIMEOUT_SECONDS": self.zhipu_timeout,
            "ZHIPU_MCP_API_KEY": self._mask_api_key(self.zhipu_mcp_api_key) if self.zhipu_mcp_api_key else "未配置",
            "ZHIPU_MCP_SEARCH_API_URL": self.zhipu_mcp_search_api_url,
            "ZHIPU_MCP_READER_API_URL": self.zhipu_mcp_reader_api_url,
            "ZHIPU_MCP_ZREAD_API_URL": self.zhipu_mcp_zread_api_url,
            "ZHIPU_MCP_TIMEOUT_SECONDS": self.zhipu_mcp_timeout,
            "JINA_API_KEY": self._mask_api_key(self.jina_api_key) if self.jina_api_key else "未配置",
            "JINA_READER_API_URL": self.jina_reader_api_url,
            "JINA_RESPOND_WITH": self.jina_respond_with,
            "JINA_TIMEOUT_SECONDS": self.jina_timeout,
            "primary_api_mode": (
                model_routes[0]["provider"]
                if model_routes
                else ("xai-responses" if self.xai_api_key else ("chat-completions" if self.openai_compatible_api_url and self.openai_compatible_api_key else "未配置"))
            ),
            "primary_api_mode_source": "config_file" if explicit_main_configured else "default",
            "config_file": str(self.config_file),
            "config_dir": str(self.config_file.parent),
            "config_dir_source": self.config_dir_source,
            "config_storage_ok": config_path.get("ok", False),
            "config_storage_error": config_path.get("error", ""),
            "default_config_file": str(self._default_config_dir() / "config.json"),
            "legacy_windows_config_file": str(self._legacy_windows_config_dir() / "config.json") if sys.platform.startswith("win") else "",
            "legacy_windows_config_exists": (self._legacy_windows_config_dir() / "config.json").exists() if sys.platform.startswith("win") else False,
            "config_dir_override_value": self._config_dir_override_value(),
            "config_dir_override_matches_default": self._config_dir_override_matches_default(),
            "log_dir_config_value": self.log_dir_config_value,
            "resolved_log_dir": str(self.log_dir),
            "file_logging_enabled": self.debug_enabled or self.log_to_file_enabled,
            "config_sources": self.get_config_sources(),
            "config_parameter_errors": config_parameter_errors,
            "config_status": config_status
        }

config = Config()
