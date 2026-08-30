"""H07 统计分析业务：隐患类型分布 / 整改率 / 趋势。

SQLAlchemy func.count + group_by 聚合，不加载全表。
"""
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.models.hazard import Hazard
from src.schemas.stats import HazardStatsOut, TrendPoint

"""
StatsService: 统计分析业务层，提供隐患统计分析功能。
返回HazardStatsOut。
"""

class StatsService:
    @staticmethod  # static method: 静态方法，不依赖实例属性
    def hazard_stats(db: Session) -> HazardStatsOut:
        total = db.scalar(select(func.count()).select_from(Hazard)) or 0
        """
        注释文档:
        scalar 查询总数，避免加载全表。
        func.count() 聚合函数，返回指定列的行数。
        select() 构建 SELECT 语句，指定要查询的列。
        from_() 指定要查询的表。
        where() 筛选条件，返回符合条件的行。
        """
        # 等级分布（H07：隐患类型分布）
        by_level = dict(
            db.execute(select(Hazard.level, func.count()).group_by(Hazard.level)).all()
        )
        """
        注释文档:
        execute() 执行 SQL 语句，返回结果集。
        """

        # 类型分布 Top-10（hazard_type 可空，NULL 归为"未分类"）
        by_type_rows = db.execute(
            select(Hazard.hazard_type, func.count())
            .group_by(Hazard.hazard_type)
            .order_by(func.count().desc())
            .limit(10)
        ).all()
        by_type = {k or "未分类": v for k, v in by_type_rows}

        # 整改率 = 已闭环 / 总数
        closed = db.scalar(
            select(func.count()).select_from(Hazard).where(Hazard.status == "已闭环")
        ) or 0
        rectify_rate = round(closed / total, 4) if total else 0.0

        # 未闭环数（待处理/处理中/待验收/已驳回）
        pending = total - closed

        # 近 7 天趋势（按 created_at 日期分组）
        start = date.today() - timedelta(days=6)
        created_counts = dict(
            db.execute(
                select(func.date(Hazard.created_at), func.count())
                .where(Hazard.created_at >= start)
                .group_by(func.date(Hazard.created_at))
            ).all()
        )
        closed_counts = dict(
            db.execute(
                select(func.date(Hazard.updated_at), func.count())
                .where(Hazard.status == "已闭环", Hazard.updated_at >= start)
                .group_by(func.date(Hazard.updated_at))
            ).all()
        )
        trend = [
            TrendPoint(
                date=str(start + timedelta(days=i)),
                created=int(created_counts.get(start + timedelta(days=i), 0)),
                closed=int(closed_counts.get(start + timedelta(days=i), 0)),
            )
            for i in range(7)
        ]

        return HazardStatsOut(
            total=total,
            by_level=by_level,
            by_type=by_type,
            rectify_rate=rectify_rate,
            pending=pending,
            trend=trend,
        )
