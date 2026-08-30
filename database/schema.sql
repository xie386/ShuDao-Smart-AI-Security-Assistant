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

-- ------------------------------------------------------------
-- 考试工坊（优化丰富期新增）：试卷生成记录表
-- 业务：提示词/文档输入 → 出卷子智能体生成试卷+参考答案 → 导出 md/docx/pdf
-- 模式对齐 ppt_tasks：状态机（草稿→生成中→已完成/失败）+ file_path 落库
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `exam_papers` (
  `id` int NOT NULL AUTO_INCREMENT,
  `paper_id` varchar(32) NOT NULL,
  `title` varchar(128) NOT NULL,
  `template` varchar(32) NOT NULL DEFAULT 'general',
  `input_type` enum('prompt','document') NOT NULL DEFAULT 'prompt',
  `input_text` text,
  `input_file` varchar(255) DEFAULT NULL,
  `exam_content` longtext,
  `export_format` enum('md','docx','pdf') DEFAULT NULL,
  `file_path` varchar(255) DEFAULT NULL,
  `status` enum('草稿','生成中','已完成','失败') NOT NULL DEFAULT '草稿',
  `error` varchar(255) DEFAULT NULL,
  `created_by` int NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_exam_papers_paper_id` (`paper_id`),
  KEY `ix_exam_papers_created_by` (`created_by`),
  CONSTRAINT `exam_papers_ibfk_1` FOREIGN KEY (`created_by`) REFERENCES `users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---- 知识贡献模块（优化丰富期：全员可上传 → admin 审核 → 审核通过才入向量库）----
CREATE TABLE IF NOT EXISTS `knowledge_contributions` (
  `id` int NOT NULL AUTO_INCREMENT,
  `title` varchar(128) NOT NULL,
  `content` mediumtext NOT NULL,
  `file_intro` text,
  `file_path` varchar(255) DEFAULT NULL,
  `doc_type` enum('laws','procedures','plans','cases') NOT NULL DEFAULT 'laws',
  `contributor_id` int NOT NULL,
  `status` enum('待审核','已入库','已驳回') NOT NULL DEFAULT '待审核',
  `review_note` text,
  `review_by` int DEFAULT NULL,
  `chunk_count` int DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_knowledge_contributions_contributor` (`contributor_id`),
  KEY `ix_knowledge_contributions_status` (`status`),
  CONSTRAINT `knowledge_contributions_ibfk_1` FOREIGN KEY (`contributor_id`) REFERENCES `users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `knowledge_contrib_timeline` (
  `id` int NOT NULL AUTO_INCREMENT,
  `contrib_id` int NOT NULL,
  `action` varchar(64) NOT NULL,
  `operator_id` int DEFAULT NULL,
  `note` text,
  `created_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_knowledge_contrib_timeline_contrib` (`contrib_id`),
  CONSTRAINT `knowledge_contrib_timeline_ibfk_1` FOREIGN KEY (`contrib_id`) REFERENCES `knowledge_contributions` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 个人主页扩展资料（P3 之后新增表；users 表零改动，严格守 P3「勿改表」铁律）
CREATE TABLE IF NOT EXISTS `user_profiles` (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_id` int NOT NULL,
  `email` varchar(128) DEFAULT NULL,
  `avatar` varchar(255) DEFAULT NULL,
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_user_profiles_user` (`user_id`),
  CONSTRAINT `user_profiles_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
