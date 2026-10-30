# SignPath 免费代码签名：配置与申请

SignPath Foundation 给开源项目免费签名：证书和私钥在他们的 HSM 里，不经过你的机器，
也不要求你提供身份证——它担保的是"这个二进制确实由这个公开仓库构建"（<https://signpath.org/>）。

## 现在仓库里已经就绪的部分

| 文件 | 作用 |
|---|---|
| `.signpath/artifact-configurations/windows-installer.xml` | 声明签名范围：zip 里的 `*-setup.exe` |
| `.github/actions/build-windows-installer/action.yml` | 构建完安装包后，若配置了 SignPath 变量就提交签名请求、替换成签名版并校验 `Get-AuthenticodeSignature` |
| `.github/workflows/release-windows.yml` | 使用上表中的 composite action；未配置 SignPath 时行为不变（产物不签名） |

工作流读的是这些仓库变量与密钥：

```
vars.SIGNPATH_ORGANIZATION_ID      SignPath 组织 ID（UUID）
vars.SIGNPATH_PROJECT_SLUG         项目 slug，例如 mediaforge
vars.SIGNPATH_SIGNING_POLICY_SLUG  签名策略 slug，例如 release-signing
vars.SIGNPATH_CONNECTOR_URL        Pipeline Connector 的 GitHub Actions endpoint（官方 action 必填）
secrets.SIGNPATH_API_TOKEN         在 SignPath 网页生成的 API token
```

`SIGNPATH_CONNECTOR_URL` 容易漏：SignPath 的 action 把它列为必填输入，但要在
SignPath 项目里**先创建一个 GitHub Actions connector** 才会给出这个地址。

**没有配置时不会失败**：安装包照常构建，只是没有签名（日志里不会有签名步骤）。

## 只有项目所有者能做的步骤

1. **申请**：打开 <https://signpath.org/apply>，表单内容见 `docs/signpath-application.md`
2. **接受条款**：SignPath 的服务条款是法律协议，只能由你本人接受
3. **创建项目**：审批通过后，在 SignPath 里建 project（slug 建议 `mediaforge`）、
   signing policy（建议 `release-signing`，允许从 `main` 分支的构建签名）、
   并上传/关联 artifact configuration（对应本仓库 `.signpath/` 下的 XML）
4. **授权 GitHub**：安装 SignPath 的 GitHub App 并授权本仓库（只读代码 + 写 artifact）
5. **生成 API token** 并填进仓库 Secrets
6. **把 org id / 两个 slug 填进仓库 Variables**

做完之后，下一次 `release-windows.yml` 的产物就是签过名的；用户看到的发布者会是
SignPath Foundation（代替现在的"未知发布者"）。

## 第一次跑需要验证什么

签名步骤依赖 SignPath 的 action 输入名（`api-token`、`organization-id`、
`artifact-configuration-slug` 等）。这些是按其文档写的，但**第一次真实运行前无法验证**。
如果报"未知输入"，按 SignPath 当时的最新文档改 `action.yml` 里那一段即可，
不影响其他步骤。

验证成功的标志：

```
Authenticode: Valid CN=SignPath Foundation, ...
```

（`Get-AuthenticodeSignature` 的 Status 必须是 Valid，工作流里对这点做了断言。）

## 不在签名范围内的东西

| 对象 | 原因 |
|---|---|
| 便携包里的 `MediaForge.exe` | 目前只签安装包；要签它需要再加一个 artifact configuration 和一次签名请求 |
| 运行时资产 `runtime-cuda-*.zip` | 不是可执行文件，用我们自己的 minisign `.sig`（更新通道） |
| 模型权重 | 第三方分发，各自许可，不做 SignPath 签名 |

## 签名与"安全"的关系

签名证明**来源**，不证明**安全**，也不是运行时的技术前提。它解决的是三件事：
用户在 SmartScreen 里不再看到"未知发布者"、企业策略（WDAC/AppLocker）能放行、
以及"这个包确实来自该仓库"可被第三方核验。
