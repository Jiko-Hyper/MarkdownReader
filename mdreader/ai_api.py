"""Versioned, explicitly enabled tools for local AI clients (no model dependency)."""
import re
from datetime import datetime

from . import documents as D
from .storage import safe_join

PREFIX = "/api/ai/v1/"
MAX_TEXT_BYTES = 8 * 1024 * 1024


def _tool(name, description, properties=None, required=(), write=False):
    return {"name": name, "description": description, "write": write,
            "inputSchema": {"type": "object", "properties": properties or {},
                            "required": list(required), "additionalProperties": False}}


STRING = {"type": "string"}
NONEMPTY = {"type": "string", "minLength": 1}
DOC = {"pid": NONEMPTY, "doc": NONEMPTY}
TOOLS = [
    _tool("list_projects", "列出软件中已登记的项目。"),
    _tool("list_documents", "列出项目中的文档和目录。", {"pid": NONEMPTY}, ("pid",)),
    _tool("search_documents", "搜索项目文档，返回文件名、路径和摘要，最多 60 条。",
          {"pid": NONEMPTY, "query": NONEMPTY}, ("pid", "query")),
    _tool("read_document", "读取磁盘上的文档及 revision；不包含界面未保存内容。", DOC, ("pid", "doc")),
    _tool("create_document", "新建文档；重名时自动编号，返回实际文档 ID。",
          {"pid": NONEMPTY, "name": NONEMPTY, "directory": STRING, "content": STRING},
          ("pid", "name", "content"), True),
    _tool("save_document", "用完整内容保存文档，必须携带读取时的 revision；冲突返回 HTTP 409。",
          {**DOC, "content": STRING, "expected": {"type": "string", "pattern": "^[0-9a-f]{64}$"}},
          ("pid", "doc", "content", "expected"), True),
    _tool("replace_text", "精确替换唯一匹配的文本；未匹配或多处匹配均拒绝。必须携带 revision。",
          {**DOC, "old_text": NONEMPTY, "new_text": STRING,
           "expected": {"type": "string", "pattern": "^[0-9a-f]{64}$"}},
          ("pid", "doc", "old_text", "new_text", "expected"), True),
]


class AiApi:
    def __init__(self, ws, token, writable=False):
        if not isinstance(token, str) or len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
            raise ValueError("MDREADER_AI_TOKEN 必须是至少 32 位、不含空白的 ASCII 密钥")
        self.ws, self.token, self.writable = ws, token, writable
        self.last_access = ''

    def get(self, path):
        if path != PREFIX + "tools":
            raise KeyError("未知 AI 接口")
        self.last_access = datetime.now().strftime('%H:%M:%S')
        return {"ok": True, "api_version": "1", "writable": self.writable,
                "tools": [t for t in TOOLS if self.writable or not t["write"]]}

    def call(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"name", "arguments"}:
            raise ValueError("请求必须包含 name 和 arguments")
        spec = next((t for t in TOOLS if t["name"] == payload["name"]), None)
        if spec is None:
            raise KeyError("未知 AI 工具")
        if spec["write"] and not self.writable:
            raise PermissionError("AI 接口当前只读；请在软件的「AI 接入」中开启「允许 AI 修改文档」")
        args, schema = payload["arguments"], spec["inputSchema"]
        if not isinstance(args, dict) or set(args) - set(schema["properties"]):
            raise ValueError("工具参数必须是对象，且不能包含未声明字段")
        if set(schema["required"]) - set(args):
            raise ValueError("缺少必填参数")
        for key, value in args.items():
            rule = schema["properties"][key]
            if not isinstance(value, str) or len(value) < rule.get("minLength", 0):
                raise ValueError("参数 %s 必须是有效字符串" % key)
            if len(value.encode("utf-8")) > MAX_TEXT_BYTES or "\x00" in value:
                raise ValueError("参数 %s 过大或包含二进制内容" % key)
            if "pattern" in rule and not re.fullmatch(rule["pattern"], value):
                raise ValueError("expected 必须是读取文档时返回的 revision")
        result = self._execute(spec["name"], args)
        self.last_access = datetime.now().strftime('%H:%M:%S')
        return {"ok": True, "result": result}

    def _execute(self, name, args):
        ws = self.ws
        if name == "list_projects":
            return {"projects": ws.list_projects()}
        pdir = ws.require_project(args["pid"])
        if name == "list_documents":
            return {"documents": [{k: v for k, v in d.items() if k != "_abs"}
                                  for d in ws.scan_docs(pdir)], "directories": ws.list_dirs(pdir)}
        if name == "search_documents":
            return {"hits": ws.search(pdir, args["query"])}
        if name == "create_document":
            info = ws.create_doc(pdir, args["name"], args.get("directory", ""),
                                 args["content"], use_template=False)
            return {k: v for k, v in info.items() if k != "_abs"}
        did = args["doc"]
        if not did.lower().endswith((".md", ".markdown", ".mdown", ".mkd", ".txt")):
            raise ValueError("AI 接口仅支持 Markdown 和文本文件")
        full = safe_join(pdir, did)
        # One snapshot binds the returned text to its revision. The shared write lock
        # serializes API writers; external programs retain the usual optimistic checks.
        with D._write_lock:
            if name == "read_document":
                snap = D.snapshot(full)
                return {"pid": args["pid"], "doc": did, "content": snap["text"],
                        "revision": snap["revision"], "encoding": snap["encoding"]}
            content = args.get("content")
            if name == "replace_text":
                if D.revision(full) != args["expected"]:
                    raise D.ConflictError(full, D.revision(full))
                snap = D.snapshot(full)
                if snap["text"].count(args["old_text"]) != 1:
                    raise ValueError("old_text 必须在文档中恰好匹配一次")
                content = snap["text"].replace(args["old_text"], args["new_text"], 1)
            if len(content.encode("utf-8")) > MAX_TEXT_BYTES:
                raise ValueError("修改后的文档超过 8 MB")
            info = ws.save_doc(pdir, did, content, args["expected"])
            return {k: v for k, v in info.items() if k != "_abs"}
