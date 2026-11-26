# -*- coding: utf-8 -*-
"""
MOD 物品编辑器（图形化）

功能：
  - 读取 MOD 的 Scripts/items.xml，列表浏览、搜索、按类型筛选
  - 编辑物品基础信息、图标、效果(trigger)、条件(require)、技能等
  - 新增 / 复制 / 删除物品
  - 自动备份并保存回 items.xml；新增图标可写入 resource.xml
  - 校验重名、空字段、非法数字、图标映射缺失等

运行：
  python item_editor.py
  python item_editor.py --mod "D:\\...\\modcache\\your_mod"
  python item_editor.py --selftest
"""

import argparse
import os
import re
import shutil
import sys
import tempfile
import traceback
import xml.etree.ElementTree as ET
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from PIL import Image, ImageTk
except Exception:  # Pillow 缺失时仅不显示预览
    Image = None
    ImageTk = None

SCRIPT_DIR = Path(__file__).resolve().parent


def _is_mod_dir(path) -> bool:
    return (Path(path) / "Scripts" / "items.xml").is_file()


def find_default_mod_dir():
    """优先查找本地真实 MOD；找不到时使用仓库内的演示 MOD。"""
    demo = SCRIPT_DIR / "examples" / "demo_mod"
    roots = [Path.cwd(), SCRIPT_DIR] + list(SCRIPT_DIR.parents)[:3]
    patterns = ("modcache/*", "*/modcache/*", "*/mod/modcache/*")
    for root in roots:
        if _is_mod_dir(root):
            return root
        for pattern in patterns:
            try:
                for candidate in root.glob(pattern):
                    if candidate.is_dir() and _is_mod_dir(candidate):
                        return candidate
            except OSError:
                pass
    return demo


DEFAULT_MOD_DIR = find_default_mod_dir()

ICON_EXTS = ("", ".png", ".jpg", ".jpeg", ".gif", ".webp")

TYPE_INFO = OrderedDict([
    ("0", "消耗品"),
    ("1", "武器"),
    ("2", "防具"),
    ("3", "饰品"),
    ("4", "武学书"),
    ("5", "任务物品"),
    ("6", "特技书"),
    ("7", "天赋书"),
    ("8", "升级材料/丹药"),
    ("9", "特殊物品"),
    ("10", "残章"),
    ("11", "礼包（跳剧情）"),
    ("12", "心得/秘籍"),
    ("13", "武学秘籍（NLS2）"),
    ("14", "暗器"),
])

COMMON_TRIGGERS = [
    "attribute", "att_atk", "att_def", "att_atk_pct", "att_def_pct",
    "att_gedang", "att_poge", "att_baoji", "att_anticriticalp",
    "powerup_skill", "powerup_internalskill", "powerup_special",
    "powerup_jianfa", "powerup_daofa", "powerup_quanzhang", "powerup_qimen",
    "base_pozhao", "base_chaizhao", "anti_debuff", "anti_special_def",
    "AddHp", "AddMp", "RecoverHp", "RecoverMp", "AddBuff",
    "AddMaxHp", "AddMaxMp", "getitem", "SwitchGameScene", "NLS", "NLS2",
    "animation", "祛毒", "Balls",
]

COMMON_REQUIRES = [
    "level", "talent", "item", "!item", "female", "male",
    "gengu", "wuxing", "shenfa", "dingli", "bili", "fuyuan", "wuxue",
    "jianfa", "daofa", "quanzhang", "qimen", "neigong",
]

TRIGGER_LABELS = {
    "attribute": "属性加成",
    "att_atk": "攻击",
    "att_def": "防御",
    "att_atk_pct": "攻击百分比",
    "att_def_pct": "防御百分比",
    "att_gedang": "格挡",
    "att_poge": "破格",
    "att_baoji": "暴击",
    "att_anticriticalp": "抗暴击",
    "powerup_skill": "外功威力",
    "powerup_internalskill": "内功威力",
    "powerup_special": "武学威力",
    "AddHp": "回复生命",
    "AddMp": "回复内力",
    "RecoverHp": "生命恢复率",
    "RecoverMp": "内力恢复率",
    "AddBuff": "附加增益",
    "AddMaxHp": "生命上限",
    "AddMaxMp": "内力上限",
    "getitem": "获得物品",
    "SwitchGameScene": "使用后触发剧情",
    "NLS": "学习武学",
    "NLS2": "学习秘籍",
    "anti_debuff": "抗负面效果",
    "anti_special_def": "抗特殊效果",
    "祛毒": "解毒",
}

REQUIRE_LABELS = {
    "level": "等级要求",
    "talent": "天赋要求",
    "item": "持有物品",
    "!item": "同名物品数量",
    "female": "女性限定",
    "male": "男性限定",
    "gengu": "根骨要求",
    "wuxing": "悟性要求",
    "shenfa": "身法要求",
    "dingli": "定力要求",
    "bili": "臂力要求",
    "fuyuan": "福缘要求",
    "jianfa": "剑法要求",
    "daofa": "刀法要求",
    "quanzhang": "拳掌要求",
    "qimen": "奇门要求",
    "neigong": "内功要求",
}

TEMPLATES = {
    "空白物品": {},
    "消耗品": {"type": "0", "triggers": [("attribute", "身法,10")]},
    "武器": {"type": "1", "triggers": [("att_atk", "100")], "requires": [("level", "10")]},
    "防具": {"type": "2", "triggers": [("att_def", "100")], "requires": [("level", "10")]},
    "饰品": {"type": "3", "triggers": [("attribute", "生命上限百分率,5")], "requires": [("dingli", "60")]},
    "心得/秘籍": {"type": "12", "triggers": [("powerup_skill", "示例武学,10")]},
    "礼包": {"type": "11", "triggers": [("SwitchGameScene", "story#示例剧情@打开礼包")]},
    "暗器": {"type": "14", "triggers": [("animation", "anqi")]},
    "武学秘籍": {"type": "13", "triggers": [("NLS2", "示例武学")]},
}


# ---------------------------------------------------------------- 基础工具

def esc(value) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ")
    return escape(text, {'"': "&quot;"})


def is_int(value: str) -> bool:
    try:
        int(str(value).strip())
        return True
    except Exception:
        return False


