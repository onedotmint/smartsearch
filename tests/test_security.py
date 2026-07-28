from smart_search.logger import logger
from smart_search.security import redact_url_credentials, sanitize_text


def test_redact_url_credentials_masks_userinfo_and_sensitive_query_values():
    """
    /*
     * ================================================================================
     * 步骤1：验证 URL 凭据脱敏规则
     * ================================================================================
     * 目标：确保 userinfo 和敏感查询参数不会出现在展示用 URL 中。
     * 数据源：同时包含用户名、密码、API key 和普通查询参数的 URL。
     * 操作：
     * 1) 直接验证 URL 脱敏 helper 的稳定输出。
     * 2) 验证通用文本清理复用相同规则。
     * ================================================================================
    */
    """
    raw_url = "https://user:password@relay.example/v1?api_key=query-secret&region=cn"
    expected = "https://[REDACTED]@relay.example/v1?api_key=%5BREDACTED%5D&region=cn"

    # 1.1 端点身份保留，所有 URL 内嵌凭据必须替换为统一标记。
    assert redact_url_credentials(raw_url) == expected
    assert sanitize_text(f"request failed: {raw_url}") == f"request failed: {expected}"


def test_redact_url_credentials_fails_closed_for_invalid_urls():
    """
    /*
     * ================================================================================
     * 步骤2：验证异常 URL 的失败关闭
     * ================================================================================
     * 目标：避免无法解析的 URL 把可能存在的凭据原样返回。
     * 数据源：包含 userinfo 且主机格式非法的 URL。
     * 操作：
     * 1) 触发 URL 解析失败。
     * 2) 验证 helper 返回完整脱敏标记而非原始输入。
     * ================================================================================
    */
    """
    raw_url = "https://user:password@[invalid-host"

    # 2.1 解析异常时宁可隐藏整个值，也不能输出部分凭据。
    assert redact_url_credentials(raw_url) == "[REDACTED]"


def test_redact_url_credentials_normalizes_sensitive_query_key_separators():
    """
    /*
     * ================================================================================
     * 步骤3：验证查询参数名归一化
     * ================================================================================
     * 目标：确保敏感参数的大小写和 -/_ 变体都不会绕过 URL 脱敏。
     * 数据源：同一 URL 中的 api-key、access-token 和 client-secret 变体。
     * 操作：
     * 1) 保留非敏感 region 参数。
     * 2) 断言每种敏感参数的原始值均被替换。
     * ================================================================================
    */
    """
    logger.info("步骤3开始：验证查询参数名归一化")
    raw_url = (
        "https://relay.example/v1?api-key=hyphen-secret&API_KEY=upper-secret"
        "&access-token=access-secret&client-secret=client-value-secret&region=cn"
    )

    # 3.1 所有等价敏感参数都必须脱敏，诊断参数保持可读。
    redacted = redact_url_credentials(raw_url)
    for secret in ("hyphen-secret", "upper-secret", "access-secret", "client-value-secret"):
        assert secret not in redacted
    for key in ("api-key", "API_KEY", "access-token", "client-secret"):
        assert f"{key}=%5BREDACTED%5D" in redacted
    assert "region=cn" in redacted
    logger.info("步骤3结束：查询参数名归一化验证完成")


def test_redact_url_credentials_masks_semicolon_query_and_fragment_parameters():
    """
    /*
     * ==============================================================================
     * 步骤4：验证分号查询参数和 URL fragment 脱敏
     * ==============================================================================
     * 目标：堵住 parse_qsl 默认分隔符和 fragment 透传造成的 URL 凭据泄露。
     * 数据源：含非敏感 region、分号分隔 api-key 和 fragment access_token 的 URL。
     * 操作：
     * 1) 直接验证 URL helper 的展示输出。
     * 2) 验证文本清理复用相同的 URL 规则。
     * ==============================================================================
    */
    """
    logger.info("步骤4开始：验证分号查询参数和 URL fragment 脱敏")
    raw_url = (
        "https://relay.example/v1?region=cn;api-key=semicolon-secret"
        "#access_token=fragment-secret;state=ready"
    )

    # 4.1 端点、非敏感查询参数和 fragment state 保持可诊断。
    redacted = redact_url_credentials(raw_url)
    assert "semicolon-secret" not in redacted
    assert "fragment-secret" not in redacted
    assert "region=cn" in redacted
    assert "state=ready" in redacted
    assert "api-key=%5BREDACTED%5D" in redacted
    assert "access_token=%5BREDACTED%5D" in redacted
    assert "semicolon-secret" not in sanitize_text(f"request failed: {raw_url}")
    assert "fragment-secret" not in sanitize_text(f"request failed: {raw_url}")
    logger.info("步骤4结束：分号查询参数和 URL fragment 脱敏验证完成")
