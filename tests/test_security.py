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
