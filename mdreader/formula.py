# -*- coding: utf-8 -*-
"""数学公式：TeX 子集 → 版面盒子 → 图片（离线、不引入第三方库）。

为什么要自己画（F05 的小样验证结论，2026-09-26）：

* **两端要的其实都是图片**。桌面预览是 Tk 的 ``Text`` 控件（`image_create` 只能放
  ``PhotoImage``），网页与自包含 HTML 导出要能在断网时显示，PDF/DOCX 插件也要把
  公式嵌进文档——四条路都通向同一张位图。KaTeX 的产物是 HTML，只能在浏览器里用，
  桌面端仍然得另做一套；两套渲染器意味着两套结果要分别验证。
* **离线与分发**。KaTeX 的样式、脚本和字体加起来约 1.5 MB，HTML 导出要自包含就得
  把这些一起塞进去；GDI 是 Windows 自带的，程序体积不变。本程序只面向 Windows。
* **代价写在明处**：本模块只承诺任务书列出的那批语法（分数、上下标、根号、希腊
  字母、求和、积分、矩阵、常用对齐），**不是完整 LaTeX**；不支持的一律报错并原样
  显示源码，不猜。KaTeX 的排版质量（可变长括号、数学间距、断行）比这里好，这一点
  在交付记录里如实写明。

对外只有四个入口：:func:`scan`（在 Markdown 里找公式）、:func:`to_png`（渲染一张）、
:func:`cache_key` / :func:`cached`（按“表达式 + 字号 + 缩放 + 主题 + 渲染器版本”缓存）。

本模块不在导入时创建任何 GDI 资源；第一次真正渲染时才建立隐藏 DC（并用锁保护，
因为 HTTP 服务是多线程的）。
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import re
import struct
import threading
import zlib
from ctypes import wintypes

RENDERER_VERSION = "1"

#: 公式基础字号（CSS 像素）与跟随正文缩放的上下限。
DEFAULT_SIZE = 16
MIN_SIZE = 8
MAX_SIZE = 96
MAX_EXPRESSIONS = 400
MAX_TEX_CHARS = 2000
MAX_IMAGE_PX = 4000
MATH_FONT = "Cambria Math"
TEXT_FONT = "Microsoft YaHei UI"
TEXT_FONT_FALLBACK = "Microsoft YaHei"
MONO_FONT = "Consolas"
SYMBOL_FONT = "Segoe UI Symbol"

#: 三种阅读主题下公式的前景/背景。取值与 `winui.THEMES` 的 ``fg``/``bg`` 一致，
#: 这样公式图片贴在正文里不会出现一块颜色不同的方块。主题也进缓存键。
THEME_COLORS = {
    "light": ("#1f2328", "#ffffff"),
    "dark": ("#dce1e8", "#22262d"),
    "eye": ("#394538", "#f3efdf"),
    # 打印/导出用的浅色版式：与阅读主题无关（任务书 §8.1）。
    "print": ("#1f2328", "#ffffff"),
}
DEFAULT_THEME = "light"


def colors_for(theme: str):
    """主题 → ``(前景色, 背景色)``；不认识的主题按浅色处理。"""
    return THEME_COLORS.get((theme or DEFAULT_THEME).lower(), THEME_COLORS[DEFAULT_THEME])

#: 数学排版里的三档字号，按 LaTeX 的习惯取值。
SCRIPT_SCALE = 0.7
SCRIPTSCRIPT_SCALE = 0.55

_ALIGN_ENVS = ("aligned", "align", "gathered")
_MATRIX_ENVS = {"matrix": "none", "pmatrix": "()", "bmatrix": "[]",
                "vmatrix": "||", "cases": "{"}

#: 单个控制序列支持的符号。写成一张表是为了让“支持范围”可读、可测。
_SYMBOLS = {
    # 希腊字母
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ϵ", "varepsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "varpi": "ϖ", "rho": "ρ",
    "sigma": "σ", "varsigma": "ς", "tau": "τ", "upsilon": "υ", "phi": "ϕ", "varphi": "φ",
    "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π",
    "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    # 运算符与关系
    "times": "×", "cdot": "⋅", "div": "÷", "pm": "±", "mp": "∓", "ast": "∗",
    "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "ne": "≠", "neq": "≠",
    "approx": "≈", "equiv": "≡", "sim": "∼", "simeq": "≃", "propto": "∝",
    "ll": "≪", "gg": "≫", "in": "∈", "notin": "∉", "subset": "⊂", "subseteq": "⊆",
    "supset": "⊃", "supseteq": "⊇", "cup": "∪", "cap": "∩", "emptyset": "∅",
    "forall": "∀", "exists": "∃", "neg": "¬", "land": "∧", "lor": "∨",
    "to": "→", "rightarrow": "→", "leftarrow": "←", "longrightarrow": "⟶",
    "Rightarrow": "⇒", "Leftarrow": "⇐", "leftrightarrow": "↔", "mapsto": "↦",
    "infty": "∞", "partial": "∂", "nabla": "∇", "sqrt": "√", "angle": "∠",
    "degree": "°", "circ": "∘", "bullet": "∙", "prime": "′",
    "ldots": "…", "dots": "…", "cdots": "⋯", "vdots": "⋮", "ddots": "⋱",
    "perp": "⊥", "parallel": "∥", "cong": "≅", "triangle": "△",
    "star": "⋆", "dagger": "†", "ell": "ℓ", "hbar": "ℏ", "Re": "ℜ", "Im": "ℑ",
    # 大运算符（带上下限）
    "sum": "∑", "prod": "∏", "coprod": "∐", "int": "∫", "iint": "∬", "oint": "∮",
    "bigcup": "⋃", "bigcap": "⋂", "bigoplus": "⨁",
}

_LIMIT_OPS = {"sum", "prod", "coprod", "bigcup", "bigcap", "bigoplus",
              "lim", "max", "min", "sup", "inf", "det", "gcd"}
_SIDE_LIMIT_OPS = {"int", "iint", "oint"}

#: 间距控制序列（单位是 em，相对当前字号）。
_SPACES = {",": 0.167, ":": 0.222, ";": 0.278, "!": -0.167, " ": 0.333,
           "quad": 1.0, "qquad": 2.0, "thinspace": 0.167, "enspace": 0.5}

#: 需要上下加符号的装饰。
_ACCENTS = {"hat": "ˆ", "bar": "¯", "overline": "¯", "vec": "→", "dot": "˙",
            "ddot": "¨", "tilde": "˜", "widehat": "ˆ", "widetilde": "˜",
            "underline": "_"}

_TEXT_COMMANDS = {"text", "mbox"}
_UPRIGHT_COMMANDS = {"mathrm", "operatorname", "mathbf", "mathit"}

#: 函数名按正体排版；其中一部分把上下限放在正上/正下方（与 LaTeX 的 \lim 一类一致）。
_FUNCTIONS = {"sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan",
              "sinh", "cosh", "tanh", "coth", "log", "ln", "lg", "exp", "arg", "deg",
              "dim", "hom", "ker", "tr", "rank", "diag", "mod", "bmod", "Pr"}

#: 黑板粗体与花体的常用大写字母（只列常用的，其余明确报错）。
_BLACKBOARD = {"R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "C": "ℂ", "E": "𝔼", "P": "ℙ",
               "F": "𝔽", "H": "ℍ", "D": "𝔻", "B": "𝔹", "A": "𝔸", "1": "𝟙"}
_CALIGRAPHIC = {"L": "ℒ", "M": "ℳ", "F": "ℱ", "H": "ℋ", "B": "ℬ", "E": "ℰ", "R": "ℛ",
                "S": "𝒮", "T": "𝒯", "I": "ℐ", "C": "𝒞", "D": "𝒟", "P": "𝒫", "N": "𝒩",
                "O": "𝒪", "A": "𝒜", "G": "𝒢", "U": "𝒰", "V": "𝒱", "W": "𝒲", "X": "𝒳",
                "Y": "𝒴", "Z": "𝒵", "K": "𝒦", "Q": "𝒬", "J": "𝒥"}

_INVISIBLE = ""


#: 固定公式样本表：本模块**承诺支持**的语法范围（任务书 F05 “以固定公式样本表声明
#: 支持范围，不承诺完整 LaTeX”）。用例、验收矩阵与文档都引用这一份，改语法先改它。
SUPPORTED_SAMPLES = (
    (r"E = mc^{2}", "上下标"),
    (r"x_{i}^{2} + y_{i}^{2} = r^{2}", "同时带上下标"),
    (r"\frac{a + b}{c - d}", "分数"),
    (r"\frac{\partial u}{\partial t} = \kappa \nabla^{2} u", "偏导数"),
    (r"\sqrt{x^{2} + y^{2}}", "根号"),
    (r"\sqrt[3]{\frac{a}{b}}", "带次数的根号"),
    (r"\sum_{i=1}^{n} \frac{1}{i^{2}}", "求和（上下限）"),
    (r"\prod_{k=1}^{n} a_{k}", "连乘"),
    (r"\int_{a}^{b} f(x)\,dx", "积分（侧标）"),
    (r"\oint_{C} \vec{F} \cdot d\vec{r}", "环路积分"),
    (r"\lim_{n \to \infty} \left(1 + \frac{1}{n}\right)^{n} = e", "极限与自适应括号"),
    (r"\alpha + \beta = \gamma, \quad \theta_{2} - \theta_{1}", "希腊字母与间距"),
    (r"a \le b \ne c \approx d \propto e \in \mathbb{R}", "关系符与黑板粗体"),
    (r"\begin{pmatrix} a & b \\ c & d \end{pmatrix}", "矩阵"),
    (r"\begin{bmatrix} 1 & 0 \\ 0 & 1 \end{bmatrix}", "方括号矩阵"),
    (r"\begin{cases} x + y = 1 \\ x - y = 3 \end{cases}", "分段函数"),
    (r"\begin{aligned} f(x) &= x^{2} + 2x \\ &= (x+1)^{2} - 1 \end{aligned}", "对齐"),
    (r"\binom{n}{k} = \frac{n!}{k!(n-k)!}", "组合数"),
    (r"\vec{F} = m\vec{a}, \quad \hat{H}\psi = E\psi", "向量与算符帽子"),
    (r"\overline{x} = \frac{1}{n}\sum_{i=1}^{n} x_{i}", "上划线（均值）"),
    (r"\text{当 } x \to 0 \text{ 时，} \frac{\sin x}{x} \to 1", "\\text 里的中文与函数名"),
    (r"R_{eq} = \frac{R_{single}}{n - m}, \quad 23 \times 10^{-6}/K", "工程写法"),
)

class FormulaError(ValueError):    """公式本身有问题（不支持的命令、括号不配对等）。界面要原样显示源码。"""


# --------------------------------------------------------------------------
# 在 Markdown 里找公式
# --------------------------------------------------------------------------

def _skip_inline_code(text: str, index: int) -> int:
    """``index`` 指着反引号时，返回这段行内代码之后的偏移（没有结尾就返回原文末尾）。"""
    fence = 1
    while index + fence < len(text) and text[index + fence] == "`":
        fence += 1
    closing = text.find("`" * fence, index + fence)
    return len(text) if closing < 0 else closing + fence


def _escaped(text: str, index: int) -> bool:
    backslashes = 0
    position = index - 1
    while position >= 0 and text[position] == "\\":
        backslashes += 1
        position -= 1
    return backslashes % 2 == 1


def _fence_at(text: str, index: int) -> str:
    """``index`` 在行首时判断这里是不是围栏代码块的开始。"""
    if index and text[index - 1] != "\n":
        return ""
    line = text[index:text.find("\n", index) if text.find("\n", index) >= 0 else len(text)]
    match = re.match(r" {0,3}(`{3,}|~{3,})", line)
    return match.group(1) if match else ""


def _skip_fence(text: str, index: int, marker: str) -> int:
    closing = re.search(r"(?m)^ {0,3}%s" % re.escape(marker), text[index + len(marker):])
    if closing is None:
        return len(text)
    end = index + len(marker) + closing.end()
    newline = text.find("\n", end)
    return len(text) if newline < 0 else newline + 1


def _find_closing(text: str, index: int, marker: str, stop_at_newline: bool) -> int:
    position = index + len(marker)
    while position < len(text):
        char = text[position]
        if stop_at_newline and char == "\n":
            return -1
        if char == "$" and not _escaped(text, position):
            if marker == "$$":
                if text.startswith("$$", position):
                    return position
                position += 1
                continue
            return position
        position += 1
    return -1


def scan(text: str) -> list[dict]:
    """找出应当渲染的公式。

    规则（任务书 F05）：``$$...$$`` 是独立块，``$...$`` 是行内；``\\$`` 是普通美元符号；
    单美元不能跨行，内容首尾不得是空白（``价格 $5 与 $6 元`` 里的美元符号因此不会被
    当成公式）；识别不了的（未闭合、内容为空、首尾带空白）原样保留文本并给出原因。
    代码块与行内代码里的 ``$`` 不参与。
    """
    text = text or ""
    found: list[dict] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "`":
            ended = _skip_inline_code(text, index)
            if ended > index:
                index = ended
                continue
        if char == "\n":
            marker = _fence_at(text, index + 1)
            if marker:
                index = _skip_fence(text, index + 1, marker)
                continue
        if char != "$" or _escaped(text, index):
            index += 1
            continue
        display = text.startswith("$$", index)
        marker = "$$" if display else "$"
        close = _find_closing(text, index, marker, stop_at_newline=not display)
        if close < 0:
            found.append({"start": index, "end": len(text),
                          "tex": text[index + len(marker):].strip(), "display": display,
                          "error": "没有找到配对的 %s" % marker})
            break
        body = text[index + len(marker):close]
        entry = {"start": index, "end": close + len(marker), "tex": body.strip(),
                 "display": display, "error": ""}
        if not body.strip():
            entry["error"] = "公式内容是空的"
        elif not display and (body[:1].isspace() or body[-1:].isspace()):
            entry["error"] = "行内公式的首尾不能是空白（金额请写成 \\$）"
        found.append(entry)
        if len(found) >= MAX_EXPRESSIONS:
            break
        index = close + len(marker)
    return found


# --------------------------------------------------------------------------
# 记号化与语法分析
# --------------------------------------------------------------------------

_TOKEN = re.compile(r"(\\\\|~)|\\([A-Za-z]+|.)|([{}^_&])|(\s+)|(.)", re.S)


def tokenize(tex: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(tex):
        match = _TOKEN.match(tex, position)
        if not match:                                   # pragma: no cover - 正则覆盖全部字符
            position += 1
            continue
        position = match.end()
        if match.group(1) == "\\\\":
            tokens.append(("break", "\\\\"))     # \\ 必须在 \命令 之前匹配
        elif match.group(1) == "~":
            tokens.append(("space", " "))
        elif match.group(2):
            tokens.append(("cmd", match.group(2)))
        elif match.group(3):
            tokens.append((match.group(3), match.group(3)))
        elif match.group(4):
            tokens.append(("space", " "))
        else:
            tokens.append(("char", match.group(5)))
    return tokens


class _Parser:
    def __init__(self, tex: str):
        self.tokens = tokenize(tex)
        self.index = 0
        if len(tex) > MAX_TEX_CHARS:
            raise FormulaError("公式太长（上限 %d 个字符）" % MAX_TEX_CHARS)

    # -- 记号工具 --------------------------------------------------------
    def peek(self):
        return self.tokens[self.index] if self.index < len(self.tokens) else (None, None)

    def next(self):
        token = self.peek()
        self.index += 1
        return token

    def expect(self, kind):
        token = self.next()
        if token[0] != kind:
            raise FormulaError("这里应当是 %s" % kind)
        return token

    # -- 文法 ------------------------------------------------------------
    def parse(self) -> list:
        nodes = self.row()
        if self.index < len(self.tokens):
            raise FormulaError("有多余的记号：%s" % (self.peek()[1],))
        return nodes

    def row(self, stop=("}", "&", "break"), stop_cmd=frozenset(), stop_char="") -> list:
        nodes: list = []
        while self.index < len(self.tokens):
            kind, value = self.peek()
            if kind in stop or (kind == "cmd" and value in stop_cmd) or kind == "end":
                break
            if stop_char and kind == "char" and value == stop_char:
                break
            node = self.atom()
            while self.peek()[0] in ("^", "_"):
                marker = self.next()[0]
                script = self.atom()
                node = _attach(node, marker, script)
            nodes.append(node)
        return nodes

    def atom(self):
        kind, value = self.next()
        if kind in ("{", None):
            if kind is None:
                raise FormulaError("公式不完整")
            return self.group()
        if kind in ("^", "_", "&", "break", "}"):
            raise FormulaError("记号 %s 出现在不该出现的位置" % value)
        if kind == "char":
            return _Symbol(value)
        if kind == "space":
            return _Space(" ")
        if kind == "cmd":
            return self.command(value)
        raise FormulaError("不认识的记号：%s" % value)          # pragma: no cover

    def group(self) -> list:
        nodes = self.row()
        if self.index >= len(self.tokens):
            raise FormulaError("花括号没有配对")
        token = self.next()
        if token[0] != "}":
            raise FormulaError("花括号没有配对")
        return _Row(nodes)

    def optional(self, name: str):
        """``\\sqrt[3]{x}`` 里的可选参数。"""
        if self.peek() == ("char", "["):
            saved = self.index
            self.next()
            nodes = self.row(stop=("}", "&", "break"), stop_char="]")
            if self.peek() == ("char", "]"):
                self.next()
                return _Row(nodes)
            self.index = saved
        return None

    def argument(self) -> list:
        token = self.peek()
        if token[0] == "{":
            self.next()
            return self.group()
        if token[0] in (None, "}"):
            raise FormulaError("命令缺少参数")
        return _Row([self.atom()])

    def command(self, name: str):
        if name == "frac" or name == "dfrac" or name == "tfrac":
            top = self.argument()
            bottom = self.argument()
            return _Fraction(top, bottom)
        if name == "binom":
            top = self.argument()
            bottom = self.argument()
            return _Delimited("(", _Fraction(top, bottom, rule=False), ")")
        if name == "sqrt":
            index = self.optional("sqrt")
            return _Root(self.argument(), index)
        if name in _TEXT_COMMANDS:
            return _Text(self.argument_text(), family=TEXT_FONT)
        if name in _UPRIGHT_COMMANDS:
            weight = 700 if name == "mathbf" else 400
            return _Text(self.argument_text(), family=MATH_FONT, weight=weight)
        if name in ("mathbb", "mathcal", "mathscr", "mathfrak"):
            table = _BLACKBOARD if name == "mathbb" else _CALIGRAPHIC
            raw = self.argument_text()
            out = []
            for char in raw:
                if char.isspace():
                    out.append(char)
                elif char in table:
                    out.append(table[char])
                else:
                    raise FormulaError("\\%s 里暂不支持这个字母：%s" % (name, char))
            return _Symbol("".join(out))
        if name in _ACCENTS:
            return _Accent(self.argument(), _ACCENTS[name])
        if name in _SPACES:
            return _Space(name)
        if name == "left":
            return self.delimited()
        if name == "middle" or name == "right":
            raise FormulaError("\\%s 必须和 \\left 配对使用" % name)
        if name == "begin":
            return self.environment()
        if name == "end":
            raise FormulaError("没有配对的 \\begin")
        if name in _LIMIT_OPS:
            return _Symbol(_SYMBOLS.get(name, name), big=name in _SYMBOLS, limits=True)
        if name in _SIDE_LIMIT_OPS:
            return _Symbol(_SYMBOLS[name], big=True, side=True)
        if name in _FUNCTIONS:
            return _Text(name, family=MATH_FONT)
        if name in _SYMBOLS:
            return _Symbol(_SYMBOLS[name])
        if name in ("displaystyle", "textstyle", "limits", "nolimits"):
            return _Space("!")
        if len(name) == 1:
            return _Symbol(name)                    # \{ \} \, \% \$ 之类的转义
        raise FormulaError("暂不支持的命令：\\%s" % name)

    def argument_text(self) -> str:
        """``\\text{...}`` 里按字面读取（不再解释控制序列，除了几个转义）。"""
        token = self.peek()
        if token[0] != "{":
            raise FormulaError("\\text 后面需要一对花括号")
        self.next()
        depth, out = 1, []
        while self.index < len(self.tokens):
            kind, value = self.next()
            if kind == "{":
                depth += 1
            elif kind == "}":
                depth -= 1
                if depth == 0:
                    break
            elif kind == "cmd":
                value = {"%": "%", "$": "$", "_": "_", "&": "&", "#": "#"}.get(value, "\\" + value)
            elif kind == "space":
                value = " "
            elif kind == "break":
                value = " "
            out.append(value)
        if depth:
            raise FormulaError("\\text 的花括号没有配对")
        return "".join(out)

    def delimited(self):
        opener = self.next()
        left = opener[1] if opener[0] in ("char", "cmd") else "."
        body = self.row(stop=("}",), stop_cmd={"right"})
        closer = self.peek()
        if closer[0] != "cmd" or closer[1] != "right":
            raise FormulaError("\\left 没有配对的 \\right")
        self.next()
        end = self.next()
        right = end[1] if end[0] in ("char", "cmd") else "."
        return _Delimited(left, _Row(body), right)

    def environment(self):
        if self.peek()[0] != "{":
            raise FormulaError("\\begin 后面要写 \\{环境名\\}")
        self.next()
        name = []
        while self.index < len(self.tokens):
            kind, value = self.next()
            if kind == "}":
                break
            if kind == "char":
                name.append(value)
        if not name:
            raise FormulaError("\\begin 后面要写环境名")
        name = "".join(name)
        rows = [self.row(stop=("&", "break"), stop_cmd={"end"})]
        while True:
            kind, value = self.peek()
            if kind == "&":
                self.next()
                rows[-1].append(_Align())
                rows[-1].extend(self.row(stop=("&", "break"), stop_cmd={"end"}))
                continue
            if kind == "break":
                self.next()
                rows.append(self.row(stop=("&", "break"), stop_cmd={"end"}))
                continue
            if kind == "cmd" and value == "end":
                self.next()
                if self.peek()[0] != "{":
                    raise FormulaError("\\end 后面要写 \\{环境名\\}")
                self.next()
                closed = []
                while self.index < len(self.tokens):
                    token = self.next()
                    if token[0] == "}":
                        break
                    if token[0] == "char":
                        closed.append(token[1])
                if "".join(closed) != name:
                    raise FormulaError("\\begin{%s} 与 \\end{%s} 不配对" % (name, "".join(closed)))
                break
            if kind is None:
                raise FormulaError("环境 %s 没有 \\end" % name)
            raise FormulaError("环境 %s 里不认识的记号：%s" % (name, value))
        if name in _MATRIX_ENVS:
            return _Matrix(rows, _MATRIX_ENVS[name])
        if name in _ALIGN_ENVS:
            return _Aligned(rows)
        raise FormulaError("暂不支持的环境：%s" % name)


def _attach(node, marker: str, script):
    """把 ``^``/``_`` 挂到原节点上；连着写多个时合并成一个上下标节点。"""
    if isinstance(node, _Script):
        if marker == "^" and node.sup is None:
            return _Script(node.base, script, node.sub)
        if marker == "_" and node.sub is None:
            return _Script(node.base, node.sup, script)
    if marker == "^":
        return _Script(node, script, None)
    return _Script(node, None, script)


# --------------------------------------------------------------------------
# 盒子模型
# --------------------------------------------------------------------------

class _Node:
    pass


class _Symbol(_Node):
    def __init__(self, text, big=False, side=False, limits=False):
        self.text, self.big, self.side, self.limits = text, big, side, limits


class _Space(_Node):
    def __init__(self, name):
        self.name = name


class _Row(_Node):
    def __init__(self, nodes):
        self.nodes = list(nodes)


class _Text(_Node):
    """``\\text{}``/``\\mathrm{}``/``\\mathbf{}`` 这类按字面排版的片段。"""

    def __init__(self, text, family=None, weight=400):
        self.text, self.family, self.weight = text, family, weight


class _Fraction(_Node):
    def __init__(self, top, bottom, rule=True):
        self.top, self.bottom, self.rule = top, bottom, rule


class _Root(_Node):
    def __init__(self, body, index):
        self.body, self.index = body, index


class _Script(_Node):
    def __init__(self, base, sup, sub):
        self.base, self.sup, self.sub = base, sup, sub


class _Accent(_Node):
    def __init__(self, base, mark):
        self.base, self.mark = base, mark


class _Delimited(_Node):
    def __init__(self, left, body, right):
        self.left, self.body, self.right = left, body, right


class _Matrix(_Node):
    def __init__(self, rows, delimiter):
        self.rows, self.delimiter = rows, delimiter


class _Aligned(_Node):
    def __init__(self, rows):
        self.rows = rows


class _Align(_Node):
    """``&`` 对齐点。"""


class Box:
    """一个版面盒子：相对基线的上下高度，加上一串绘制指令（局部坐标）。"""

    __slots__ = ("width", "height", "depth", "ops")

    def __init__(self, width, height, depth, ops=None):
        self.width = float(width)
        self.height = float(height)
        self.depth = float(depth)
        self.ops = list(ops or ())

    def shifted(self, dx: float, dy: float) -> "Box":
        """整体平移（dy 为正表示向下）。"""
        ops = []
        for op in self.ops:
            ops.append((op[0], op[1] + dx, op[2] + dy) + tuple(op[3:]))
        return Box(self.width, self.height - dy, self.depth + dy, ops)


# --------------------------------------------------------------------------
# 度量与绘制（Windows GDI）
# --------------------------------------------------------------------------

_gdi_lock = threading.RLock()
_gdi: "_Canvas | None" = None

_font_cache: dict = {}
_ink_cache: dict = {}


def _gdi_library():
    if os.name != "nt":
        raise FormulaError("公式渲染依赖 Windows 的 GDI，本程序只在 Windows 上运行")
    return ctypes.WinDLL("gdi32", use_last_error=True), ctypes.WinDLL("user32", use_last_error=True)


class _Metrics(ctypes.Structure):
    _fields_ = [("tmHeight", wintypes.LONG), ("tmAscent", wintypes.LONG),
                ("tmDescent", wintypes.LONG), ("tmInternalLeading", wintypes.LONG),
                ("tmExternalLeading", wintypes.LONG), ("tmAveCharWidth", wintypes.LONG),
                ("tmMaxCharWidth", wintypes.LONG), ("tmWeight", wintypes.LONG),
                ("tmOverhang", wintypes.LONG), ("tmDigitizedAspectX", wintypes.LONG),
                ("tmDigitizedAspectY", wintypes.LONG), ("tmFirstChar", wintypes.WCHAR),
                ("tmLastChar", wintypes.WCHAR), ("tmDefaultChar", wintypes.WCHAR),
                ("tmBreakChar", wintypes.WCHAR), ("tmItalic", ctypes.c_byte),
                ("tmUnderlined", ctypes.c_byte), ("tmStruckOut", ctypes.c_byte),
                ("tmPitchAndFamily", ctypes.c_byte), ("tmCharSet", ctypes.c_byte)]


class _Size(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


# 备注：这里曾经用过 ``GetGlyphOutlineW`` 取字形墨迹框（连带需要 GLYPHMETRICS 与 MAT2，
# 其中 FIXED 结构的字段顺序是 fract 在前）。小样验证发现本机所有字体都返回 GDI_ERROR，
# 于是改成 ``GetGlyphIndicesW`` 判字形 + 探针位图扫像素。相关结构体一并删掉，
# 免得留下“看着还在用”的死代码。


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("bmiHeader", _BitmapInfoHeader), ("bmiColors", wintypes.DWORD * 3)]


class _Canvas:
    """一块离屏位图 + 一个用于度量的 DC。"""

    CLEARTYPE = 5
    TRANSPARENT = 1
    DEFAULT_CHARSET = 1
    OUT_TT_PRECIS = 4

    def __init__(self, width, height, background=None):
        self.gdi32, self.user32 = _gdi_library()
        self._bind()
        self.width, self.height = int(width), int(height)
        self.screen = self.user32.GetDC(None)
        self.dc = self.gdi32.CreateCompatibleDC(self.screen)
        self.bitmap = None
        self.bits = ctypes.c_void_p()
        self.old_bitmap = None
        if width and height:
            info = _BitmapInfo()
            info.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
            info.bmiHeader.biWidth = self.width
            info.bmiHeader.biHeight = -self.height
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            self.bitmap = self.gdi32.CreateDIBSection(self.screen, ctypes.byref(info), 0,
                                                      ctypes.byref(self.bits), None, 0)
            self.old_bitmap = self.gdi32.SelectObject(self.dc, self.bitmap)
            self.fill(background if background is not None else 0x00FFFFFF)
        self.font = None
        self.old_font = None
        self.gdi32.SetBkMode(self.dc, self.TRANSPARENT)

    def _bind(self):
        gdi32, user32 = self.gdi32, self.user32
        gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(_BitmapInfo), wintypes.UINT,
                                           ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE,
                                           wintypes.DWORD]
        gdi32.CreateDIBSection.restype = wintypes.HANDLE
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
        gdi32.SelectObject.restype = wintypes.HANDLE
        gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
        gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
        gdi32.CreateSolidBrush.restype = wintypes.HANDLE
        gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.COLORREF]
        gdi32.CreatePen.restype = wintypes.HANDLE
        gdi32.CreateFontW.argtypes = ([ctypes.c_int] * 5 + [wintypes.DWORD] * 8 + [wintypes.LPCWSTR])
        gdi32.CreateFontW.restype = wintypes.HANDLE
        gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
        gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
        gdi32.TextOutW.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.LPCWSTR,
                                   ctypes.c_int]
        gdi32.TextOutW.restype = wintypes.BOOL
        gdi32.GetTextExtentPoint32W.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                                                ctypes.POINTER(_Size)]
        gdi32.GetTextExtentPoint32W.restype = wintypes.BOOL
        gdi32.GetTextMetricsW.argtypes = [wintypes.HDC, ctypes.POINTER(_Metrics)]
        gdi32.GetTextMetricsW.restype = wintypes.BOOL
        gdi32.GetGlyphIndicesW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                                           ctypes.POINTER(ctypes.c_ushort), wintypes.UINT]
        gdi32.GetGlyphIndicesW.restype = wintypes.DWORD
        gdi32.MoveToEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        gdi32.LineTo.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        gdi32.DeleteDC.argtypes = [wintypes.HDC]
        user32.GetDC.argtypes = [wintypes.HWND]
        user32.GetDC.restype = wintypes.HDC
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HANDLE]

    # -- 资源 ------------------------------------------------------------
    def fill(self, color):
        brush = self.gdi32.CreateSolidBrush(color)
        self.user32.FillRect(self.dc, ctypes.byref(wintypes.RECT(0, 0, self.width, self.height)),
                             brush)
        self.gdi32.DeleteObject(brush)

    def select_font(self, family, px, weight=400, quality=CLEARTYPE):
        key = (family, int(px), int(weight), int(quality))
        handle = _font_cache.get(key)
        if handle is None:
            handle = self.gdi32.CreateFontW(-max(1, int(px)), 0, 0, 0, int(weight), 0, 0, 0,
                                            self.DEFAULT_CHARSET, self.OUT_TT_PRECIS, 0,
                                            quality, 0, family)
            _font_cache[key] = handle
        self.font = handle
        self.old_font = self.gdi32.SelectObject(self.dc, handle)
        return handle

    def release(self):
        if self.font:
            self.gdi32.SelectObject(self.dc, self.old_font)
            self.font = None

    def close(self):
        self.release()
        if self.bitmap:
            self.gdi32.SelectObject(self.dc, self.old_bitmap)
            self.gdi32.DeleteObject(self.bitmap)
            self.bitmap = None
        self.gdi32.DeleteDC(self.dc)
        self.user32.ReleaseDC(None, self.screen)

    # -- 度量 ------------------------------------------------------------
    def metrics(self):
        info = _Metrics()
        self.gdi32.GetTextMetricsW(self.dc, ctypes.byref(info))
        return info

    def measure(self, text, family, px, weight=400):
        """返回 ``(advance, ascent, descent)``。"""
        if not text:
            return 0.0, 0.0, 0.0
        self.select_font(family, px, weight)
        size = _Size()
        self.gdi32.GetTextExtentPoint32W(self.dc, text, len(text), ctypes.byref(size))
        info = self.metrics()
        return float(size.cx), float(info.tmAscent), float(info.tmDescent)

    def glyph_ink(self, char, family, px):
        """字形的墨迹度量 ``(上伸, 下伸, 步进)``；字体里没有这个字形时返回 ``None``。

        这里**不看**字体的行高：Cambria Math 的行高是字号的五倍多（它的 em 方块
        特意留得很大），按行高排版会得到一堆空白。也不用 ``GetGlyphOutline``——
        本机所有字体都返回 ``GDI_ERROR``（见 F05 小样验证记录）。改成：先用
        ``GetGlyphIndicesW`` 判断字体里有没有这个字形（没字形时 GDI 会画一个空框，
        不能靠像素判断），再把字符画在探针位图上扫描像素，量出真实的上下伸。
        """
        key = (char, family, int(px))
        if key in _ink_cache:
            return _ink_cache[key]
        if not self.has_glyph(char, family):
            _ink_cache[key] = None
            return None
        probe = _probe_for(family, px)
        ascent, descent = probe.scan(char)
        advance = self.measure(char, family, px)[0]
        value = (ascent, descent, advance)
        _ink_cache[key] = value
        return value

    def has_glyph(self, char: str, family: str) -> bool:
        """``GetGlyphIndicesW`` 报 0xFFFF 就是字体里没有这个字符。"""
        key = (char, family)
        if key in _glyph_cache:
            return _glyph_cache[key]
        self.select_font(family, 20)
        indices = (ctypes.c_ushort * 1)()
        result = self.gdi32.GetGlyphIndicesW(self.dc, char, 1, indices, 1)
        value = result != 0xFFFFFFFF and indices[0] != 0xFFFF
        _glyph_cache[key] = value
        return value

    def ink_extent(self, text: str, family: str, px: float):
        """整段文字的 ``(上伸, 下伸)``（取每个字符墨迹框的并集）。"""
        ascent = descent = 0.0
        for char in text:
            ink = self.glyph_ink(char, family, px)
            if not ink:
                continue
            ascent = max(ascent, ink[0])
            descent = max(descent, ink[1])
        return ascent, descent

    def draw(self, ops, color, weight=400):
        self.gdi32.SetTextColor(self.dc, color)
        for op in ops:
            if op[0] == "text":
                _kind, x, baseline, text, family, px = op[:6]
                run_weight = op[6] if len(op) > 6 else weight
                self.select_font(family, px, run_weight)
                info = self.metrics()
                self.gdi32.TextOutW(self.dc, int(round(x)), int(round(baseline - info.tmAscent)),
                                    text, len(text))
            else:
                _kind, x, top, width, height, shade = op
                pen = self.gdi32.CreatePen(0, max(1, int(round(height))),
                                           color if shade is None else shade)
                old = self.gdi32.SelectObject(self.dc, pen)
                y = int(round(top + height / 2.0))
                self.gdi32.MoveToEx(self.dc, int(round(x)), y, None)
                self.gdi32.LineTo(self.dc, int(round(x + width)), y)
                self.gdi32.SelectObject(self.dc, old)
                self.gdi32.DeleteObject(pen)

    def png(self):
        stride = self.width * 4
        raw = bytearray()
        for row in range(self.height):
            raw.append(0)
            line = self.bits.value + row * stride if False else None
            raw += ctypes.string_at(self.bits.value + row * stride, stride)
        return _encode_png(self.width, self.height, raw)


def _encode_png(width: int, height: int, bgra_rows: bytes | bytearray) -> bytes:
    """把 BGRA 行（每行前面已经放好 filter 字节）编成 PNG。"""
    stride = width * 4
    raw = bytearray()
    source = bytes(bgra_rows)
    for row in range(height):
        start = row * (stride + 1)
        raw.append(source[start])
        line = source[start + 1:start + 1 + stride]
        for x in range(width):
            b, g, r = line[x * 4], line[x * 4 + 1], line[x * 4 + 2]
            raw += bytes((r, g, b))
    chunks = [b"\x89PNG\r\n\x1a\n"]

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    chunks.append(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)))
    chunks.append(chunk(b"IDAT", zlib.compress(bytes(raw), 6)))
    chunks.append(chunk(b"IEND", b""))
    return b"".join(chunks)


def _measure_ctx():
    """度量用的单例画布（没有位图，只用来问字体）。"""
    global _gdi
    with _gdi_lock:
        if _gdi is None or _gdi.dc is None:
            _gdi = _Canvas(0, 0, None)
        return _gdi


def _family_for(char: str, family: str, px: float) -> str:
    """字体里没有这个字形时换一个能画的（GDI 缺字形时不报错，只画空框）。"""
    canvas = _measure_ctx()
    if canvas.glyph_ink(char, family, px) is not None:
        return family
    for fallback in (SYMBOL_FONT, TEXT_FONT, TEXT_FONT_FALLBACK, "Times New Roman"):
        if fallback != family and canvas.glyph_ink(char, fallback, px) is not None:
            return fallback
    return family


class _InkProbe:
    """把单个字符画在白底位图上、扫描像素量它的墨迹范围。"""

    BACKGROUND = 0x00FFFFFF

    def __init__(self, family: str, px: float):
        self.family = family
        self.px = float(px)
        self.width = int(px * 2.6) + 8
        self.height = int(px * 4.5) + 8
        self.baseline = int(px * 2.2) + 4
        self.canvas = _Canvas(self.width, self.height, self.BACKGROUND)

    def scan(self, char: str):
        """返回 ``(上伸, 下伸)``；字符没有墨迹（空格）时两者都是 0。"""
        self.canvas.fill(self.BACKGROUND)
        self.canvas.draw([("text", 2.0, float(self.baseline), char, self.family, self.px, 400)],
                         _colorref("#000000"))
        raw = ctypes.string_at(self.canvas.bits.value, self.width * self.height * 4)
        top, bottom = None, None
        for row in range(self.height):
            line = raw[row * self.width * 4:(row + 1) * self.width * 4]
            if any(line[index:index + 3] != b"\xff\xff\xff" for index in range(0, len(line), 4)):
                if top is None:
                    top = row
                bottom = row
        if top is None:
            return (0.0, 0.0)
        return (float(self.baseline - top), float(max(0, bottom - self.baseline)))


_probes: dict = {}


def _probe_for(family: str, px: float) -> _InkProbe:
    key = (family, int(px))
    probe = _probes.get(key)
    if probe is None:
        if len(_probes) > 24:                      # 别把位图攒着不放
            for old_key in list(_probes)[:-12]:
                _probes.pop(old_key).canvas.close()
        probe = _InkProbe(family, px)
        _probes[key] = probe
    return probe


_glyph_cache: dict = {}


def _text_box(text: str, family: str, px: float, weight: int = 400) -> Box:
    """一段文字的盒子：按墨迹框记上下伸，缺字形时逐段换字体。

    GDI 遇到字体里没有的字形**不报错**，只画一个空框；这里先用字形墨迹框判断，
    再决定这一段用哪个字体（与导出插件里读 cmap 的做法是同一个目的）。
    """
    if not text:
        return Box(0.0, 0.0, 0.0)
    runs: list[list] = []
    for char in text:
        chosen = family if char.isspace() else _family_for(char, family, px)
        if runs and runs[-1][0] == chosen:
            runs[-1][1] += char
        else:
            runs.append([chosen, char])
    parts = [_plain_text_box(run_family, run_text, px, weight)
             for run_family, run_text in runs]
    return _pack_row(parts, 0.0)


def _plain_text_box(family: str, text: str, px: float, weight: int = 400) -> Box:
    """一段同字体文字：宽度用整段度量（含字距），上下伸用墨迹框并集。"""
    canvas = _measure_ctx()
    advance = canvas.measure(text, family, px, weight)[0]
    ascent, descent = canvas.ink_extent(text, family, px)
    return Box(advance, ascent, descent,
               [("text", 0.0, 0.0, text, family, float(px), int(weight))])


def _pack_row(parts: list[Box], gap: float) -> Box:
    """把若干盒子水平排好，基线对齐。"""
    width = sum(part.width for part in parts) + gap * max(0, len(parts) - 1)
    height = max((part.height for part in parts), default=0.0)
    depth = max((part.depth for part in parts), default=0.0)
    ops, x = [], 0.0
    for part in parts:
        ops.extend(part.shifted(x, 0.0).ops)
        x += part.width + gap
    return Box(width, height, depth, ops)


#: 关系符两侧留 0.28em，二元运算符两侧留 0.22em（近似 LaTeX 的间距）。
_RELATIONS = set("=≤≥≠≈≡∼≃∝≪≫∈∉⊂⊆⊃⊇→←↔⇒⇐↦⊥∥≅")
_BINARIES = set("+−±∓×÷⋅∗∪∩∖")


def _row_box(nodes, size: float) -> Box:
    parts: list[tuple[str, Box, float]] = []
    for node in nodes:
        box = _layout(node, size)
        text = node.text if isinstance(node, _Symbol) else ""
        before = 0.0
        if text in _RELATIONS:
            before = size * 0.28
        elif text in _BINARIES:
            before = size * 0.22
        parts.append((text, box, before if parts else 0.0))
    if not parts:
        return Box(0.0, 0.0, 0.0)
    width = sum(box.width + before for _t, box, before in parts)
    height = max(box.height for _t, box, _b in parts)
    depth = max(box.depth for _t, box, _b in parts)
    ops, x = [], 0.0
    for index, (_text, box, before) in enumerate(parts):
        x += before
        ops.extend(box.shifted(x, 0.0).ops)
        x += box.width
        if index + 1 < len(parts):
            after = 0.0
            text = parts[index][0]
            if text in _RELATIONS:
                after = size * 0.28
            elif text in _BINARIES:
                after = size * 0.22
            elif text in (",", ";"):
                after = size * 0.17
            x += after - parts[index + 1][2]
            if x < 0:
                x = 0
    return Box(width, height, depth, ops)


def _layout(node, size: float) -> Box:
    if isinstance(node, _Symbol):
        if node.big:
            px = size * (1.5 if not node.side else 1.4)
            box = _plain_text_box(MATH_FONT, node.text, px)
            return box
        return _text_box(node.text, MATH_FONT, size)
    if isinstance(node, _Text):
        return _text_box(node.text, node.family or TEXT_FONT, size * 0.95, node.weight)
    if isinstance(node, _Space):
        amount = _SPACES.get(node.name, 0.333)
        return Box(size * amount, 0.0, 0.0)
    if isinstance(node, _Row):
        return _row_box(node.nodes, size)
    if isinstance(node, _Fraction):
        return _fraction_box(node, size)
    if isinstance(node, _Script):
        return _script_box(node, size)
    if isinstance(node, _Root):
        return _root_box(node, size)
    if isinstance(node, _Accent):
        return _accent_box(node, size)
    if isinstance(node, _Delimited):
        return _delimited_box(node, size)
    if isinstance(node, _Matrix):
        return _grid_box(node.rows, size, delimiter=node.delimiter)
    if isinstance(node, _Aligned):
        return _grid_box(node.rows, size, delimiter="", aligned=True)
    raise FormulaError("不认识的公式结构：%s" % type(node).__name__)   # pragma: no cover


def _fraction_box(node, size: float) -> Box:
    numerator = _layout(node.top, size * 0.95)
    denominator = _layout(node.bottom, size * 0.95)
    rule = max(1.0, round(size * 0.055)) if node.rule else 0.0
    gap = size * 0.18
    padding = size * 0.18
    width = max(numerator.width, denominator.width) + padding * 2
    ops = []
    ops.extend(numerator.shifted((width - numerator.width) / 2.0,
                                 -(gap + rule + numerator.depth)).ops)
    if rule:
        ops.append(("rule", 0.0, -rule, width, rule, None))
    ops.extend(denominator.shifted((width - denominator.width) / 2.0,
                                   gap + denominator.height).ops)
    height = numerator.height + numerator.depth + gap + rule
    depth = gap + denominator.height + denominator.depth
    return Box(width, height, depth, ops)


def _script_box(node, size: float) -> Box:
    base = _layout(node.base, size)
    limits = bool(getattr(node.base, "limits", False))
    superscript = _layout(node.sup, size * SCRIPT_SCALE) if node.sup is not None else None
    subscript = _layout(node.sub, size * SCRIPT_SCALE) if node.sub is not None else None
    if limits:
        width = max([base.width] + [box.width for box in (superscript, subscript) if box])
        ops = list(base.shifted((width - base.width) / 2.0, 0.0).ops)
        height, depth = base.height, base.depth
        if superscript is not None:
            shift = base.height + size * 0.12
            ops.extend(superscript.shifted((width - superscript.width) / 2.0,
                                           -(shift + superscript.depth)).ops)
            height = shift + superscript.height + superscript.depth
        if subscript is not None:
            shift = base.depth + size * 0.12
            ops.extend(subscript.shifted((width - subscript.width) / 2.0,
                                         shift + subscript.height).ops)
            depth = shift + subscript.height + subscript.depth
        return Box(width, height, depth, ops)
    width = base.width
    ops = list(base.ops)
    height, depth = base.height, base.depth
    stacked = superscript is not None and subscript is not None
    if superscript is not None:
        rise = size * (0.62 if stacked else 0.46)
        offset = base.width + size * 0.06
        ops.extend(superscript.shifted(offset, -(rise + superscript.depth)).ops)
        width = max(width, offset + superscript.width)
        height = max(height, rise + superscript.height + superscript.depth)
    if subscript is not None:
        drop = size * (0.18 if not stacked else 0.15)
        offset = base.width + size * 0.06
        ops.extend(subscript.shifted(offset, drop + subscript.height).ops)
        width = max(width, offset + subscript.width)
        depth = max(depth, drop + subscript.height + subscript.depth)
    return Box(width, height, depth, ops)


def _root_box(node, size: float) -> Box:
    body = _layout(node.body, size)
    extent = body.height + body.depth
    index = _layout(node.index, size * SCRIPTSCRIPT_SCALE) if node.index is not None else None
    # 次数排在根号左上角：先在左边留出它的宽度，再放根号
    index_width = (index.width + size * 0.05) if index is not None else 0.0
    glyph_px = max(size * 1.1, extent * 1.3)
    radical = _plain_text_box(MATH_FONT, _SYMBOLS["sqrt"], glyph_px)
    over = max(1.0, round(size * 0.055))
    start = index_width + radical.width * 0.58
    radical_shift = (body.depth - radical.depth) / 2.0 \
        - (body.height + body.depth - radical.height - radical.depth) / 2.0
    width = start + body.width + size * 0.06
    ops = list(radical.shifted(index_width, radical_shift).ops)
    rule_y = -(body.height + size * 0.06)
    ops.append(("rule", start, rule_y, width - start, over, None))
    ops.extend(body.shifted(start, 0.0).ops)
    height = max(body.height + size * 0.06 + over, radical.height - radical_shift)
    depth = max(body.depth, radical_shift + radical.height + radical.depth)
    if index is not None:
        lift = (radical.height - radical_shift) + size * 0.04
        ops.extend(index.shifted(max(0.0, index_width - size * 0.05 - index.width),
                                 -(lift + index.depth)).ops)
        height = max(height, lift + index.height + index.depth)
    return Box(width, height, depth, ops)


def _accent_box(node, size: float) -> Box:
    base = _layout(node.base, size)
    if node.mark == "_":                       # 下划线用一条线，跟 LaTeX 一样盖满整段
        thick = max(1.0, round(size * 0.05))
        offset = max(1.0, round(size * 0.08))
        ops = list(base.ops) + [("rule", 0.0, base.depth + offset, base.width, thick, None)]
        return Box(base.width, base.height, base.depth + offset + thick, ops)
    if node.mark == "¯":                       # 上划线同理
        thick = max(1.0, round(size * 0.05))
        offset = max(1.0, round(size * 0.08))
        ops = [("rule", 0.0, -(base.height + offset + thick), base.width, thick, None)] \
            + list(base.ops)
        return Box(base.width, base.height + offset + thick, base.depth, ops)
    mark_px = size * (0.85 if node.mark in "ˆ˜˙¨¯" else 0.95)
    mark = _plain_text_box(MATH_FONT, node.mark, mark_px)
    ops = list(mark.shifted(base.width / 2.0 - mark.width / 2.0,
                            -(base.height + mark.depth * 0.7)).ops) + list(base.ops)
    return Box(base.width, base.height + mark.height, base.depth, ops)


def _delimiter_box(char: str, extent: float, size: float) -> Box:
    if char in (".", ""):
        return Box(0.0, 0.0, 0.0)
    glyph = {"(": "(", ")": ")", "[": "[", "]": "]", "{": "{", "}": "}",
             "|": "∣", "‖": "‖", "⟨": "⟨", "⟩": "⟩", "/": "/", "\\": "\\"}.get(char, char)
    px = max(size * 0.95, extent * 0.92)
    return _plain_text_box(MATH_FONT, glyph, px)


def _delimited_box(node, size: float) -> Box:
    body = _layout(node.body, size)
    extent = body.height + body.depth
    left = _delimiter_box(node.left, extent, size)
    right = _delimiter_box(node.right, extent, size)
    left_shift = (body.depth - body.height) / 2.0 - (left.depth - left.height) / 2.0
    right_shift = (body.depth - body.height) / 2.0 - (right.depth - right.height) / 2.0
    gap = size * 0.08
    ops = list(left.shifted(0.0, left_shift).ops)
    ops.extend(body.shifted(left.width + gap, 0.0).ops)
    ops.extend(right.shifted(left.width + gap + body.width + gap, right_shift).ops)
    width = left.width + gap + body.width + gap + right.width
    height = max(body.height, left_shift + left.height, right_shift + right.height)
    depth = max(body.depth, left_shift + left.depth, right_shift + right.depth)
    return Box(width, max(0.0, height), max(0.0, depth), ops)


def _split_cells(nodes) -> list[list]:
    cells, current = [], []
    for node in nodes:
        if isinstance(node, _Align):
            cells.append(current)
            current = []
        else:
            current.append(node)
    cells.append(current)
    return cells


def _grid_box(rows, size: float, *, delimiter: str = "", aligned: bool = False) -> Box:
    grid = [_split_cells(row) for row in rows]
    if not grid:
        raise FormulaError("矩阵是空的")
    columns = max(len(row) for row in grid)
    for row in grid:
        row.extend([[]] * (columns - len(row)))
    cells = [[_row_box(cell, size * 0.95) if cell else Box(0.0, 0.0, 0.0) for cell in row]
             for row in grid]
    column_width = [max(cells[r][c].width for r in range(len(cells))) for c in range(columns)]
    row_height = [max(cells[r][c].height for c in range(columns)) for r in range(len(cells))]
    row_depth = [max(cells[r][c].depth for c in range(columns)) for r in range(len(cells))]
    column_gap = size * 0.9
    row_gap = size * 0.45
    width = sum(column_width) + column_gap * (columns - 1)
    ops, y = [], 0.0
    for r, row in enumerate(cells):
        x = 0.0
        for c, cell in enumerate(row):
            if aligned:
                offset = x + column_width[c] - cell.width if c % 2 == 0 else x
            else:
                offset = x + (column_width[c] - cell.width) / 2.0
            ops.extend(cell.shifted(offset, y).ops)
            x += column_width[c] + column_gap
        y += row_height[r] + row_depth[r] + row_gap
    total_height = sum(row_height) + sum(row_depth) + row_gap * (len(cells) - 1)
    # 矩阵按竖直中心对齐（近似 LaTeX 把矩阵放在数学轴上的做法）
    box = Box(width, total_height / 2.0, total_height / 2.0, ops)
    if not delimiter:
        return box
    left, right = "", ""
    if delimiter == "()":
        left, right = "(", ")"
    elif delimiter == "[]":
        left, right = "[", "]"
    elif delimiter == "||":
        left, right = "|", "|"
    elif delimiter == "{":
        left, right = "{", ""
    return _wrap_delimiters(box, left, right, size)


def _wrap_delimiters(box: Box, left: str, right: str, size: float) -> Box:
    if not left and not right:
        return box
    left_box = _delimiter_box(left, box.height + box.depth, size)
    right_box = _delimiter_box(right, box.height + box.depth, size)
    gap = size * 0.08
    left_shift = (box.depth - box.height) / 2.0 - (left_box.depth - left_box.height) / 2.0
    right_shift = (box.depth - box.height) / 2.0 - (right_box.depth - right_box.height) / 2.0
    ops = list(left_box.shifted(0.0, left_shift).ops)
    ops.extend(box.shifted(left_box.width + gap, 0.0).ops)
    ops.extend(right_box.shifted(left_box.width + gap + box.width + gap, right_shift).ops)
    return Box(left_box.width + gap + box.width + gap + right_box.width,
               max(box.height, left_shift + left_box.height, right_shift + right_box.height),
               max(box.depth, left_shift + left_box.depth, right_shift + right_box.depth), ops)


# --------------------------------------------------------------------------
# 渲染成 PNG
# --------------------------------------------------------------------------

def _colorref(color: str) -> int:
    value = (color or "#000000").lstrip("#")
    if len(value) == 3:
        value = "".join(char * 2 for char in value)
    try:
        red, green, blue = (int(value[index:index + 2], 16) for index in (0, 2, 4))
    except (ValueError, IndexError):
        red = green = blue = 0
    return red | (green << 8) | (blue << 16)


def validate(tex: str) -> dict:
    """只做语法检查（不建 GDI 资源、不排版），导出预检用它。

    返回 ``{"ok", "reason"}``：能画的表达式这里一定通过；这里通过的**不一定**能画
    （字号/像素超限要等渲染才知道），所以它只用来提前报“写法不支持”。
    """
    tex = (tex or "").strip()
    if not tex:
        return {"ok": False, "reason": "公式内容是空的"}
    if len(tex) > MAX_TEX_CHARS:
        return {"ok": False, "reason": "公式太长（上限 %d 个字符）" % MAX_TEX_CHARS}
    try:
        _Parser(tex).parse()
    except FormulaError as error:
        return {"ok": False, "reason": str(error)}
    except RecursionError:
        return {"ok": False, "reason": "公式嵌套太深"}
    return {"ok": True, "reason": ""}


def to_png(tex: str, *, size: float = DEFAULT_SIZE, display: bool = False,
           color: str = "#1f2933", background: str = "#ffffff", scale: float = 1.0,
           weight: int = 400) -> dict:
    """把一个公式渲染成 PNG。失败时返回 ``ok=False`` 与原因（界面显示源码）。"""
    tex = (tex or "").strip()
    if not tex:
        return {"ok": False, "reason": "公式内容是空的", "tex": tex}
    if len(tex) > MAX_TEX_CHARS:
        return {"ok": False, "reason": "公式太长（上限 %d 个字符）" % MAX_TEX_CHARS, "tex": tex}
    pixels = max(MIN_SIZE, min(MAX_SIZE, int(round(float(size) * max(0.1, float(scale))))))
    with _gdi_lock:
        try:
            nodes = _Parser(tex).parse()
            box = _row_box(nodes, float(pixels))
        except FormulaError as error:
            return {"ok": False, "reason": str(error), "tex": tex}
        except RecursionError:
            return {"ok": False, "reason": "公式嵌套太深", "tex": tex}
        padding = max(2, int(round(pixels * 0.22)))
        width = int(box.width + 2 * padding + 0.5)
        height = int(box.height + box.depth + 2 * padding + 0.5)
        if width <= 0 or height <= 0:
            return {"ok": False, "reason": "公式没有可见内容", "tex": tex}
        if width > MAX_IMAGE_PX or height > MAX_IMAGE_PX:
            return {"ok": False, "reason": "公式太大（%d×%d 像素，上限 %d）"
                    % (width, height, MAX_IMAGE_PX), "tex": tex}
        try:
            canvas = _Canvas(width, height, _colorref(background))
        except (FormulaError, OSError, AttributeError) as error:
            return {"ok": False, "reason": "画布创建失败：%s" % error, "tex": tex}
        try:
            ops = Box(box.width, box.height, box.depth, box.ops).shifted(
                float(padding), float(padding + box.height)).ops
            canvas.draw(ops, _colorref(color), weight)
            data = canvas.png()
        finally:
            canvas.close()
    return {"ok": True, "png": data, "width": width, "height": height, "pixels": pixels,
            "baseline": padding + int(box.height + 0.5), "tex": tex, "display": bool(display)}


# --------------------------------------------------------------------------
# 缓存：键包含表达式、字号、缩放、主题与渲染器版本
# --------------------------------------------------------------------------

def cache_key(tex: str, *, size: float = DEFAULT_SIZE, display: bool = False,
              theme: str = "light", scale: float = 1.0, weight: int = 400,
              version: str = RENDERER_VERSION) -> str:
    payload = "|".join((version, str(int(round(float(size)))), str(round(float(scale), 3)),
                        theme, str(int(weight)), "1" if display else "0", tex.strip()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def png_size(data: bytes) -> tuple[int, int]:
    """从 PNG 头部读宽高（不必解码整张图）。"""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return 0, 0
    return struct.unpack(">II", data[16:24])


def cached(directory: str, tex: str, *, size: float = DEFAULT_SIZE, display: bool = False,
           theme: str = "light", scale: float = 1.0, color: str = "#1f2933",
           background: str = "#ffffff", weight: int = 400) -> dict:
    """按缓存键取图；没有就渲染一次并落盘。

    返回 ``{"ok", "path", "key", "width", "height", "cached", "reason"}``。写盘失败
    （目录只读、磁盘满）时退回“内存里渲染好的那张图”，不因为缓存写不进去就让公式消失。
    """
    key = cache_key(tex, size=size, display=display, theme=theme, scale=scale, weight=weight)
    path = os.path.join(directory, key + ".png") if directory else ""
    if path and os.path.isfile(path):
        try:
            with open(path, "rb") as handle:
                data = handle.read()
            width, height = png_size(data)
            if width and height:
                return {"ok": True, "path": path, "key": key, "width": width, "height": height,
                        "cached": True, "png": data}
        except OSError:
            pass
    result = to_png(tex, size=size, display=display, color=color, background=background,
                    scale=scale, weight=weight)
    if not result.get("ok"):
        return {"ok": False, "reason": result.get("reason", ""), "key": key, "path": "",
                "cached": False}
    if path:
        try:
            os.makedirs(directory, exist_ok=True)
            temporary = path + ".tmp"
            with open(temporary, "wb") as handle:
                handle.write(result["png"])
            os.replace(temporary, path)
        except OSError:
            path = ""
    return {"ok": True, "path": path, "key": key, "width": result["width"],
            "height": result["height"], "cached": False, "png": result["png"]}