def to_int(value: str, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def type_label(t: str) -> str:
    return TYPE_INFO.get(str(t), str(t))


def type_display(t: str) -> str:
    return "%s %s" % (t, type_label(t))


def parse_type_display(text: str) -> str:
    return str(text).strip().split(" ", 1)[0]


ITEM_SCOPES = {
    0: "背包",
    1: "角色装备",
    2: "箱子",
    3: "装备+背包",
    4: "背包+箱子",
    5: "装备+箱子",
    6: "装备+背包+箱子",
}


def parse_quantity_require(require):
    """解析 !item / item 的 argvs：物品名,数量,统计范围。"""
    if require.get("name") not in ("!item", "item"):
        return None
    parts = [p.strip() for p in str(require.get("argvs", "")).split(",")]
    if len(parts) < 2 or not parts[0] or not is_int(parts[1]):
        return None
    count = max(1, to_int(parts[1], 1))
    mode = to_int(parts[2], 0) if len(parts) > 2 and is_int(parts[2]) else 0
    mode = max(0, min(6, mode))
    return parts[0], count, mode


def quantity_scope_label(mode: int) -> str:
    return ITEM_SCOPES.get(max(0, min(6, int(mode))), ITEM_SCOPES[6])


def quantity_condition_text(require, short=False):
    info = parse_quantity_require(require)
    if not info:
        return "数量条件参数不完整"
    item, count, mode = info
    scope = quantity_scope_label(mode)
    if require.get("name") == "!item":
        # 引擎判定：合计数量 >= N 时不满足，即必须 < N（最多 N-1 个）
        if short:
            return "≤%d（%s）" % (count - 1, scope)
        return "数量上限：合计最多 %d 个「%s」（%s）" % (count - 1, item, scope)
    if short:
        return "≥%d（%s）" % (count, scope)
    return "数量下限：需合计持有 ≥%d 个「%s」（%s）" % (count, item, scope)


def require_description(require):
    name = require.get("name", "")
    argvs = require.get("argvs", "")
    if name in ("!item", "item"):
        return quantity_condition_text(require)
    label = REQUIRE_LABELS.get(name, name)
    return "%s：%s" % (label, argvs) if argvs else label


def item_limit_text(d):
    parts = []
    for r in d.requires:
        info = parse_quantity_require(r)
        if info:
            if r.get("name") == "!item":
                parts.append("≤%d" % (info[1] - 1))
            else:
                parts.append("≥%d" % info[1])
    return "/".join(parts)


# ---------------------------------------------------------------- 数据模型

@dataclass
class ItemData:
    name: str = ""
    type: str = "0"
    level: str = "1"
    cd: str = "0"
    talent: str = ""
    price: str = "0"
    drop: bool = False
    pic: str = ""
    desc: str = ""
    buff: str = ""
    parameters: str = ""
    box: str = ""
    round: str = ""
    canzhang: str = ""

    triggers: list = field(default_factory=list)        # [{"name","argvs","lv"}]
    requires: list = field(default_factory=list)        # [{"name","argvs"}]
    skills: list = field(default_factory=list)          # [{"name","level","maxlevel"}]
    special_skills: list = field(default_factory=list)  # [{"name"}]

    extra_attrs: dict = field(default_factory=dict)
    extra_children: list = field(default_factory=list)  # 原始 XML 片段

    orig_name: str = None  # 磁盘上的原名，新增物品为 None


class ItemStore:
    """负责 items.xml / resource.xml 的解析、序列化、校验与保存。"""

    def __init__(self, mod_dir: Path):
        self.mod_dir = Path(mod_dir)
        self.items_path = self.mod_dir / "Scripts" / "items.xml"
        self.resource_path = self.mod_dir / "Scripts" / "resource.xml"
        self.index_path = self.mod_dir / "index.xml"
        self.items = OrderedDict()
        self.spans = {}
        self.raw_text = ""
        self.raw_bom = False
        self.resource_map = {}
        self.resource_text = ""
        self.resource_bom = False
        self.index_map = {}
        self.index_text = ""
        self.index_bom = False
        self.dirty = set()      # 修改过的 key
        self.deleted = set()    # 删除物品的原名

    # ---------------- 读取 ----------------

    def load(self):
        if not self.items_path.is_file():
            raise FileNotFoundError("找不到 items.xml：%s" % self.items_path)

        raw = self.items_path.read_bytes()
        self.raw_bom = raw.startswith(b"\xef\xbb\xbf")
        self.raw_text = raw.decode("utf-8-sig")
        root = ET.fromstring(self.raw_text)

        self.items = OrderedDict()
        for elem in root.findall("item"):
            data = self._parse_item(elem)
            key = data.name
            # 同名物品理论不存在；若存在则加后缀避免覆盖
            while key in self.items:
                key += "_#dup"
            data.orig_name = data.name
            self.items[key] = data

        self.spans = {}
        pattern = re.compile(
            r'[ \t]*<item\b[^>]*?\bname\s*=\s*"([^"]*)"[^>]*?(?:/>|>.*?</item>)',
            re.S,
        )
        for m in pattern.finditer(self.raw_text):
            self.spans.setdefault(m.group(1), (m.start(), m.end()))

        self.dirty = set()
        self.deleted = set()
        self._load_resources()
        self._load_index()

    def _load_resources(self):
        self.resource_map = {}
        self.resource_text = ""
        if not self.resource_path.is_file():
            return
        raw = self.resource_path.read_bytes()
        self.resource_bom = raw.startswith(b"\xef\xbb\xbf")
        self.resource_text = raw.decode("utf-8-sig")
        try:
            root = ET.fromstring(self.resource_text)
        except Exception:
            return
        for elem in root.findall("resource"):
            key = elem.get("key")
            value = elem.get("value")
            if key is not None and value is not None:
                self.resource_map[key] = value

    def _load_index(self):
        """index.xml 记录每个实际资源文件的尺寸，MOD 图标必须在此登记。"""
        self.index_map = {}
        self.index_text = ""
        if not self.index_path.is_file():
            return
        raw = self.index_path.read_bytes()
        self.index_bom = raw.startswith(b"\xef\xbb\xbf")
        self.index_text = raw.decode("utf-8-sig")
        try:
            root = ET.fromstring(self.index_text)
        except Exception:
            return
        for elem in root.findall("data"):
            url = elem.get("url")
            if url:
                self.index_map[url] = (elem.get("w", ""), elem.get("h", ""))

    def _parse_item(self, elem) -> ItemData:
        d = ItemData()
        d.name = elem.get("name", "")
        d.type = elem.get("type", "0")
        d.level = elem.get("level", "1")
        d.cd = elem.get("cd", "0")
        d.talent = elem.get("talent", "")
        d.price = elem.get("price", "0")
        d.drop = str(elem.get("drop", "false")).lower() == "true"
        d.pic = elem.get("pic", "")
        d.desc = elem.get("desc", "")
        d.buff = elem.get("buff", "")
        d.parameters = elem.get("parameters", "")
        d.box = elem.get("box", "")
        d.round = elem.get("round", "")
        d.canzhang = elem.get("CanzhangSkill", "")

        known = {
            "name", "type", "level", "cd", "talent", "price", "drop",
            "pic", "desc", "buff", "parameters", "box", "round", "CanzhangSkill",
        }
        for k, v in elem.attrib.items():
            if k not in known:
                d.extra_attrs[k] = v

        for ch in elem:
            if ch.tag == "trigger":
                d.triggers.append({
                    "name": ch.get("name", ""),
                    "argvs": ch.get("argvs", ""),
                    "lv": ch.get("lv", ""),
                })
            elif ch.tag == "require":
                d.requires.append({
                    "name": ch.get("name", ""),
                    "argvs": ch.get("argvs", ""),
                })
            elif ch.tag == "skills":
                for sk in ch.findall("skill"):
                    d.skills.append({
                        "name": sk.get("name", ""),
                        "level": sk.get("level", ""),
                        "maxlevel": sk.get("maxlevel", ""),
                    })
            elif ch.tag == "special_skills":
                for sk in ch.findall("skill"):
                    d.special_skills.append({"name": sk.get("name", "")})
            else:
                d.extra_children.append(ET.tostring(ch, encoding="unicode"))
        return d

    # ---------------- 图标 ----------------

    def icon_keys(self):
        return sorted([k for k in self.resource_map if k.startswith("物品.")])

    def icon_path(self, pic: str):
        value = self.resource_map.get(pic)
        if not value:
            return None
        base = self.mod_dir / value.replace("\\", "/")
        for ext in ICON_EXTS:
            p = Path(str(base) + ext)
            if p.is_file():
                return p
        parent = base.parent
        if parent.is_dir():
            stem = base.name.lower()
            for f in parent.iterdir():
                if f.is_file() and f.stem.lower() == stem:
                    return f
        return None

    def icon_url(self, pic: str):
        path = self.icon_path(pic)
        if not path:
            return None
        try:
            return path.relative_to(self.mod_dir).as_posix()
        except Exception:
            return path.name

    def index_state(self, pic: str):
        """返回 (状态, url, 尺寸)；状态：no_key / no_file / no_index / ok。"""
        if not pic:
            return "no_key", None, None
        if pic not in self.resource_map:
            return "no_key", None, None
        url = self.icon_url(pic)
        if not url:
            return "no_file", None, None
        if url in self.index_map:
            return "ok", url, self.index_map[url]
        return "no_index", url, None

    # ---------------- 序列化 ----------------

    def serialize_item(self, d: ItemData, nl: str = "\r\n", indent: str = "        ") -> str:
        attrs = [
            ("name", d.name),
            ("type", d.type),
            ("level", d.level),
            ("cd", d.cd),
            ("talent", d.talent),
            ("price", d.price),
            ("drop", "true" if d.drop else "false"),
            ("pic", d.pic),
        ]
        if d.buff:
            attrs.append(("buff", d.buff))
        if d.parameters:
            attrs.append(("parameters", d.parameters))
        if d.box and str(d.box).strip() not in ("", "0"):
            attrs.append(("box", d.box))
        if d.round and str(d.round).strip() not in ("", "1000"):
            attrs.append(("round", d.round))
        if d.canzhang:
            attrs.append(("CanzhangSkill", d.canzhang))
        for k, v in d.extra_attrs.items():
            attrs.append((k, v))
        attrs.append(("desc", d.desc))

        head = " ".join('%s="%s"' % (k, esc(v)) for k, v in attrs)
        lines = ["%s<item %s>" % (indent, head)]
        child_indent = indent + "        "

        for t in d.triggers:
            parts = ['name="%s"' % esc(t.get("name", "")),
                     'argvs="%s"' % esc(t.get("argvs", ""))]
            if str(t.get("lv", "")).strip() != "":
                parts.append('lv="%s"' % esc(t.get("lv")))
            lines.append("%s<trigger %s />" % (child_indent, " ".join(parts)))

        for r in d.requires:
            lines.append('%s<require name="%s" argvs="%s" />' % (
                child_indent, esc(r.get("name", "")), esc(r.get("argvs", ""))))

        if d.skills:
            lines.append("%s<skills>" % child_indent)
            for sk in d.skills:
                parts = ['name="%s"' % esc(sk.get("name", ""))]
                if str(sk.get("level", "")).strip() != "":
                    parts.append('level="%s"' % esc(sk.get("level")))
                if str(sk.get("maxlevel", "")).strip() != "":
                    parts.append('maxlevel="%s"' % esc(sk.get("maxlevel")))
                lines.append("%s        <skill %s />" % (child_indent, " ".join(parts)))
            lines.append("%s</skills>" % child_indent)

        if d.special_skills:
            lines.append("%s<special_skills>" % child_indent)
            for sk in d.special_skills:
                lines.append('%s        <skill name="%s" />' % (
                    child_indent, esc(sk.get("name", ""))))
            lines.append("%s</special_skills>" % child_indent)

        for raw in d.extra_children:
            lines.append("%s%s" % (child_indent, raw.strip()))

        lines.append("%s</item>" % indent)
        return nl.join(lines)

    # ---------------- 保存 ----------------

    def backup(self, path: Path) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = path.with_name(path.name + ".bak_" + stamp)
        i = 1
        while bak.exists():
            bak = path.with_name(path.name + ".bak_%s_%d" % (stamp, i))
            i += 1
        shutil.copy2(path, bak)
        return bak

    def save(self, resource_additions=None, index_additions=None):
        resource_additions = resource_additions or {}
        index_additions = index_additions or {}
        text = self.raw_text
        nl = "\r\n" if "\r\n" in text else "\n"
        edits = []

        for key, d in self.items.items():
            if d.orig_name is None:
                continue
            if d.orig_name in self.deleted:
                continue
            if key in self.dirty or d.name != d.orig_name:
                span = self.spans.get(d.orig_name)
                if span is None:
                    continue
                edits.append((span[0], span[1], self.serialize_item(d, nl=nl)))

        for name in self.deleted:
            span = self.spans.get(name)
            if span is None:
                continue
            start, end = span
            if text[end:end + len(nl)] == nl:
                end += len(nl)
            elif text[end:end + 1] in ("\r", "\n"):
                end += 1
            edits.append((start, end, ""))

        for start, end, block in sorted(edits, key=lambda x: x[0], reverse=True):
            text = text[:start] + block + text[end:]

        new_blocks = [
            self.serialize_item(d, nl=nl)
            for d in self.items.values() if d.orig_name is None
        ]
        if new_blocks:
            pos = text.rfind("</root>")
            if pos < 0:
                raise ValueError("items.xml 缺少 </root>")
            insert = nl.join(new_blocks) + nl
            if not text[:pos].endswith(nl):
                insert = nl + insert
            text = text[:pos] + insert + text[pos:]

        self.backup(self.items_path)
        data = text.encode("utf-8")
        if self.raw_bom:
            data = b"\xef\xbb\xbf" + data
        self.items_path.write_bytes(data)

        if resource_additions:
            self._save_resources(resource_additions)
        if index_additions:
            self._save_index(index_additions)

        self.load()

    def _save_resources(self, additions: dict):
        if not self.resource_path.is_file():
            return
        text = self.resource_text
        nl = "\r\n" if "\r\n" in text else "\n"

        appended = []
        for key, value in additions.items():
            line = '        <resource key="%s" value="%s"/>' % (esc(key), esc(value))
            pattern = re.compile(
                r'<resource\b[^>]*\bkey\s*=\s*"%s"[^>]*/>' % re.escape(esc(key))
            )
            if pattern.search(text):
                text = pattern.sub(lambda m: line, text, count=1)
            else:
                appended.append(line)

        pos = text.rfind("</root>")
        if pos < 0:
            return
        if appended:
            insert = nl.join(appended) + nl
            if not text[:pos].endswith(nl):
                insert = nl + insert
            text = text[:pos] + insert + text[pos:]

        self.backup(self.resource_path)
        data = text.encode("utf-8")
        if self.resource_bom:
            data = b"\xef\xbb\xbf" + data
        self.resource_path.write_bytes(data)

    def _save_index(self, additions: dict):
        """把新图标的实际文件登记到 index.xml（url + 宽高）。"""
        if not self.index_path.is_file():
            return
        text = self.index_text
        nl = "\r\n" if "\r\n" in text else "\n"
        appended = []
        for url, size in additions.items():
            w, h = size
            line = '  <data url="%s" w="%s" h="%s" />' % (esc(url), esc(w), esc(h))
            pattern = re.compile(
                r'<data\b[^>]*\burl\s*=\s*"%s"[^>]*/>' % re.escape(esc(url))
            )
            if pattern.search(text):
                text = pattern.sub(lambda m: line, text, count=1)
            else:
                appended.append(line)

        pos = text.rfind("</root>")
        if pos < 0:
            return
        if appended:
            insert = nl.join(appended) + nl
            if not text[:pos].endswith(nl):
                insert = nl + insert
            text = text[:pos] + insert + text[pos:]

        self.backup(self.index_path)
        data = text.encode("utf-8")
        if self.index_bom:
            data = b"\xef\xbb\xbf" + data
        self.index_path.write_bytes(data)


# ---------------------------------------------------------------- 通用弹窗

class FormDialog(tk.Toplevel):
    """简单的字段编辑弹窗，返回 dict 或 None。"""

    def __init__(self, parent, title, fields, values=None, combos=None):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.result = None
        self.vars = {}
        values = values or {}
        combos = combos or {}

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)

        for row, (key, label, width) in enumerate(fields):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 8))
            var = tk.StringVar(value=str(values.get(key, "")))
            self.vars[key] = var
            if key in combos:
                w = ttk.Combobox(body, textvariable=var, values=combos[key], width=width)
                w.grid(row=row, column=1, sticky="we", pady=4)
                w.focus_set()
            else:
                w = ttk.Entry(body, textvariable=var, width=width)
                w.grid(row=row, column=1, sticky="we", pady=4)
                if row == 0:
                    w.focus_set()

        btns = ttk.Frame(body)
        btns.grid(row=len(fields), column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="确定", command=self.on_ok).pack(side="left", padx=4)
        ttk.Button(btns, text="取消", command=self.on_cancel).pack(side="left", padx=4)

        self.bind("<Return>", lambda e: self.on_ok())
        self.bind("<Escape>", lambda e: self.on_cancel())
        self.grab_set()
        self.wait_window(self)

    def on_ok(self):
        self.result = {k: v.get() for k, v in self.vars.items()}
        self.destroy()

    def on_cancel(self):
        self.result = None
        self.destroy()


