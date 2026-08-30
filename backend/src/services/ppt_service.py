"""PPT 工坊业务层（P01–P07，D4）。

职责：
- P01 大纲生成：LLM 调 prompts.yml ppt_outline → JSON 大纲落库；
- P02 大纲编辑：整篇替换；
- P03 生成：百度 PPT API（主方案）→ 失败降级本地 python-pptx（兜底）；
  同步 PptTask 状态机（草稿→生成中→已完成/失败）+ file_path 落库；
- P04 模板：生成时按 template 映射百度 tpl_id；
- P05 内容填充：百度 gen_mode=1 智能润色 / 本地大纲 points；
- P07 导出：静态托管 /ppt-files/{file_path}。

降级策略（用户决策 2026-08-15）：
- 百度 API 每日 10 次免费；额度用尽/故障时自动走本地方案；
- 调用方（router）可传 use_local 强制本地（调试/演示用）。
"""
import json
import logging
import re
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.ai.llm_client import llm_client
from src.ai.ppt_baidu import PptBaiduError, download_pptx, generate_ppt
from src.ai.ppt_local import generate_local_ppt
from src.ai.prompts import render
from src.core.config import settings
from src.models import PptTask
from src.schemas.ppt import PptCreate

logger = logging.getLogger(__name__)

PPT_FILES_DIR = Path("ppt_files")  # 生成的 pptx 存放目录（相对 backend/，StaticFiles 托管 /ppt-files）
STATUS_DRAFT, STATUS_GENERATING, STATUS_DONE, STATUS_FAILED = "草稿", "生成中", "已完成", "失败"


