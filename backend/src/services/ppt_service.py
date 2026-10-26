"""PPT 工坊业务层（v2.0 大点 02 重写）。

两条链路（裁定 1/2/3）：
- **默认链路：PPT 生成 Agent** —— `POST /api/ppt/{id}/agent`（SSE），逐阶段产出与自检；
  业务逻辑在 `src/ai/ppt_agent.py`，本模块只负责：任务状态机、规格校验、模板解析、落盘登记。
- **可选链路：直连 PPT LLM** —— `src/ai/ppt_llm.py` 只有抽象位，当前无实现（百度已移除）。

另保留一个"快速模式"：`POST /api/ppt/{id}/generate` —— 不跑 Agent，直接用 `ppt_local`
渲染大纲（无配图、无自检），给调试与 OfficeCLI 缺失环境用（验证项 7）。

产物与目录（裁定 4）：
- 新品一律落 `user_data/ppt/u{uid}/tasks/t{tid}/out/`（`src/core/paths.py` 唯一事实源）；
- 历史产物仍在 `ppt_files/`，下载端点双路径兼容，不迁移。
"""
import json
import logging
import re
import shutil
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.ai.llm_client import llm_client
from src.ai.ppt_local import generate_local_ppt
from src.ai.prompts import render
from src.core import paths
from src.core.config import settings
from src.core.paths import ensure_dir, safe_name
from src.models import PptTask
from src.schemas.ppt import PptAgentSpec, PptCreate
from src.services import ppt_progress

logger = logging.getLogger(__name__)

#: 历史产物目录（大点 00 的命名口径）。新链路不再写这里，但下载要兼容读取。
PPT_FILES_DIR = Path("ppt_files")
STATUS_DRAFT, STATUS_GENERATING, STATUS_DONE, STATUS_FAILED = "草稿", "生成中", "已完成", "失败"
BUILTIN_TEMPLATE = "builtin_simple"