class NewItemDialog(tk.Toplevel):
    def __init__(self, parent, existing_names):
        super().__init__(parent)
        self.title("新增物品")
        self.resizable(False, False)
        self.transient(parent)
        self.result = None
        self.existing_names = set(existing_names)

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="物品名称").grid(row=0, column=0, sticky="w", pady=4, padx=(0, 8))
        self.name_var = tk.StringVar()
        e = ttk.Entry(body, textvariable=self.name_var, width=36)
        e.grid(row=0, column=1, sticky="we", pady=4)
        e.focus_set()

        ttk.Label(body, text="模板").grid(row=1, column=0, sticky="w", pady=4, padx=(0, 8))
        self.tpl_var = tk.StringVar(value="空白物品")
        ttk.Combobox(body, textvariable=self.tpl_var, values=list(TEMPLATES.keys()),
                     state="readonly", width=33).grid(row=1, column=1, sticky="we", pady=4)

        self.tip = ttk.Label(body, text="", foreground="#b00020", wraplength=320)
        self.tip.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        btns = ttk.Frame(body)
        btns.grid(row=3, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="创建", command=self.on_ok).pack(side="left", padx=4)
        ttk.Button(btns, text="取消", command=self.on_cancel).pack(side="left", padx=4)

        self.bind("<Return>", lambda e: self.on_ok())
        self.bind("<Escape>", lambda e: self.on_cancel())
        self.grab_set()
        self.wait_window(self)

    def on_ok(self):
        name = self.name_var.get().strip()
        if not name:
            self.tip.configure(text="名称不能为空")
            return
        if name in self.existing_names:
            self.tip.configure(text="已存在同名物品")
            return
        self.result = (name, self.tpl_var.get())
        self.destroy()

    def on_cancel(self):
        self.result = None
        self.destroy()


# ---------------------------------------------------------------- 主界面

