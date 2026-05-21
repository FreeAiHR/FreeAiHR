# Security Policy

## Supported Versions

我们只对**最新一个 release** 与 **`main` 分支**提供安全修复。较早版本默认不做 backport，请升级到最新版。

| 版本 | 支持状态 |
|---|---|
| `main` (HEAD)  | ✅ 接收安全修复 |
| 最新 tag 发布版 | ✅ 接收安全修复 |
| 历史版本       | ❌ 请升级 |

## Reporting a Vulnerability

**请不要通过公开 GitHub Issue 报告安全漏洞。**

请通过以下任一渠道**私下披露**：

- 安全邮箱：如仓库主页未提供专用地址，请优先使用 GitHub 私密披露渠道
- GitHub 私密披露：仓库 -> Security -> Report a vulnerability

报告时建议包含：
- 受影响版本(commit hash 或 release tag)
- 复现步骤(最小可复现示例最佳)
- 评估的影响范围(数据泄漏 / RCE / 权限提升 / DoS)
- 建议的修复方向(可选)

## Response Timeline

| 节点 | 承诺 |
|---|---|
| 初步响应 | 收到报告后 **72 小时**内确认 |
| 评估反馈 | **7 天**内反馈是否接受 / 是否被认定为漏洞 / 严重等级 |
| 修复发布 | 严重漏洞 **30 天**内、中低风险 **90 天**内 |
| 公开披露 | 修复发布 **后** 14 天内，视情况致谢报告者 |

如果漏洞已经被利用，或影响范围较大，我们可能缩短披露窗口并优先发布修复。

## Scope

**在范围内**:
- 服务端 RCE / SSRF / SQL 注入 / 路径穿越
- 鉴权绕过 / 越权 / JWT 伪造 / Session 劫持
- 简历 / 候选人数据泄漏(横向越权读其他租户数据)
- License 签名校验绕过(尽管前面分析过这不是商业风险,但仍是 bug)
- LLM API key 泄漏

**不在范围内**:
- 默认开发环境中的弱密码(`admin@example.com / admin123456`)。生产部署前应改为自定义凭据
- 自托管时未启用 HTTPS 或未正确配置 `JWT_SECRET`
- 拒绝服务(DoS)需要消耗大量算力 / 带宽的场景
- 由于部署方自行配置不当导致的后台公网暴露

## Acknowledgements

我们感谢负责任披露的安全研究者。已修复的漏洞和报告者会在后续发布说明中按需致谢。

## Contact us
联系邮箱：lihaiya@gousandian.com

