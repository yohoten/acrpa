"""
脚本版本管理 — SQLite 存储修改历史，支持 diff 对比和回退。

数据库: {APP_ROOT}/versions.db
表结构:
    script_versions:
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        script_path TEXT NOT NULL,        -- 脚本文件绝对路径
        version_num INTEGER NOT NULL,     -- 版本序号 (自增)
        timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
        content_json TEXT NOT NULL,       -- JSON 序列化的 ScriptData 列表
        row_count INTEGER,               -- 命令行数
        comment TEXT                      -- 版本备注

使用:
    from version_manager import VersionManager
    vm = VersionManager()
    vm.save_version(script_path, rows)
    history = vm.get_history(script_path)
    rows = vm.restore_version(script_path, version_num)
    diff = vm.diff_versions(script_path, v1, v2)  # 返回变更摘要
"""
import os
import json
import sqlite3
import time
import threading

from utils import log1

_lock = threading.Lock()

def _get_db_path():
    """获取数据库路径（放在 APP_ROOT 下）"""
    import state
    base = os.path.dirname(state.CONFIG_PATH)
    return os.path.join(base, "script_versions.db")


class VersionManager:
    """脚本版本管理器 — 线程安全"""

    def __init__(self, db_path=None):
        self._db = db_path or _get_db_path()
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self._db)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self):
        with _lock:
            conn = self._connect()
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS script_versions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        script_path TEXT NOT NULL,
                        version_num INTEGER NOT NULL,
                        timestamp TEXT DEFAULT (datetime('now','localtime')),
                        content_json TEXT NOT NULL,
                        row_count INTEGER DEFAULT 0,
                        comment TEXT DEFAULT ''
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_script_path
                    ON script_versions(script_path, version_num)
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS workflow_versions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        workflow_path TEXT NOT NULL,
                        version_num INTEGER NOT NULL,
                        timestamp TEXT DEFAULT (datetime('now','localtime')),
                        content_json TEXT NOT NULL,
                        step_count INTEGER DEFAULT 0,
                        comment TEXT DEFAULT ''
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_workflow_path
                    ON workflow_versions(workflow_path, version_num)
                """)
                conn.commit()
            finally:
                conn.close()

    def save_version(self, script_path, rows, comment=""):
        """
        保存当前脚本快照为新的版本记录。

        Args:
            script_path: 脚本文件绝对路径
            rows: ScriptData 列表
            comment: 版本备注（如"自动保存"）

        Returns:
            version_num: 新版本号
        """
        if not script_path:
            return 0

        # 序列化 ScriptData 为 JSON 兼容格式
        content = []
        for sd in rows:
            content.append({
                "cmd": sd.cmd_type,
                "args": list(sd.args)
            })

        content_json = json.dumps(content, ensure_ascii=False)

        with _lock:
            conn = self._connect()
            try:
                # 获取下一个版本号
                cur = conn.execute(
                    "SELECT COALESCE(MAX(version_num), 0) + 1 FROM script_versions WHERE script_path = ?",
                    (script_path,)
                )
                version_num = cur.fetchone()[0]

                conn.execute(
                    """INSERT INTO script_versions 
                       (script_path, version_num, content_json, row_count, comment)
                       VALUES (?, ?, ?, ?, ?)""",
                    (script_path, version_num, content_json, len(rows), comment)
                )
                conn.commit()

                # 限制每个脚本最多保留 50 个版本
                conn.execute("""
                    DELETE FROM script_versions WHERE script_path = ? AND id NOT IN (
                        SELECT id FROM script_versions WHERE script_path = ?
                        ORDER BY version_num DESC LIMIT 50
                    )
                """, (script_path, script_path))
                conn.commit()

                log1("版本已保存: {} v{} ({} 行)".format(
                    os.path.basename(script_path), version_num, len(rows)))
                return version_num
            except Exception as e:
                log1("版本保存失败: {}".format(e), "error")
                return 0
            finally:
                conn.close()

    def get_history(self, script_path):
        """
        获取脚本的版本历史。

        Returns:
            [(version_num, timestamp, row_count, comment), ...] 按版本号降序
        """
        if not script_path:
            return []

        conn = self._connect()
        try:
            cur = conn.execute(
                """SELECT version_num, timestamp, row_count, comment
                   FROM script_versions WHERE script_path = ?
                   ORDER BY version_num DESC""",
                (script_path,)
            )
            return cur.fetchall()
        except Exception:
            return []
        finally:
            conn.close()

    def restore_version(self, script_path, version_num):
        """
        恢复指定版本的脚本内容。

        Returns:
            [(cmd_type, [args...]), ...] 或 None
        """
        conn = self._connect()
        try:
            cur = conn.execute(
                "SELECT content_json FROM script_versions WHERE script_path = ? AND version_num = ?",
                (script_path, version_num)
            )
            row = cur.fetchone()
            if not row:
                log1("版本 v{} 不存在".format(version_num), "warning")
                return None

            content = json.loads(row[0])
            rows = []
            from scriptdata import ScriptData
            for item in content:
                rows.append(ScriptData(item["cmd"], item["args"]))

            log1("已恢复版本 v{} ({} 行)".format(version_num, len(rows)))
            return rows
        except Exception as e:
            log1("版本恢复失败: {}".format(e), "error")
            return None
        finally:
            conn.close()

    def diff_versions(self, script_path, v1, v2=None):
        """
        对比两个版本的差异。

        Args:
            v1: 旧版本号
            v2: 新版本号（None=当前编辑器内容）

        Returns:
            str: 人类可读的 diff 摘要
        """
        old_rows = self.restore_version(script_path, v1)
        if old_rows is None:
            return "无法加载版本 v{}".format(v1)

        old_map = {}  # row_index -> cmd_type
        for i, sd in enumerate(old_rows):
            old_map[i] = "{}: {}".format(sd.cmd_type, ",".join(sd.args[:3]))

        if v2 is not None:
            new_rows = self.restore_version(script_path, v2)
            if new_rows is None:
                return "无法加载版本 v{}".format(v2)
        else:
            # 对比当前编辑器
            import state
            new_rows = getattr(state, '_editor_rows', [])

        lines = []
        lines.append("版本对比: v{} → v{}".format(v1, v2 or "当前"))
        lines.append("=" * 50)

        old_count = len(old_rows)
        new_count = len(new_rows)
        max_rows = max(old_count, new_count)
        changes = 0
        added = 0
        removed = 0

        for i in range(max_rows):
            old_line = old_map.get(i, None)
            if i < new_count:
                new_sd = new_rows[i]
                new_line = "{}: {}".format(new_sd.cmd_type, ",".join(new_sd.args[:3]))
            else:
                new_line = None

            if old_line is None and new_line is not None:
                lines.append("  + [行{}] {}".format(i + 1, new_line))
                added += 1
                changes += 1
            elif old_line is not None and new_line is None:
                lines.append("  - [行{}] {}".format(i + 1, old_line))
                removed += 1
                changes += 1
            elif old_line != new_line:
                lines.append("  ~ [行{}] {} → {}".format(i + 1, old_line, new_line))
                changes += 1

        lines.append("-" * 50)
        if changes == 0:
            lines.append("无差异")
        else:
            lines.append("变更: {} 处 (新增 {} 行, 删除 {} 行)".format(changes, added, removed))
        lines.append("总行数: {} → {}".format(old_count, new_count))

        return "\n".join(lines)

    def get_version_count(self, script_path):
        """获取版本总数"""
        conn = self._connect()
        try:
            cur = conn.execute(
                "SELECT COUNT(*) FROM script_versions WHERE script_path = ?",
                (script_path,)
            )
            return cur.fetchone()[0]
        except Exception:
            return 0
        finally:
            conn.close()

    # ── 工作流版本历史 ────────────────────────────────────────────────
    def save_workflow_version(self, workflow_path, wf_data, comment=""):
        """保存工作流 JSON dict 快照为新版本。

        Args:
            workflow_path: 工作流文件绝对路径
            wf_data: 工作流 dict ({"steps": [...], ...})
            comment: 版本备注

        Returns:
            version_num 或 0 (失败)
        """
        if not workflow_path:
            return 0
        try:
            content_json = json.dumps(wf_data, ensure_ascii=False)
        except Exception as e:
            log1("工作流版本序列化失败: {}".format(e), "error")
            return 0

        steps = wf_data.get("steps", []) if isinstance(wf_data, dict) else []
        step_count = len(steps)

        with _lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    """SELECT COALESCE(MAX(version_num), 0) + 1
                       FROM workflow_versions WHERE workflow_path = ?""",
                    (workflow_path,)
                )
                version_num = cur.fetchone()[0]
                conn.execute(
                    """INSERT INTO workflow_versions
                       (workflow_path, version_num, content_json, step_count, comment)
                       VALUES (?, ?, ?, ?, ?)""",
                    (workflow_path, version_num, content_json, step_count, comment)
                )
                conn.commit()
                # 每个工作流最多保留 50 个版本
                conn.execute("""
                    DELETE FROM workflow_versions WHERE workflow_path = ? AND id NOT IN (
                        SELECT id FROM workflow_versions WHERE workflow_path = ?
                        ORDER BY version_num DESC LIMIT 50
                    )
                """, (workflow_path, workflow_path))
                conn.commit()
                log1("工作流版本已保存: {} v{} ({} 步骤)".format(
                    os.path.basename(workflow_path), version_num, step_count))
                return version_num
            except Exception as e:
                log1("工作流版本保存失败: {}".format(e), "error")
                return 0
            finally:
                conn.close()

    def get_workflow_history(self, workflow_path):
        """返回 [(version_num, timestamp, step_count, comment), ...] 按版本号降序。"""
        if not workflow_path:
            return []
        conn = self._connect()
        try:
            cur = conn.execute(
                """SELECT version_num, timestamp, step_count, comment
                   FROM workflow_versions WHERE workflow_path = ?
                   ORDER BY version_num DESC""",
                (workflow_path,)
            )
            return cur.fetchall()
        except Exception:
            return []
        finally:
            conn.close()

    def restore_workflow_version(self, workflow_path, version_num):
        """恢复指定版本的工作流 dict; 不存在返回 None。"""
        conn = self._connect()
        try:
            cur = conn.execute(
                "SELECT content_json FROM workflow_versions WHERE workflow_path = ? AND version_num = ?",
                (workflow_path, version_num)
            )
            row = cur.fetchone()
            if not row:
                log1("工作流版本 v{} 不存在".format(version_num), "warning")
                return None
            return json.loads(row[0])
        except Exception as e:
            log1("工作流版本恢复失败: {}".format(e), "error")
            return None
        finally:
            conn.close()

    def diff_workflow_versions(self, workflow_path, v1, v2=None):
        """对比两个工作流版本 (v2=None 时对比 v1 与给定 current)。

        Returns:
            str 人类可读差异摘要。
        """
        old_def = self.restore_workflow_version(workflow_path, v1)
        if old_def is None:
            return "无法加载工作流版本 v{}".format(v1)
        old_steps = old_def.get("steps", []) if isinstance(old_def, dict) else []

        if v2 is not None:
            new_def = self.restore_workflow_version(workflow_path, v2)
            if new_def is None:
                return "无法加载工作流版本 v{}".format(v2)
            new_steps = new_def.get("steps", []) if isinstance(new_def, dict) else []
            other = "v{}".format(v2)
        else:
            new_steps = getattr(self, "_wf_current_steps", None) or []
            other = "当前"

        lines = ["工作流版本对比: v{} → {}".format(v1, other), "=" * 50]
        old_count, new_count = len(old_steps), len(new_steps)
        max_rows = max(old_count, new_count)
        changes = added = removed = 0

        def _desc(s):
            if not isinstance(s, dict):
                return str(s)
            t = s.get("type", "?")
            if t == "script":
                return "script {}".format(s.get("path", ""))
            if t == "command":
                return "command {}".format(s.get("cmd", ""))
            if t == "variable":
                return "variable {}={}".format(s.get("var_name", ""), s.get("var_value", ""))
            if t in ("parallel", "loop", "retry"):
                return "{} ({} 子步骤)".format(t, len(s.get("steps", [])))
            if t == "workflow":
                return "workflow {}".format(s.get("path", ""))
            if t == "condition":
                return "condition {}".format(s.get("if", ""))
            if t == "wait":
                return "wait {}s".format(s.get("seconds", ""))
            if t == "log":
                return "log {}".format(s.get("text", ""))
            return t

        for i in range(max_rows):
            old_desc = _desc(old_steps[i]) if i < old_count else None
            new_desc = _desc(new_steps[i]) if i < new_count else None
            if old_desc is None and new_desc is not None:
                lines.append("  + [步骤{}] {}".format(i + 1, new_desc))
                added += 1
                changes += 1
            elif old_desc is not None and new_desc is None:
                lines.append("  - [步骤{}] {}".format(i + 1, old_desc))
                removed += 1
                changes += 1
            elif old_desc != new_desc:
                lines.append("  ~ [步骤{}] {} → {}".format(i + 1, old_desc, new_desc))
                changes += 1

        lines.append("-" * 50)
        if changes == 0:
            lines.append("无差异")
        else:
            lines.append("变更: {} 处 (新增 {} 步, 删除 {} 步)".format(changes, added, removed))
        lines.append("总步骤数: {} → {}".format(old_count, new_count))
        return "\n".join(lines)


# 全局单例
_version_manager = None

def get_version_manager():
    global _version_manager
    if _version_manager is None:
        _version_manager = VersionManager()
    return _version_manager
