-- ============================================================
-- 蜀道智能安全助手 — 数据库建表脚本（P3 线维护）
-- 库: shudao_safety ｜ 字符集: utf8mb4 ｜ 引擎: InnoDB
--
-- 说明:
--   1. 本脚本与 backend/src/models/ 下 ORM 模型严格对齐（用 scripts/db_verify.py 校验）;
--   2. 应用启动不自动建表, 表结构以此文件为准, 变更需走 P3 同步;
--   3. 执行: mysql -h127.0.0.1 -uroot -p123456 shudao_safety < database/schema.sql
--   4. 初始化数据: 管理员账号用 backend/scripts/init_admin.py 创建（不写入 SQL 避免明文密码）。
-- ============================================================

CREATE TABLE IF NOT EXISTS `users` (
  `id` int NOT NULL AUTO_INCREMENT,
  `username` varchar(64) NOT NULL,
  `hashed_password` varchar(255) NOT NULL,
  `role` enum('admin','safety_officer','employee') NOT NULL,
  `real_name` varchar(64) DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_users_username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `hazards` (
  `id` int NOT NULL AUTO_INCREMENT,
  `title` varchar(128) NOT NULL,
  `description` text NOT NULL,
  `level` enum('重大','较大','一般','轻微') NOT NULL,
  `hazard_type` varchar(64) DEFAULT NULL,
  `location` varchar(128) DEFAULT NULL,
  `status` enum('待处理','处理中','待验收','已闭环','已驳回') NOT NULL,
  `reporter_id` int NOT NULL,
  `assignee_id` int DEFAULT NULL,
  `due_date` datetime DEFAULT NULL,
  `image_urls` text,
  `rectify_measures` text,
  `rectify_images` text,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_hazards_reporter_id` (`reporter_id`),
  KEY `ix_hazards_assignee_id` (`assignee_id`),
  CONSTRAINT `hazards_ibfk_1` FOREIGN KEY (`reporter_id`) REFERENCES `users` (`id`),
  CONSTRAINT `hazards_ibfk_2` FOREIGN KEY (`assignee_id`) REFERENCES `users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `hazard_timeline` (
  `id` int NOT NULL AUTO_INCREMENT,
  `hazard_id` int NOT NULL,
  `action` varchar(64) NOT NULL,
  `operator_id` int DEFAULT NULL,
  `note` text,
  `created_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `operator_id` (`operator_id`),
  KEY `ix_hazard_timeline_hazard_id` (`hazard_id`),
  CONSTRAINT `hazard_timeline_ibfk_1` FOREIGN KEY (`hazard_id`) REFERENCES `hazards` (`id`),
  CONSTRAINT `hazard_timeline_ibfk_2` FOREIGN KEY (`operator_id`) REFERENCES `users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `conversations` (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_id` int NOT NULL,
  `title` varchar(128) DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_conversations_user_id` (`user_id`),
  CONSTRAINT `conversations_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `chat_messages` (
  `id` int NOT NULL AUTO_INCREMENT,
  `conversation_id` int NOT NULL,
  `role` varchar(16) NOT NULL,
  `content` text NOT NULL,
  `references` text,
  `rating` int DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_chat_messages_conversation_id` (`conversation_id`),
  CONSTRAINT `chat_messages_ibfk_1` FOREIGN KEY (`conversation_id`) REFERENCES `conversations` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `ppt_tasks` (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_id` int NOT NULL,
  `topic` varchar(256) NOT NULL,
  `outline` text,
  `template` varchar(32) NOT NULL,
  `file_path` varchar(512) DEFAULT NULL,
  `status` enum('草稿','生成中','已完成','失败') NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_ppt_tasks_user_id` (`user_id`),
  CONSTRAINT `ppt_tasks_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
