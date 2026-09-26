# 外部 AI 控制接口（高级，v1）

如果你要填写 GPT、Claude、DeepSeek 的 API Key，在软件中直接与模型对话，请使用顶部「AI 接入」，详见 [模型接入](AI_MODELS.md)。本文描述给外部程序调用的软件控制接口，是独立的高级功能。

外部 AI 程序可通过本机 HTTP 接口获取项目上下文、搜索文档、编写开发说明和修改文档。
接口不绑定模型厂商，不主动把内容发送到网络，也不需要安装额外依赖。
本版本面向软件内的 Markdown / 文本文档，不提供终端执行、任意源码工程操作或内置聊天。

## 三步接入（无需命令行）

1. 正常打开软件，在桌面端点击 **更多 → 外部 AI 控制接口（高级）…**。
2. 打开 **开启 AI 接入**。软件会自动生成密钥、显示连接地址；需要 AI 编辑时，勾选 **允许 AI 修改文档**。
3. 点击 **复制接入信息**，粘贴给能调用本机 HTTP 接口的 AI 助手。助手按其中的地址和工具定义调用即可。

不需要 PowerShell、环境变量、手工选择端口或手动生成密钥。
普通网页聊天不能直接访问本机接口，需要支持本机工具调用的 AI 助手。

### 面板上的其他操作

- **检测连接**：真实请求工具列表，确认本机服务和密钥可用；这不代表某个外部 AI 已完成配置。
- **连接状态**：显示已关闭、已开启及最近成功访问时间（包括面板自身的检测）。
- **复制地址 / 密钥**：用于需要分项填写连接设置的 AI 工具；密钥默认遮蔽显示。
- **更换密钥**：立即使旧密钥失效，随后重新复制接入信息。
- **关闭接入**：立即拒绝后续请求，并清除已保存的密钥；再次开启会生成新密钥。

设置自动保存在当前工作区的 `ai-connection.json`，下次正常启动自动恢复，无需重复配置。
该文件含接入密钥，不应分享或提交到代码仓库。界面修改以保存成功为准；写入失败时保留原设置。
浏览器不允许自动复制时，面板会显示并选中内容，按 Ctrl+C 即可。

服务仅监听本机，退出软件后停止。之前的命令行入口仍兼容开发脚本，普通用户使用上面的面板即可。
旧的已安装副本需要更新程序文件后，才会出现新入口。

## 协议

所有请求均携带 `Authorization: Bearer <密钥>`。POST 使用 `Content-Type: application/json`。
AI 密钥与内部页面的会话凭据相互独立，不能用 AI 密钥调用普通 `/api/*` 接口。

`GET /api/ai/v1/tools` 返回当前允许的工具及 JSON Schema：

```json
{"ok": true, "api_version": "1", "writable": false, "tools": []}
```

上例省略工具列表内容；实际列表包含 `name`、`description`、`write`、`inputSchema`。
将这些定义转换成所用模型 SDK 的工具格式，模型提出工具调用后，由本机宿主程序代为执行：

`POST /api/ai/v1/tools/call`

```json
{"name": "read_document", "arguments": {"pid": "项目ID", "doc": "docs/设计.md"}}
```

成功返回 `{"ok": true, "result": ...}`，失败返回 `{"ok": false, "error": "原因"}`。

| 工具 | 参数 | 结果 |
| --- | --- | --- |
| `list_projects` | 无 | `projects` 列表 |
| `list_documents` | `pid` | `documents`、`directories` |
| `search_documents` | `pid`、`query` | `hits`，最多 60 条 |
| `read_document` | `pid`、`doc` | `content`、`revision`、`encoding` |
| `create_document` | `pid`、`name`、`content`；可选 `directory` | 新文档信息，包括 `id`、`revision` |
| `save_document` | `pid`、`doc`、`content`、`expected` | 保存后的文档信息 |
| `replace_text` | `pid`、`doc`、`old_text`、`new_text`、`expected` | 修改后的文档信息 |

`pid` 从项目列表获取，`doc` 从文档列表的 `id` 获取。所有参数都是字符串；未知字段会被拒绝。
项目范围沿用软件已登记的项目，包括用户在其他位置创建的项目；不自动授权任意本地路径。

## Python 调用示例

开发者可使用标准库客户端 `tools/ai_client.py`。地址和密钥直接从接入面板复制。
应用集成示例（开发代码，普通用户无需操作）：

```python
from tools.ai_client import MDReaderClient

client = MDReaderClient(base_url="http://127.0.0.1:8642", token="从面板复制的密钥")
# base_url 使用面板地址中的 http://127.0.0.1:端口 部分。
tools = client.tools()    # 交给 AI 宿主转换为模型工具定义
projects = client.call("list_projects")["projects"]
# 用户选择目标项目后，把对应 id 赋给 pid。
pid = projects[0]["id"]
created = client.call("create_document", pid=pid, name="开发计划", content="# 开发计划\n\n待完善\n")
doc = client.call("read_document", pid=pid, doc=created["id"])
updated = client.call("replace_text", pid=pid, doc=created["id"],
                      old_text="待完善", new_text="1. 明确需求\n2. 实现接口\n3. 验证结果",
                      expected=doc["revision"])
```

模型工具调用可转发为 `client.call(tool_name, **arguments)`。本接口是普通 HTTP 工具协议，
不是 MCP 服务；云端模型需要本机运行的宿主程序代理调用，无法直接连接用户的回环地址。

## 保存和错误处理

读取返回的文本与 `revision` 来自同一份磁盘快照。每次修改都必须传入它作为 `expected`，
不得把最新版本号盲目补进旧修改再重试。冲突后重新读取、重新生成修改，并交由宿主按用户意图处理。

- 400：参数无效、路径越界、只读模式写入，或替换文字不是唯一匹配。
- 403：密钥无效，或 Host / Origin 被拒绝。
- 404：未启用、接口 / 工具未知、项目 / 文件不存在。
- 409：文档已被更改或删除；另返回 `conflict: true` 和当前 `revision`，原文保留。
- 413：HTTP 请求超过 24 MB；单个文本参数与修改结果另限制为 8 MB（UTF-8 字节）。

新建同名文件会自动编号，不覆盖已有文件；空内容可新建或保存为空文档。
保存沿用软件的编码、换行、原子写入和冲突检查。不提供绕过版本检查、删除文件或执行程序的工具。

接口读写**磁盘已保存内容**，不读取桌面或浏览器中的未保存编辑，也不会自动替换打开标签的内容。
在软件中重新加载文档即可查看 AI 修改。若用户尚有未保存编辑，后续保存会触发现有冲突提示。
本服务内的文档写入按共享锁串行处理；外部编辑器在最终检查与替换间写入的竞态仍是既有实现的限制。
