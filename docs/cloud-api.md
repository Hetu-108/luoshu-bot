# 本地模型与云端 API

本地 Ollama 与可选云端 API 共存，默认继续使用本地模型。
云端接口采用 [OpenAI Chat Completions 协议](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)：
`POST <Base URL>/chat/completions`、Bearer API Key、`messages` 输入和 `choices[].message.content` 输出。
仅兼容该协议的服务可直接接入；原生 Responses 或 Anthropic Messages 接口需要其他适配器。

## 配置

在实际使用的 `.env` 文件中设置（占位值须替换）：

```dotenv
AI_DEFAULT_BACKEND=ollama
CLOUD_ENABLED=true
CLOUD_BASE_URL=https://api.provider.example/v1
CLOUD_API_KEY="填写服务商APIKey"
CLOUD_MODEL=填写服务商的模型名
CLOUD_VISION_ENABLED=false
CLOUD_TIMEOUT=90
CLOUD_MAX_TOKENS=1024
CLOUD_TOKEN_PARAMETER=max_tokens
CLOUD_MAX_CONCURRENT=2
```

Base URL 包含服务商要求的 `/v1` 等路径前缀，不要再填 `/chat/completions`，也不接受带鉴权信息或查询参数的URL。
云端地址和Key仅由服务器配置，不提供QQ命令录入，也不写入会话JSON、帮助菜单和错误回复。
HTTP错误仅返回状态与通用说明，不回显服务商可能包含Key或用户输入的响应正文。
推荐使用HTTPS；本机兼容网关或模拟测试也可使用HTTP地址。

`CLOUD_TOKEN_PARAMETER` 可设为 `max_completion_tokens`，适配不接受 `max_tokens` 的模型。
不同服务商的模型、输出参数和多模态能力需要按其接口实测；没有指定默认付费服务商或模型。
云端不发送 Ollama 专用的 `num_ctx`、`num_predict`、`think` 参数。

Jiangnan 部署的配置文件是 `/home/jiangnan/apps/luoshu-bot/.env`，`repo/.env` 为它的链接。
NoneBot 自定义配置项应实际出现在这个文件中，修改后执行 `sudo systemctl restart luoshu-bot`。
管理员 `SUPERUSERS` 也需填入实际QQ号，才能通过QQ命令切换后端和模型。

## 使用

| 命令 | 行为 |
|---|---|
| `/后端`（别名 `/backend`、`/模型后端`） | 任何人查看当前会话的后端及模型 |
| `/后端 云端` | 机器人管理员将自己的当前会话切到已配置云端 |
| `/后端 本地` | 机器人管理员将自己的当前会话切回Ollama |
| `/model 模型名` | 机器人管理员修改当前后端的模型 |
| `/history` | 查看人设、后端、模型、已记录轮数 |

命令沿用按「用户+群」或私聊用户隔离的会话，不会改变同群其他成员。
本地与云端各自保留模型选择。切换后端会清空当前会话历史，并使正在生成或排队的旧请求失效。
切到云端后，新对话、云端会话中的历史、当前人设提示及本轮附图会发送至配置的服务商。
供应商的数据保存政策和费用由其账号与条款决定。

`AI_DEFAULT_BACKEND=cloud` 可使**新会话**默认走云端；旧会话缺少后端字段时仍按本地迁移，
须显式切换，避免旧本地聊天被带到云端。现有会话执行 `/reset` 只清空历史，不改后端。
模型故障不会自动切换服务商，也不自动重发云端请求；下一条请求可以重试。

## 识图

`/识图`、回复图片、群内@附图和私聊直接发图都使用当前会话后端。
云端模型确认支持图片后设置 `CLOUD_VISION_ENABLED=true`。
程序将本轮已校验的图片转换为 `image_url` 内容块（带正确MIME的base64 data URL），
不会把Ollama的 `images` 字段原样发给云端。仍限制3张、每张8MB，图片不写入持久聊天历史。
未启用云端识图时会提示，不会自动把请求切到其他服务。

答题插件的出题、判分、截止、排行、导出不依赖这个后端，继续使用确定性规则。

## 验证边界

`python -m pytest -q` 包含请求契约、图片转换、权限、旧会话迁移、切换失效、错误脱敏与失败后恢复测试。
`PROBE_CLOUD=1 python scripts/verify_onebot.py . onebot-probe.json` 使用真实NoneBot进程、模拟OneBot和
本机HTTP云端桩验证切换与聊天链路，不调用付费服务。Windows PowerShell先设置 `$env:PROBE_CLOUD='1'`。
未配置真实服务商和Key时，不能声称已完成真实云端调用、余额/权限或视觉模型准确率验收。
