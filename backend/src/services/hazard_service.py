"""隐患业务（H01–H06）：上报 / 列表 / 详情 / 派单 / 整改 / 验收。

核心设计：
- **状态机由 service 强制流转**（PRD）：待处理 →处理中→ 待验收 →已闭环/已驳回，已驳回→处理中；
- 每一步写 hazard_timeline（H03 时间线），关键流转联动 H08 通知（Redis 队列，优雅降级）；
- 非法流转一律 400，权限不足 403（router 层 RBAC + service 层 assignee 校验）。
"""
import json
import logging
from typing import Optional

from fastapi import HTTPException, status 
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.models.hazard import Hazard, HazardTimeline
from src.models.user import User
from src.schemas.hazard import (
    AcceptRequest,
    AssignRequest,
    HazardCreate,
    HazardListOut,
    HazardOut,
    RectifyRequest,
)
from src.services.notification_service import (
    NotificationService,
    TYPE_ACCEPT,
    TYPE_FINAL,
    TYPE_RECTIFY,
    TYPE_REPORT,
)

logger = logging.getLogger(__name__)

# 合法状态流转表：当前状态 -> 允许的下一个状态集合
VALID_TRANSITIONS: dict[str, set[str]] = {
    "待处理": {"处理中"},          # H04 派单
    "处理中": {"待验收"},          # H05 整改反馈
    "待验收": {"已闭环", "已驳回"},  # H06 验收通过/驳回
    "已驳回": {"处理中"},          # 重新派单/处理
}


