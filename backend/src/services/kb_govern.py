"""贡献文件 · 在线清洗治理（审核通过入库前执行）。

背景：官方采集数据有 scripts/govern_html.py（HTML 治理）+ clean_empty_kb.py（空壳清理）
+ backend/docs/施工安全领域文件鉴别判定标准.md §5 内容质量门槛。
但**用户上传文件走的是独立通道**（md/txt/docx/pdf 解析文本直接入库），
此前没有任何治理——本模块补齐这条链路：

1. clean_text()：HTML 残留剥离（若含标签）→ 控制字符/乱码清理 → 噪声行剔除
   （复用 govern_html 噪声词表精选子集）→ 空白压缩 → 相邻重复行去重；
2. quality_check()：判定标准 §5 在线校验——有效字数 / 乱码比例 / 重复段落比例 /
   HTML 残留检测，全部达标才算"通过清洗治理"（审核通过时才执行，不达标拒绝入库）。

字数门槛决策：判定标准 §5 的 2000 字针对批量采集网页（正文可能在附件）；
用户主动上传的完整文件（单条通知/规程）几百到上千字常见且是完整知识，
故按分档执行：<300 字 = 碎片直接拒绝；300-2000 = 允许但标注"偏短"供审核员参考；
≥2000 = 正常。（如需严格 2000 硬门槛，改 _MIN_HARD 即可）
"""
import logging
import re

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---- 质量门槛（判定标准 §5 在线版） ----
_MIN_HARD = 300       # 清洗后有效字数下限（低于=碎片，拒绝入库）
_MIN_NORMAL = 2000    # 判定标准建议值：低于此标注"偏短"
_MAX_REPEAT_RATIO = 0.4   # 重复段落比例上限（复制粘贴污染）
_MAX_GARBLE_RATIO = 0.05  # 乱码字符比例上限

# 噪声行（复用 govern_html.NOISE_WORDS 精选：页眉页脚/版权/站点导航）
_NOISE_LINES = [
    "首页", "收藏", "打印", "字号", "无障碍", "登录", "注册", "网站地图",
    "联系我们", "设为首页", "加入收藏", "分享到", "备案号", "版权所有",
    "主办单位", "邮编", "网站标识码", "免责声明", "中国政府网", "当前位置",
    "来源：", "点击数", "更新时间", "文档大小", "资料下载", "推荐资料",
    "热门排行", "上一篇", "下一篇", "相关文章", "扫一扫", "手机版",
]
_EXACT_NOISE = {"简", "繁", "EN", "邮箱", "默认", "超大", "登录", "注册"}


def _is_noise_line(line: str) -> bool:
    t = line.strip()
    if not t:
        return False
    if t in _EXACT_NOISE:
        return True
    if len(t) <= 30 and t.endswith(("…", "...", "···")):
        return True
    for n in _NOISE_LINES:
        if n in t:
            return True
    return False


def clean_text(text: str) -> str:
    """清洗治理：HTML 剥离 → 控制字符/乱码 → 噪声行 → 空白压缩 → 相邻重复行去重。"""
    if not text:
        return ""
    # 1. HTML 残留剥离（用户上传的 txt/md 可能粘了网页源码）
    if re.search(r"<[a-zA-Z/][^>]{0,120}>", text):
        soup = BeautifulSoup(text, "html.parser")
        text = soup.get_text("\n")
    # 2. 控制字符 + Unicode 替换字符（乱码常见形态）
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = text.replace("\ufffd", "")
    # 3. 逐行：噪声剔除 + 空白压缩 + 空行过滤
    lines: list[str] = []
    for raw in text.splitlines():
        ln = re.sub(r"\s+", " ", raw).strip()
        if not ln:
            continue
        if _is_noise_line(ln):
            continue
        lines.append(ln)
    # 4. 相邻重复行去重（复制粘贴/分页页头重复）
    dedup: list[str] = []
    for ln in lines:
        if dedup and ln == dedup[-1]:
            continue
        dedup.append(ln)
    return "\n".join(dedup)


def quality_check(text: str, min_chars: int | None = None) -> dict:
    """判定标准 §5 在线校验。返回 {ok, reasons, chars, short, repeat_ratio, garble_ratio}。

    `min_chars`：本次校验的最小有效字数（v2.0 大点 04 起贡献路径传 `KB_CONTRIB_MIN_CHARS=1000`）；
    不传则用历史常量 `_MIN_HARD=300`（官方采集链路与既有测试保持原行为）。

    ok=False 时 reasons 给出具体未达标项（review 接口据此拒绝入库并提示管理员）。
    """
    reasons: list[str] = []
    chars = len(text)
    hard = int(min_chars or _MIN_HARD)
    if chars < hard:
        reasons.append(f"有效正文仅 {chars} 字（低于碎片阈值 {hard} 字）")

    # HTML 残留（清洗后仍有成对标签 = 剥离失败）
    if re.search(r"<[a-zA-Z/][^>]{0,120}>", text):
        reasons.append("存在 HTML 标签残留，无法完整剥离")

    # 重复段落比例（按行统计）
    lines = [l for l in text.splitlines() if l.strip()]
    if lines:
        repeat = len(lines) - len(set(lines))
        repeat_ratio = repeat / len(lines)
        if repeat_ratio > _MAX_REPEAT_RATIO:
            reasons.append(f"重复段落比例 {repeat_ratio:.0%}（超过上限 {_MAX_REPEAT_RATIO:.0%}）")
    else:
        repeat_ratio = 0.0
        reasons.append("无有效段落")

    # 乱码比例（异常字符：CJK 兼容区/私有区/其他罕见的替换形态）
    garbles = len(re.findall(r"[\uE000-\uF8FF\uF900-\uFAFF\ufff0-\uffff]", text))
    garble_ratio = garbles / max(chars, 1)
    if garble_ratio > _MAX_GARBLE_RATIO:
        reasons.append(f"乱码字符比例 {garble_ratio:.1%}（超过上限 {_MAX_GARBLE_RATIO:.0%}）")

    return {
        "ok": not reasons,
        "reasons": reasons,
        "chars": chars,
        "short": hard <= chars < _MIN_NORMAL,  # 偏短但允许（标注供审核员参考）
        "repeat_ratio": round(repeat_ratio, 3),
        "garble_ratio": round(garble_ratio, 4),
    }