class PptService:
    # ---------- P01 大纲生成 ----------
    @staticmethod
    def create_task(db: Session, user_id: int, data: PptCreate) -> PptTask:
        """新建任务：LLM 生成大纲 JSON → 落库（状态=草稿）。

        领导优化：data.md_content 非空时走「按文档生成大纲」（prompts.yml ppt_outline_md 场景）。
        """
        try:
            if data.md_content:
                outline = PptService._generate_outline_from_md(data.topic, data.md_content)
            else:
                outline = PptService._generate_outline(data.topic)
        except Exception as exc:  # noqa: BLE001
            logger.error("大纲生成失败 topic=%s: %s", data.topic, exc)
            raise HTTPException(status_code=502, detail=f"大纲生成失败：{exc}") from exc

        task = PptTask(
            user_id=user_id,
            topic=data.topic,
            outline=json.dumps(outline, ensure_ascii=False),
            template=data.template,
            status=STATUS_DRAFT,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return task

    @staticmethod
    def _generate_outline_from_md(topic: str, md_content: str) -> dict:
        """按 .md 文档内容生成大纲（ppt_outline_md 场景），宽容 JSON 解析。"""
        system_prompt = render("ppt_outline_md", topic=topic, md_content=md_content)
        content = llm_client.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请根据文档《{topic}》的内容生成 PPT 大纲。"},
            ],
            temperature=0.3,
        )
        return PptService._parse_outline_json(content)

    @staticmethod
    def _generate_outline(topic: str) -> dict:
        """LLM 生成大纲（prompts.yml ppt_outline 场景），宽容 JSON 解析。"""
        system_prompt = render("ppt_outline", topic=topic)
        content = llm_client.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请为主题《{topic}》生成大纲。"},
            ],
            temperature=0.3,
        )
        return PptService._parse_outline_json(content)

    @staticmethod
    def _parse_outline_json(content: str | None) -> dict:
        """宽容解析 LLM 大纲 JSON：剥 ``` 围栏 → 取首个 { 到末尾 } → 校验 sections。"""
        text = (content or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise RuntimeError(f"LLM 未返回 JSON 大纲: {text[:200]}")
        outline = json.loads(text[start : end + 1])
        if not outline.get("sections"):
            raise RuntimeError("大纲缺少 sections")
        return outline

    # ---------- P02 大纲编辑 ----------
    @staticmethod
    def update_outline(db: Session, user_id: int, task_id: int, outline: dict) -> PptTask:
        """整篇替换大纲（P02）。仅草稿/失败状态可编辑。"""
        task = PptService._get_owned(db, user_id, task_id)
        if task.status == STATUS_GENERATING:
            raise HTTPException(status_code=400, detail="生成中不可编辑大纲")
        task.outline = json.dumps(outline, ensure_ascii=False)
        if task.status == STATUS_FAILED:
            task.status = STATUS_DRAFT
        db.commit()
        db.refresh(task)
        return task

    # ---------- P03 生成（百度主 + 本地兜底） ----------
    @staticmethod
    def generate(
        db: Session,
        user_id: int,
        task_id: int,
        outline_override: dict | None = None,
        template_override: str | None = None,
        use_local: bool = False,
    ) -> PptTask:
        """按大纲生成 PPT（P03）。同步状态机 + file_path。

        主方案：百度 API（PptBaiduError 时降级本地）；
        兜底：本地 python-pptx（ppt_local.generate_local_ppt）。
        """
        task = PptService._get_owned(db, user_id, task_id)
        if task.status == STATUS_GENERATING:
            raise HTTPException(status_code=400, detail="任务正在生成中")

        # 确定大纲与模板（允许请求覆盖）
        if outline_override is not None:
            task.outline = json.dumps(outline_override, ensure_ascii=False)
        if template_override is not None:
            task.template = template_override
        outline = json.loads(task.outline) if task.outline else None
        if not outline or not outline.get("sections"):
            raise HTTPException(status_code=400, detail="大纲为空，请先编辑大纲")

        task.status = STATUS_GENERATING
        db.commit()

        try:
            filename = PptService._safe_filename(task.topic)
            dest = PPT_FILES_DIR / f"{filename}.pptx"
            # 领导优化：自定义模板（非内置 3 套）→ 强制本地生成（百度 API 无自定义模板概念）
            custom_tpl = None
            if not use_local:
                from src.services.ppt_template_service import is_custom_template

                if is_custom_template(task.template):
                    custom_tpl = task.template
                    use_local = True
                    logger.info("自定义模板 %s → 走本地生成", custom_tpl)
            if use_local:
                logger.info("PPT 本地模式生成: task=%s", task_id)
                tpl_path = None
                if custom_tpl:
                    from src.services.ppt_template_service import get_custom_template_path

                    tpl_path = get_custom_template_path(custom_tpl)
                generate_local_ppt(outline, task.topic, dest, template_path=tpl_path)
            else:
                try:
                    pptx_url, _page_count = generate_ppt(
                        outline, task.topic, task.template
                    )
                    download_pptx(pptx_url, dest)
                except PptBaiduError as exc:
                    logger.warning("百度 PPT 失败，降级本地: %s", exc)
                    generate_local_ppt(outline, task.topic, dest)
            task.file_path = str(dest)
            task.status = STATUS_DONE
            db.commit()
            db.refresh(task)
            return task
        except Exception as exc:  # noqa: BLE001
            logger.error("PPT 生成失败 task=%s: %s", task_id, exc)
            task.status = STATUS_FAILED
            db.commit()
            raise HTTPException(status_code=502, detail=f"PPT 生成失败：{exc}") from exc

    # ---------- 列表/详情/删除 ----------
    @staticmethod
    def list_tasks(db: Session, user_id: int, page: int = 1, page_size: int = 10) -> dict:
        total = db.query(PptTask).filter(PptTask.user_id == user_id).count()
        items = (
            db.query(PptTask)
            .filter(PptTask.user_id == user_id)
            .order_by(PptTask.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    @staticmethod
    def get_task(db: Session, user_id: int, task_id: int) -> PptTask:
        return PptService._get_owned(db, user_id, task_id)

    @staticmethod
    def delete_task(db: Session, user_id: int, task_id: int) -> None:
        task = PptService._get_owned(db, user_id, task_id)
        if task.status == STATUS_GENERATING:
            raise HTTPException(status_code=400, detail="生成中不可删除")
        # 清理产物文件（尽力而为）
        if task.file_path:
            p = Path(task.file_path)
            try:
                if p.exists():
                    p.unlink()
            except OSError:  # pragma: no cover
                logger.warning("删除 PPT 文件失败: %s", p)
        db.delete(task)
        db.commit()

    # ---------- 内部 ----------
    @staticmethod
    def _get_owned(db: Session, user_id: int, task_id: int) -> PptTask:
        task = db.get(PptTask, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="PPT 任务不存在")
        if task.user_id != user_id:
            raise HTTPException(status_code=403, detail="无权操作该任务")
        return task

    @staticmethod
    def _safe_filename(topic: str) -> str:
        """主题 → 安全文件名（去非法字符 + 截断）。"""
        safe = re.sub(r'[\\/:*?"<>|]', "_", topic).strip()
        return (safe or "ppt")[:80]