class App:
    def __init__(self, root: tk.Tk, mod_dir: Path):
        self.root = root
        self.store = ItemStore(mod_dir)
        self.store.load()
        self.current_key = None
        self.loading = False
        self.iid_to_key = {}
        self.key_to_iid = {}
        self._icon_ref = None
        self.resource_additions = {}
        self.index_additions = {}
        self._new_counter = 0

        self.search_var = tk.StringVar()
        self.filter_var = tk.StringVar(value="全部")
        self.limit_only = tk.BooleanVar(value=False)
        self.vars = {
            "name": tk.StringVar(),
            "type": tk.StringVar(),
            "level": tk.StringVar(),
            "cd": tk.StringVar(),
            "price": tk.StringVar(),
            "talent": tk.StringVar(),
            "pic": tk.StringVar(),
            "drop": tk.BooleanVar(),
            "buff": tk.StringVar(),
            "parameters": tk.StringVar(),
            "box": tk.StringVar(),
            "round": tk.StringVar(),
            "canzhang": tk.StringVar(),
        }

        self.build_ui()
        self.refresh_list()
        self.select_first()

    # ---------------- 界面搭建 ----------------

    def build_ui(self):
        self.root.title("金庸群侠传X MOD 物品编辑器")
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        win_w = min(1320, max(1000, screen_w - 80))
        win_h = min(840, max(650, screen_h - 100))
        self.root.geometry("%dx%d+%d+%d" % (
            win_w, win_h, max(0, (screen_w - win_w) // 2), max(0, (screen_h - win_h) // 2 - 20)))
        self.root.minsize(1000, 650)

        toolbar = ttk.Frame(self.root, padding=(10, 8))
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="MOD 目录：").pack(side="left")
        self.path_label = ttk.Label(toolbar, text=str(self.store.mod_dir), foreground="#444")
        self.path_label.pack(side="left", padx=(0, 8))
        ttk.Button(toolbar, text="更换", command=self.choose_mod).pack(side="left")
        ttk.Button(toolbar, text="重新载入", command=self.reload_all).pack(side="left", padx=(8, 0))

        ttk.Button(toolbar, text="保存 (Ctrl+S)", command=self.save).pack(side="right")
        ttk.Button(toolbar, text="校验", command=self.show_validation).pack(side="right", padx=(0, 8))
        ttk.Button(toolbar, text="使用说明", command=self.show_help).pack(side="right", padx=(0, 8))

        paned = ttk.PanedWindow(self.root, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        # -------- 左栏
        left = ttk.Frame(paned)
        paned.add(left, weight=1)

        search_row = ttk.Frame(left)
        search_row.pack(fill="x", pady=(0, 6))
        ttk.Label(search_row, text="搜索").pack(side="left")
        search_entry = ttk.Entry(search_row, textvariable=self.search_var)
        search_entry.pack(side="left", fill="x", expand=True, padx=(6, 6))
        search_entry.bind("<Control-f>", lambda e: search_entry.focus_set())
        self.search_var.trace_add("write", lambda *a: self.refresh_list(keep_key=True))

        filter_row = ttk.Frame(left)
        filter_row.pack(fill="x", pady=(0, 6))
        ttk.Label(filter_row, text="类型").pack(side="left")
        self.filter_combo = ttk.Combobox(
            filter_row, textvariable=self.filter_var, state="readonly", width=18,
            values=["全部"] + [type_display(k) for k in TYPE_INFO],
        )
        self.filter_combo.pack(side="left", padx=(6, 8))
        self.filter_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_list(keep_key=True))
        ttk.Checkbutton(filter_row, text="仅数量条件", variable=self.limit_only,
                        command=lambda: self.refresh_list(keep_key=True)).pack(side="left", padx=(0, 8))
        self.count_label = ttk.Label(filter_row, text="")
        self.count_label.pack(side="left")

        tree_frame = ttk.Frame(left)
        tree_frame.pack(fill="both", expand=True)
        columns = ("name", "type", "level", "price", "limit")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("name", text="名称")
        self.tree.heading("type", text="类型")
        self.tree.heading("level", text="等级")
        self.tree.heading("price", text="价格")
        self.tree.heading("limit", text="数量条件")
        self.tree.column("name", width=180, anchor="w")
        self.tree.column("type", width=100, anchor="w")
        self.tree.column("level", width=50, anchor="center")
        self.tree.column("price", width=65, anchor="e")
        self.tree.column("limit", width=85, anchor="center")
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.tree.bind("<Delete>", lambda e: self.delete_item())

        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text="新增", command=self.new_item).pack(side="left")
        ttk.Button(btns, text="复制", command=self.copy_item).pack(side="left", padx=6)
        ttk.Button(btns, text="删除", command=self.delete_item).pack(side="left")

        # -------- 右栏
        right = ttk.Frame(paned)
        paned.add(right, weight=3)
        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill="both", expand=True)

        self.tab_basic = ttk.Frame(self.notebook, padding=12)
        self.tab_effects = ttk.Frame(self.notebook, padding=12)
        self.tab_advanced = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.tab_basic, text="基本信息")
        self.notebook.add(self.tab_effects, text="效果与条件")
        self.notebook.add(self.tab_advanced, text="高级")

        self.build_basic_tab()
        self.build_effects_tab()
        self.build_advanced_tab()

        # -------- 状态栏
        self.status_var = tk.StringVar(value="")
        status = ttk.Label(self.root, textvariable=self.status_var, anchor="w",
                           relief="sunken", padding=(8, 3))
        status.pack(fill="x", side="bottom")

        self.root.bind("<Control-s>", lambda e: self.save())
        self.root.bind("<Control-f>", lambda e: search_entry.focus_set())
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.update_title()

    def build_basic_tab(self):
        f = self.tab_basic
        f.columnconfigure(1, weight=1)
        f.columnconfigure(3, weight=1)

        def label(row, col, text):
            ttk.Label(f, text=text).grid(row=row, column=col, sticky="w", pady=4)

        label(0, 0, "名称")
        e_name = ttk.Entry(f, textvariable=self.vars["name"])
        e_name.grid(row=0, column=1, sticky="we", padx=(6, 16), pady=4)

        label(0, 2, "类型")
        self.type_combo = ttk.Combobox(
            f, textvariable=self.vars["type"], state="readonly",
            values=[type_display(k) for k in TYPE_INFO],
        )
        self.type_combo.grid(row=0, column=3, sticky="we", padx=(6, 12), pady=4)

        label(1, 0, "等级")
        ttk.Spinbox(f, from_=-999, to=9999, textvariable=self.vars["level"]).grid(
            row=1, column=1, sticky="we", padx=(6, 16), pady=4)
        label(1, 2, "冷却 cd")
        ttk.Spinbox(f, from_=0, to=9999, textvariable=self.vars["cd"]).grid(
            row=1, column=3, sticky="we", padx=(6, 12), pady=4)

        label(2, 0, "价格")
        ttk.Spinbox(f, from_=0, to=99999999, textvariable=self.vars["price"]).grid(
            row=2, column=1, sticky="we", padx=(6, 16), pady=4)
        ttk.Checkbutton(f, text="可掉落", variable=self.vars["drop"]).grid(
            row=2, column=3, sticky="w", padx=(6, 12), pady=4)

        label(3, 0, "天赋")
        ttk.Entry(f, textvariable=self.vars["talent"]).grid(
            row=3, column=1, columnspan=3, sticky="we", padx=(6, 0), pady=4)

        label(4, 0, "图标资源键")
        self.pic_combo = ttk.Combobox(f, textvariable=self.vars["pic"],
                                      values=self.store.icon_keys())
        self.pic_combo.grid(row=4, column=1, columnspan=3, sticky="we", padx=(6, 0), pady=4)

        icon_row = ttk.Frame(f)
        icon_row.grid(row=5, column=0, columnspan=4, sticky="we", pady=(2, 6))
        self.icon_label = ttk.Label(icon_row, text="无图标", width=14, anchor="center",
                                    relief="groove", padding=2)
        self.icon_label.pack(side="left")
        ttk.Button(icon_row, text="打开图标", command=self.open_icon).pack(side="left", padx=(8, 0))
        ttk.Button(icon_row, text="导入图片…", command=self.import_icon).pack(side="left", padx=(6, 0))
        ttk.Button(icon_row, text="补全索引", command=self.repair_index).pack(side="left", padx=(6, 0))
        self.icon_status = ttk.Label(icon_row, text="", foreground="#666")
        self.icon_status.pack(side="left", padx=(10, 0))

        ttk.Label(f, text="说明").grid(row=6, column=0, sticky="nw", pady=(4, 4))
        self.desc_text = tk.Text(f, height=5, wrap="word", undo=True)
        self.desc_text.grid(row=6, column=1, columnspan=3, sticky="nsew", padx=(6, 0), pady=4)
        f.rowconfigure(6, weight=1)

        hint_frame = ttk.LabelFrame(f, text="字段提示", padding=8)
        hint_frame.grid(row=7, column=0, columnspan=4, sticky="we", pady=(8, 0))
        self.type_hint = ttk.Label(hint_frame, text="", wraplength=820, justify="left")
        self.type_hint.pack(anchor="w")

        action_row = ttk.Frame(f)
        action_row.grid(row=8, column=0, columnspan=4, sticky="e", pady=(8, 0))
        ttk.Button(action_row, text="还原当前物品", command=self.revert_current).pack(side="right")

        for var in self.vars.values():
            var.trace_add("write", self.on_form_change)
        self.desc_text.bind("<<Modified>>", self.on_desc_modified)

    def build_effects_tab(self):
        f = self.tab_effects
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        f.rowconfigure(4, weight=1)

        header = ttk.Frame(f)
        header.grid(row=0, column=0, sticky="we", pady=(0, 6))
        self.summary_label = ttk.Label(header, text="", wraplength=900, justify="left",
                                       foreground="#333")
        self.summary_label.pack(anchor="w", fill="x")
        self.limit_label = ttk.Label(header, text="", wraplength=900, justify="left",
                                     foreground="#8a5a00")
        self.limit_label.pack(anchor="w", fill="x", pady=(3, 0))

        def _rewrap(event):
            width = max(200, event.width - 12)
            self.summary_label.configure(wraplength=width)
            self.limit_label.configure(wraplength=width)

        header.bind("<Configure>", _rewrap)

        trig_frame = ttk.LabelFrame(f, text="效果 trigger", padding=6)
        trig_frame.grid(row=1, column=0, sticky="nsew")
        trig_frame.columnconfigure(0, weight=1)
        trig_frame.rowconfigure(0, weight=1)
        self.trigger_tree = ttk.Treeview(trig_frame, columns=("name", "argvs", "lv"),
                                         show="headings", selectmode="browse")
        self.trigger_tree.heading("name", text="名称")
        self.trigger_tree.heading("argvs", text="参数 argvs")
        self.trigger_tree.heading("lv", text="等级 lv")
        self.trigger_tree.column("name", width=180)
        self.trigger_tree.column("argvs", width=460)
        self.trigger_tree.column("lv", width=80, anchor="center")
        tsb = ttk.Scrollbar(trig_frame, orient="vertical", command=self.trigger_tree.yview)
        xsb = ttk.Scrollbar(trig_frame, orient="horizontal", command=self.trigger_tree.xview)
        self.trigger_tree.configure(yscrollcommand=tsb.set, xscrollcommand=xsb.set)
        self.trigger_tree.grid(row=0, column=0, sticky="nsew")
        tsb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="we")
        tbtns = ttk.Frame(trig_frame)
        tbtns.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        for text, cmd in (("添加", self.add_trigger), ("编辑", self.edit_trigger),
                          ("删除", self.delete_trigger), ("上移", lambda: self.move_trigger(-1)),
                          ("下移", lambda: self.move_trigger(1))):
            ttk.Button(tbtns, text=text, command=cmd).pack(side="left", padx=(0, 6))
        self.trigger_tree.bind("<Double-1>", lambda e: self.edit_trigger())

        req_frame = ttk.LabelFrame(f, text="条件 require", padding=6)
        req_frame.grid(row=4, column=0, sticky="nsew", pady=(8, 0))
        req_frame.columnconfigure(0, weight=1)
        req_frame.rowconfigure(0, weight=1)
        self.require_tree = ttk.Treeview(req_frame, columns=("name", "argvs", "desc"),
                                         show="headings", selectmode="browse")
        self.require_tree.heading("name", text="名称")
        self.require_tree.heading("argvs", text="参数 argvs")
        self.require_tree.heading("desc", text="说明")
        self.require_tree.column("name", width=150)
        self.require_tree.column("argvs", width=300)
        self.require_tree.column("desc", width=300)
        self.require_tree.tag_configure("limit", background="#fff3cd")
        rsb = ttk.Scrollbar(req_frame, orient="vertical", command=self.require_tree.yview)
        rxsb = ttk.Scrollbar(req_frame, orient="horizontal", command=self.require_tree.xview)
        self.require_tree.configure(yscrollcommand=rsb.set, xscrollcommand=rxsb.set)
        self.require_tree.grid(row=0, column=0, sticky="nsew")
        rsb.grid(row=0, column=1, sticky="ns")
        rxsb.grid(row=1, column=0, sticky="we")
        rbtns = ttk.Frame(req_frame)
        rbtns.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        for text, cmd in (("添加", self.add_require), ("编辑", self.edit_require),
                          ("数量条件…", self.edit_quantity_require),
                          ("删除", self.delete_require), ("上移", lambda: self.move_require(-1)),
                          ("下移", lambda: self.move_require(1))):
            ttk.Button(rbtns, text=text, command=cmd).pack(side="left", padx=(0, 6))
        self.require_tree.bind("<Double-1>", lambda e: self.edit_require())

    def build_advanced_tab(self):
        f = self.tab_advanced
        f.columnconfigure(1, weight=1)

        def label(row, text):
            ttk.Label(f, text=text).grid(row=row, column=0, sticky="w", pady=4)

        label(0, "buff")
        ttk.Entry(f, textvariable=self.vars["buff"]).grid(
            row=0, column=1, sticky="we", padx=(6, 0), pady=4)

        label(1, "parameters")
        ttk.Entry(f, textvariable=self.vars["parameters"]).grid(
            row=1, column=1, sticky="we", padx=(6, 0), pady=4)

        label(2, "box")
        ttk.Entry(f, textvariable=self.vars["box"]).grid(
            row=2, column=1, sticky="we", padx=(6, 0), pady=4)

        label(3, "round")
        ttk.Entry(f, textvariable=self.vars["round"]).grid(
            row=3, column=1, sticky="we", padx=(6, 0), pady=4)

        label(4, "CanzhangSkill")
        ttk.Entry(f, textvariable=self.vars["canzhang"]).grid(
            row=4, column=1, sticky="we", padx=(6, 0), pady=4)

        skills_frame = ttk.LabelFrame(f, text="skills（自带武学）", padding=6)
        skills_frame.grid(row=5, column=0, columnspan=2, sticky="nsew", pady=(10, 0))
        skills_frame.columnconfigure(0, weight=1)
        self.skills_tree = ttk.Treeview(skills_frame, columns=("name", "level", "maxlevel"),
                                        show="headings", height=4)
        self.skills_tree.heading("name", text="武学名")
        self.skills_tree.heading("level", text="等级")
        self.skills_tree.heading("maxlevel", text="最大等级")
        self.skills_tree.column("name", width=300)
        self.skills_tree.column("level", width=100, anchor="center")
        self.skills_tree.column("maxlevel", width=100, anchor="center")
        self.skills_tree.grid(row=0, column=0, sticky="nsew")
        skbtns = ttk.Frame(skills_frame)
        skbtns.grid(row=1, column=0, sticky="w", pady=(4, 0))
        for text, cmd in (("添加", self.add_skill), ("编辑", self.edit_skill),
                          ("删除", self.delete_skill)):
            ttk.Button(skbtns, text=text, command=cmd).pack(side="left", padx=(0, 6))

        ss_frame = ttk.LabelFrame(f, text="special_skills（自带特技）", padding=6)
        ss_frame.grid(row=6, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        ss_frame.columnconfigure(0, weight=1)
        self.ss_tree = ttk.Treeview(ss_frame, columns=("name",), show="headings", height=3)
        self.ss_tree.heading("name", text="特技名")
        self.ss_tree.column("name", width=300)
        self.ss_tree.grid(row=0, column=0, sticky="nsew")
        ssbtns = ttk.Frame(ss_frame)
        ssbtns.grid(row=1, column=0, sticky="w", pady=(4, 0))
        for text, cmd in (("添加", self.add_special_skill), ("编辑", self.edit_special_skill),
                          ("删除", self.delete_special_skill)):
            ttk.Button(ssbtns, text=text, command=cmd).pack(side="left", padx=(0, 6))

        xml_frame = ttk.LabelFrame(f, text="当前物品 XML 预览（只读）", padding=6)
        xml_frame.grid(row=7, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        xml_frame.columnconfigure(0, weight=1)
        self.xml_preview = tk.Text(xml_frame, height=10, wrap="none", state="disabled",
                                   font=("Consolas", 9))
        self.xml_preview.grid(row=0, column=0, sticky="nsew")

        f.rowconfigure(7, weight=1)

    # ---------------- 列表 ----------------

    def refresh_list(self, keep_key=False):
        selected = self.current_key if keep_key else None
        self.tree.delete(*self.tree.get_children())
        self.iid_to_key.clear()
        self.key_to_iid.clear()

        query = self.search_var.get().strip().lower()
        wanted_type = None
        filter_text = self.filter_var.get()
        if filter_text and filter_text != "全部":
            wanted_type = parse_type_display(filter_text)

        idx = 0
        for key, d in self.store.items.items():
            if wanted_type is not None and str(d.type) != wanted_type:
                continue
            limit_text = item_limit_text(d)
            if self.limit_only.get() and not limit_text:
                continue
            haystack = " ".join([d.name, d.desc, d.talent, d.pic]).lower()
            if query and query not in haystack:
                continue
            iid = str(idx)
            idx += 1
            self.tree.insert("", "end", iid=iid,
                             values=(d.name, type_label(d.type), d.level, d.price, limit_text))
            self.iid_to_key[iid] = key
            self.key_to_iid[key] = iid

        self.count_label.configure(text="显示 %d / 共 %d" % (idx, len(self.store.items)))
        if selected and selected in self.key_to_iid:
            iid = self.key_to_iid[selected]
            self.tree.selection_set(iid)
            self.tree.see(iid)
        elif self.current_key:
            self.current_key = None

    def select_first(self):
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
            self.tree.focus(children[0])

    def on_tree_select(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        key = self.iid_to_key.get(sel[0])
        if not key or key == self.current_key:
            return
        self.current_key = key
        self.load_item(key)

    def update_current_row(self):
        if not self.current_key:
            return
        iid = self.key_to_iid.get(self.current_key)
        if not iid:
            return
        d = self.store.items[self.current_key]
        try:
            self.tree.item(iid, values=(d.name, type_label(d.type), d.level, d.price,
                                        item_limit_text(d)))
        except tk.TclError:
            pass

    # ---------------- 表单 ----------------

    def load_item(self, key):
        d = self.store.items.get(key)
        if not d:
            return
        self.loading = True
        try:
            self.vars["name"].set(d.name)
            self.vars["type"].set(type_display(d.type))
            self.vars["level"].set(d.level)
            self.vars["cd"].set(d.cd)
            self.vars["price"].set(d.price)
            self.vars["talent"].set(d.talent)
            self.vars["pic"].set(d.pic)
            self.vars["drop"].set(bool(d.drop))
            self.vars["buff"].set(d.buff)
            self.vars["parameters"].set(d.parameters)
            self.vars["box"].set(d.box)
            self.vars["round"].set(d.round)
            self.vars["canzhang"].set(d.canzhang)

            self.desc_text.delete("1.0", "end")
            self.desc_text.insert("1.0", d.desc)
            self.desc_text.edit_modified(False)

            self.refresh_effect_tables()
            self.refresh_skill_tables()
            self.update_icon()
            self.update_type_hint()
            self.update_preview()
            self.update_status()
        finally:
            self.loading = False

    def collect_form(self, d: ItemData):
        d.name = self.vars["name"].get().strip()
        d.type = parse_type_display(self.vars["type"].get()) or "0"
        d.level = self.vars["level"].get().strip()
        d.cd = self.vars["cd"].get().strip()
        d.price = self.vars["price"].get().strip()
        d.talent = self.vars["talent"].get().strip()
        d.pic = self.vars["pic"].get().strip()
        d.drop = bool(self.vars["drop"].get())
        d.desc = self.desc_text.get("1.0", "end-1c")
        d.buff = self.vars["buff"].get().strip()
        d.parameters = self.vars["parameters"].get().strip()
        d.box = self.vars["box"].get().strip()
        d.round = self.vars["round"].get().strip()
        d.canzhang = self.vars["canzhang"].get().strip()

    def on_form_change(self, *args):
        if self.loading or not self.current_key:
            return
        d = self.store.items.get(self.current_key)
        if not d:
            return
        self.collect_form(d)
        self.store.dirty.add(self.current_key)
        self.update_current_row()
        self.update_type_hint()
        self.update_preview()
        self.update_title()

    def on_desc_modified(self, event=None):
        if self.loading:
            self.desc_text.edit_modified(False)
            return
        if self.desc_text.edit_modified():
            self.on_form_change()
            self.desc_text.edit_modified(False)

    def update_type_hint(self):
        t = parse_type_display(self.vars["type"].get())
        label = type_label(t)
        hints = {
            "0": "消耗品：常用 trigger 为 AddHp / AddMp / AddBuff / attribute。",
            "1": "武器：常用 att_atk、att_atk_pct、powerup_*，require 常用 level 与属性门槛。",
            "2": "防具：常用 att_def、att_def_pct、attribute 生命/内力上限。",
            "3": "饰品：常用 attribute 六维与百分比上限，require 常用定力/福缘等。",
            "4": "武学书：通常配合 NLS 或 powerup_skill。",
            "5": "任务物品：一般无 trigger，由剧情/地图检查持有。",
            "6": "特技书：学习特技，复制现有同类物品写法最稳妥。",
            "7": "天赋书：talent 字段或用天赋相关 trigger。",
            "8": "升级材料/丹药：常用 attribute，box=-1 常见于礼包类。",
            "9": "特殊物品：按剧情需要自定义。",
            "10": "残章：通常由 CanzhangSkill 指定武学名。",
            "11": "礼包：trigger SwitchGameScene，argvs 形如 story#剧情名@打开礼包。",
            "12": "心得/秘籍：常用 powerup_skill / powerup_internalskill / attribute。",
            "13": "武学秘籍：现有数据多用 trigger NLS2 指定武学。",
            "14": "暗器：parameters 中配置 type/covertype/castsize/basepower/animation/buff/icon 等。",
        }
        self.type_hint.configure(text="类型 %s：%s\n%s" % (t, label, hints.get(str(t), "")))

    def update_icon(self):
        pic = self.vars["pic"].get().strip()
        state, url, dims = self.store.index_state(pic)
        path = self.store.icon_path(pic)
        if path and Image is not None:
            try:
                im = Image.open(path)
                im.thumbnail((96, 96))
                self._icon_ref = ImageTk.PhotoImage(im)
                self.icon_label.configure(image=self._icon_ref, text="")
            except Exception as exc:
                self._icon_ref = None
                self.icon_label.configure(image="", text="无图标")
                self.icon_status.configure(text="读取失败：%s" % exc, foreground="#b00020")
                return
        else:
            self._icon_ref = None
            self.icon_label.configure(image="", text="无图标")

        if state == "ok":
            w, h = dims if dims else ("?", "?")
            self.icon_status.configure(
                text="%s ｜ index.xml 已登记 %s×%s" % (url, w, h), foreground="#2e7d32")
        elif state == "no_index":
            self.icon_status.configure(
                text="%s ｜ index.xml 未登记（游戏内可能不显示，点“补全索引”）" % url,
                foreground="#ef6c00")
        elif state == "no_file":
            self.icon_status.configure(
                text="resource.xml 有映射，但 MOD 下缺少图片文件（可能使用内置资源）",
                foreground="#ef6c00")
        elif not pic:
            self.icon_status.configure(text="未设置图标资源键", foreground="#b00020")
        else:
            self.icon_status.configure(text="resource.xml 中不存在该资源键", foreground="#b00020")

    def open_icon(self):
        path = self.store.icon_path(self.vars["pic"].get().strip())
        if not path:
            messagebox.showinfo("提示", "找不到图标文件。")
            return
        try:
            os.startfile(str(path))
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))

    def import_icon(self):
        if not self.current_key:
            return
        d = self.store.items[self.current_key]
        filename = filedialog.askopenfilename(
            title="选择物品图标",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.gif *.webp"), ("所有文件", "*.*")],
        )
        if not filename:
            return
        src = Path(filename)
        ext = src.suffix.lower()
        if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            messagebox.showerror("不支持", "仅支持 png / jpg / jpeg / gif / webp。")
            return
        items_dir = self.store.mod_dir / "Items"
        items_dir.mkdir(parents=True, exist_ok=True)
        stem = "custom_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = items_dir / (stem + ext)
        shutil.copy2(src, dest)

        key = "物品." + (d.name or stem)
        if key in self.store.resource_map:
            if not messagebox.askyesno("资源键已存在", "资源键 %s 已存在，是否覆盖它的映射？" % key):
                return
        value = "Items/" + stem
        self.resource_additions[key] = value
        self.store.resource_map[key] = value

        url = "Items/%s%s" % (stem, ext)
        try:
            with Image.open(dest) as im:
                width, height = im.size
        except Exception:
            width, height = 256, 256
        self.index_additions[url] = (width, height)
        self.store.index_map[url] = (str(width), str(height))

        self.pic_combo.configure(values=self.store.icon_keys())
        self.vars["pic"].set(key)
        self.update_icon()
        self.set_status("已导入图标并登记 index.xml：%s（%d×%d）" % (dest.name, width, height))

    def repair_index(self):
        """为所有可定位但未登记 index.xml 的图标补登记。"""
        missing = {}
        for d in self.store.items.values():
            state, url, dims = self.store.index_state(d.pic)
            if state == "no_index" and url:
                missing[url] = d.pic
        if not missing:
            messagebox.showinfo("补全索引", "所有可定位图标的 index.xml 登记都完整。")
            return
        preview = "\n".join(sorted(missing)[:12])
        if len(missing) > 12:
            preview += "\n… 共 %d 条" % len(missing)
        if not messagebox.askyesno(
                "补全索引",
                "以下 %d 个图标文件缺少 index.xml 登记：\n\n%s\n\n是否补登记？保存后写入 index.xml。"
                % (len(missing), preview)):
            return
        for url in missing:
            path = self.store.mod_dir / url
            try:
                with Image.open(path) as im:
                    width, height = im.size
            except Exception:
                width, height = 256, 256
            self.index_additions[url] = (width, height)
            self.store.index_map[url] = (str(width), str(height))
        self.update_icon()
        self.update_title()
        self.set_status("已补登记 %d 条 index.xml，保存后写入" % len(missing))

    def update_preview(self):
        if not self.current_key:
            self.xml_preview.configure(state="normal")
            self.xml_preview.delete("1.0", "end")
            self.xml_preview.configure(state="disabled")
            return
        d = self.store.items[self.current_key]
        text = self.store.serialize_item(d, nl="\n")
        self.xml_preview.configure(state="normal")
        self.xml_preview.delete("1.0", "end")
        self.xml_preview.insert("1.0", text)
        self.xml_preview.configure(state="disabled")
        self.update_summary()

    def update_summary(self):
        if not self.current_key:
            self.summary_label.configure(text="")
            self.limit_label.configure(text="")
            return
        d = self.store.items[self.current_key]
        parts = []
        for t in d.triggers:
            name = t.get("name", "")
            args = t.get("argvs", "")
            label = TRIGGER_LABELS.get(name, name)
            if name == "attribute":
                parts.append("%s：%s" % (label, args))
            else:
                parts.append("%s(%s)" % (label, args) if args else label)
        for r in d.requires:
            if r.get("name") in ("!item", "item"):
                continue
            name = r.get("name", "")
            args = r.get("argvs", "")
            label = REQUIRE_LABELS.get(name, name)
            parts.append("条件·%s(%s)" % (label, args) if args else "条件·%s" % label)
        self.summary_label.configure(text=" ｜ ".join(parts))

        limits = [r for r in d.requires if r.get("name") in ("!item", "item")]
        if limits:
            text = "数量条件：" + "；".join(quantity_condition_text(r) for r in limits)
            foreign = []
            for r in limits:
                info = parse_quantity_require(r)
                if info and info[0] and info[0] != d.name:
                    foreign.append(info[0])
            if foreign:
                text += " ｜ 注意：条件物品与当前物品名不同（%s）" % "、".join(sorted(set(foreign)))
            self.limit_label.configure(text=text, foreground="#8a5a00")
        else:
            self.limit_label.configure(text="数量条件：无（!item / item）", foreground="#777")

    # ---------------- 效果表 ----------------

    def refresh_effect_tables(self):
        self.trigger_tree.delete(*self.trigger_tree.get_children())
        self.require_tree.delete(*self.require_tree.get_children())
        if not self.current_key:
            return
        d = self.store.items[self.current_key]
        for i, t in enumerate(d.triggers):
            self.trigger_tree.insert("", "end", iid=str(i),
                                     values=(t.get("name", ""), t.get("argvs", ""),
                                             t.get("lv", "")))
        for i, r in enumerate(d.requires):
            tags = ("limit",) if r.get("name") in ("!item", "item") else ()
            self.require_tree.insert("", "end", iid=str(i), tags=tags,
                                     values=(r.get("name", ""), r.get("argvs", ""),
                                             require_description(r)))

    def mark_dirty(self):
        if self.current_key:
            self.store.dirty.add(self.current_key)
            self.update_current_row()
            self.update_preview()
            self.update_status()
            self.update_title()

    def add_trigger(self):
        d = self.current_item()
        if not d:
            return
        dlg = FormDialog(self.root, "添加效果", [
            ("name", "名称", 28), ("argvs", "参数 argvs", 52), ("lv", "等级 lv（可选）", 28),
        ], combos={"name": COMMON_TRIGGERS})
        if dlg.result is not None:
            d.triggers.append(dlg.result)
            self.refresh_effect_tables()
            self.mark_dirty()

    def edit_trigger(self):
        d = self.current_item()
        sel = self.trigger_tree.selection()
        if not d or not sel:
            return
        i = int(sel[0])
        dlg = FormDialog(self.root, "编辑效果", [
            ("name", "名称", 28), ("argvs", "参数 argvs", 52), ("lv", "等级 lv（可选）", 28),
        ], values=d.triggers[i], combos={"name": COMMON_TRIGGERS})
        if dlg.result is not None:
            d.triggers[i] = dlg.result
            self.refresh_effect_tables()
            self.trigger_tree.selection_set(str(i))
            self.mark_dirty()

    def delete_trigger(self):
        d = self.current_item()
        sel = self.trigger_tree.selection()
        if not d or not sel:
            return
        i = int(sel[0])
        del d.triggers[i]
        self.refresh_effect_tables()
        self.mark_dirty()

    def move_trigger(self, delta):
        d = self.current_item()
        sel = self.trigger_tree.selection()
        if not d or not sel:
            return
        i = int(sel[0])
        j = i + delta
        if 0 <= j < len(d.triggers):
            d.triggers[i], d.triggers[j] = d.triggers[j], d.triggers[i]
            self.refresh_effect_tables()
            self.trigger_tree.selection_set(str(j))
            self.mark_dirty()

    def add_require(self):
        d = self.current_item()
        if not d:
            return
        dlg = FormDialog(self.root, "添加条件", [
            ("name", "名称", 28), ("argvs", "参数 argvs", 52),
        ], combos={"name": COMMON_REQUIRES})
        if dlg.result is not None:
            d.requires.append(dlg.result)
            self.refresh_effect_tables()
            self.mark_dirty()

    def edit_require(self):
        d = self.current_item()
        sel = self.require_tree.selection()
        if not d or not sel:
            return
        i = int(sel[0])
        dlg = FormDialog(self.root, "编辑条件", [
            ("name", "名称", 28), ("argvs", "参数 argvs", 52),
        ], values=d.requires[i], combos={"name": COMMON_REQUIRES})
        if dlg.result is not None:
            d.requires[i] = dlg.result
            self.refresh_effect_tables()
            self.require_tree.selection_set(str(i))
            self.mark_dirty()

    def edit_quantity_require(self):
        """结构化编辑 !item / item 数量条件。"""
        d = self.current_item()
        if not d:
            return
        sel = self.require_tree.selection()
        index = int(sel[0]) if sel else None
        values = {"kind": "!item（数量上限：最多 N-1 个）", "name": d.name,
                  "count": "2", "scope": "6 %s" % ITEM_SCOPES[6]}
        if index is not None and d.requires[index].get("name") in ("!item", "item"):
            info = parse_quantity_require(d.requires[index])
            if info:
                values = {
                    "kind": ("!item（数量上限：最多 N-1 个）"
                             if d.requires[index].get("name") == "!item"
                             else "item（数量下限：至少 N 个）"),
                    "name": info[0],
                    "count": str(info[1]),
                    "scope": "%d %s" % (info[2], quantity_scope_label(info[2])),
                }
        dlg = FormDialog(self.root, "数量条件（同名物品）", [
            ("kind", "条件类型", 30),
            ("name", "统计物品名", 24),
            ("count", "数量 N（!item 上限=N-1）", 14),
            ("scope", "统计范围", 26),
        ], values=values, combos={
            "kind": ["!item（数量上限：最多 N-1 个）", "item（数量下限：至少 N 个）"],
            "scope": ["%d %s" % (k, v) for k, v in ITEM_SCOPES.items()],
        })
        if dlg.result is None:
            return
        name = dlg.result["name"].strip()
        if not name:
            messagebox.showerror("参数错误", "统计物品名不能为空。")
            return
        count_text = dlg.result["count"].strip()
        if not is_int(count_text) or int(count_text) < 1:
            messagebox.showerror("参数错误", "数量 N 必须是 >=1 的整数。")
            return
        kind = "!item" if dlg.result["kind"].strip().startswith("!item") else "item"
        scope = parse_type_display(dlg.result["scope"])
        if not is_int(scope):
            scope = "6"
        row = {"name": kind, "argvs": "%s,%d,%s" % (name, int(count_text), scope)}
        if index is not None and d.requires[index].get("name") in ("!item", "item"):
            d.requires[index] = row
        else:
            d.requires.append(row)
            index = len(d.requires) - 1
        self.refresh_effect_tables()
        self.require_tree.selection_set(str(index))
        self.mark_dirty()

    def delete_require(self):
        d = self.current_item()
        sel = self.require_tree.selection()
        if not d or not sel:
            return
        i = int(sel[0])
        del d.requires[i]
        self.refresh_effect_tables()
        self.mark_dirty()

    def move_require(self, delta):
        d = self.current_item()
        sel = self.require_tree.selection()
        if not d or not sel:
            return
        i = int(sel[0])
        j = i + delta
        if 0 <= j < len(d.requires):
            d.requires[i], d.requires[j] = d.requires[j], d.requires[i]
            self.refresh_effect_tables()
            self.require_tree.selection_set(str(j))
            self.mark_dirty()

    # ---------------- 技能表 ----------------

    def refresh_skill_tables(self):
        self.skills_tree.delete(*self.skills_tree.get_children())
        self.ss_tree.delete(*self.ss_tree.get_children())
        if not self.current_key:
            return
        d = self.store.items[self.current_key]
        for i, sk in enumerate(d.skills):
            self.skills_tree.insert("", "end", iid=str(i),
                                    values=(sk.get("name", ""), sk.get("level", ""),
                                            sk.get("maxlevel", "")))
        for i, sk in enumerate(d.special_skills):
            self.ss_tree.insert("", "end", iid=str(i), values=(sk.get("name", ""),))

    def add_skill(self):
        d = self.current_item()
        if not d:
            return
        dlg = FormDialog(self.root, "添加自带武学", [
            ("name", "武学名", 36), ("level", "等级", 20), ("maxlevel", "最大等级", 20),
        ])
        if dlg.result is not None:
            d.skills.append(dlg.result)
            self.refresh_skill_tables()
            self.mark_dirty()

    def edit_skill(self):
        d = self.current_item()
        sel = self.skills_tree.selection()
        if not d or not sel:
            return
        i = int(sel[0])
        dlg = FormDialog(self.root, "编辑自带武学", [
            ("name", "武学名", 36), ("level", "等级", 20), ("maxlevel", "最大等级", 20),
        ], values=d.skills[i])
        if dlg.result is not None:
            d.skills[i] = dlg.result
            self.refresh_skill_tables()
            self.skills_tree.selection_set(str(i))
            self.mark_dirty()

    def delete_skill(self):
        d = self.current_item()
        sel = self.skills_tree.selection()
        if not d or not sel:
            return
        del d.skills[int(sel[0])]
        self.refresh_skill_tables()
        self.mark_dirty()

    def add_special_skill(self):
        d = self.current_item()
        if not d:
            return
        dlg = FormDialog(self.root, "添加自带特技", [("name", "特技名", 40)])
        if dlg.result is not None:
            d.special_skills.append(dlg.result)
            self.refresh_skill_tables()
            self.mark_dirty()

    def edit_special_skill(self):
        d = self.current_item()
        sel = self.ss_tree.selection()
        if not d or not sel:
            return
        i = int(sel[0])
        dlg = FormDialog(self.root, "编辑自带特技", [("name", "特技名", 40)],
                         values=d.special_skills[i])
        if dlg.result is not None:
            d.special_skills[i] = dlg.result
            self.refresh_skill_tables()
            self.ss_tree.selection_set(str(i))
            self.mark_dirty()

    def delete_special_skill(self):
        d = self.current_item()
        sel = self.ss_tree.selection()
        if not d or not sel:
            return
        del d.special_skills[int(sel[0])]
        self.refresh_skill_tables()
        self.mark_dirty()

    # ---------------- 新增 / 复制 / 删除 ----------------

    def current_item(self):
        return self.store.items.get(self.current_key) if self.current_key else None

    def new_item(self):
        existing = [d.name for d in self.store.items.values()]
        dlg = NewItemDialog(self.root, existing)
        if dlg.result is None:
            return
        name, template = dlg.result
        d = ItemData(name=name, type="0", level="1", cd="0", price="0", drop=False,
                     pic="", desc="")
        keys = self.store.icon_keys()
        if "物品.百宝袋" in self.store.resource_map:
            d.pic = "物品.百宝袋"
        elif keys:
            d.pic = keys[0]
        for name_t, argvs in TEMPLATES.get(template, {}).get("triggers", []):
            d.triggers.append({"name": name_t, "argvs": argvs, "lv": ""})
        for name_r, argvs in TEMPLATES.get(template, {}).get("requires", []):
            d.requires.append({"name": name_r, "argvs": argvs})
        tpl_type = TEMPLATES.get(template, {}).get("type")
        if tpl_type is not None:
            d.type = tpl_type

        self._new_counter += 1
        key = "__new_%d" % self._new_counter
        self.store.items[key] = d
        self.store.dirty.add(key)
        self.refresh_list(keep_key=True)
        if key in self.key_to_iid:
            self.current_key = None
            self.tree.selection_set(self.key_to_iid[key])
            self.tree.see(self.key_to_iid[key])
        self.update_title()
        self.set_status("已新增物品：%s" % name)

    def copy_item(self):
        d = self.current_item()
        if not d:
            return
        existing = {x.name for x in self.store.items.values()}
        name = d.name + "_副本"
        i = 2
        while name in existing:
            name = "%s_副本%d" % (d.name, i)
            i += 1
        nd = ItemData(
            name=name, type=d.type, level=d.level, cd=d.cd, talent=d.talent,
            price=d.price, drop=d.drop, pic=d.pic, desc=d.desc, buff=d.buff,
            parameters=d.parameters, box=d.box, round=d.round, canzhang=d.canzhang,
            triggers=[dict(x) for x in d.triggers],
            requires=[dict(x) for x in d.requires],
            skills=[dict(x) for x in d.skills],
            special_skills=[dict(x) for x in d.special_skills],
            extra_attrs=dict(d.extra_attrs),
            extra_children=list(d.extra_children),
        )
        # 复制时，指向原物品自身的数量条件同步改为新物品名
        for r in nd.requires:
            info = parse_quantity_require(r)
            if info and info[0] == d.name:
                r["argvs"] = "%s,%d,%d" % (name, info[1], info[2])
        self._new_counter += 1
        key = "__new_%d" % self._new_counter
        self.store.items[key] = nd
        self.store.dirty.add(key)
        self.refresh_list(keep_key=True)
        if key in self.key_to_iid:
            self.current_key = None
            self.tree.selection_set(self.key_to_iid[key])
            self.tree.see(self.key_to_iid[key])
        self.update_title()
        self.set_status("已复制为：%s" % name)

    def delete_item(self):
        d = self.current_item()
        if not d:
            return
        if not messagebox.askyesno("删除物品", "确定删除物品「%s」？\n保存后才会写入文件。" % d.name):
            return
        key = self.current_key
        if d.orig_name:
            self.store.deleted.add(d.orig_name)
        self.store.items.pop(key, None)
        self.store.dirty.discard(key)
        self.current_key = None
        self.refresh_list()
        self.clear_detail()
        self.select_first()
        self.update_title()
        self.set_status("已删除：%s（尚未写入文件）" % d.name)

    def clear_detail(self):
        self.loading = True
        try:
            for var in self.vars.values():
                try:
                    var.set(False if isinstance(var, tk.BooleanVar) else "")
                except Exception:
                    pass
            self.desc_text.delete("1.0", "end")
            self.desc_text.edit_modified(False)
            self.trigger_tree.delete(*self.trigger_tree.get_children())
            self.require_tree.delete(*self.require_tree.get_children())
            self.skills_tree.delete(*self.skills_tree.get_children())
            self.ss_tree.delete(*self.ss_tree.get_children())
            self.xml_preview.configure(state="normal")
            self.xml_preview.delete("1.0", "end")
            self.xml_preview.configure(state="disabled")
            self.summary_label.configure(text="")
            self.limit_label.configure(text="")
            self.icon_label.configure(image="", text="无图标")
            self.icon_status.configure(text="")
            self.type_hint.configure(text="")
        finally:
            self.loading = False

    def revert_current(self):
        d = self.current_item()
        if not d:
            return
        if d.orig_name is None:
            if not messagebox.askyesno("还原", "这是新物品，还原将丢弃它，是否继续？"):
                return
            self.store.items.pop(self.current_key, None)
            self.store.dirty.discard(self.current_key)
            self.current_key = None
            self.refresh_list()
            self.clear_detail()
            self.select_first()
            self.update_title()
            return
        # 从磁盘重新解析当前物品
        tmp = ItemStore(self.store.mod_dir)
        tmp.load()
        for d2 in tmp.items.values():
            if d2.name == d.orig_name:
                key = self.current_key
                self.store.items[key] = d2
                self.store.dirty.discard(key)
                self.load_item(key)
                self.refresh_list(keep_key=True)
                self.update_title()
                self.set_status("已还原：%s" % d2.name)
                return

    # ---------------- 校验 / 保存 ----------------

    def collect_issues(self):
        issues = []
        names = {}
        index_checked = set()
        limit_items = 0
        for key, d in self.store.items.items():
            if not d.name.strip():
                issues.append(("错误", d.name or "(空名称)", "名称为空"))
            else:
                if d.name in names:
                    issues.append(("错误", d.name, "物品名重复"))
                else:
                    names[d.name] = key
            if not is_int(d.type) or not (0 <= to_int(d.type) <= 14):
                issues.append(("错误", d.name, "type 必须是 0-14 的整数"))
            for field_name, value in (("level", d.level), ("cd", d.cd), ("price", d.price)):
                if not is_int(value):
                    issues.append(("错误", d.name, "%s 不是整数：%s" % (field_name, value)))
            if str(d.box).strip() and not is_int(d.box):
                issues.append(("错误", d.name, "box 不是整数：%s" % d.box))
            if str(d.round).strip() and not is_int(d.round):
                issues.append(("错误", d.name, "round 不是整数：%s" % d.round))
            for i, t in enumerate(d.triggers):
                if not str(t.get("name", "")).strip():
                    issues.append(("错误", d.name, "第 %d 个 trigger 名称为空" % (i + 1)))
                if str(t.get("lv", "")).strip() and not is_int(t.get("lv")):
                    issues.append(("错误", d.name, "第 %d 个 trigger 的 lv 不是整数" % (i + 1)))
            has_limit = False
            for i, r in enumerate(d.requires):
                if not str(r.get("name", "")).strip():
                    issues.append(("错误", d.name, "第 %d 个 require 名称为空" % (i + 1)))
                if r.get("name") in ("!item", "item"):
                    info = parse_quantity_require(r)
                    if info is None:
                        issues.append(("警告", d.name,
                                       "数量条件参数不完整：%s %s" % (r.get("name"), r.get("argvs", ""))))
                    else:
                        has_limit = True
                        if info[0] != d.name:
                            issues.append(("提示", d.name,
                                           "数量条件指向其他物品「%s」：%s（可能是复制残留）"
                                           % (info[0], quantity_condition_text(r))))
            if has_limit:
                limit_items += 1
            if not d.pic.strip():
                issues.append(("警告", d.name, "未设置图标资源键 pic"))
            elif d.pic not in self.store.resource_map:
                issues.append(("警告", d.name, "resource.xml 缺少图标映射：%s" % d.pic))
            elif self.store.icon_path(d.pic) is None:
                issues.append(("提示", d.name, "MOD/Items 下没有图标文件，可能依赖内置资源"))
            else:
                state, url, dims = self.store.index_state(d.pic)
                if state == "no_index" and url not in index_checked:
                    index_checked.add(url)
                    issues.append(("警告", d.name,
                                   "index.xml 未登记图标：%s（游戏内可能无法加载）" % url))
        if limit_items:
            issues.append(("提示", "(全局)",
                           "%d 个物品设置了 !item / item 数量条件，可勾选左栏“仅数量条件”筛选查看"
                           % limit_items))
        return issues

    def show_validation(self):
        issues = self.collect_issues()
        win = tk.Toplevel(self.root)
        win.title("校验结果")
        win.geometry("820x460")
        win.transient(self.root)
        frame = ttk.Frame(win, padding=10)
        frame.pack(fill="both", expand=True)
        summary = "共 %d 条：错误 %d，警告 %d，提示 %d" % (
            len(issues),
            sum(1 for x in issues if x[0] == "错误"),
            sum(1 for x in issues if x[0] == "警告"),
            sum(1 for x in issues if x[0] == "提示"),
        )
        ttk.Label(frame, text=summary).pack(anchor="w", pady=(0, 6))
        cols = ("level", "item", "msg")
        tree = ttk.Treeview(frame, columns=cols, show="headings")
        tree.heading("level", text="级别")
        tree.heading("item", text="物品")
        tree.heading("msg", text="问题")
        tree.column("level", width=70, anchor="center")
        tree.column("item", width=180)
        tree.column("msg", width=520)
        for level, item, msg in issues:
            tree.insert("", "end", values=(level, item, msg))
        vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.set_status(summary)

    def show_help(self):
        messagebox.showinfo(
            "使用说明",
            "《金庸群侠传X》（JYX）MOD 物品编辑器\n\n"
            "· 左栏搜索/按类型筛选，单击物品在右侧编辑。\n"
            "· 基本信息：名称、类型、等级、冷却、价格、天赋、图标、说明。\n"
            "· 效果与条件：编辑 trigger / require，双击表格行编辑。\n"
            "· 高级：buff、parameters、box、round、自带武学/特技，底部可查看 XML。\n"
            "· 新增/复制/删除按钮位于左栏下方。\n"
            "· 保存会写回 Scripts/items.xml，并自动生成 items.xml.bak_时间戳 备份。\n"
            "· 导入图片会复制到 MOD/Items，并写入 Scripts/resource.xml 与 index.xml。\n"
            "· “补全索引”会为缺少 index.xml 登记的图标补上尺寸记录。\n"
            "· 左栏“数量条件”列显示 !item / item 数量条件，可勾选“仅数量条件”筛选。\n"
            "· !item 是数量上限：按范围合计必须 <N（即最多 N-1 个）；item 是数量下限：合计 ≥N。\n"
            "· 例：!item 学神笔记,2,6 = 装备+背包+箱子合计最多 1 个学神笔记。\n"
            "· 校验会检查重名、空字段、非法数字、图标映射与 index.xml 登记。",
        )

    def normalize_self_quantity_refs(self, d: ItemData):
        """改名时同步更新指向自己的 !item / item 数量条件。"""
        if not d.orig_name or d.name == d.orig_name:
            return
        for r in d.requires:
            info = parse_quantity_require(r)
            if info and info[0] == d.orig_name:
                r["argvs"] = "%s,%d,%d" % (d.name, info[1], info[2])

    def save(self):
        if not self.has_changes():
            self.set_status("没有需要保存的修改。")
            return
        if self.current_key:
            d = self.store.items.get(self.current_key)
            if d:
                self.collect_form(d)

        for d in self.store.items.values():
            if d.orig_name is not None and d.name != d.orig_name:
                self.normalize_self_quantity_refs(d)

        renamed = [
            (d.orig_name, d.name) for d in self.store.items.values()
            if d.orig_name is not None and d.name != d.orig_name
        ]
        if renamed or self.store.deleted:
            lines = ["以下改动不会自动同步到剧情/商店/NPC/掉落等其他 XML：", ""]
            for old, new in renamed[:10]:
                lines.append("改名：%s → %s" % (old, new))
            for name in list(self.store.deleted)[:10]:
                lines.append("删除：%s" % name)
            if len(renamed) + len(self.store.deleted) > 20:
                lines.append("…")
            lines.append("")
            lines.append("是否继续保存？")
            if not messagebox.askyesno("确认保存", "\n".join(lines)):
                return

        issues = self.collect_issues()
        errors = [x for x in issues if x[0] == "错误"]
        if errors:
            preview = "\n".join("· %s：%s" % (x[1], x[2]) for x in errors[:12])
            if len(errors) > 12:
                preview += "\n… 共 %d 条错误" % len(errors)
            messagebox.showerror("无法保存", "请先修正以下错误：\n\n" + preview)
            return
        try:
            self.store.save(self.resource_additions, self.index_additions)
        except Exception as exc:
            traceback.print_exc()
            messagebox.showerror("保存失败", str(exc))
            return
        self.resource_additions = {}
        self.index_additions = {}
        self.refresh_list()
        self.clear_detail()
        self.select_first()
        self.set_status("已保存。原文件已备份为 items.xml / resource.xml / index.xml 的 .bak_*")
        self.update_title()

    def reload_all(self):
        if self.has_changes():
            ans = messagebox.askyesnocancel("未保存的修改", "是否先保存当前修改？")
            if ans is None:
                return
            if ans:
                self.save()
                if self.has_changes():
                    return
        try:
            self.store.load()
        except Exception as exc:
            messagebox.showerror("重新载入失败", str(exc))
            return
        self.resource_additions = {}
        self.index_additions = {}
        self.current_key = None
        self.refresh_list()
        self.clear_detail()
        self.select_first()
        self.set_status("已重新载入 items.xml")
        self.update_title()

    def choose_mod(self):
        directory = filedialog.askdirectory(title="选择包含 Scripts/items.xml 的 MOD 目录",
                                            initialdir=str(self.store.mod_dir))
        if not directory:
            return
        mod_dir = Path(directory)
        if not (mod_dir / "Scripts" / "items.xml").is_file():
            messagebox.showerror("目录无效", "该目录下没有 Scripts/items.xml")
            return
        if self.has_changes():
            ans = messagebox.askyesnocancel("未保存的修改", "是否先保存当前修改？")
            if ans is None:
                return
            if ans:
                self.save()
                if self.has_changes():
                    return
        try:
            store = ItemStore(mod_dir)
            store.load()
        except Exception as exc:
            messagebox.showerror("载入失败", str(exc))
            return
        self.store = store
        self.resource_additions = {}
        self.index_additions = {}
        self.current_key = None
        self.path_label.configure(text=str(mod_dir))
        self.pic_combo.configure(values=self.store.icon_keys())
        self.refresh_list()
        self.clear_detail()
        self.select_first()
        self.set_status("已载入：%s" % mod_dir)
        self.update_title()

    def has_changes(self):
        if self.resource_additions or self.index_additions:
            return True
        if self.store.deleted:
            return True
        for key, d in self.store.items.items():
            if d.orig_name is None or key in self.store.dirty:
                return True
        return False

    def on_close(self):
        if self.has_changes():
            ans = messagebox.askyesnocancel("退出", "有未保存的修改，是否保存？")
            if ans is None:
                return
            if ans:
                self.save()
                if self.has_changes():
                    return
        self.root.destroy()

    # ---------------- 状态 ----------------

    def set_status(self, text):
        self.status_var.set(text)

    def update_status(self):
        if not self.current_key:
            self.set_status("")
            return
        d = self.store.items[self.current_key]
        state = "未保存" if (self.current_key in self.store.dirty or
                             d.orig_name is None or
                             d.name != d.orig_name) else "已保存"
        limit = item_limit_text(d)
        suffix = (" ｜ 数量条件 %s" % limit) if limit else ""
        self.set_status("物品：%s ｜ 类型 %s ｜ %s ｜ trigger %d ｜ require %d%s" % (
            d.name, type_display(d.type), state, len(d.triggers), len(d.requires), suffix))

    def update_title(self):
        mark = " *" if self.has_changes() else ""
        self.root.title("金庸群侠传X MOD 物品编辑器 - %s%s" % (self.store.mod_dir.name, mark))


# ---------------------------------------------------------------- 自检

def run_selftest(mod_dir: Path) -> int:
    print("MOD:", mod_dir)
    store = ItemStore(mod_dir)
    store.load()
    print("items:", len(store.items), "resources:", len(store.resource_map),
          "index:", len(store.index_map))
    if not store.items:
        print("FAIL: no items")
        return 1

    # 序列化往返
    bad = 0
    for key, d in store.items.items():
        xml = store.serialize_item(d, nl="\n").strip()
        try:
            e = ET.fromstring(xml)
            assert e.get("name") == d.name
            assert len(e.findall("trigger")) == len(d.triggers)
            assert len(e.findall("require")) == len(d.requires)
        except Exception as exc:
            bad += 1
            if bad <= 5:
                print("serialize fail:", d.name, exc)
    print("roundtrip errors:", bad)

    # 临时目录保存回归
    with tempfile.TemporaryDirectory() as td:
        tmp_mod = Path(td)
        (tmp_mod / "Scripts").mkdir()
        shutil.copy2(store.items_path, tmp_mod / "Scripts" / "items.xml")
        if store.resource_path.is_file():
            shutil.copy2(store.resource_path, tmp_mod / "Scripts" / "resource.xml")
        if store.index_path.is_file():
            shutil.copy2(store.index_path, tmp_mod / "index.xml")
        t = ItemStore(tmp_mod)
        t.load()
        n = len(t.items)
        first_key = next(iter(t.items))
        first = t.items[first_key]
        first.desc += "[SELFTEST]"
        t.dirty.add(first_key)

        add = ItemData(name="__SELFTEST_ITEM__", type="0", level="1", cd="0",
                       price="1", drop=False, pic="物品.百宝袋", desc="自检物品",
                       triggers=[{"name": "attribute", "argvs": "身法,1", "lv": ""}],
                       requires=[{"name": "level", "argvs": "1"}])
        t.items["__new_1"] = add
        t.dirty.add("__new_1")

        existing_keys = [k for k, x in t.items.items() if x.orig_name is not None]
        if existing_keys:
            last = t.items.pop(existing_keys[-1])
            t.deleted.add(last.orig_name)

        t.save({}, {"Items/__selftest__.png": (64, 48)})
        t2 = ItemStore(tmp_mod)
        t2.load()
        assert len(t2.items) == n, (len(t2.items), n)
        names = [x.name for x in t2.items.values()]
        assert "__SELFTEST_ITEM__" in names
        assert t2.items[first_key].desc.endswith("[SELFTEST]")
        assert any(p.name.startswith("items.xml.bak_") for p in (tmp_mod / "Scripts").iterdir())
        ET.fromstring((tmp_mod / "Scripts" / "items.xml").read_text(encoding="utf-8"))
        if store.index_path.is_file():
            assert t2.index_map.get("Items/__selftest__.png") == ("64", "48")

    print("selftest:", "PASS" if bad == 0 else "FAIL")
    return 0 if bad == 0 else 1


# ---------------------------------------------------------------- 入口

def main():
    parser = argparse.ArgumentParser(description="MOD 物品编辑器")
    parser.add_argument("--mod", help="MOD 目录（包含 Scripts/items.xml）")
    parser.add_argument("--selftest", action="store_true", help="运行数据层自检")
    args = parser.parse_args()

    mod_dir = Path(args.mod).resolve() if args.mod else DEFAULT_MOD_DIR
    if args.selftest:
        return run_selftest(mod_dir)

    if not (mod_dir / "Scripts" / "items.xml").is_file():
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("找不到 MOD", "默认 MOD 目录无效：\n%s\n\n请选择包含 Scripts/items.xml 的目录。" % mod_dir)
        chosen = filedialog.askdirectory(title="选择 MOD 目录")
        root.destroy()
        if not chosen:
            return 1
        mod_dir = Path(chosen)

    root = tk.Tk()
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        import tkinter.font as tkfont
        tkfont.nametofont("TkDefaultFont").configure(family="Microsoft YaHei UI", size=9)
        tkfont.nametofont("TkTextFont").configure(family="Microsoft YaHei UI", size=9)
        style.configure("Treeview", rowheight=24)
    except Exception:
        pass

    try:
        App(root, mod_dir)
    except Exception as exc:
        traceback.print_exc()
        messagebox.showerror("启动失败", str(exc))
        root.destroy()
        return 1

    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