class PptService:
    # ---------- P01 大纲生成 ----------
    @staticmethod
    def create_task(db: Session, user_id: int, data: PptCreate) -> PptTask:
        """新建任务：LLM 生成大纲 JSON → 落库（状态=草稿）。

        `data.md_content` 非空时走「按文档生成大纲」（prompts.yml ppt_outline_md 场景）。
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
        task = PptService._get_owned(db, user_id, task_id)
        PptService._reap_if_stale(task)
        if task.status == STATUS_GENERATING:
            raise HTTPException(status_code=400, detail="生成中不可编辑大纲")
        task.outline = json.dumps(outline, ensure_ascii=False)
        if task.status == STATUS_FAILED:
            task.status = STATUS_DRAFT
        db.commit()
        db.refresh(task)
        return task

    # ---------- Agent 链路（默认） ----------
    @staticmethod
    def start_agent(db: Session, user_id: int, task_id: int, spec_in: PptAgentSpec) -> dict:
        """抢占任务并准备规格；返回给 SSE 生成器用的 spec（已含沙箱路径）。"""
        if not settings.PPT_AGENT_ENABLED:
            raise HTTPException(status_code=503, detail="PPT Agent 已关闭（PPT_AGENT_ENABLED=false）")

        task = PptService._get_owned(db, user_id, task_id)
        PptService._reap_if_stale(task)
        if task.status == STATUS_GENERATING:
            raise HTTPException(status_code=409, detail="该任务正在生成中，请等待完成")

        if not ppt_progress.acquire_lock(task_id):
            raise HTTPException(status_code=409, detail="该任务正在生成中（已有生成进程），请稍后再试")

        task.status = STATUS_GENERATING
        task.template = spec_in.template or task.template or BUILTIN_TEMPLATE
        db.commit()
        ppt_progress.set_progress(task_id, {"stage": "S0", "progress": 0, "msg": "已进入队列"})

        try:
            spec = PptService._build_spec(task, spec_in)
        except Exception:
            ppt_progress.release_lock(task_id)
            task.status = STATUS_DRAFT
            db.commit()
            raise

        drafts = ensure_dir(paths.task_dir(user_id, task_id, "drafts"))
        (drafts / "spec.json").write_text(
            json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return spec

    @staticmethod
    def finish_agent(db: Session, task_id: int, result: dict) -> None:
        task = db.get(PptTask, task_id)
        if not task:
            return
        task.file_path = result.get("file")
        task.status = STATUS_DONE
        db.commit()
        ppt_progress.release_lock(task_id)

    @staticmethod
    def fail_agent(db: Session, task_id: int, message: str) -> None:
        task = db.get(PptTask, task_id)
        if task:
            task.status = STATUS_FAILED
            db.commit()
        ppt_progress.release_lock(task_id)
        logger.error("PPT Agent 失败 task=%s: %s", task_id, message)

    @staticmethod
    def _build_spec(task: PptTask, spec_in: PptAgentSpec) -> dict:
        """规格归一：主题、页数、字数、模板（含复制副本到沙箱）。"""
        pages = max(3, min(int(spec_in.pages or 10), settings.PPT_AGENT_PAGE_LIMIT))
        requirements = (spec_in.requirements or "").strip()
        if spec_in.md_content:
            requirements = (requirements + "\n\n" + spec_in.md_content).strip()

        template_id = (spec_in.template or task.template or BUILTIN_TEMPLATE).strip()
        template_path: str | None = None
        if template_id and template_id != BUILTIN_TEMPLATE:
            try:
                from src.services.ppt_template_service import get_custom_template_path

                src = get_custom_template_path(template_id)
                tpl_dir = ensure_dir(paths.templates_dir(task.user_id))
                dest = tpl_dir / f"t{task.id}_{safe_name(src.name)}"
                if not dest.exists() or dest.stat().st_size != src.stat().st_size:
                    shutil.copyfile(src, dest)
                template_path = str(dest)
            except HTTPException:
                raise
            except Exception as exc:  # noqa: BLE001 —— 模板复制失败不致命，退回内置配色
                logger.warning("模板副本准备失败(%s)，使用内置配色: %s", template_id, exc)

        return {
            "topic": (spec_in.topic or task.topic).strip(),
            "requirements": requirements,
            "pages": pages,
            "words_per_page": spec_in.words_per_page,
            "allow_overflow": bool(spec_in.allow_overflow),
            "template": template_id,
            "template_path": template_path,
        }

    # ---------- 快速模式（无 Agent，直接渲染大纲） ----------
    @staticmethod
    def generate(
        db: Session,
        user_id: int,
        task_id: int,
        outline_override: dict | None = None,
        template_override: str | None = None,
        use_local: bool = True,
    ) -> PptTask:
        """同步渲染大纲（旧接口保留）。不再有任何"直连 PPT LLM"分支（百度已移除）。"""
        task = PptService._get_owned(db, user_id, task_id)
        PptService._reap_if_stale(task)
        if task.status == STATUS_GENERATING:
            raise HTTPException(status_code=400, detail="任务正在生成中")

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
            dest = PptService._sandbox_dest(task)
            ensure_dir(dest.parent)
            tpl_path = PptService._template_path(task)
            generate_local_ppt(outline, task.topic, dest, template_path=tpl_path)
            task.file_path = str(dest)
            task.status = STATUS_DONE
            db.commit()
            db.refresh(task)
            return task
        except Exception as exc:  # noqa: BLE001
            logger.error("PPT 快速生成失败 task=%s: %s", task_id, exc)
            task.status = STATUS_FAILED
            db.commit()
            raise HTTPException(status_code=502, detail=f"PPT 生成失败：{exc}") from exc

    @staticmethod
    def _template_path(task: PptTask) -> Path | None:
        if not task.template or task.template == BUILTIN_TEMPLATE:
            return None
        try:
            from src.services.ppt_template_service import get_custom_template_path

            return get_custom_template_path(task.template)
        except Exception:  # noqa: BLE001
            return None

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
        task = PptService._get_owned(db, user_id, task_id)
        PptService._reap_if_stale(task)
        return task

    @staticmethod
    def delete_task(db: Session, user_id: int, task_id: int) -> None:
        task = PptService._get_owned(db, user_id, task_id)
        if task.status == STATUS_GENERATING and ppt_progress.lock_held(task_id):
            raise HTTPException(status_code=400, detail="生成中不可删除")
        if task.file_path:
            try:
                from src.ai import ppt_tools

                ppt_tools.release_file(task.file_path)   # 先释放 officecli 常驻进程
                p = Path(task.file_path)
                if p.is_file():
                    p.unlink()
            except OSError:  # pragma: no cover
                logger.warning("删除 PPT 文件失败: %s", task.file_path)
        # 清掉整个任务工作区（模板副本在账号级目录，按账号复用，不动）
        paths.cleanup_task(user_id, task_id, keep_out=False)
        ppt_progress.clear_progress(task_id)
        db.delete(task)
        db.commit()

    # ---------- 下载（P07，仅本人） ----------
    @staticmethod
    def download_path(db: Session, user_id: int, task_id: int) -> tuple[Path, str]:
        """返回 (产物绝对路径, 下载文件名)。

        双路径（裁定 4）：
        1. 新产物在沙箱 `user_data/ppt/u{uid}/tasks/t{tid}/out/`；
        2. 历史产物在 `ppt_files/`（只取 basename 拼回目录，DB 里即使混进 `../` 也出不去）。
        """
        task = PptService._get_owned(db, user_id, task_id)
        if task.status != STATUS_DONE or not task.file_path:
            raise HTTPException(
                status_code=400, detail=f"PPT 尚未生成（当前状态: {task.status}），无法下载"
            )

        raw = Path(task.file_path)
        if raw.is_absolute() or raw.parts[:1] == ("user_data",):
            candidate = raw.resolve()
            if paths.is_within(candidate, paths.SANDBOX_ROOT.resolve()) and candidate.is_file():
                return candidate, f"{task.topic}.pptx"
            raise HTTPException(status_code=404, detail="产物文件已丢失，请重新生成")

        legacy = (PPT_FILES_DIR / raw.name).resolve()
        root = PPT_FILES_DIR.resolve()
        if root not in legacy.parents:
            raise HTTPException(status_code=404, detail="产物路径非法")
        if not legacy.is_file():
            raise HTTPException(status_code=404, detail="产物文件已丢失，请重新生成")
        return legacy, f"{task.topic}.pptx"

    # ---------- 工作区（裁定 10：用户自清理） ----------
    @staticmethod
    def workspace(db: Session, user_id: int, task_id: int) -> dict:
        """列出任务工作区（前端"我的工作区"用）。"""
        PptService._get_owned(db, user_id, task_id)
        out: dict = {"shots": [], "images": [], "drafts": [], "logs": [], "out": []}
        for kind in ("images", "shots", "drafts", "logs", "out"):
            d = paths.task_dir(user_id, task_id, kind)
            out[kind] = sorted(p.name for p in d.iterdir() if p.is_file()) if d.exists() else []
        return out

    @staticmethod
    def cleanup_workspace(db: Session, user_id: int, task_id: int, keep_out: bool = True) -> dict:
        PptService._get_owned(db, user_id, task_id)
        return paths.cleanup_task(user_id, task_id, keep_out=keep_out)

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
    def _reap_if_stale(task: PptTask) -> None:
        """卡死治理（裁定 12）：进程中断后任务会永久停"生成中"，这里在读取时兜底置失败。"""
        if task.status != STATUS_GENERATING:
            return
        if ppt_progress.lock_held(task.id):
            return
        progress = ppt_progress.get_progress(task.id)
        if progress and progress.get("status") == "done":
            return
        logger.warning("PPT 任务 %s 状态为生成中但无生成进程，置为失败", task.id)
        task.status = STATUS_FAILED

    @staticmethod
    def _artifact_dest(task: PptTask) -> Path:
        """历史产物路径（`ppt_files/u{uid}_t{tid}_{主题}.pptx`）。

        保留给"把老产物写回 ppt_files"的场景与隔离回归测试；新链路用 `_sandbox_dest`。
        """
        stem = PptService._safe_filename(task.topic)[:60]
        return PPT_FILES_DIR / f"u{task.user_id}_t{task.id}_{stem}.pptx"

    @staticmethod
    def _sandbox_dest(task: PptTask) -> Path:
        return paths.out_path(task.user_id, task.id, task.topic)

    @staticmethod
    def _safe_filename(topic: str) -> str:
        safe = re.sub(r'[\\/:*?"<>|]', "_", topic).strip()
        return (safe or "ppt")[:80]
