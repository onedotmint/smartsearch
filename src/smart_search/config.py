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
        "Run `smart-search setup --format json` to configure BRAVE_API_KEY, "
        "EXA_API_KEY, and/or TAVILY_API_KEY, optionally JINA_API_KEY, or provide "
        "provider keys through the environment for CI. Setup selections control "
        "which discovery providers are enabled. Then use `smart-search search ... --format json`."
    )
    _DEFAULT_VALIDATION_LEVEL = "balanced"
    _DEFAULT_FALLBACK_MODE = "auto"
    _DEFAULT_MINIMUM_PROFILE = "standard"
    _DEFAULT_INTENT_ROUTER_MODE = "hybrid"
    _DEFAULT_RETRIEVAL_MODE = "balanced"
    _DEFAULT_INTENT_ROUTER_TIMEOUT_SECONDS = "8"
    _DEFAULT_INTENT_EMBEDDING_THRESHOLD = "0.74"
    _DEFAULT_INTENT_EMBEDDING_MARGIN = "0.05"
    _DEFAULT_CACHE_ENABLED = "false"
    _DEFAULT_SEARCH_CACHE_TTL_SECONDS = "30"
    _DEFAULT_FETCH_CACHE_TTL_SECONDS = "300"
    _DEFAULT_CACHE_MAX_SIZE = "256"
    _CACHE_TTL_BOUNDS = (1, 604800)
    _CACHE_MAX_SIZE_BOUNDS = (1, 10000)
    _CONFIG_DIR_MODE = 0o700
    _CONFIG_FILE_MODE = 0o600
    _ALLOWED_VALIDATION_LEVELS = {"fast", "balanced", "strict"}
    _ALLOWED_FALLBACK_MODES = {"auto", "off"}
    _ALLOWED_MINIMUM_PROFILES = {"lite", "standard", "full", "off"}
    _ALLOWED_INTENT_ROUTER_MODES = {"hybrid", "rules", "off"}
    _ALLOWED_RETRIEVAL_MODES = {"fast", "balanced", "research"}
    _CONFIG_KEYS = {
        "SMART_SEARCH_VALIDATION_LEVEL",
        "SMART_SEARCH_FALLBACK_MODE",
        "SMART_SEARCH_MINIMUM_PROFILE",
        "SMART_SEARCH_RESEARCH_PREFERRED_PROVIDERS",
        "SMART_SEARCH_RESEARCH_DISABLED_PROVIDERS",
        "SMART_SEARCH_INTENT_ROUTER",
        "SMART_SEARCH_DEFAULT_MODE",
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
        "EXA_ENABLED",
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
        "BRAVE_API_KEY",
        "BRAVE_API_URL",
        "BRAVE_ENABLED",
        "BRAVE_TIMEOUT_SECONDS",
        "JINA_RERANK_API_URL",
        "JINA_RERANK_MODEL",
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
    }

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._config_file = None
            cls._instance._config_dir_source = None
            cls._instance._config_snapshot = None
            cls._instance._credential_state_digest = None
            cls._instance._credential_epoch = 0
            cls._instance._load_error = None
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
                self._load_error = None
                return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            self._load_error = None
            return {}
        except json.JSONDecodeError as exc:
            self._load_error = {"kind": "json_decode", "path": str(self.config_file), "detail": str(exc)}
            return {}
        except OSError as exc:
            self._load_error = {"kind": "io", "path": str(self.config_file), "detail": str(exc)}
            return {}

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
            return default
        return str(value)

    def get_saved_config(self, masked: bool = True) -> dict:
        data = self._get_config_snapshot().file_values
        normalized: dict[str, Any] = {}
        for key, value in data.items():
            if key in self._CONFIG_KEYS and value is not None:
                normalized[key] = str(value)
        if not masked:
            return normalized
        masked_config: dict[str, Any] = {}
        for key, value in normalized.items():
            if isinstance(value, str):
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
                sources[key] = "default"
        return sources

    def config_load_error(self) -> dict | None:
        """Return the typed config-file load error fact, or None if the file loaded cleanly."""
        self._get_config_snapshot()
        return self._load_error

    @staticmethod
    def _reject_env_owned_key(key: str, snapshot: ConfigSnapshot) -> None:
        if snapshot.environment_values.get(key) is not None:
            raise ValueError(f"{key} is controlled by the environment and cannot be edited locally.")

    def _reject_malformed_write(self) -> None:
        if self._load_error is not None:
            kind = self._load_error.get("kind") or "unknown"
            raise ConfigStorageError(
                f"config file is malformed ({kind}); refusing to overwrite it. "
                "Inspect and repair the file, then retry."
            )

    def set_config_values(self, values: Mapping[str, object]) -> None:
        """Atomically persist several local settings while preserving other keys."""
        if not isinstance(values, Mapping):
            raise ValueError("config values must be an object")
        normalized_values = {str(key).strip().upper(): value for key, value in values.items()}
        unknown = next((key for key in normalized_values if key not in self._CONFIG_KEYS), None)
        if unknown:
            raise ValueError(f"Unsupported config key: {unknown}")
        snapshot = self._get_config_snapshot()
        for key in normalized_values:
            self._reject_env_owned_key(key, snapshot)
        self._reject_malformed_write()
        config_data = dict(snapshot.file_values)
        for key, value in normalized_values.items():
            if key == "SMART_SEARCH_DEFAULT_MODE":
                mode = str(value or "").strip().lower()
                if mode not in self._ALLOWED_RETRIEVAL_MODES:
                    allowed = ", ".join(sorted(self._ALLOWED_RETRIEVAL_MODES))
                    raise ValueError(f"Invalid SMART_SEARCH_DEFAULT_MODE: {mode}. Supported values: {allowed}")
                value = mode

            config_data[key] = value
        self._save_config_file(config_data)


    def set_config_value(self, key: str, value: object) -> None:
        key = key.strip().upper()
        if key not in self._CONFIG_KEYS:
            raise ValueError(f"Unsupported config key: {key}")
        snapshot = self._get_config_snapshot()
        self._reject_env_owned_key(key, snapshot)
        self._reject_malformed_write()
        config_data = dict(snapshot.file_values)
        if key == "SMART_SEARCH_DEFAULT_MODE":
            mode = str(value or "").strip().lower()
            if mode not in self._ALLOWED_RETRIEVAL_MODES:
                allowed = ", ".join(sorted(self._ALLOWED_RETRIEVAL_MODES))
                raise ValueError(f"Invalid SMART_SEARCH_DEFAULT_MODE: {mode}. Supported values: {allowed}")
            value = mode
        config_data[key] = value
        self._save_config_file(config_data)


    def unset_config_value(self, key: str) -> None:
        key = key.strip().upper()
        if key not in self._CONFIG_KEYS:
            raise ValueError(f"Unsupported config key: {key}")
        snapshot = self._get_config_snapshot()
        self._reject_env_owned_key(key, snapshot)
        self._reject_malformed_write()
        config_data = dict(snapshot.file_values)
        config_data.pop(key, None)
        self._save_config_file(config_data)


    def config_path_info(self) -> dict:
        self._get_config_snapshot()
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
            "config_load_error": self._load_error,
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
    def default_mode(self) -> str:
        return self._validated_enum(
            "SMART_SEARCH_DEFAULT_MODE",
            self._DEFAULT_RETRIEVAL_MODE,
            self._ALLOWED_RETRIEVAL_MODES,
        )

    @property
    def default_retrieval_mode(self) -> str:
        """Explicit alias for callers selecting the stored search mode."""
        return self.default_mode

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
                "brave": ("BRAVE_API_URL", "BRAVE_ENABLED", "BRAVE_TIMEOUT_SECONDS"),
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
    def brave_enabled(self) -> bool:
        return (self._get_config_value("BRAVE_ENABLED", "true") or "true").lower() in ("true", "1", "yes")

    @property
    def brave_api_url(self) -> str:
        return (
            self._get_config_value("BRAVE_API_URL", "https://api.search.brave.com/res/v1")
            or "https://api.search.brave.com/res/v1"
        )

    @property
    def brave_api_key(self) -> str | None:
        return self._get_config_value("BRAVE_API_KEY")

    @property
    def brave_timeout(self) -> float:
        return float(self._get_config_value("BRAVE_TIMEOUT_SECONDS", "30") or "30")

    @property
    def jina_rerank_api_url(self) -> str:
        return (
            self._get_config_value("JINA_RERANK_API_URL", "https://api.jina.ai/v1/rerank")
            or "https://api.jina.ai/v1/rerank"
        )

    @property
    def jina_rerank_model(self) -> str:
        return self._get_config_value("JINA_RERANK_MODEL", "jina-reranker-v2-base-multilingual") or ""

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
    def exa_enabled(self) -> bool:
        return self._bool_value("EXA_ENABLED", "true")

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
        """Return active configuration values and storage diagnostics."""
        logger.info("开始聚合配置诊断状态")
        config_parameter_errors: list[str] = []
        config_path = self.config_path_info()
        explicit_main_configured = bool(self.brave_api_key or self.exa_api_key or self.tavily_api_key)
        if explicit_main_configured:
            config_status = "ok: 配置完整"
        else:
            config_status = f"config_error: {self._SETUP_COMMAND}"

        default_mode, default_mode_error = self._enum_info(
            "SMART_SEARCH_DEFAULT_MODE",
            self._DEFAULT_RETRIEVAL_MODE,
            self._ALLOWED_RETRIEVAL_MODES,
        )
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
                default_mode_error,
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
            )
            if error
        )
        if config_parameter_errors and config_status.startswith("ok:"):
            config_status = f"config_error: {'; '.join(config_parameter_errors)}"
        if not config_path.get("ok", False):
            config_status = f"config_error: {config_path.get('error', self._config_storage_hint())}"
        if config_path.get("config_load_error") is not None:
            config_status = (
                "config_error: config file is malformed "
                f"({config_path['config_load_error'].get('kind')}); repair the file"
            )

        logger.info("配置诊断状态聚合完成")
        return {
            "SMART_SEARCH_VALIDATION_LEVEL": validation_level,
            "SMART_SEARCH_DEFAULT_MODE": default_mode,
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
            "BRAVE_API_URL": self.brave_api_url,
            "BRAVE_ENABLED": self.brave_enabled,
            "BRAVE_API_KEY": self._mask_api_key(self.brave_api_key) if self.brave_api_key else "未配置",
            "BRAVE_TIMEOUT_SECONDS": self.brave_timeout,
            "JINA_RERANK_API_URL": self.jina_rerank_api_url,
            "JINA_RERANK_MODEL": self.jina_rerank_model,
            "FIRECRAWL_API_URL": self.firecrawl_api_url,
            "FIRECRAWL_API_KEY": self._mask_api_key(self.firecrawl_api_key) if self.firecrawl_api_key else "未配置",
            "ANYSEARCH_API_URL": self.anysearch_api_url,
            "ANYSEARCH_API_KEY": self._mask_api_key(self.anysearch_api_key) if self.anysearch_api_key else "未配置",
            "ANYSEARCH_TIMEOUT_SECONDS": self.anysearch_timeout,
            "SMART_SEARCH_OUTPUT_CLEANUP": self.output_cleanup_enabled,
            "SMART_SEARCH_LOG_TO_FILE": self.log_to_file_enabled,
            "SSL_VERIFY": self.ssl_verify_enabled,
            "EXA_API_KEY": self._mask_api_key(self.exa_api_key) if self.exa_api_key else "未配置",
            "EXA_ENABLED": self.exa_enabled,
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
            "config_load_error": config_path.get("config_load_error"),
            "config_parameter_errors": config_parameter_errors,
            "config_status": config_status
        }

config = Config()