class HazardService:
    # ---------- H01 上报 ----------

    @staticmethod
    def create(db: Session, data: HazardCreate, user_id: int) -> Hazard:
        hazard = Hazard(
            title=data.title,
            description=data.description,
            level=data.level,
            hazard_type=data.hazard_type,
            location=data.location,
            status="待处理",
            reporter_id=user_id,
            image_urls=json.dumps(data.image_urls, ensure_ascii=False) if data.image_urls else None,
        )
        db.add(hazard)
        db.flush()
        HazardService._timeline(db, hazard.id, "上报", user_id, note="提交隐患上报")
        db.commit()
        db.refresh(hazard)
        # H08：接收上报 → 通知全部管理员（有人上报新隐患，待派单）
        admins = db.execute(select(User.id).where(User.role == "admin")).scalars().all()
        for admin_id in admins:
            NotificationService.push(
                admin_id,
                f"【接收上报】用户 #{user_id} 上报新隐患：{hazard.title}（隐患 #{hazard.id}），请及时派单处理",
                hazard_id=hazard.id,
                msg_type=TYPE_REPORT,
            )
        return hazard 

    # ---------- H02 列表（分页/筛选/排序） ----------

    @staticmethod
    def list_hazards(
        db: Session,
        page: int = 1,
        page_size: int = 10,
        status_filter: Optional[str] = None,
        level: Optional[str] = None,
        hazard_type: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        sort: str = "created_at_desc",
        user_id: Optional[int] = None,
        only_mine: bool = False,
        rectified_by: Optional[int] = None,   # 个人主页：我整改的（=被派单人）
        reviewed_by: Optional[int] = None,    # 个人主页：我审批的（验收操作人）
    ) -> HazardListOut:
        q = select(Hazard)
        if only_mine and user_id is not None:
            # "我的隐患"：上报人 / 责任人 / 派发人（时间线"派单"操作人）任一等于当前用户
            assigned_as_assigner = select(HazardTimeline.hazard_id).where(
                HazardTimeline.operator_id == user_id,
                HazardTimeline.action == "派单",
            )
            q = q.where(
                (Hazard.reporter_id == user_id)
                | (Hazard.assignee_id == user_id)
                | (Hazard.id.in_(assigned_as_assigner))
            )
        if rectified_by is not None:
            # 我整改的：整改反馈人 = 被派单人（H05 校验 assignee_id == 提交人）
            q = q.where(Hazard.assignee_id == rectified_by)
        if reviewed_by is not None:
            # 我审批的：验收操作人（H06 action 含"验收"）出现在该隐患时间线
            reviewed_hazard_ids = select(HazardTimeline.hazard_id).where(
                HazardTimeline.operator_id == reviewed_by,
                HazardTimeline.action.in_(["验收通过", "验收驳回"]),
            )
            q = q.where(Hazard.id.in_(reviewed_hazard_ids))
        if status_filter:
            q = q.where(Hazard.status == status_filter)
        if level:
            q = q.where(Hazard.level == level)
        if hazard_type:
            q = q.where(Hazard.hazard_type == hazard_type)
        if start:
            q = q.where(Hazard.created_at >= start)
        if end:
            q = q.where(Hazard.created_at <= end)

        total = db.scalar(select(func.count()).select_from(q.subquery())) or 0

        order_map = {
            "created_at_desc": Hazard.created_at.desc(),
            "created_at_asc": Hazard.created_at.asc(),
            "level_desc": Hazard.level.desc(),
            "updated_at_desc": Hazard.updated_at.desc(),
        }
        q = q.order_by(order_map.get(sort, Hazard.created_at.desc()))
        items = db.scalars(
            q.offset((page - 1) * page_size).limit(page_size)
        ).all()

        return HazardListOut(
            total=total,
            page=page,
            page_size=page_size,
            items=[HazardOut.model_validate(h) for h in items],
        )

    # ---------- H03 详情 ----------

    @staticmethod
    def detail(db: Session, hazard_id: int) -> Hazard:
        hazard = db.get(Hazard, hazard_id)
        if not hazard:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="隐患不存在")
        return hazard

    @staticmethod
    def timeline(db: Session, hazard_id: int) -> list[HazardTimeline]:
        return list(
            db.scalars(
                select(HazardTimeline)
                .where(HazardTimeline.hazard_id == hazard_id)
                .order_by(HazardTimeline.created_at.asc())
            )
        )

    # ---------- H04 派单 ----------

    @staticmethod
    def assign(db: Session, hazard_id: int, data: AssignRequest, operator: dict) -> Hazard:
        hazard = HazardService.detail(db, hazard_id)
        HazardService._ensure_transition(hazard, "处理中", "派单")
        hazard.assignee_id = data.assignee_id
        hazard.due_date = data.due_date
        hazard.status = "处理中"
        HazardService._timeline(
            db, hazard.id, "派单", operator["id"],
            note=f"指派给用户 {data.assignee_id}" + (f"，期限 {data.due_date:%Y-%m-%d}" if data.due_date else ""),
        )
        db.commit()
        db.refresh(hazard)
        # H08：通知责任人（带 hazard_id 供前端跳转）
        NotificationService.push(
            data.assignee_id,
            f"【隐患整改】您有一条整改任务：{hazard.title}（隐患 #{hazard.id}）",
            hazard_id=hazard.id,
        )
        return hazard

    # ---------- H05 整改反馈 ----------

    @staticmethod
    def rectify(db: Session, hazard_id: int, data: RectifyRequest, user: dict) -> Hazard:
        hazard = HazardService.detail(db, hazard_id)
        # 权限：admin 或 被指派责任人
        if user["role"] != "admin" and hazard.assignee_id != user["id"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅责任人本人可提交整改反馈")
        HazardService._ensure_transition(hazard, "待验收", "整改反馈")
        hazard.rectify_measures = data.rectify_measures
        hazard.rectify_images = (
            json.dumps(data.rectify_images, ensure_ascii=False) if data.rectify_images else None
        )
        hazard.status = "待验收"
        HazardService._timeline(db, hazard.id, "整改反馈", user["id"], note=data.rectify_measures[:100])
        db.commit()
        db.refresh(hazard)
        # H08：通知上报人「待验收」+ 通知管理员「收到整改反馈」
        NotificationService.push(
            hazard.reporter_id,
            f"【隐患待验收】您上报的隐患已整改完成：{hazard.title}（隐患 #{hazard.id}）",
            hazard_id=hazard.id,
            msg_type=TYPE_RECTIFY,
        )
        # 管理员收到整改反馈（可验收）
        admins = db.execute(select(User.id).where(User.role == "admin")).scalars().all()
        for admin_id in admins:
            NotificationService.push(
                admin_id,
                f"【整改反馈】隐患 {hazard.title}（#{hazard.id}）已整改完成，请及时验收",
                hazard_id=hazard.id,
                msg_type=TYPE_RECTIFY,
            )
        return hazard

    # ---------- H06 验收 ----------

    @staticmethod
    def accept(db: Session, hazard_id: int, data: AcceptRequest, operator: dict) -> Hazard:
        hazard = HazardService.detail(db, hazard_id)
        target = "已闭环" if data.approved else "已驳回"
        HazardService._ensure_transition(hazard, target, "验收")
        hazard.status = target
        action = "验收通过" if data.approved else "验收驳回"
        HazardService._timeline(db, hazard.id, action, operator["id"], note=data.note)
        db.commit()
        db.refresh(hazard)
        # H08：通知责任人与上报人「验收结果」+ 通知安全员/管理员 + 最终处理结果广播
        note_text = f"验收{'通过' if data.approved else '驳回'}"
        # ① 责任人与上报人（当事人）
        for uid in {hazard.assignee_id, hazard.reporter_id} - {None}:
            NotificationService.push(
                uid,
                f"【隐患{note_text}】{hazard.title}（隐患 #{hazard.id}）" + (f"：{data.note}" if data.note else ""),
                hazard_id=hazard.id,
                msg_type=TYPE_ACCEPT,
            )
        # ② 安全员/管理员（验收结果通知，当事人除外）
        managers = db.execute(
            select(User.id).where(User.role.in_(["admin", "safety_officer"]))
        ).scalars().all()
        for mid in set(managers) - {hazard.assignee_id, hazard.reporter_id}:
            NotificationService.push(
                mid,
                f"【验收结果】隐患 {hazard.title}（#{hazard.id}）{note_text}" + (f"：{data.note}" if data.note else ""),
                hazard_id=hazard.id,
                msg_type=TYPE_ACCEPT,
            )
        # ③ 最终处理结果（上报人/责任人/安全员/管理员全员）
        final_text = f"【最终处理结果】隐患 {hazard.title}（#{hazard.id}）{target}" + (f"：{data.note}" if data.note else "")
        for uid in {hazard.assignee_id, hazard.reporter_id} | set(managers) - {None}:
            NotificationService.push(
                uid,
                final_text,
                hazard_id=hazard.id,
                msg_type=TYPE_FINAL,
            )
        return hazard

    # ---------- 内部工具 ----------

    @staticmethod
    def _ensure_transition(hazard: Hazard, to_status: str, action: str) -> None:
        """状态机强校验：非法流转抛 400。"""
        allowed = VALID_TRANSITIONS.get(hazard.status, set())
        if to_status not in allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"当前状态[{hazard.status}]不允许执行[{action}]（可流转至 {sorted(allowed)}）",
            )

    @staticmethod
    def _timeline(db: Session, hazard_id: int, action: str, operator_id: int, note: Optional[str] = None) -> None:
        db.add(
            HazardTimeline(
                hazard_id=hazard_id,
                action=action,
                operator_id=operator_id,
                note=note,
            )
        )
 